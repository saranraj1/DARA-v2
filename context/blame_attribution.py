"""
DARA — Blame Attribution Engine (Week 9-10)
============================================
Answers: which commit, by which engineer, introduced this bug?

Algorithm:
  1. Pull deploy events for the erroring service (from deploy_events table)
  2. Find the LATEST deploy BEFORE the error timestamp
  3. Use PyGitHub to fetch commits on that SHA
  4. If the erroring file_path is known, find git blame for that file
  5. Cross-reference: commit in blame matches last deploy → high confidence

Output: BlameResult with commit SHA, author, confidence, and narrative.

Falls back gracefully at each step:
  - No deploy events → blame_confidence = low, uses raw commit history
  - No GitHub access → returns partial result from deploy_events only
  - No file_path → blames last deployer

Usage:
    engine = BlameAttributionEngine(github_token=..., postgres=...)
    result = await engine.attribute(
        service_name="payment-svc",
        error_timestamp=datetime.now(timezone.utc),
        file_path="payment/charge.py",
        line_number=142,
    )
    # result.author_email, result.commit_sha, result.blame_confidence
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BlameResult:
    """Structured output from BlameAttributionEngine."""
    service_name: str
    commit_sha: str | None
    commit_sha_short: str | None
    author_email: str | None
    author_name: str | None
    commit_message: str | None
    committed_at: datetime | None
    deployed_at: datetime | None
    hours_before_error: float | None        # How long between deploy and error
    file_path: str | None
    blame_line: int | None
    blame_confidence: float               # 0.0–1.0
    confidence_reasons: list[str]
    deploy_found: bool = False
    blame_method: str = "unknown"         # deploy_correlation | git_blame | commit_history

    @property
    def summary(self) -> str:
        if not self.commit_sha:
            return f"[{self.service_name}] No blame identified (confidence=0.0)"
        hours = f"{self.hours_before_error:.1f}h before" if self.hours_before_error else "unknown time"
        return (
            f"[{self.service_name}] {self.author_name or self.author_email} "
            f"({self.commit_sha_short}) deployed {hours} error — "
            f"confidence={self.blame_confidence:.0%}"
        )


class BlameAttributionEngine:
    """
    Correlates deploy events, git commits, and error timestamps
    to identify the root cause commit.
    """

    # A commit deployed within this window before an error is suspicious
    RECENT_DEPLOY_HOURS = 24

    def __init__(
        self,
        github_token: str | None = None,
        postgres=None,
    ) -> None:
        self._token = github_token
        self._postgres = postgres

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public API ─────────────────────────────────────────────

    async def attribute(
        self,
        service_name: str,
        error_timestamp: datetime,
        file_path: str | None = None,
        line_number: int | None = None,
        repo_full_name: str | None = None,
    ) -> BlameResult:
        """
        Main entry point. Returns the most probable blame result.
        """
        reasons: list[str] = []
        confidence = 0.0
        commit_sha = None
        author_email = None
        author_name = None
        commit_message = None
        committed_at = None
        deployed_at = None
        hours_before = None
        blame_method = "unknown"

        # Step 1: Find latest deploy before error
        pg = self._get_pg()
        deploy = await pg.get_latest_deploy_before(service_name, error_timestamp)
        deploy_found = deploy is not None

        if deploy:
            commit_sha = deploy.get("commit_sha")
            deployed_at_raw = deploy.get("deployed_at")
            deployed_at = (
                datetime.fromisoformat(deployed_at_raw)
                if isinstance(deployed_at_raw, str)
                else deployed_at_raw
            )
            deployer = deploy.get("deployed_by", "unknown")
            confidence += 0.35
            reasons.append(f"Deploy found: commit {(commit_sha or '')[:8]} by {deployer}")

            # Check if deploy was recent (within RECENT_DEPLOY_HOURS)
            if deployed_at:
                diff = error_timestamp - deployed_at
                hours_before = diff.total_seconds() / 3600
                if hours_before <= self.RECENT_DEPLOY_HOURS:
                    confidence += 0.25
                    reasons.append(
                        f"Deploy was {hours_before:.1f}h before error "
                        f"(within {self.RECENT_DEPLOY_HOURS}h window) — HIGH suspicion"
                    )
                elif hours_before <= 72:
                    confidence += 0.10
                    reasons.append(f"Deploy was {hours_before:.1f}h before error — moderate suspicion")
            blame_method = "deploy_correlation"

        # Step 2: Enrich with GitHub commit details
        if commit_sha and self._token and repo_full_name:
            gh_info = await self._fetch_commit_from_github(repo_full_name, commit_sha)
            if gh_info:
                author_email = gh_info.get("email")
                author_name = gh_info.get("name")
                commit_message = gh_info.get("message")
                committed_at = gh_info.get("committed_at")
                confidence += 0.15
                reasons.append(
                    f"Commit verified on GitHub: {author_name} <{author_email}>"
                )
                blame_method = "deploy_correlation+github"

        # Step 3: Git blame enhancement (if file_path and line_number known)
        if file_path and line_number and self._token and repo_full_name and not commit_sha:
            blame_info = await self._git_blame(
                repo_full_name, file_path, line_number
            )
            if blame_info:
                commit_sha = blame_info.get("commit_sha")
                author_email = blame_info.get("email")
                author_name = blame_info.get("name")
                commit_message = blame_info.get("message")
                confidence += 0.20
                reasons.append(
                    f"Git blame on {file_path}:{line_number} → "
                    f"commit {(commit_sha or '')[:8]} by {author_name}"
                )
                blame_method = "git_blame"

        # Step 4: No info at all
        if not commit_sha and not deploy_found:
            reasons.append("No deploy events or GitHub access — cannot identify blame")

        confidence = round(max(0.0, min(1.0, confidence)), 2)

        return BlameResult(
            service_name=service_name,
            commit_sha=commit_sha,
            commit_sha_short=commit_sha[:8] if commit_sha else None,
            author_email=author_email,
            author_name=author_name,
            commit_message=commit_message,
            committed_at=committed_at,
            deployed_at=deployed_at,
            hours_before_error=hours_before,
            file_path=file_path,
            blame_line=line_number,
            blame_confidence=confidence,
            confidence_reasons=reasons,
            deploy_found=deploy_found,
            blame_method=blame_method,
        )

    # ── Private helpers ────────────────────────────────────────

    async def _fetch_commit_from_github(
        self, repo_full_name: str, commit_sha: str
    ) -> dict | None:
        """Fetch commit author details from GitHub."""
        try:
            import httpx
            url = f"https://api.github.com/repos/{repo_full_name}/commits/{commit_sha}"
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                commit_data = data.get("commit", {})
                author_data = commit_data.get("author", {})
                gh_author = data.get("author") or {}
                return {
                    "name": author_data.get("name"),
                    "email": author_data.get("email"),
                    "message": commit_data.get("message", "")[:200],
                    "committed_at": datetime.fromisoformat(
                        author_data.get("date", "").replace("Z", "+00:00")
                    ) if author_data.get("date") else None,
                    "login": gh_author.get("login"),
                }
        except Exception as e:
            logger.warning(
                "BlameAttribution: GitHub commit fetch failed %s: %s", commit_sha[:8], e
            )
        return None

    async def _git_blame(
        self, repo_full_name: str, file_path: str, line_number: int
    ) -> dict | None:
        """
        Use GitHub Blame API to identify who last modified a specific line.
        Uses GraphQL API (REST doesn't expose per-line blame).
        """
        if not self._token:
            return None
        try:
            import httpx
            query = """
            query($owner: String!, $name: String!, $path: String!) {
              repository(owner: $owner, name: $name) {
                defaultBranchRef { target { ... on Commit {
                  blame(path: $path) {
                    ranges {
                      startingLine endingLine
                      commit {
                        oid abbreviatedOid
                        message
                        author { name email date }
                      }
                    }
                  }
                }}}
              }
            }"""
            owner, repo = repo_full_name.split("/", 1)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.github.com/graphql",
                    json={"query": query, "variables": {"owner": owner, "name": repo, "path": file_path}},
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if resp.status_code != 200:
                return None
            ranges = (
                resp.json()
                .get("data", {})
                .get("repository", {})
                .get("defaultBranchRef", {})
                .get("target", {})
                .get("blame", {})
                .get("ranges", [])
            )
            for r in ranges:
                if r["startingLine"] <= line_number <= r["endingLine"]:
                    commit = r["commit"]
                    author = commit.get("author", {})
                    return {
                        "commit_sha": commit.get("oid"),
                        "name": author.get("name"),
                        "email": author.get("email"),
                        "message": commit.get("message", "")[:200],
                    }
        except Exception as e:
            logger.warning("BlameAttribution: git blame failed %s: %s", file_path, e)
        return None
