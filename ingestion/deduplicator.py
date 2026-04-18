"""
DARA — Error Deduplicator
==========================
Prevents the same error from spawning multiple pipeline runs.

Strategy:
  signature = sha256(error_class + "::" + message[:200] + "::" + service + "::" + file_path)

  If an error with the same signature already exists in status
  {analyzing, fixing, validating, fixed} → skip and return existing error_id.

  'failed' or 'escalated' errors are eligible for re-analysis (may be transient).
  'pending' errors → also skip to avoid parallel processing of the same event.

Design note:
  The signature is computed before DB insertion so it costs only a SHA256 hash.
  No LLM calls are made during deduplication.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Statuses that count as "already being handled" — skip pipeline
_ACTIVE_STATUSES = {"pending", "analyzing", "fixing", "validating", "fixed"}


@dataclass
class DedupResult:
    is_duplicate: bool
    existing_error_id: str | None
    existing_status: str | None
    signature: str


class ErrorDeduplicator:
    """
    Computes error signatures and checks for duplicate active pipelines.
    Thread-safe, stateless — all state is in Postgres.
    """

    def compute_signature(self, error: dict) -> str:
        """
        Deterministic sha256 hash from the key fields of an error payload.
        Stable across process restarts.
        """
        parts = [
            str(error.get("error_class", "") or ""),
            str(error.get("message", "") or "")[:200],
            str(error.get("service", "") or ""),
            str(error.get("file_path", "") or ""),
        ]
        raw = "::".join(parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def check(self, error: dict, postgres) -> DedupResult:
        """
        Check if an active pipeline already exists for this error signature.

        Args:
            error: normalized error dict
            postgres: PostgresClient instance

        Returns:
            DedupResult(is_duplicate=True, existing_error_id=...) if duplicate
            DedupResult(is_duplicate=False, ...) if safe to proceed
        """
        sig = self.compute_signature(error)

        try:
            existing = await postgres.find_error_by_signature(sig, active_statuses=list(_ACTIVE_STATUSES))
            if existing:
                logger.info(
                    "Deduplicator: SKIP — sig=%s... matches existing error_id=%s status=%s",
                    sig[:12], existing.get("id"), existing.get("status"),
                )
                return DedupResult(
                    is_duplicate=True,
                    existing_error_id=str(existing["id"]),
                    existing_status=existing.get("status"),
                    signature=sig,
                )
        except Exception as e:
            # Deduplication is best-effort — never block ingestion on a DB error
            logger.warning("Deduplicator: DB check failed: %s — proceeding", e)

        return DedupResult(is_duplicate=False, existing_error_id=None,
                           existing_status=None, signature=sig)
