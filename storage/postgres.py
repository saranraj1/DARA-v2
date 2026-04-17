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
from storage.models import Base, Error, Fix, PatternLibrary, PipelineRun

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
            )
            sess.add(error)
            await sess.flush()
            logger.info("Saved error", extra={"error_id": str(error.id)})
            return str(error.id)

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

    # ─── Pattern Library ─────────────────────────────────────

    async def get_fix_template(
        self, error_class: str, language: str | None = None
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


_postgres_instance: PostgresClient | None = None


def get_postgres() -> PostgresClient:
    global _postgres_instance
    if _postgres_instance is None:
        _postgres_instance = PostgresClient(get_settings().postgres_url)
    return _postgres_instance
