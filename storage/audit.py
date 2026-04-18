"""
DARA — Audit Logger Service
============================
High-level service that wraps PostgresClient.log_audit_event() to provide
a clean interface for recording all HITL decisions and admin actions.

This is injected (or used as a singleton) wherever audit events need to be fired.
It is designed to be fire-and-forget — audit failures NEVER block the business logic.

Usage:
    from storage.audit import AuditLogger
    audit = AuditLogger(postgres)
    await audit.record(
        action="fix_approved",
        actor="@slack-user",
        resource_type="fix",
        resource_id=fix_id,
        after_state={"pr_url": pr_url},
        ip_address=request_ip,
    )
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    """Structured audit event ready for persistence."""
    action: str
    actor: str = "system"
    resource_type: str | None = None
    resource_id: str | None = None
    before_state: dict | None = None
    after_state: dict | None = None
    ip_address: str | None = None
    extra_data: dict | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AuditLogger:
    """
    Thread-safe audit logger that persists events to the audit_log table.
    All write operations are best-effort — exceptions are caught and logged.
    """

    def __init__(self, postgres=None) -> None:
        self._pg = postgres

    def _get_pg(self):
        if self._pg:
            return self._pg
        from storage.postgres import get_postgres
        return get_postgres()

    async def record(
        self,
        action: str,
        actor: str = "system",
        resource_type: str | None = None,
        resource_id: str | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        ip_address: str | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """
        Persist an audit event.
        Returns the audit log entry UUID (empty string on failure).
        """
        try:
            pg = self._get_pg()
            audit_id = await pg.log_audit_event(
                action=action,
                actor=actor,
                resource_type=resource_type,
                resource_id=resource_id,
                before_state=before_state,
                after_state=after_state,
                ip_address=ip_address,
                metadata=extra_data,
            )
            logger.debug("AuditLogger: %s by %s → %s/%s [id=%s]",
                         action, actor, resource_type, resource_id, audit_id[:8] if audit_id else "-")
            return audit_id
        except Exception as e:
            logger.warning("AuditLogger: record failed (non-fatal): %s", e)
            return ""

    def record_background(
        self,
        action: str,
        actor: str = "system",
        resource_type: str | None = None,
        resource_id: str | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        ip_address: str | None = None,
        extra_data: dict | None = None,
    ) -> None:
        """
        Schedule audit recording as a background task (non-blocking).
        Use inside sync code or when you don't need the audit ID.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.record(
                    action=action, actor=actor,
                    resource_type=resource_type, resource_id=resource_id,
                    before_state=before_state, after_state=after_state,
                    ip_address=ip_address, extra_data=extra_data,
                ))
        except Exception as e:
            logger.warning("AuditLogger: background schedule failed: %s", e)


# Singleton — used by middleware and routers.
# Postgres is injected lazily on first use.
_audit_logger: AuditLogger | None = None


def get_audit_logger(postgres=None) -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(postgres=postgres)
    return _audit_logger
