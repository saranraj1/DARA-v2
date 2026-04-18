"""
DARA — Cross-Service Context Builder (Week 8-9)
=================================================
When a distributed bug spans multiple services, fetches code context from
ALL implicated codebases — not just the service that reported the error.

Flow:
  1. Receive FaultCascade (from FaultPropagationMapper)
  2. For each affected service, look up its repo from ServiceRegistry
  3. Fetch the erroring file/function from that repo via GitHub API
  4. AST-chunk and embed the relevant code
  5. Return a CrossServiceBundle with per-service context

Unlike the single-service ContextBuilder which reads from disk,
CrossServiceContextBuilder fetches from GitHub because:
  - the erroring service may not be local
  - the repo may be on a different branch / deployment commit

Usage:
    from context.cross_service import CrossServiceContextBuilder
    from graph.fault_propagation import FaultCascade

    builder = CrossServiceContextBuilder(github_token=..., postgres=...)
    bundle = await builder.build(cascade, context_bundle)
    # bundle.service_contexts["payment-svc"].erroring_function
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServiceContext:
    """Context fetched from a single service's codebase."""
    service_name: str
    repo_full_name: str
    file_path: str | None
    function_name: str | None
    code_snippet: str | None
    language: str | None = None
    fetch_status: str = "ok"      # ok | not_found | no_registry | error


@dataclass
class CrossServiceBundle:
    """Aggregated context across all services implicated in a distributed bug."""
    trace_id: str
    root_service: str
    service_contexts: dict[str, ServiceContext] = field(default_factory=dict)
    services_fetched: int = 0
    services_failed: int = 0
    total_code_lines: int = 0

    @property
    def has_multi_service_context(self) -> bool:
        return self.services_fetched > 1

    def as_prompt_block(self) -> str:
        """Format all service contexts as a prompt-ready block for the LLM."""
        blocks = []
        for svc, ctx in self.service_contexts.items():
            if ctx.code_snippet:
                blocks.append(
                    f"=== Service: {svc} | Repo: {ctx.repo_full_name} ===\n"
                    f"File: {ctx.file_path or 'unknown'} | "
                    f"Function: {ctx.function_name or 'unknown'}\n"
                    f"```{ctx.language or 'python'}\n{ctx.code_snippet[:1500]}\n```"
                )
        return "\n\n".join(blocks) if blocks else "No cross-service context available."


