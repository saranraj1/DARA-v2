"""
DARA — PostgreSQL Async Client
Provides async connection pooling, session management, and CRUD helpers
for all core tables using SQLAlchemy 2.0 async ORM.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import get_settings
from storage.models import AuditLog, Error, Fix, PatternLibrary, PipelineRun

logger = logging.getLogger(__name__)


class PostgresClient:
    """
    Async PostgreSQL client using SQLAlchemy 2.0.
    Provides a connection pool and typed CRUD methods for all DARA tables.
    """

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Context manager that provides a transactional session."""
        async with self._session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    async def close(self) -> None:
        await self._engine.dispose()

    # ─── Error CRUD ──────────────────────────────────────────

    async def save_error(self, error_data: dict) -> str:
        """Persist a normalized error. Returns UUID string."""
        async with self.session() as sess:
            error = Error(
                error_class=error_data["error_class"],
                message=error_data["message"],
                file_path=error_data.get("file_path"),
                line_number=error_data.get("line_number"),
                column_number=error_data.get("column_number"),
                stack_trace=error_data.get("stack_trace"),
                service=error_data.get("service"),
                environment=error_data.get("environment", "production"),
                severity=error_data.get("severity", "medium"),
                status="pending",
                source=error_data.get("source", "direct"),
                commit_sha=error_data.get("commit_sha"),
                branch=error_data.get("branch"),
                trace_id=error_data.get("trace_id"),
                raw_payload=error_data.get("raw_payload"),
                auto_fix_eligible=error_data.get("auto_fix_eligible", False),
                signature=error_data.get("signature"),
            )
            sess.add(error)
            await sess.flush()
            logger.info("Saved error", extra={"error_id": str(error.id)})
            return str(error.id)

    async def find_error_by_signature(
        self,
        signature: str,
        active_statuses: list[str] | None = None,
    ) -> dict | None:
        """
        Look up the most recent error matching this signature with an active status.
        Returns a plain dict (id, status, created_at) or None.
        """
        statuses = active_statuses or ["pending", "analyzing", "fixing", "validating", "fixed"]
        async with self.session() as sess:
            result = await sess.execute(
                select(Error)
                .where(Error.signature == signature)
                .where(Error.status.in_(statuses))
                .order_by(Error.created_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row:
                return {"id": str(row.id), "status": row.status, "created_at": row.created_at}
            return None

    async def get_error(self, error_id: str) -> Error | None:
        async with self.session() as sess:
            result = await sess.execute(
                select(Error).where(Error.id == uuid.UUID(error_id))
            )
            return result.scalar_one_or_none()

    async def update_error_status(self, error_id: str, status: str) -> None:
        async with self.session() as sess:
            await sess.execute(
                update(Error)
                .where(Error.id == uuid.UUID(error_id))
                .values(status=status)
            )

    async def resolve_error(self, error_id: str) -> None:
        async with self.session() as sess:
            await sess.execute(
                update(Error)
                .where(Error.id == uuid.UUID(error_id))
                .values(status="fixed", resolved_at=datetime.now(timezone.utc))
            )

    # ─── Fix CRUD ────────────────────────────────────────────

    async def save_fix(self, fix_data: dict) -> str:
        """Persist a generated fix. Returns fix UUID string."""
        async with self.session() as sess:
            fix = Fix(
                error_id=uuid.UUID(fix_data["error_id"]),
                pipeline_run_id=uuid.UUID(fix_data["pipeline_run_id"])
                if fix_data.get("pipeline_run_id")
                else None,
                patch_content=fix_data["patch_content"],
                files_changed=fix_data.get("files_changed", []),
                lines_changed=fix_data.get("lines_changed"),
                confidence=fix_data.get("confidence"),
                strategy=fix_data.get("strategy"),
                prompt_version=fix_data.get("prompt_version", "v1"),
                llm_provider_used=fix_data.get("llm_provider_used"),
            )
            sess.add(fix)
            await sess.flush()
            logger.info(
                "Saved fix",
                extra={"fix_id": str(fix.id), "confidence": fix_data.get("confidence")},
            )
            return str(fix.id)

    async def get_fix(self, fix_id: str) -> Fix | None:
        async with self.session() as sess:
            result = await sess.execute(
                select(Fix).where(Fix.id == uuid.UUID(fix_id))
            )
            return result.scalar_one_or_none()

    async def update_fix_outcome(
        self,
        fix_id: str,
        outcome: str,
        reviewer_notes: str | None = None,
        pr_url: str | None = None,
        pr_number: int | None = None,
    ) -> None:
        values: dict = {"outcome": outcome}
        if reviewer_notes:
            values["reviewer_notes"] = reviewer_notes
        if pr_url:
            values["pr_url"] = pr_url
        if pr_number:
            values["pr_number"] = pr_number
        if outcome == "accepted":
            values["approved_at"] = datetime.now(timezone.utc)
        async with self.session() as sess:
            await sess.execute(
                update(Fix).where(Fix.id == uuid.UUID(fix_id)).values(**values)
            )

    async def update_fix_validation(
        self,
        fix_id: str,
        validation_pass: bool,
        test_results: dict,
        semgrep_results: dict,
        coverage_delta: float | None = None,
    ) -> None:
        async with self.session() as sess:
            await sess.execute(
                update(Fix)
                .where(Fix.id == uuid.UUID(fix_id))
                .values(
                    validation_pass=validation_pass,
                    test_results=test_results,
                    semgrep_results=semgrep_results,
                    coverage_delta=coverage_delta,
                )
            )

    # ─── Pipeline Run CRUD ───────────────────────────────────

    async def create_pipeline_run(self, error_id: str) -> str:
        async with self.session() as sess:
            run = PipelineRun(
                error_id=uuid.UUID(error_id),
                status="started",
                stages_completed=[],
            )
            sess.add(run)
            await sess.flush()
            return str(run.id)

    async def update_pipeline_stage(
        self, run_id: str, stage: str, status: str = "running"
    ) -> None:
        async with self.session() as sess:
            result = await sess.execute(
                select(PipelineRun).where(PipelineRun.id == uuid.UUID(run_id))
            )
            run = result.scalar_one_or_none()
            if run:
                run.current_stage = stage
                run.status = status
                if status == "completed":
                    stages = list(run.stages_completed or [])
                    run.stages_completed = stages + [stage]
                    run.completed_at = datetime.now(timezone.utc)
                elif status == "failed":
                    run.completed_at = datetime.now(timezone.utc)

    async def record_pipeline_run_completion(
        self,
        error_id: str,
        status: str,
        stage_reached: str | None = None,
        stages_completed: list[str] | None = None,
        sandbox_iterations: int = 0,
        security_retries: int = 0,
        escalation_trigger: str | None = None,
        error_message: str | None = None,
    ) -> str:
        async with self.session() as sess:
            result = await sess.execute(
                select(PipelineRun)
                .where(PipelineRun.error_id == uuid.UUID(error_id))
                .order_by(PipelineRun.started_at.desc())
                .limit(1)
            )
            run = result.scalar_one_or_none()
            if not run:
                run = PipelineRun(
                    error_id=uuid.UUID(error_id),
                    status=status,
                    stages_completed=stages_completed or [],
                )
                sess.add(run)

            run.status = status
            run.current_stage = stage_reached
            if stages_completed is not None:
                run.stages_completed = stages_completed
            run.sandbox_iterations = sandbox_iterations
            run.security_retries = security_retries
            run.escalation_trigger = escalation_trigger
            run.error_message = error_message
            run.completed_at = datetime.now(timezone.utc)
            await sess.flush()
            return str(run.id)

    async def get_latest_pipeline_run(self, error_id: str) -> PipelineRun | None:
        async with self.session() as sess:
            result = await sess.execute(
                select(PipelineRun)
                .where(PipelineRun.error_id == uuid.UUID(error_id))
                .order_by(PipelineRun.started_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    # ─── Pattern Library ─────────────────────────────────────

    async def get_fix_template(
        self, error_class: str, service: str | None = None
    ) -> PatternLibrary | None:
        async with self.session() as sess:
            query = (
                select(PatternLibrary)
                .where(PatternLibrary.error_class == error_class)
                .where(PatternLibrary.is_active.is_(True))
                .order_by(PatternLibrary.success_rate.desc())
                .limit(1)
            )
            result = await sess.execute(query)
            return result.scalar_one_or_none()

    async def upsert_pattern(self, pattern: dict) -> None:
        """Insert or update a pattern_library entry from an accepted fix."""
        async with self.session() as sess:
            existing = await sess.execute(
                select(PatternLibrary)
                .where(PatternLibrary.error_class == pattern["error_class"])
                .where(PatternLibrary.is_active.is_(True))
                .limit(1)
            )
            row = existing.scalar_one_or_none()
            if row:
                # Increment success rate via moving average
                row.success_rate = min(1.0, float(row.success_rate or 0.5) * 0.9 + 0.1)
                row.example_fix = pattern.get("example_fix", row.example_fix)
            else:
                row = PatternLibrary(
                    error_class=pattern["error_class"],
                    language="python",
                    fix_template=pattern.get("fix_strategy", "llm_single_file"),
                    example_fix=pattern.get("example_fix", ""),
                    success_rate=0.7,
                    is_active=True,
                )
                sess.add(row)
            await sess.commit()

    # ─── Metrics ─────────────────────────────────────────────

    async def get_pipeline_stats(self) -> dict:
        from sqlalchemy import func
        async with self.session() as sess:
            total_errors = await sess.scalar(select(func.count(Error.id)))
            total_fixes = await sess.scalar(select(func.count(Fix.id)))
            accepted = await sess.scalar(
                select(func.count(Fix.id)).where(Fix.outcome == "accepted")
            )
            avg_confidence = await sess.scalar(select(func.avg(Fix.confidence)))
            return {
                "total_errors": total_errors or 0,
                "total_fixes": total_fixes or 0,
                "accepted_fixes": accepted or 0,
                "avg_confidence": round(float(avg_confidence or 0), 3),
            }



    async def log_audit_event(
        self,
        action: str,
        actor: str = "system",
        resource_type: str | None = None,
        resource_id: str | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        ip_address: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Write an immutable audit event. Returns UUID. Never raises."""
        try:
            async with self.session() as sess:
                entry = AuditLog(
                    action=action, actor=actor,
                    resource_type=resource_type, resource_id=resource_id,
                    before_state=before_state, after_state=after_state,
                    ip_address=ip_address, extra_data=metadata,
                )
                sess.add(entry)
                await sess.flush()
                return str(entry.id)
        except Exception as e:
            logger.warning("AuditLog write failed (non-fatal): %s", e)
            return ""

    async def cleanup_stale_errors(self, stale_after_minutes: int = 30) -> int:
        """Mark errors stuck in analyzing/fixing as failed after timeout."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)
        async with self.session() as sess:
            result = await sess.execute(
                update(Error)
                .where(Error.status.in_(["analyzing", "fixing"]))
                .where(Error.created_at < cutoff)
                .values(status="failed")
                .returning(Error.id)
            )
            count = len(result.fetchall())
            if count:
                logger.warning("cleanup_stale_errors: marked %d errors as failed", count)
            return count

    # ── Distributed Traces (Week 6-7) ─────────────────────────

    async def save_span(self, span) -> str:
        """Persist a NormalisedSpan to distributed_traces. Returns UUID."""
        import uuid as _uuid

        from storage.models import DistributedTrace
        async with self.session() as sess:
            row = DistributedTrace(
                id=_uuid.uuid4(),
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                service_name=span.service_name,
                operation_name=span.operation_name,
                status_code=span.status_code,
                status_message=span.status_message,
                duration_ms=span.duration_ms,
                attributes=span.attributes,
                events=span.events,
                started_at=span.started_at,
            )
            sess.add(row)
            await sess.flush()
            return str(row.id)

    async def get_trace(self, trace_id: str) -> list[dict]:
        """Return all spans for a trace_id, ordered by start time."""
        from sqlalchemy import select

        from storage.models import DistributedTrace
        async with self.session() as sess:
            rows = (
                await sess.execute(
                    select(DistributedTrace)
                    .where(DistributedTrace.trace_id == trace_id)
                    .order_by(DistributedTrace.started_at.asc())
                )
            ).scalars().all()
            return [
                {
                    "id": str(r.id),
                    "trace_id": r.trace_id,
                    "span_id": r.span_id,
                    "parent_span_id": r.parent_span_id,
                    "service_name": r.service_name,
                    "operation_name": r.operation_name,
                    "status_code": r.status_code,
                    "status_message": r.status_message,
                    "duration_ms": r.duration_ms,
                    "attributes": r.attributes or {},
                    "started_at": r.started_at.isoformat(),
                }
                for r in rows
            ]

    async def list_traces(
        self, service: str | None = None, error_only: bool = False, limit: int = 50
    ) -> list[dict]:
        """List distinct traces with summary info."""
        from sqlalchemy import Integer, distinct, func, select


        from storage.models import DistributedTrace
        async with self.session() as sess:
            q = (
                select(
                    DistributedTrace.trace_id,
                    func.count(DistributedTrace.id).label("span_count"),
                    func.min(DistributedTrace.started_at).label("started_at"),
                    func.array_agg(distinct(DistributedTrace.service_name)).label("services"),
                    func.sum(
                        func.cast(DistributedTrace.status_code == "ERROR", Integer)
                    ).label("error_span_count"),
                )
                .group_by(DistributedTrace.trace_id)
                .order_by(func.min(DistributedTrace.started_at).desc())
                .limit(limit)
            )
            if service:
                q = q.where(DistributedTrace.service_name == service)
            if error_only:
                q = q.having(
                    func.sum(func.cast(DistributedTrace.status_code == "ERROR", Integer)) > 0
                )
            rows = (await sess.execute(q)).all()
            return [
                {
                    "trace_id": r.trace_id,
                    "span_count": r.span_count,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "services": sorted(r.services) if r.services else [],
                    "error_span_count": int(r.error_span_count or 0),
                }
                for r in rows
            ]

    async def upsert_topology_edge(
        self,
        source: str,
        target: str,
        call_count: int,
        error_count: int,
        avg_latency_ms: float | None,
    ) -> None:
        """Upsert a service→service call edge in service_topology."""
        import uuid as _uuid
        from datetime import datetime, timezone

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from storage.models import ServiceTopology
        stmt = pg_insert(ServiceTopology).values(
            id=_uuid.uuid4(),
            source_service=source,
            target_service=target,
            call_count=call_count,
            error_count=error_count,
            avg_latency_ms=avg_latency_ms,
            last_seen=datetime.now(timezone.utc),
        ).on_conflict_do_update(
            constraint="uq_topology_edge",
            set_={
                "call_count": ServiceTopology.call_count + call_count,
                "error_count": ServiceTopology.error_count + error_count,
                "avg_latency_ms": avg_latency_ms,
                "last_seen": datetime.now(timezone.utc),
            },
        )
        async with self.session() as sess:
            await sess.execute(stmt)

    async def get_topology(self) -> list[dict]:
        """Return all service topology edges ordered by call count desc."""
        from sqlalchemy import select

        from storage.models import ServiceTopology
        async with self.session() as sess:
            rows = (
                await sess.execute(
                    select(ServiceTopology)
                    .order_by(ServiceTopology.call_count.desc())
                    .limit(200)
                )
            ).scalars().all()
            return [
                {
                    "source_service": r.source_service,
                    "target_service": r.target_service,
                    "call_count": r.call_count,
                    "error_count": r.error_count,
                    "error_rate": round(r.error_count / r.call_count, 3) if r.call_count else 0,
                    "avg_latency_ms": r.avg_latency_ms,
                    "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                }
                for r in rows
            ]


    async def save_deploy_event(self, event: dict) -> str:
        """Save a CI/CD deploy event for blame attribution."""
        import uuid as _uuid

        from storage.models import DeployEvent
        async with self.session() as sess:
            row = DeployEvent(
                id=_uuid.uuid4(),
                service_name=event["service_name"],
                commit_sha=event["commit_sha"],
                branch=event.get("branch"),
                environment=event.get("environment", "production"),
                deployed_by=event.get("deployed_by"),
                version_tag=event.get("version_tag"),
                deployed_at=event["deployed_at"],
            )
            sess.add(row)
            await sess.flush()
            return str(row.id)

    async def get_latest_deploy_before(
        self, service_name: str, before: datetime
    ) -> dict | None:
        """Find the most recent deploy for a service before a given timestamp."""
        from sqlalchemy import select

        from storage.models import DeployEvent
        async with self.session() as sess:
            row = (
                await sess.execute(
                    select(DeployEvent)
                    .where(DeployEvent.service_name == service_name)
                    .where(DeployEvent.deployed_at <= before)
                    .order_by(DeployEvent.deployed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not row:
                return None
            return {
                "service_name": row.service_name,
                "commit_sha": row.commit_sha,
                "branch": row.branch,
                "deployed_by": row.deployed_by,
                "deployed_at": row.deployed_at.isoformat(),
                "version_tag": row.version_tag,
            }


_postgres_instance: PostgresClient | None = None


def get_postgres() -> PostgresClient:
    global _postgres_instance
    if _postgres_instance is None:
        _postgres_instance = PostgresClient(get_settings().postgres_url)
    return _postgres_instance

