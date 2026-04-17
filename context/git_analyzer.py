"""DARA - Git blame + commit history analyzer"""
from __future__ import annotations
import logging, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

class GitAnalyzer:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self._ok = self._git(["rev-parse","--git-dir"]) is not None

    def get_recent_commits(self, file_path: str | None = None, days: int = 30, max_commits: int = 10) -> list[dict]:
        if not self._ok:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        cmd = ["log", f"--since={since}", f"--max-count={max_commits}",
               "--format=%H|%an|%ae|%aI|%s", "--name-only"]
        if file_path:
            rel = self._rel(file_path)
            if rel:
                cmd += ["--", rel]
        out = self._git(cmd)
        return self._parse_log(out) if out else []

    def get_blame_info(self, file_path: str, line_number: int) -> dict | None:
        if not self._ok:
            return None
        rel = self._rel(file_path)
        if not rel:
            return None
        out = self._git(["blame", "-L", f"{line_number},{line_number}", "-p", "--", rel])
        return self._parse_blame(out, line_number) if out else None

    def get_file_authors(self, file_path: str) -> list[dict]:
        if not self._ok:
            return []
        rel = self._rel(file_path)
        if not rel:
            return []
        out = self._git(["log", "--format=%an|%ae", "--", rel])
        if not out:
            return []
        counts: dict = {}
        for line in out.splitlines():
            if "|" not in line:
                continue
            name, email = line.split("|", 1)
            k = email.strip()
            counts.setdefault(k, {"author": name.strip(), "email": k, "commits": 0})
            counts[k]["commits"] += 1
        return sorted(counts.values(), key=lambda x: x["commits"], reverse=True)[:5]

    def _git(self, args: list[str]) -> str | None:
        try:
            r = subprocess.run(["git"] + args, capture_output=True, text=True,
                               cwd=str(self.repo_path), timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception as e:
            logger.warning("git error: %s", e)
            return None

    def _rel(self, file_path: str) -> str | None:
        try:
            return str(Path(file_path).relative_to(self.repo_path))
        except ValueError:
            return file_path if (self.repo_path / file_path).exists() else None

    def _parse_log(self, output: str) -> list[dict]:
        commits, current = [], None
        for line in output.splitlines():
            parts = line.split("|", 4)
            if len(parts) >= 5:
                current = {"sha": parts[0][:12], "sha_full": parts[0], "author": parts[1],
                           "email": parts[2], "date": parts[3], "message": parts[4], "files_changed": []}
                commits.append(current)
            elif line.strip() and current is not None:
                current["files_changed"].append(line.strip())
        return commits

    def _parse_blame(self, output: str, line_number: int) -> dict | None:
        result: dict = {"line_number": line_number}
        for line in output.splitlines():
            if not result.get("sha") and len(line.split()) >= 3:
                result["sha"] = line.split()[0][:12]
            elif line.startswith("author "):
                result["author"] = line[7:].strip()
            elif line.startswith("author-mail "):
                result["email"] = line[12:].strip().strip("<>")
            elif line.startswith("summary "):
                result["summary"] = line[8:].strip()
            elif line.startswith("\t"):
                result["line_content"] = line[1:]
        return result if "sha" in result else None
