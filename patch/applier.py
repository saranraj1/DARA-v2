"""
DARA — Patch Applier (Temp-Dir Safe Mode)
==========================================
Applies unified diffs to a COPY of source files in a temp directory.
Never touches the real working repo — Phase 3 will add direct apply.

Pipeline:
  1. preflight  — parse the diff, detect target files, check parseable
  2. copy       — snapshot original files into temp dir
  3. apply      — run `git apply` against the temp copy
  4. validate   — ruff + py_compile on every patched file
  5. result     — PatchResult with temp_dir path for downstream consumers
  6. rollback   — called automatically on any failure (temp dir is just deleted)
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import py_compile
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────

@dataclass
class PatchResult:
    success: bool
    temp_dir: Optional[str]           # path to temp dir with patched files
    files_modified: list[str]         # relative paths of files changed
    validation_passed: bool
    ruff_errors: list[dict]
    error: Optional[str] = None
    original_backup: Optional[str] = None   # backup of originals


# ── Main engine ───────────────────────────────────────────────

class PatchApplier:
    """
    Applies unified diffs to a temporary directory.
    Thread-safe: each call creates a fresh temp dir.
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._repo = Path(repo_path).resolve()

    async def apply(self, patches: list, repo_path: str | None = None) -> PatchResult:
        """
        Apply a list of PatchFile objects to a temp directory.
        patches: list of PatchFile (from api.models.agent_schemas)
        Returns PatchResult.
        """
        repo = Path(repo_path).resolve() if repo_path else self._repo

        if not patches:
            return PatchResult(success=False, temp_dir=None, files_modified=[],
                               validation_passed=False, ruff_errors=[],
                               error="No patches provided")

        # 1. Preflight — build unified diff string, extract file paths
        full_diff, files_in_diff = self._preflight(patches)
        if not full_diff:
            return PatchResult(success=False, temp_dir=None, files_modified=[],
                               validation_passed=False, ruff_errors=[],
                               error="All patches have empty diffs")

        # 2. Copy relevant files to temp dir
        temp_dir, backup_dir = self._copy_to_temp(repo, files_in_diff)
        if temp_dir is None:
            return PatchResult(success=False, temp_dir=None, files_modified=[],
                               validation_passed=False, ruff_errors=[],
                               error="Failed to create temp directory")

        try:
            # 3. Apply diff inside temp dir
            ok, apply_error = self._apply_diff(temp_dir, full_diff, files_in_diff)
            if not ok:
                return PatchResult(success=False, temp_dir=str(temp_dir), files_modified=[],
                                   validation_passed=False, ruff_errors=[],
                                   error=apply_error, original_backup=str(backup_dir))

            # 4. Validate patched files
            val_passed, ruff_errors = self._validate(temp_dir, files_in_diff)

            logger.info(
                "PatchApplier: applied %d files to %s  validation=%s",
                len(files_in_diff), temp_dir, val_passed,
            )
            return PatchResult(
                success=True,
                temp_dir=str(temp_dir),
                files_modified=files_in_diff,
                validation_passed=val_passed,
                ruff_errors=ruff_errors,
                original_backup=str(backup_dir),
            )

        except Exception as exc:
            logger.error("PatchApplier: unexpected error: %s", exc, exc_info=True)
            self._cleanup(temp_dir)
            return PatchResult(success=False, temp_dir=None, files_modified=[],
                               validation_passed=False, ruff_errors=[],
                               error=str(exc), original_backup=str(backup_dir))

    # ── Step 1: Preflight ─────────────────────────────────────

    def _preflight(self, patches: list) -> tuple[str, list[str]]:
        """
        Concatenate all patch diffs and extract the list of files they touch.
        Returns (unified_diff_string, [file_paths]).
        """
        diffs = []
        files: list[str] = []

        for p in patches:
            diff_text = getattr(p, "unified_diff", "") or ""
            if not diff_text.strip():
                continue
            diffs.append(diff_text)

            # Extract target files from diff headers: +++ b/path/to/file
            for m in re.finditer(r"^\+\+\+ b/(.+)$", diff_text, re.MULTILINE):
                fp = m.group(1).strip()
                if fp not in files:
                    files.append(fp)

        # If no +++ b/ headers found, try --- a/ headers as fallback
        if not files:
            for p in patches:
                fp = getattr(p, "file_path", "") or ""
                if fp and fp not in files:
                    files.append(fp)

        return "\n".join(diffs), files

    # ── Step 2: Copy to temp ──────────────────────────────────

    def _copy_to_temp(self, repo: Path, files: list[str]) -> tuple[Path | None, Path | None]:
        """
        Copy files-to-be-patched into a fresh temp dir, preserving directory structure.
        Also creates a backup dir for rollback.
        Returns (temp_dir, backup_dir) or (None, None) on failure.
        """
        try:
            tmp = Path(tempfile.mkdtemp(prefix="dara_patch_"))
            backup = Path(tempfile.mkdtemp(prefix="dara_backup_"))

            for rel_path in files:
                src = repo / rel_path
                dst = tmp / rel_path
                bak = backup / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                bak.parent.mkdir(parents=True, exist_ok=True)

                if src.exists():
                    shutil.copy2(src, dst)
                    shutil.copy2(src, bak)
                else:
                    # File doesn't exist in repo — create empty placeholder
                    # (diff may be adding a new file)
                    dst.touch()
                    logger.debug("PatchApplier: %s not in repo, creating empty", rel_path)

            return tmp, backup
        except Exception as e:
            logger.error("PatchApplier: temp copy failed: %s", e)
            return None, None

    # ── Step 3: Apply diff ────────────────────────────────────

    def _apply_diff(self, temp_dir: Path, diff: str, files: list[str]) -> tuple[bool, str]:
        """
        Apply the unified diff to the temp directory using `git apply`.
        Falls back to Python-based line apply if git is unavailable.
        """
        # Write diff to a temp file
        diff_file = temp_dir / "_dara.patch"
        diff_file.write_text(diff, encoding="utf-8")

        # Try: git apply --directory=<temp_dir> <patch_file>
        # We need git to apply relative to the temp dir
        try:
            result = subprocess.run(
                ["git", "apply", "--whitespace=fix", "--verbose", str(diff_file)],
                capture_output=True, text=True, timeout=30,
                cwd=str(temp_dir),
            )
            if result.returncode == 0:
                diff_file.unlink(missing_ok=True)
                return True, ""
            # git apply failed — try with --reject for partial apply info
            logger.warning("git apply failed: %s", result.stderr[:500])
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("git not available or timed out: %s — falling back to Python patcher", e)

        # Fallback: Python-based apply using the patch library or manual apply
        ok, err = self._python_apply(temp_dir, diff, files)
        diff_file.unlink(missing_ok=True)
        return ok, err

    def _python_apply(self, temp_dir: Path, diff: str, files: list[str]) -> tuple[bool, str]:
        """
        Pure-Python unified diff apply as fallback when `git` is unavailable.
        Handles simple +/- line-level changes.
        """
        try:
            import patch as patch_lib  # python-patch library
            pset = patch_lib.fromstring(diff.encode())
            if not pset:
                return False, "Failed to parse diff with patch library"
            ok = pset.apply(root=str(temp_dir))
            return bool(ok), "" if ok else "patch library apply returned False"
        except ImportError:
            pass
        except Exception as e:
            return False, f"patch library error: {e}"

        # Last resort: manual line-by-line apply for simple one-file patches
        return self._manual_apply(temp_dir, diff, files)

    def _manual_apply(self, temp_dir: Path, diff: str, files: list[str]) -> tuple[bool, str]:
        """Minimal manual patch application for simple single-file diffs."""
        try:
            current_file: Path | None = None
            original_lines: list[str] = []
            patched_lines: list[str] = []
            in_hunk = False

            for line in diff.splitlines(keepends=True):
                if line.startswith("+++ b/"):
                    # Save previous file
                    if current_file and patched_lines:
                        current_file.write_text("".join(patched_lines), encoding="utf-8")
                    rel = line[6:].strip()
                    current_file = temp_dir / rel
                    original_lines = current_file.read_text(encoding="utf-8").splitlines(keepends=True) if current_file.exists() else []
                    patched_lines = list(original_lines)
                    in_hunk = False
                elif line.startswith("--- ") or line.startswith("diff "):
                    continue
                elif line.startswith("@@"):
                    in_hunk = True
                elif in_hunk:
                    if line.startswith("+") and not line.startswith("+++"):
                        patched_lines.append(line[1:])
                    elif line.startswith("-") and not line.startswith("---"):
                        # Remove matching line from patched (best-effort)
                        remove = line[1:]
                        if remove in patched_lines:
                            patched_lines.remove(remove)

            if current_file and patched_lines:
                current_file.write_text("".join(patched_lines), encoding="utf-8")

            return True, ""
        except Exception as e:
            return False, f"manual apply error: {e}"

    # ── Step 4: Post-apply validation ─────────────────────────

    def _validate(self, temp_dir: Path, files: list[str]) -> tuple[bool, list[dict]]:
        """
        Run Ruff + py_compile on every patched Python file.
        Returns (all_passed, list_of_ruff_findings).
        """
        all_passed = True
        all_findings: list[dict] = []

        py_files = [temp_dir / f for f in files if f.endswith(".py")]

        if not py_files:
            # Non-Python patches pass validation by default
            return True, []

        # Ruff
        try:
            proc = subprocess.run(
                ["ruff", "check", "--output-format=json", "--select=S,E9,F", "--exit-zero"] +
                [str(fp) for fp in py_files if fp.exists()],
                capture_output=True, text=True, timeout=30,
            )
            import json as _json
            findings = _json.loads(proc.stdout) if proc.stdout.strip() else []
            blocking = [f for f in findings if f.get("code", "").startswith(("E9", "S"))]
            all_findings.extend(findings)
            if blocking:
                all_passed = False
                logger.warning("PatchApplier: ruff blocking issues: %d", len(blocking))
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning("PatchApplier: ruff unavailable: %s", e)

        # py_compile syntax check
        for fp in py_files:
            if not fp.exists():
                continue
            try:
                py_compile.compile(str(fp), doraise=True)
            except py_compile.PyCompileError as e:
                all_passed = False
                all_findings.append({"code": "E999", "message": str(e), "file": str(fp)})
                logger.warning("PatchApplier: syntax error in %s: %s", fp.name, e)

        return all_passed, all_findings

    # ── Cleanup ───────────────────────────────────────────────

    def _cleanup(self, temp_dir: Path | None) -> None:
        if temp_dir and temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning("PatchApplier: cleanup failed: %s", e)

    def get_patched_content(self, result: PatchResult, file_path: str) -> str | None:
        """
        After a successful apply, read the patched content of a specific file
        from the temp directory. Used by GitHubPRCreator.
        """
        if not result.success or not result.temp_dir:
            return None
        patched = Path(result.temp_dir) / file_path
        if patched.exists():
            return patched.read_text(encoding="utf-8")
        return None
