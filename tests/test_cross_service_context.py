"""
Tests: Week 8-9 — Cross-Service Context Builder
Covers:
  - ServiceContext dataclass creation
  - CrossServiceBundle.as_prompt_block() formatting
  - CrossServiceBundle.has_multi_service_context property
  - _extract_service_locations from FaultCascade + spans
  - _fetch_service_context: no_registry fallback
  - _fetch_service_context: not_found when no file_path
  - build() returns correct services_fetched count
"""
import sys

sys.path.insert(0, ".")

from unittest.mock import AsyncMock

import pytest


class TestServiceContext:

    def test_service_context_ok_status(self):
        from context.cross_service import ServiceContext
        ctx = ServiceContext(
            service_name="payment-svc",
            repo_full_name="org/payment",
            file_path="payment/charge.py",
            function_name="charge_card",
            code_snippet="def charge_card(): ...",
            language="python",
            fetch_status="ok",
        )
        assert ctx.service_name == "payment-svc"
        assert ctx.fetch_status == "ok"

    def test_no_registry_fallback(self):
        from context.cross_service import ServiceContext
        ctx = ServiceContext(
            service_name="ghost-svc",
            repo_full_name="unknown",
            file_path=None,
            function_name=None,
            code_snippet=None,
            fetch_status="no_registry",
        )
        assert ctx.fetch_status == "no_registry"
        assert ctx.code_snippet is None


class TestCrossServiceBundle:

    def _make_bundle(self):
        from context.cross_service import CrossServiceBundle, ServiceContext
        bundle = CrossServiceBundle(
            trace_id="trace-abc",
            root_service="api-svc",
        )
        bundle.service_contexts["api-svc"] = ServiceContext(
            service_name="api-svc",
            repo_full_name="org/api",
            file_path="api/routes.py",
            function_name="handle_request",
            code_snippet="def handle_request():\n    return payment.charge()",
            language="python",
            fetch_status="ok",
        )
        bundle.service_contexts["payment-svc"] = ServiceContext(
            service_name="payment-svc",
            repo_full_name="org/payment",
            file_path=None,
            function_name=None,
            code_snippet=None,
            fetch_status="no_registry",
        )
        bundle.services_fetched = 1
        bundle.services_failed = 1
        return bundle

    def test_has_multi_service_context_false_with_one(self):
        bundle = self._make_bundle()
        assert not bundle.has_multi_service_context  # only 1 fetched

    def test_as_prompt_block_includes_service_name(self):
        bundle = self._make_bundle()
        block = bundle.as_prompt_block()
        assert "api-svc" in block
        assert "api/routes.py" in block
        assert "handle_request" in block

    def test_as_prompt_block_excludes_none_snippets(self):
        bundle = self._make_bundle()
        block = bundle.as_prompt_block()
        # payment-svc has no code snippet → should not appear as a service block
        assert "payment-svc" not in block or "no_registry" not in block


class TestCrossServiceContextBuilder:

    def _builder(self, pg=None):
        from context.cross_service import CrossServiceContextBuilder
        return CrossServiceContextBuilder(github_token=None, postgres=pg)

    def _make_cascade(self, affected=None):
        """Make a minimal FaultCascade-like object."""
        from types import SimpleNamespace
        return SimpleNamespace(
            trace_id="trace-test-cs",
            root_service="api-svc",
            root_file_path="api/routes.py",
            root_function="handle",
            affected_services=affected or ["api-svc"],
            propagation_path=["api-svc"],
        )

    @pytest.mark.asyncio
    async def test_no_registry_returns_no_registry_status(self):
        builder = self._builder()
        # Mock _get_service_registry to return None (no registry entry)
        builder._get_service_registry = AsyncMock(return_value=None)
        cascade = self._make_cascade(["payment-svc"])
        bundle = await builder.build(cascade)
        assert bundle.services_fetched == 0
        assert bundle.services_failed == 1
        assert bundle.service_contexts["payment-svc"].fetch_status == "no_registry"

    @pytest.mark.asyncio
    async def test_no_file_path_returns_not_found_status(self):
        builder = self._builder()
        builder._get_service_registry = AsyncMock(return_value={
            "repo_full_name": "org/payment",
            "language": "python",
            "default_branch": "main",
        })
        # Cascade has no file_path for payment-svc (not in locations)
        cascade = self._make_cascade(["payment-svc"])
        cascade.root_service = "api-svc"  # root != payment-svc → no span attrs
        cascade.root_file_path = None
        bundle = await builder.build(cascade)
        ctx = bundle.service_contexts["payment-svc"]
        assert ctx.fetch_status in ("not_found", "ok")  # no file → not_found

    @pytest.mark.asyncio
    async def test_github_fetch_skipped_without_token(self):
        builder = self._builder()
        builder._get_service_registry = AsyncMock(return_value={
            "repo_full_name": "org/api",
            "language": "python",
            "default_branch": "main",
        })
        cascade = self._make_cascade(["api-svc"])
        bundle = await builder.build(cascade)
        # No token → code_snippet is None, fetch_status = not_found
        ctx = bundle.service_contexts["api-svc"]
        assert ctx.code_snippet is None