class CrossServiceContextBuilder:
    """
    Fetches code from multiple service repos using GitHub API.
    Falls back gracefully when a repo is not registered or not accessible.
    """

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

    async def build(
        self,
        cascade,  # FaultCascade
        spans: list | None = None,
    ) -> CrossServiceBundle:
        """
        Build cross-service context from a FaultCascade.
        For each affected service, find file+function from OTel attributes,
        then fetch from GitHub.
        """
        bundle = CrossServiceBundle(
            trace_id=cascade.trace_id,
            root_service=cascade.root_service,
        )

        # Build a map of service → (file_path, function) from cascade attributes
        svc_locations = self._extract_service_locations(cascade, spans or [])

        for service_name in cascade.affected_services:
            ctx = await self._fetch_service_context(
                service_name=service_name,
                location=svc_locations.get(service_name, {}),
            )
            bundle.service_contexts[service_name] = ctx
            if ctx.fetch_status == "ok":
                bundle.services_fetched += 1
                if ctx.code_snippet:
                    bundle.total_code_lines += len(ctx.code_snippet.splitlines())
            else:
                bundle.services_failed += 1

        logger.info(
            "CrossServiceContextBuilder: fetched %d/%d services for trace=%s",
            bundle.services_fetched,
            len(cascade.affected_services),
            cascade.trace_id[:12],
        )
        return bundle

    # ── Private helpers ────────────────────────────────────────

    def _extract_service_locations(self, cascade, spans: list) -> dict[str, dict]:
        """
        Extract file_path and function for each service from:
        1. OTel span attributes (code.filepath, code.function)
        2. FaultCascade.root_file_path / root_function (for root service)
        """
        locations: dict[str, dict] = {}

        # Root service gets cascade-level attributes
        if cascade.root_service:
            locations[cascade.root_service] = {
                "file_path": cascade.root_file_path,
                "function": cascade.root_function,
            }

        # Other services: pull from span attributes
        for span in spans:
            span_dict = span if isinstance(span, dict) else {
                "service_name": getattr(span, "service_name", None),
                "status_code": getattr(span, "status_code", None),
                "attributes": getattr(span, "attributes", {}) or {},
            }
            svc = span_dict.get("service_name")
            if not svc or span_dict.get("status_code") != "ERROR":
                continue
            attrs = span_dict.get("attributes") or {}
            if svc not in locations:
                locations[svc] = {
                    "file_path": attrs.get("code.filepath") or attrs.get("code.namespace"),
                    "function": attrs.get("code.function") or attrs.get("rpc.method"),
                }
        return locations

    async def _get_service_registry(self, service_name: str) -> dict | None:
        """Lookup service → repo mapping from ServiceRegistry in Postgres."""
        try:
            from storage.models import ServiceRegistry
            from sqlalchemy import select
            pg = self._get_pg()
            async with pg.session() as sess:
                row = (
                    await sess.execute(
                        select(ServiceRegistry).where(
                            ServiceRegistry.service_name == service_name
                        )
                    )
                ).scalar_one_or_none()
                if not row:
                    return None
                return {
                    "repo_full_name": row.repo_full_name,
                    "language": row.primary_language,
                    "default_branch": row.default_branch,
                }
        except Exception as e:
            logger.warning(
                "CrossServiceContextBuilder: registry lookup failed for %s: %s",
                service_name, e,
            )
            return None

    async def _fetch_service_context(
        self, service_name: str, location: dict
    ) -> ServiceContext:
        """Fetch erroring file content from GitHub for a given service."""
        registry = await self._get_service_registry(service_name)
        if not registry:
            return ServiceContext(
                service_name=service_name,
                repo_full_name="unknown",
                file_path=location.get("file_path"),
                function_name=location.get("function"),
                code_snippet=None,
                fetch_status="no_registry",
            )

        repo = registry["repo_full_name"]
        branch = registry.get("default_branch", "main")
        language = registry.get("language", "python")
        file_path = location.get("file_path")

        if not file_path:
            return ServiceContext(
                service_name=service_name,
                repo_full_name=repo,
                file_path=None,
                function_name=location.get("function"),
                code_snippet=None,
                language=language,
                fetch_status="not_found",
            )

        code = await self._fetch_file_from_github(
            repo_full_name=repo,
            file_path=file_path,
            branch=branch,
        )
        return ServiceContext(
            service_name=service_name,
            repo_full_name=repo,
            file_path=file_path,
            function_name=location.get("function"),
            code_snippet=code,
            language=language,
            fetch_status="ok" if code else "not_found",
        )

    async def _fetch_file_from_github(
        self, repo_full_name: str, file_path: str, branch: str = "main"
    ) -> str | None:
        """Fetch raw file content from GitHub Contents API."""
        if not self._token:
            logger.debug(
                "CrossServiceContextBuilder: no GitHub token, skipping fetch for %s",
                file_path,
            )
            return None
        try:
            import httpx, base64
            url = f"https://api.github.com/repos/{repo_full_name}/contents/{file_path}"
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers, params={"ref": branch})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("encoding") == "base64":
                    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            logger.debug(
                "CrossServiceContextBuilder: GitHub fetch %d for %s/%s",
                resp.status_code, repo_full_name, file_path,
            )
            return None
        except Exception as e:
            logger.warning(
                "CrossServiceContextBuilder: fetch failed %s/%s: %s",
                repo_full_name, file_path, e,
            )
            return None
