"""
DARA — Git Committer
=====================
Creates a feature branch, commits patched files from temp dir,
and pushes to remote. Works alongside PatchApplier (temp-dir mode).

In Phase 2, files come from temp dir (safe, no working tree changes).
In Phase 3, will apply directly to working tree before committing.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CommitResult:
    success: bool
    branch: Optional[str]
    commit_sha: Optional[str]
    pushed: bool
    error: Optional[str] = None


class GitCommitter:
    """
    Creates a git branch, copies patched files from temp dir, commits, pushes.
    Requires git to be available and the repo to have a remote configured.
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._repo = Path(repo_path).resolve()

    async def commit_and_push(
        self,
        fix_id: str,
        error_class: str,
        temp_dir: str,
        files_modified: list[str],
        fix_explanation: str,
        base_branch: str = "main",
    ) -> CommitResult:
        """
        Full flow: stash safety → create branch → copy from temp → commit → push.
        """
        branch = f"dara/fix-{error_class.lower().replace(' ', '-')}-{fix_id[:8]}"

        # 1. Verify git is available
        if not self._git_available():
            return CommitResult(success=False, branch=None, commit_sha=None,
                                pushed=False, error="git not available in PATH")

        # 2. Verify we're in a git repo
        if not (self._repo / ".git").exists():
            return CommitResult(success=False, branch=None, commit_sha=None,
                                pushed=False, error=f"Not a git repo: {self._repo}")

        try:
            # 3. Get current branch (so we can return to it if needed)
            current = self._git("rev-parse", "--abbrev-ref", "HEAD")

            # 4. Create new branch from base
            self._git("checkout", "-b", branch, f"origin/{base_branch}",
                      check=False)
            # If branch creation failed (branch exists), just checkout
            out = self._git("checkout", branch, check=False)
            logger.info("GitCommitter: on branch %s", branch)

            # 5. Copy patched files from temp dir into repo
            self._copy_temp_to_repo(temp_dir, files_modified)

            # 6. Stage files
            for fp in files_modified:
                self._git("add", fp)

            # 7. Check if there are staged changes
            diff_out = self._git("diff", "--cached", "--name-only")
            if not diff_out.strip():
                logger.info("GitCommitter: no staged changes after copy — using empty commit")

            # 8. Commit
            commit_msg = self._build_commit_message(fix_id, error_class, fix_explanation,
                                                     files_modified)
            self._git("commit", "--allow-empty", "-m", commit_msg)

            # 9. Get commit SHA
            sha = self._git("rev-parse", "HEAD").strip()

            # 10. Push
            push_result = subprocess.run(
                ["git", "push", "--set-upstream", "origin", branch],
                capture_output=True, text=True, cwd=str(self._repo),
            )
            pushed = push_result.returncode == 0
            if not pushed:
                logger.warning("GitCommitter: push failed: %s", push_result.stderr[:300])

            # 11. Return to original branch
            self._git("checkout", current, check=False)

            logger.info("GitCommitter: committed sha=%s branch=%s pushed=%s", sha, branch, pushed)
            return CommitResult(success=True, branch=branch, commit_sha=sha, pushed=pushed)

        except Exception as exc:
            logger.error("GitCommitter: failed: %s", exc, exc_info=True)
            # Attempt to return to original branch
            try:
                current = self._git("rev-parse", "--abbrev-ref", "HEAD", check=False)
                if current == branch:
                    self._git("checkout", base_branch, check=False)
            except Exception:
                pass
            return CommitResult(success=False, branch=branch, commit_sha=None,
                                pushed=False, error=str(exc))

    def _copy_temp_to_repo(self, temp_dir: str, files: list[str]) -> None:
        """Copy patched files from temp dir back to the real repo directory."""
        tmp = Path(temp_dir)
        for rel_path in files:
            src = tmp / rel_path
            dst = self._repo / rel_path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                logger.debug("GitCommitter: copied %s -> repo", rel_path)
            else:
                logger.warning("GitCommitter: patched file not found in temp: %s", rel_path)

    def _build_commit_message(
        self, fix_id: str, error_class: str,
        explanation: str, files: list[str],
    ) -> str:
        """Conventional commit message with DARA metadata."""
        short_exp = explanation[:120] if explanation else "DARA auto-generated fix"
        files_str = ", ".join(Path(f).name for f in files[:3])
        if len(files) > 3:
            files_str += f" (+{len(files)-3} more)"
        return (
            f"fix({error_class.lower()}): {short_exp}\n\n"
            f"Files: {files_str}\n"
            f"Fix-ID: {fix_id}\n"
            f"Source: DARA Autonomous Debugger (Phase 2)\n"
            f"Co-authored-by: DARA-AI <dara@autonomous-debugger>"
        )

    def _git(self, *args: str, check: bool = True) -> str:
        """Run a git command and return stdout."""
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=str(self._repo),
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr[:300]}")
        return result.stdout

    def _git_available(self) -> bool:
        try:
            subprocess.run(["git", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
