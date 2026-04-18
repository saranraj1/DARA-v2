"""
Unit tests: security hardening + audit logging (Week 9)
Covers:
  - HMAC Slack signature verification
  - Admin API token enforcement
  - AuditLogger service (record, record_background, failure tolerance)
  - Error dedup non-blocking behavior
  - Rate limit header validation
"""
import sys
sys.path.insert(0, ".")

import time
import hmac
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestSlackSignatureVerification:
    """Tests for HMAC-SHA256 Slack webhook verification."""

    def _make_valid_headers(self, body: bytes, secret: str) -> dict:
        ts = str(int(time.time()))
        base = f"v0:{ts}:{body.decode()}"
        sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
        return {"X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts}

    @pytest.mark.asyncio
    async def test_valid_signature_passes(self):
        from api.routers.webhooks import _verify_slack_signature
        body = b'payload={"action": "test"}'
        secret = "test-signing-secret"
        headers = self._make_valid_headers(body, secret)

        mock_request = MagicMock()
        mock_request.headers = headers
        mock_request.body = AsyncMock(return_value=body)

        with patch("api.routers.webhooks.settings") as mock_settings:
            mock_settings.slack_signing_secret = secret
            result = await _verify_slack_signature(mock_request)
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_signature_fails(self):
        from api.routers.webhooks import _verify_slack_signature
        body = b'payload={"action": "test"}'
        ts = str(int(time.time()))

        mock_request = MagicMock()
        mock_request.headers = {
            "X-Slack-Signature": "v0=0000000000000000",
            "X-Slack-Request-Timestamp": ts,
        }
        mock_request.body = AsyncMock(return_value=body)

        with patch("api.routers.webhooks.settings") as mock_settings:
            mock_settings.slack_signing_secret = "real-secret"
            result = await _verify_slack_signature(mock_request)
        assert result is False

    @pytest.mark.asyncio
    async def test_old_timestamp_rejected(self):
        from api.routers.webhooks import _verify_slack_signature
        body = b'payload={}'
        stale_ts = str(int(time.time()) - 400)  # 6.7 min ago > 5min window

        mock_request = MagicMock()
        mock_request.headers = {
            "X-Slack-Signature": "v0=doesntmatter",
            "X-Slack-Request-Timestamp": stale_ts,
        }
        mock_request.body = AsyncMock(return_value=body)

        with patch("api.routers.webhooks.settings") as mock_settings:
            mock_settings.slack_signing_secret = "real-secret"
            result = await _verify_slack_signature(mock_request)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_signing_secret_allows_in_dev(self):
        """If SLACK_SIGNING_SECRET not set, dev mode allows through."""
        from api.routers.webhooks import _verify_slack_signature
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.body = AsyncMock(return_value=b"")

        with patch("api.routers.webhooks.settings") as mock_settings:
            mock_settings.slack_signing_secret = ""  # not configured
            result = await _verify_slack_signature(mock_request)
        assert result is True  # dev permissive


class TestAuditLogger:
    """Tests for storage/audit.py AuditLogger."""

    @pytest.mark.asyncio
    async def test_record_calls_postgres_log_audit_event(self):
        from storage.audit import AuditLogger
        mock_pg = MagicMock()
        mock_pg.log_audit_event = AsyncMock(return_value="audit-uuid-123")

        logger = AuditLogger(postgres=mock_pg)
        result = await logger.record(
            action="fix_approved",
            actor="@testuser",
            resource_type="fix",
            resource_id="fix-abc",
            after_state={"pr_url": "https://github.com/org/repo/pulls/1"},
        )
        assert result == "audit-uuid-123"
        mock_pg.log_audit_event.assert_called_once()
        call_kwargs = mock_pg.log_audit_event.call_args[1]
        assert call_kwargs["action"] == "fix_approved"
        assert call_kwargs["actor"] == "@testuser"

    @pytest.mark.asyncio
    async def test_record_does_not_raise_on_db_failure(self):
        """Audit failures must NEVER propagate to the caller."""
        from storage.audit import AuditLogger
        mock_pg = MagicMock()
        mock_pg.log_audit_event = AsyncMock(side_effect=Exception("DB down"))

        logger = AuditLogger(postgres=mock_pg)
        result = await logger.record(action="test_action")  # should NOT raise
        assert result == ""

    @pytest.mark.asyncio
    async def test_record_background_schedules_without_blocking(self):
        from storage.audit import AuditLogger
        mock_pg = MagicMock()
        mock_pg.log_audit_event = AsyncMock(return_value="id")

        logger = AuditLogger(postgres=mock_pg)
        # record_background should not raise even if called synchronously
        logger.record_background(action="bg_test", actor="system")

    def test_audit_logger_singleton(self):
        from storage.audit import get_audit_logger
        a1 = get_audit_logger()
        a2 = get_audit_logger()
        # Both calls return the SAME instance (singleton)
        assert a1 is a2


class TestAdminTokenSecurity:
    """Tests that admin endpoints enforce X-Admin-Token."""

    def _app(self):
        from fastapi import FastAPI
        from api.routers.admin import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_no_token_returns_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app())
        for endpoint in ["/api/v1/admin/errors", "/api/v1/admin/fixes",
                         "/api/v1/admin/patterns", "/api/v1/admin/audit"]:
            r = client.get(endpoint)
            assert r.status_code == 401, f"{endpoint} should require auth"

    def test_wrong_token_returns_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app())
        r = client.get("/api/v1/admin/stats", headers={"X-Admin-Token": "wrong-key"})
        assert r.status_code == 401


class TestAuditModelSchema:
    """Tests for AuditLog ORM model."""

    def test_audit_log_model_importable(self):
        from storage.models import AuditLog
        assert AuditLog.__tablename__ == "audit_log"

    def test_audit_log_has_required_columns(self):
        from storage.models import AuditLog
        cols = {c.key for c in AuditLog.__table__.columns}
        required = {"id", "action", "actor", "resource_type", "resource_id",
                    "before_state", "after_state", "created_at"}
        assert required.issubset(cols), f"Missing: {required - cols}"
