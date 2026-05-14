"""
DARA — Strategy Monitor (Week 15-16)
======================================
Continuously scans the Institutional Memory Graph for error classes
that are repeatedly failing — i.e., where fixes are being rejected
more than FAILURE_THRESHOLD of the time.

When a class crosses the threshold, it's flagged as needing a new strategy.
The flag is written to strategy_variants(status='needs_refresh') in Postgres
and picked up by the StrategyGenerator Celery task.

Data sources (dual-source for accuracy):
  Primary:  Neo4j GET_FAILING_CLASSES Cypher (rejection_count/total per node)
  Fallback: Postgres pipeline_runs + errors JOIN (if Neo4j is unavailable)

FailingClass dataclass carries all context the StrategyGenerator needs.

Usage (hourly Celery beat):
    monitor = StrategyMonitor(neo4j=..., postgres=...)
    failing = await monitor.scan()
    for fc in failing:
        await monitor.flag_for_refresh(fc)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class FailingClass:
    """Snapshot of a failing error class with all diagnostic context."""
    error_class: str
    rejection_rate: float          # 0.0 – 1.0
    rejection_count: int
    acceptance_count: int
    sample_count: int
    dominant_strategy: str
    last_seen: datetime | None
    needs_refresh: bool = False
    flagged_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signature_hash: str = ""
    template_id: str | None = None
    template_success_rate: float | None = None

    @property
    def total(self) -> int:
        return self.rejection_count + self.acceptance_count

    @property
    def summary(self) -> str:
        return (
            f"[{self.error_class}] rejection_rate={self.rejection_rate:.0%} "
            f"({self.rejection_count}/{self.total}) "
            f"strategy={self.dominant_strategy}"
        )


class StrategyMonitor:
    """
    Dual-source failing class detector.
    Scans Neo4j memory graph and optionally falls back to Postgres analytics.
    """

    FAILURE_THRESHOLD: float = 0.40   # 40% rejection rate
    MIN_SAMPLES: int = 5              # Min cases before judging a class

    def __init__(self, neo4j=None, postgres=None) -> None:
        self._neo4j = neo4j
        self._postgres = postgres

    def _get_neo4j(self):
        if self._neo4j:
            return self._neo4j
        try:
            from storage.neo4j_client import get_neo4j
            return get_neo4j()
        except Exception:
            return None

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public API ─────────────────────────────────────────────

    async def scan(self) -> list[FailingClass]:
        """
        Scan all data sources for failing error classes.
        Returns a deduplicated list ordered by rejection_rate descending.
        """
        failing: list[FailingClass] = []

        # Primary: Neo4j institutional memory graph
        neo4j_failing = await self._scan_neo4j()
        failing.extend(neo4j_failing)

        # Fallback: Postgres aggregation (supplements or replaces Neo4j results)
        if not neo4j_failing:
            pg_failing = await self._scan_postgres()
            failing.extend(pg_failing)
        else:
            # Merge Postgres data for classes not yet in Neo4j graph
            neo4j_classes = {f.error_class for f in neo4j_failing}
            pg_failing = await self._scan_postgres()
            for fc in pg_failing:
                if fc.error_class not in neo4j_classes:
                    failing.append(fc)

        # Deduplicate and sort
        seen: set[str] = set()
        unique: list[FailingClass] = []
        for fc in sorted(failing, key=lambda x: x.rejection_rate, reverse=True):
            if fc.error_class not in seen:
                seen.add(fc.error_class)
                unique.append(fc)

        logger.info(
            "StrategyMonitor.scan: found %d failing classes (threshold=%.0f%%)",
            len(unique), self.FAILURE_THRESHOLD * 100,
        )
        return unique

    async def flag_for_refresh(self, fc: FailingClass) -> None:
        """
        Write a strategy_variants row with status='needs_refresh'.
        Idempotent: skips if an active or testing variant already exists.
        """
        try:
            from sqlalchemy import select

            from storage.models import StrategyVariant
            pg = self._get_pg()
            async with pg.session() as sess:
                # Check if already flagged/active
                existing = (await sess.execute(
                    select(StrategyVariant)
                    .where(
                        StrategyVariant.error_class == fc.error_class,
                        StrategyVariant.status.in_(["needs_refresh", "testing", "active"]),
                    )
                )).scalars().first()

                if existing:
                    logger.debug(
                        "StrategyMonitor: already flagged %s (status=%s)",
                        fc.error_class, existing.status,
                    )
                    return

                import uuid as _uuid
                variant = StrategyVariant(
                    id=_uuid.uuid4(),
                    error_class=fc.error_class,
                    variant_name=f"refresh_{fc.error_class}_{fc.flagged_at.strftime('%Y%m%d')}",
                    prompt_template="",   # filled in by StrategyGenerator
                    hypothesis=(
                        f"Current strategy '{fc.dominant_strategy}' has "
                        f"{fc.rejection_rate:.0%} rejection rate over "
                        f"{fc.sample_count} cases. Need alternative approach."
                    ),
                    status="needs_refresh",
                    generated_by="monitor",
                )
                sess.add(variant)

            logger.info(
                "StrategyMonitor: flagged %s for refresh (rate=%.0f%%)",
                fc.error_class, fc.rejection_rate * 100,
            )

            # Emit metric
            try:
                from monitoring.metrics import strategy_failures
                strategy_failures.labels(
                    error_class=fc.error_class,
                    strategy=fc.dominant_strategy,
                ).inc()
            except Exception:
                pass

        except Exception as e:
            logger.warning("StrategyMonitor.flag_for_refresh failed: %s", e)

    async def get_dashboard_data(self) -> dict:
        """Return Grafana-ready dict with strategy health overview."""
        try:
            failing = await self.scan()
            neo4j = self._get_neo4j()
            graph_stats = await neo4j.get_graph_stats() if neo4j else {}

            from sqlalchemy import func, select

            from storage.models import StrategyVariant
            pg = self._get_pg()
            variant_counts: dict[str, int] = {}
            async with pg.session() as sess:
                rows = (await sess.execute(
                    select(StrategyVariant.status, func.count())
                    .group_by(StrategyVariant.status)
                )).all()
                variant_counts = {r[0]: r[1] for r in rows}

            return {
                "failing_classes": [
                    {
                        "error_class": fc.error_class,
                        "rejection_rate": fc.rejection_rate,
                        "sample_count": fc.sample_count,
                        "dominant_strategy": fc.dominant_strategy,
                    }
                    for fc in failing
                ],
                "graph_stats": graph_stats,
                "variant_counts": variant_counts,
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.warning("StrategyMonitor.get_dashboard_data failed: %s", e)
            return {"error": str(e)}

    # ── Private data sources ────────────────────────────────────

    async def _scan_neo4j(self) -> list[FailingClass]:
        """Query Neo4j for ErrorPattern nodes with high rejection rates."""
        try:
            neo4j = self._get_neo4j()
            if not neo4j:
                return []
            rows = await neo4j.get_failing_classes(
                threshold=self.FAILURE_THRESHOLD,
                min_samples=self.MIN_SAMPLES,
            )
            result: list[FailingClass] = []
            for row in rows:
                total = (row.get("rejection_count") or 0) + (row.get("acceptance_count") or 0)
                if total < self.MIN_SAMPLES:
                    continue
                rate = (row.get("rejection_count") or 0) / total
                last_seen = row.get("last_seen")
                if isinstance(last_seen, str):
                    try:
                        last_seen = datetime.fromisoformat(last_seen)
                    except Exception:
                        last_seen = None
                result.append(FailingClass(
                    error_class=row.get("error_class", "unknown"),
                    rejection_rate=round(rate, 4),
                    rejection_count=row.get("rejection_count") or 0,
                    acceptance_count=row.get("acceptance_count") or 0,
                    sample_count=total,
                    dominant_strategy=row.get("dominant_strategy") or "unknown",
                    last_seen=last_seen,
                    needs_refresh=bool(row.get("needs_refresh", False)),
                    signature_hash=row.get("signature_hash") or "",
                    template_id=row.get("template_id"),
                    template_success_rate=row.get("template_success_rate"),
                ))
            return result
        except Exception as e:
            logger.warning("StrategyMonitor._scan_neo4j failed: %s", e)
            return []

    async def _scan_postgres(self) -> list[FailingClass]:
        """
        Fallback: compute failure rates directly from pipeline_runs + errors.
        Used when Neo4j has no data (fresh install) or is unavailable.
        """
        try:
            from sqlalchemy import text
            pg = self._get_pg()
            sql = text("""
                SELECT
                    e.error_class,
                    COUNT(*) FILTER (WHERE pr.outcome = 'rejected') AS rejection_count,
                    COUNT(*) FILTER (WHERE pr.outcome = 'accepted') AS acceptance_count,
                    COUNT(*) AS total,
                    MAX(pr.started_at) AS last_seen
                FROM pipeline_runs pr
                JOIN errors e ON e.id = pr.error_id
                WHERE pr.outcome IS NOT NULL
                  AND pr.started_at > NOW() - INTERVAL '30 days'
                GROUP BY e.error_class
                HAVING COUNT(*) >= :min_samples
                   AND (
                       COUNT(*) FILTER (WHERE pr.outcome = 'rejected')::float /
                       NULLIF(COUNT(*), 0)
                   ) >= :threshold
                ORDER BY
                    COUNT(*) FILTER (WHERE pr.outcome = 'rejected')::float /
                    NULLIF(COUNT(*), 0) DESC
                LIMIT 20
            """)
            async with pg.session() as sess:
                rows = (await sess.execute(
                    sql,
                    {"min_samples": self.MIN_SAMPLES, "threshold": self.FAILURE_THRESHOLD},
                )).all()

            result: list[FailingClass] = []
            for row in rows:
                total = row[3] or 1
                result.append(FailingClass(
                    error_class=row[0],
                    rejection_rate=round((row[1] or 0) / total, 4),
                    rejection_count=row[1] or 0,
                    acceptance_count=row[2] or 0,
                    sample_count=total,
                    dominant_strategy="unknown",
                    last_seen=row[4],
                ))
            return result
        except Exception as e:
            logger.warning("StrategyMonitor._scan_postgres failed: %s", e)
            return []
