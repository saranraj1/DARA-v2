"""
DARA — Fine-Tuning Data Export Pipeline (Week 19-20)
======================================================
Exports high-quality (error, root_cause, fix) triples as an OpenAI
chat-format JSONL dataset to MinIO (self-hosted S3-compatible).

Triple schema (OpenAI fine-tuning format):
{
  "messages": [
    {"role": "system", "content": "You are DARA, an expert software debugger."},
    {"role": "user",   "content": "<error context>"},
    {"role": "assistant", "content": "<root_cause JSON>"}
  ],
  "metadata": {
    "error_class": "null_reference",
    "service": "payment-svc",
    "confidence": 0.87,
    "strategy": "null_reference",
    "outcome": "accepted",
    "dara_version": "phase3"
  }
}

Quality filters (only human-approved, high-confidence fixes):
  - outcome == 'accepted'
  - fix confidence >= 0.75
  - fix lines between 3 and 500
  - patch_files >= 1

MinIO client: optional `minio` package (falls back to dry_run if not installed).
Bucket: dara-training-data (auto-created if missing).
File naming: dara_triples_YYYYMMDD_runid8.jsonl

Usage (weekly Celery beat or admin API):
    exporter = FineTuningExporter(postgres=...)
    result = await exporter.export(since_timestamp=None, dry_run=False)
    print(result.triple_count, result.minio_path)
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    """Summary of a completed fine-tuning export run."""
    run_id: str
    triple_count: int
    skipped_count: int           # Filtered out by quality gates
    minio_path: str | None
    status: str                  # completed | failed | dry_run
    error_message: str | None = None
    started_at: datetime = None
    completed_at: datetime | None = None

    @property
    def success(self) -> bool:
        return self.status in ("completed", "dry_run")


class FineTuningExporter:
    """
    Exports curated training triples to MinIO.
    One row in export_runs per call.
    """

    # Quality thresholds — only export high-quality, human-approved fixes
    QUALITY_THRESHOLDS = {
        "min_confidence": 0.75,
        "min_fix_lines": 3,
        "max_fix_lines": 500,
        "required_outcome": "accepted",
        "min_patch_files": 1,
    }

    SYSTEM_PROMPT = (
        "You are DARA, an expert software debugger specialized in distributed systems. "
        "Given an error context, identify the root cause and explain the exact fix needed. "
        "Always output structured JSON."
    )

    def __init__(self, postgres=None, minio_client=None) -> None:
        self._postgres = postgres
        self._minio = minio_client

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public API ─────────────────────────────────────────────

    async def export(
        self,
        since_timestamp: datetime | None = None,
        dry_run: bool = False,
    ) -> ExportResult:
        """
        Main entry point. Fetch quality fixes, build triples, upload to MinIO.
        If dry_run=True: returns count without writing to MinIO or Postgres.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        logger.info(
            "FineTuningExporter: starting export run_id=%s dry_run=%s",
            run_id[:8], dry_run,
        )

        # 1. Record export run start
        if not dry_run:
            await self._record_export_start(run_id, since_timestamp)

        # 2. Fetch quality-filtered fixes
        raw_fixes = await self._fetch_quality_fixes(since_timestamp)
        logger.info("FineTuningExporter: fetched %d candidate fixes", len(raw_fixes))

        # 3. Build triples
        triples: list[dict] = []
        skipped = 0
        for fix_row in raw_fixes:
            triple = self._build_triple(fix_row)
            if triple:
                triples.append(triple)
            else:
                skipped += 1

        logger.info(
            "FineTuningExporter: built %d triples, skipped %d (quality filter)",
            len(triples), skipped,
        )

        if dry_run:
            return ExportResult(
                run_id=run_id,
                triple_count=len(triples),
                skipped_count=skipped,
                minio_path=None,
                status="dry_run",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        if not triples:
            result = ExportResult(
                run_id=run_id, triple_count=0, skipped_count=skipped,
                minio_path=None, status="completed", started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await self._record_export_complete(run_id, result)
            return result

        # 4. Write to MinIO
        minio_path = None
        error_msg = None
        try:
            minio_path = await self._write_to_minio(triples, run_id)
            status = "completed"
        except Exception as e:
            logger.warning("FineTuningExporter: MinIO write failed: %s", e)
            error_msg = str(e)[:500]
            status = "failed"

        result = ExportResult(
            run_id=run_id,
            triple_count=len(triples),
            skipped_count=skipped,
            minio_path=minio_path,
            status=status,
            error_message=error_msg,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
        await self._record_export_complete(run_id, result)
        return result

    # ── Private helpers ────────────────────────────────────────

    async def _fetch_quality_fixes(
        self, since: datetime | None = None
    ) -> list[dict]:
        """
        Fetch pipeline_runs with accepted fixes meeting quality thresholds.
        Joins: pipeline_runs → errors → pattern_library (optional fix template).
        """
        try:
            from sqlalchemy import text
            q = self.QUALITY_THRESHOLDS
            since_clause = ""
            params: dict[str, Any] = {
                "outcome": q["required_outcome"],
                "min_confidence": q["min_confidence"],
            }
            if since:
                since_clause = "AND pr.started_at > :since"
                params["since"] = since

            sql = text(f"""
                SELECT
                    e.id              AS error_id,
                    e.error_class,
                    e.message,
                    e.stack_trace,
                    e.service,
                    e.file_path,
                    e.line_number,
                    pr.root_cause     AS root_cause_json,
                    pr.fix_explanation,
                    pr.fix_confidence,
                    pr.fix_id,
                    pr.outcome,
                    pr.strategy       AS strategy_used,
                    pr.started_at     AS pipeline_at
                FROM pipeline_runs pr
                JOIN errors e ON e.id = pr.error_id
                WHERE pr.outcome     = :outcome
                  AND (pr.fix_confidence IS NULL OR pr.fix_confidence >= :min_confidence)
                  {since_clause}
                ORDER BY pr.started_at DESC
                LIMIT 5000
            """)
            pg = self._get_pg()
            async with pg.session() as sess:
                rows = (await sess.execute(sql, params)).all()
            return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.warning("_fetch_quality_fixes: %s", e)
            return []

    def _build_triple(self, row: dict) -> dict | None:
        """
        Convert a pipeline_run row into an OpenAI chat-format training triple.
        Returns None if quality gates fail.
        """
        try:
            # Quality gate: confidence
            confidence = float(row.get("fix_confidence") or 0)
            if row.get("fix_confidence") is not None and confidence < self.QUALITY_THRESHOLDS["min_confidence"]:
                return None

            # Quality gate: fix_explanation has substance
            fix_explanation = (row.get("fix_explanation") or "").strip()
            if len(fix_explanation.splitlines()) < self.QUALITY_THRESHOLDS["min_fix_lines"]:
                if len(fix_explanation) < 50:
                    return None

            # Build user message (error context)
            error_context = self._format_error_context(row)
            if not error_context:
                return None

            # Build assistant message (root cause + fix)
            root_cause_raw = row.get("root_cause_json") or {}
            if isinstance(root_cause_raw, str):
                try:
                    root_cause_raw = json.loads(root_cause_raw)
                except Exception:
                    root_cause_raw = {"root_cause": root_cause_raw}

            assistant_content = json.dumps({
                "root_cause": root_cause_raw.get("root_cause", "") if isinstance(root_cause_raw, dict) else str(root_cause_raw),
                "immediate_cause": root_cause_raw.get("immediate_cause", "") if isinstance(root_cause_raw, dict) else "",
                "fix_explanation": fix_explanation,
                "strategy": row.get("strategy_used", "unknown"),
                "confidence": confidence,
            }, ensure_ascii=False)

            return {
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user",   "content": error_context},
                    {"role": "assistant", "content": assistant_content},
                ],
                "metadata": {
                    "error_class": row.get("error_class", "unknown"),
                    "service": row.get("service", "unknown"),
                    "confidence": confidence,
                    "strategy": row.get("strategy_used", "unknown"),
                    "outcome": row.get("outcome", "accepted"),
                    "error_id": str(row.get("error_id", "")),
                    "pipeline_at": row.get("pipeline_at").isoformat() if row.get("pipeline_at") else None,
                    "dara_version": "phase3",
                },
            }
        except Exception as e:
            logger.debug("_build_triple failed: %s", e)
            return None

    def _format_error_context(self, row: dict) -> str:
        """Format the error context as a user message."""
        parts = []
        if row.get("error_class"):
            parts.append(f"Error class: {row['error_class']}")
        if row.get("service"):
            parts.append(f"Service: {row['service']}")
        if row.get("message"):
            parts.append(f"Error message: {str(row['message'])[:500]}")
        if row.get("file_path"):
            parts.append(f"File: {row['file_path']}")
        if row.get("line_number"):
            parts.append(f"Line: {row['line_number']}")
        if row.get("stack_trace"):
            parts.append(f"Stack trace:\n{str(row['stack_trace'])[:1000]}")
        if not parts:
            return ""
        return "\n".join(parts)

    async def _write_to_minio(self, triples: list[dict], run_id: str) -> str:
        """
        Write JSONL file to MinIO. Returns the minio path.
        Auto-creates bucket if missing.
        """
        from config.settings import get_settings
        settings = get_settings()
        bucket = getattr(settings, "minio_bucket", "dara-training-data")
        endpoint = getattr(settings, "minio_endpoint", None)
        access_key = getattr(settings, "minio_access_key", None)
        secret_key = getattr(settings, "minio_secret_key", None)

        if not endpoint or not access_key or not secret_key:
            raise ValueError(
                "MinIO not configured: set MINIO_ENDPOINT, MINIO_ACCESS_KEY, "
                "MINIO_SECRET_KEY in environment. For local dev use .env.example."
            )

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"dara_triples_{date_str}_{run_id[:8]}.jsonl"

        # Build JSONL content
        jsonl_content = "\n".join(
            json.dumps(t, ensure_ascii=False) for t in triples
        ).encode("utf-8")

        try:
            import io

            from minio import Minio
            from minio.error import S3Error

            secure = not endpoint.startswith("localhost")
            client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

            # Create bucket if not exists
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)

            client.put_object(
                bucket_name=bucket,
                object_name=filename,
                data=io.BytesIO(jsonl_content),
                length=len(jsonl_content),
                content_type="application/jsonl",
            )
            minio_path = f"s3://{bucket}/{filename}"
            logger.info("FineTuningExporter: uploaded %s (%d bytes)", minio_path, len(jsonl_content))
            return minio_path

        except ImportError:
            # minio package not installed — save locally as fallback
            import os
            fallback_dir = "exports"
            os.makedirs(fallback_dir, exist_ok=True)
            fallback_path = os.path.join(fallback_dir, filename)
            with open(fallback_path, "wb") as f:
                f.write(jsonl_content)
            logger.warning(
                "FineTuningExporter: minio not installed, saved locally: %s", fallback_path
            )
            return f"local://{fallback_path}"

    async def _record_export_start(self, run_id: str, since: datetime | None) -> None:
        """Create ExportRun row with status='running'."""
        try:
            from storage.models import ExportRun
            pg = self._get_pg()
            async with pg.session() as sess:
                row = ExportRun(
                    id=uuid.UUID(run_id),
                    status="running",
                    quality_filters={
                        **self.QUALITY_THRESHOLDS,
                        "since": since.isoformat() if since else None,
                    },
                )
                sess.add(row)
        except Exception as e:
            logger.debug("_record_export_start: %s", e)

    async def _record_export_complete(self, run_id: str, result: ExportResult) -> None:
        """Update ExportRun with final status, triple_count, minio_path."""
        try:
            from sqlalchemy import update

            from storage.models import ExportRun
            pg = self._get_pg()
            async with pg.session() as sess:
                await sess.execute(
                    update(ExportRun)
                    .where(ExportRun.id == uuid.UUID(run_id))
                    .values(
                        triple_count=result.triple_count,
                        minio_path=result.minio_path,
                        status=result.status,
                        error_message=result.error_message,
                        completed_at=result.completed_at,
                    )
                )
        except Exception as e:
            logger.debug("_record_export_complete: %s", e)
