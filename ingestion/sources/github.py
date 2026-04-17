"""
DARA — GitHub Actions/App Source Parser
Parses GitHub webhook payloads (workflow_run failures, push, pull_request)
into normalized error format for the ingestion pipeline.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class GitHubSourceParser:
    """
    Converts GitHub webhook events into normalized error dicts.
    Handles: workflow_run (CI failure), push (commit triggered error),
    pull_request (PR check failure).
    """

    def parse_workflow_run(self, payload: dict) -> dict | None:
        """
        Parse a workflow_run webhook event.
        Returns None if the workflow succeeded (nothing to debug).
        """
        run = payload.get("workflow_run", {})
        if run.get("conclusion") not in ("failure", "timed_out", "cancelled"):
            return None

        repo = payload.get("repository", {})
        commit_sha = run.get("head_sha", "")
        branch = run.get("head_branch", "")

        # Extract error context from workflow name + conclusion
        workflow_name = run.get("name", "Unknown Workflow")
        failed_jobs = self._extract_failed_jobs(run)

        error_text = failed_jobs.get("error_message", f"Workflow '{workflow_name}' failed")
        stack_trace = failed_jobs.get("stack_trace")
        file_path = failed_jobs.get("file_path")
        line_number = failed_jobs.get("line_number")

        return {
            "error_class": self._classify_from_workflow(
                error_text, run.get("conclusion", "failure")
            ),
            "message": error_text,
            "stack_trace": stack_trace,
            "file_path": file_path,
            "line_number": line_number,
            "service": repo.get("name"),
            "environment": "ci",
            "severity": "high" if run.get("conclusion") == "failure" else "medium",
            "source": "github_actions",
            "commit_sha": commit_sha[:40] if commit_sha else None,
            "branch": branch,
            "raw_payload": payload,
            "workflow_name": workflow_name,
            "workflow_run_id": str(run.get("id", "")),
            "repo_full_name": repo.get("full_name"),
            "repo_clone_url": repo.get("clone_url"),
        }

    def parse_push_event(self, payload: dict) -> dict | None:
        """Parse a push event for error signals (e.g., broken CI check added)."""
        repo = payload.get("repository", {})
        commit = payload.get("head_commit", {})
        if not commit:
            return None

        return {
            "error_class": "DeploymentEvent",
            "message": f"Push to {payload.get('ref', 'unknown')}: {commit.get('message', '')[:200]}",
            "stack_trace": None,
            "file_path": None,
            "line_number": None,
            "service": repo.get("name"),
            "environment": "production",
            "severity": "low",
            "source": "github_actions",
            "commit_sha": commit.get("id", "")[:40],
            "branch": payload.get("ref", "").replace("refs/heads/", ""),
            "raw_payload": payload,
            "repo_full_name": repo.get("full_name"),
        }

    # ─── Private Helpers ──────────────────────────────────────

    def _extract_failed_jobs(self, run: dict) -> dict:
        """
        Extract structured error info from workflow run.
        In a real implementation this would call GitHub API to fetch job logs.
        Here we extract what's available in the webhook payload.
        """
        result: dict = {}
        jobs_url = run.get("jobs_url", "")
        # Patterns to detect common CI failure types from commit messages/names
        conclusion_text = f"{run.get('name', '')} {run.get('conclusion', '')}"

        python_trace = re.search(
            r"(Traceback.*?(?:Error|Exception)[^\n]*)", conclusion_text, re.S
        )
        if python_trace:
            result["stack_trace"] = python_trace.group(1)

        file_match = re.search(r'File "([^"]+)", line (\d+)', conclusion_text)
        if file_match:
            result["file_path"] = file_match.group(1)
            result["line_number"] = int(file_match.group(2))

        result["error_message"] = (
            f"GitHub Actions workflow '{run.get('name')}' "
            f"failed with conclusion: {run.get('conclusion', 'failure')}"
        )
        return result

    def _classify_from_workflow(self, error_text: str, conclusion: str) -> str:
        if conclusion == "timed_out":
            return "NetworkTimeout"
        text = error_text.lower()
        if "test" in text or "pytest" in text or "unittest" in text:
            return "TestFailure"
        if "import" in text or "module" in text:
            return "ImportError"
        if "syntax" in text:
            return "SyntaxError"
        return "WorkflowFailure"
