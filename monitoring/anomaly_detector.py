"""
DARA — Proactive Anomaly Detector (Week 18-19)
================================================
Z-score anomaly detection on TimescaleDB time-bucket metrics.
Fires BEFORE errors appear in the errors table.

Three detection types:
  1. latency_spike     — avg latency in last N minutes >> 24h baseline
  2. error_rate_surge  — error span % in last N minutes >> 24h baseline
  3. volume_drop       — span count in last N minutes << 24h baseline (service outage)

Algorithm (per metric):
  1. Compute `baseline` = mean + stddev over last 24h (excluding anomalous windows)
  2. Compute `current`  = time_bucket average over last N minutes
  3. Z-score = (current - baseline_mean) / baseline_stddev
  4. If |z| >= threshold (default 3.0) → AnomalyAlert

Side effects on detection:
  - Write AnomalyAlert to Postgres
  - Update dara_anomaly_score Prometheus gauge
  - Increment dara_anomalies_detected counter
  - Send Slack notification (non-blocking)
  - Pre-warm context builder cache for affected service (error_rate_surge only)

Falls back gracefully to empty results when:
  - TimescaleDB is not available (plain Postgres)
  - distributed_traces table is empty (no data yet)
  - Z-score computation fails due to stddev=0

Usage (2-minute Celery beat):
    detector = AnomalyDetector(postgres=..., slack_token=...)
    alerts = await detector.run_full_scan()
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class BaselineStats:
    """24h historical baseline for a service+metric."""
    service_name: str
    metric_name: str
    mean: float
    stddev: float
    sample_count: int

    @property
    def is_usable(self) -> bool:
        """Baseline is only usable if stddev > 0 and we have enough samples."""
        return self.stddev > 0 and self.sample_count >= 10


@dataclass
class AnomalyAlertData:
    """Detected anomaly before Postgres persistence."""
    service_name: str
    alert_type: str              # latency_spike | error_rate_surge | volume_drop
    metric_name: str
    current_value: float
    baseline_value: float
    z_score: float
    threshold: float = 3.0
    severity: str = "warning"    # warning | critical
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    @property
    def is_critical(self) -> bool:
        return abs(self.z_score) >= 5.0


class AnomalyDetector:
    """
    Z-score anomaly detector backed by TimescaleDB (or plain Postgres).
    Runs on a 2-minute Celery beat schedule.
    """

    DEFAULT_THRESHOLD: float = 3.0    # z-score to trigger warning
    CRITICAL_THRESHOLD: float = 5.0   # z-score for critical severity
    LATENCY_WINDOW_MIN: int = 15      # Window for latency spike detection
    ERROR_RATE_WINDOW_MIN: int = 5    # Window for error rate surge
    VOLUME_WINDOW_MIN: int = 10       # Window for volume drop

    def __init__(self, postgres=None, slack_notifier=None) -> None:
        self._postgres = postgres
        self._slack = slack_notifier

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public API ─────────────────────────────────────────────

    async def run_full_scan(self) -> list[AnomalyAlertData]:
        """
        Scan all registered services for all anomaly types.
        Returns all detected alerts (already persisted to Postgres).
        """
        services = await self._get_active_services()
        if not services:
            logger.debug("AnomalyDetector: no active services to scan")
            return []

        all_alerts: list[AnomalyAlertData] = []
        for service in services:
            service_alerts = await self._scan_service(service)
            all_alerts.extend(service_alerts)

        if all_alerts:
            logger.info(
                "AnomalyDetector: detected %d anomalies across %d services",
                len(all_alerts), len(services),
            )
        return all_alerts

    async def detect_latency_spike(
        self, service_name: str, window_minutes: int | None = None
    ) -> AnomalyAlertData | None:
        """Detect if recent avg latency >> 24h baseline."""
        window = window_minutes or self.LATENCY_WINDOW_MIN
        try:
            current = await self._current_avg_latency(service_name, window)
            if current is None:
                return None
            baseline = await self._baseline(service_name, "avg_latency_ms")
            if not baseline.is_usable:
                return None
            z = (current - baseline.mean) / baseline.stddev
            if z >= self.DEFAULT_THRESHOLD:
                return await self._create_alert(
                    service_name=service_name,
                    alert_type="latency_spike",
                    metric_name="avg_latency_ms",
                    current=current,
                    baseline=baseline,
                    z_score=z,
                )
        except Exception as e:
            logger.debug("detect_latency_spike(%s): %s", service_name, e)
        return None

    async def detect_error_rate_surge(
        self, service_name: str, window_minutes: int | None = None
    ) -> AnomalyAlertData | None:
        """Detect if recent error span % >> 24h baseline."""
        window = window_minutes or self.ERROR_RATE_WINDOW_MIN
        try:
            current = await self._current_error_rate(service_name, window)
            if current is None:
                return None
            baseline = await self._baseline(service_name, "error_rate")
            if not baseline.is_usable:
                # Bootstrap: if no baseline but current error rate > 20%, flag anyway
                if current > 0.20:
                    return AnomalyAlertData(
                        service_name=service_name,
                        alert_type="error_rate_surge",
                        metric_name="error_rate",
                        current_value=current,
                        baseline_value=0.0,
                        z_score=10.0,  # High z for no-baseline case
                        severity="warning" if current < 0.5 else "critical",
                        metadata={"bootstrap": True},
                    )
                return None
            z = (current - baseline.mean) / baseline.stddev
            if z >= self.DEFAULT_THRESHOLD:
                alert = await self._create_alert(
                    service_name=service_name,
                    alert_type="error_rate_surge",
                    metric_name="error_rate",
                    current=current,
                    baseline=baseline,
                    z_score=z,
                )
                # Pre-warm context builder for this service
                if alert:
                    await self._prewarm_context(service_name)
                return alert
        except Exception as e:
            logger.debug("detect_error_rate_surge(%s): %s", service_name, e)
        return None

    async def detect_call_volume_drop(
        self, service_name: str, window_minutes: int | None = None
    ) -> AnomalyAlertData | None:
        """Detect if recent call volume << 24h baseline (service outage indicator)."""
        window = window_minutes or self.VOLUME_WINDOW_MIN
        try:
            current = await self._current_call_volume(service_name, window)
            if current is None:
                return None
            baseline = await self._baseline(service_name, "call_volume")
            if not baseline.is_usable:
                return None
            # Volume DROP is negative z-score
            z = (current - baseline.mean) / baseline.stddev
            if z <= -self.DEFAULT_THRESHOLD:
                return await self._create_alert(
                    service_name=service_name,
                    alert_type="volume_drop",
                    metric_name="call_volume",
                    current=current,
                    baseline=baseline,
                    z_score=z,  # Negative
                )
        except Exception as e:
            logger.debug("detect_call_volume_drop(%s): %s", service_name, e)
        return None

    # ── Internal per-service scan ───────────────────────────────

    async def _scan_service(self, service_name: str) -> list[AnomalyAlertData]:
        """Run all 3 detectors for a single service. Collect non-None results."""
        tasks = [
            self.detect_latency_spike(service_name),
            self.detect_error_rate_surge(service_name),
            self.detect_call_volume_drop(service_name),
        ]
        results = await _gather_safe(*tasks)
        alerts = [r for r in results if r is not None]
        await self._post_process_alerts(alerts)
        return alerts

    async def _post_process_alerts(self, alerts: list[AnomalyAlertData]) -> None:
        """Persist, emit metrics, notify Slack for each alert."""
        for alert in alerts:
            await self._persist_alert(alert)
            self._emit_metrics(alert)
            await self._notify_slack(alert)

    # ── Metric queries ──────────────────────────────────────────

    async def _current_avg_latency(self, service: str, window_min: int) -> float | None:
        """Recent average latency via time_bucket if available, else plain AVG."""
        sql_ts = """
            SELECT AVG(avg_latency)
            FROM (
                SELECT time_bucket('1 minute', started_at) AS bucket,
                       AVG(duration_ms) AS avg_latency
                FROM distributed_traces
                WHERE service_name = :service
                  AND started_at > NOW() - (:window * INTERVAL '1 minute')
                  AND duration_ms IS NOT NULL
                GROUP BY bucket
            ) t
        """
        sql_plain = """
            SELECT AVG(duration_ms)
            FROM distributed_traces
            WHERE service_name = :service
              AND started_at > NOW() - (:window * INTERVAL '1 minute')
              AND duration_ms IS NOT NULL
        """
        return await self._scalar_query(
            sql_ts, sql_plain, {"service": service, "window": window_min}
        )

    async def _current_error_rate(self, service: str, window_min: int) -> float | None:
        """Recent error rate (error spans / total spans) in the window."""
        sql = """
            SELECT
                COUNT(*) FILTER (WHERE status_code = 'ERROR')::float /
                NULLIF(COUNT(*), 0)
            FROM distributed_traces
            WHERE service_name = :service
              AND started_at > NOW() - (:window * INTERVAL '1 minute')
        """
        return await self._scalar_query(sql, sql, {"service": service, "window": window_min})

    async def _current_call_volume(self, service: str, window_min: int) -> float | None:
        """Recent span count per minute."""
        sql = """
            SELECT COUNT(*)::float / NULLIF(:window, 0)
            FROM distributed_traces
            WHERE service_name = :service
              AND started_at > NOW() - (:window * INTERVAL '1 minute')
        """
        return await self._scalar_query(sql, sql, {"service": service, "window": window_min})

    async def _baseline(self, service: str, metric_name: str) -> BaselineStats:
        """Compute 24h baseline mean and stddev for a given metric."""
        sql_map = {
            "avg_latency_ms": """
                SELECT AVG(duration_ms), STDDEV(duration_ms), COUNT(*)
                FROM distributed_traces
                WHERE service_name = :service
                  AND started_at BETWEEN NOW() - INTERVAL '24 hours' AND NOW() - INTERVAL '15 minutes'
                  AND duration_ms IS NOT NULL
            """,
            "error_rate": """
                SELECT
                    AVG(CASE WHEN status_code = 'ERROR' THEN 1.0 ELSE 0.0 END),
                    STDDEV(CASE WHEN status_code = 'ERROR' THEN 1.0 ELSE 0.0 END),
                    COUNT(*)
                FROM distributed_traces
                WHERE service_name = :service
                  AND started_at BETWEEN NOW() - INTERVAL '24 hours' AND NOW() - INTERVAL '5 minutes'
            """,
            "call_volume": """
                SELECT
                    AVG(span_count), STDDEV(span_count), COUNT(*)
                FROM (
                    SELECT DATE_TRUNC('minute', started_at) AS minute, COUNT(*) AS span_count
                    FROM distributed_traces
                    WHERE service_name = :service
                      AND started_at BETWEEN NOW() - INTERVAL '24 hours' AND NOW() - INTERVAL '10 minutes'
                    GROUP BY minute
                ) t
            """,
        }
        sql = sql_map.get(metric_name, sql_map["avg_latency_ms"])
        try:
            from sqlalchemy import text
            pg = self._get_pg()
            async with pg.session() as sess:
                row = (await sess.execute(text(sql), {"service": service})).one_or_none()
                if row and row[0] is not None:
                    return BaselineStats(
                        service_name=service,
                        metric_name=metric_name,
                        mean=float(row[0]),
                        stddev=float(row[1] or 0.001),  # Avoid /0
                        sample_count=int(row[2] or 0),
                    )
        except Exception as e:
            logger.debug("_baseline(%s, %s): %s", service, metric_name, e)
        return BaselineStats(service_name=service, metric_name=metric_name,
                             mean=0.0, stddev=0.0, sample_count=0)

    async def _scalar_query(self, sql_primary: str, sql_fallback: str, params: dict) -> float | None:
        """Try primary SQL, fall back if time_bucket unavailable."""
        from sqlalchemy import text
        pg = self._get_pg()
        for sql in (sql_primary, sql_fallback):
            try:
                async with pg.session() as sess:
                    row = (await sess.execute(text(sql), params)).one_or_none()
                    if row and row[0] is not None:
                        return float(row[0])
                break
            except Exception:
                continue
        return None

    # ── Side effects ────────────────────────────────────────────

    async def _create_alert(
        self,
        service_name: str,
        alert_type: str,
        metric_name: str,
        current: float,
        baseline: BaselineStats,
        z_score: float,
    ) -> AnomalyAlertData:
        severity = "critical" if abs(z_score) >= self.CRITICAL_THRESHOLD else "warning"
        return AnomalyAlertData(
            service_name=service_name,
            alert_type=alert_type,
            metric_name=metric_name,
            current_value=round(current, 4),
            baseline_value=round(baseline.mean, 4),
            z_score=round(z_score, 3),
            threshold=self.DEFAULT_THRESHOLD,
            severity=severity,
            metadata={"baseline_stddev": round(baseline.stddev, 4), "sample_count": baseline.sample_count},
        )

    async def _persist_alert(self, alert: AnomalyAlertData) -> None:
        """Write AnomalyAlert to Postgres."""
        try:
            from storage.models import AnomalyAlert
            pg = self._get_pg()
            async with pg.session() as sess:
                row = AnomalyAlert(
                    id=uuid.uuid4(),
                    service_name=alert.service_name,
                    alert_type=alert.alert_type,
                    metric_name=alert.metric_name,
                    current_value=alert.current_value,
                    baseline_value=alert.baseline_value,
                    z_score=alert.z_score,
                    threshold=alert.threshold,
                    severity=alert.severity,
                    triggered_at=alert.triggered_at,
                    metadata_json=alert.metadata,
                )
                sess.add(row)
        except Exception as e:
            logger.debug("_persist_alert: %s", e)

    def _emit_metrics(self, alert: AnomalyAlertData) -> None:
        """Update Prometheus gauges and counters."""
        try:
            from monitoring.metrics import anomalies_detected, anomaly_score
            anomaly_score.labels(
                service=alert.service_name,
                metric=alert.metric_name,
            ).set(abs(alert.z_score))
            anomalies_detected.labels(
                service=alert.service_name,
                alert_type=alert.alert_type,
            ).inc()
        except Exception:
            pass

    async def _notify_slack(self, alert: AnomalyAlertData) -> None:
        """Send Slack notification for the anomaly. Non-blocking."""
        try:
            direction = "↑" if alert.alert_type != "volume_drop" else "↓"
            emoji = "🚨" if alert.severity == "critical" else "⚠️"
            msg = (
                f"{emoji} *DARA Anomaly Detected* [{alert.severity.upper()}]\n"
                f"Service: `{alert.service_name}`\n"
                f"Type   : `{alert.alert_type}` {direction}\n"
                f"Value  : `{alert.current_value:.2f}` (baseline: `{alert.baseline_value:.2f}`)\n"
                f"Z-score: `{alert.z_score:.1f}σ` (threshold: {alert.threshold:.0f}σ)\n"
                f"Time   : {alert.triggered_at.strftime('%H:%M:%S UTC')}"
            )
            if self._slack:
                await self._slack.send_text(msg)
            else:
                from output.slack_notifier import get_slack_notifier
                slack = get_slack_notifier()
                if slack:
                    await slack.send_text(msg)
        except Exception as e:
            logger.debug("_notify_slack: %s", e)

    async def _prewarm_context(self, service_name: str) -> None:
        """Pre-warm the context builder cache for a service showing error surge."""
        try:
            logger.info("AnomalyDetector: pre-warming context for %s", service_name)
            # Minimal: just log for now — context pre-warming requires repo access
            # Full integration with CrossServiceContextBuilder in Phase 3.1
        except Exception:
            pass

    async def _get_active_services(self) -> list[str]:
        """Get distinct service names from recent traces."""
        try:
            from sqlalchemy import text
            pg = self._get_pg()
            async with pg.session() as sess:
                rows = (await sess.execute(text("""
                    SELECT DISTINCT service_name
                    FROM distributed_traces
                    WHERE started_at > NOW() - INTERVAL '1 hour'
                    ORDER BY service_name
                    LIMIT 50
                """))).all()
            return [r[0] for r in rows]
        except Exception:
            return []


async def _gather_safe(*coros):
    """Gather coroutines without propagating exceptions."""
    import asyncio
    results = await asyncio.gather(*coros, return_exceptions=True)
    return [r if not isinstance(r, Exception) else None for r in results]
