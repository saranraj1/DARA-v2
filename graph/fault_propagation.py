"""
DARA — Fault Propagation Mapper (Week 7-8)
============================================
The core "distributed wedge" algorithm.

Given a distributed trace (list of spans across services), this mapper:
  1. Finds ALL spans with status ERROR
  2. Walks the parent chain to find the EARLIEST (root) error span
  3. Identifies that span's service as the ORIGIN of the fault
  4. Traces the propagation path: which downstream services received / re-raised the error
  5. Returns a structured FaultCascade with blame confidence

This data feeds:
  - CrossServiceContextBuilder (which codebases to fetch)
  - BlameAttributionEngine (which commit to blame)
  - Debugger agent prompt (enriched with cascade info)
  - GitHub PR (multi-repo PR creation)

Example:
    order-service → payment-service (ERROR here first)
                  → notification-service (ERROR, propagated)

    Result: root_service=payment-service, propagation=[notification-service]

Usage:
    mapper = FaultPropagationMapper()
    cascade = await mapper.analyze(trace_id, spans)
    # cascade.root_service = "payment-service"
    # cascade.propagation_path = ["payment-service", "notification-service"]
    # cascade.affected_services = ["order-service", "payment-service", "notification-service"]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class FaultCascade:
    """
    Structured result of fault propagation analysis for a single distributed trace.
    """
    trace_id: str
    root_service: str                    # Service where the fault ORIGINATED
    root_span_id: str | None             # Span ID of the earliest error
    root_operation: str | None           # Operation name at the origin
    propagation_path: list[str]          # Ordered path: root → downstream services
    affected_services: list[str]         # All services with error spans
    non_error_services: list[str]        # Services in trace without errors (innocent)
    total_span_count: int
    error_span_count: int
    root_error_message: str | None       # Error message from root span
    root_file_path: str | None           # code.filepath from OTel attributes
    root_function: str | None            # code.function from OTel attributes
    blame_confidence: float              # 0.0–1.0: how certain we are about root_service
    analysis_notes: list[str] = field(default_factory=list)

    @property
    def is_distributed_bug(self) -> bool:
        """True if error spans span more than one service."""
        return len(self.affected_services) > 1

    @property
    def summary(self) -> str:
        path = " → ".join(self.propagation_path)
        return (
            f"Fault originated in [{self.root_service}] | "
            f"Propagated: {path} | "
            f"Confidence: {self.blame_confidence:.0%}"
        )


class FaultPropagationMapper:
    """
    Analyses a distributed trace and maps the fault propagation path.
    Pure Python — no DB calls. Takes pre-loaded spans.
    """

    async def analyze(self, trace_id: str, spans: list) -> FaultCascade:
        """
        Main entry point.
        spans: list of NormalisedSpan objects (from otel_receiver.py) OR
               list of dicts (from postgres get_trace() return format).
        """
        if not spans:
            return self._empty_cascade(trace_id, "No spans provided")

        # Normalise: accept both NormalisedSpan objects and dict rows
        normalised = [self._to_dict(s) for s in spans]

        error_spans = [s for s in normalised if s["status_code"] == "ERROR"]
        all_services = list({s["service_name"] for s in normalised})
        error_services = list({s["service_name"] for s in error_spans})
        non_error_services = [s for s in all_services if s not in error_services]

        if not error_spans:
            # Healthy trace — no fault
            return FaultCascade(
                trace_id=trace_id,
                root_service=normalised[0]["service_name"],
                root_span_id=None,
                root_operation=None,
                propagation_path=[],
                affected_services=[],
                non_error_services=all_services,
                total_span_count=len(normalised),
                error_span_count=0,
                root_error_message=None,
                root_file_path=None,
                root_function=None,
                blame_confidence=0.0,
                analysis_notes=["Trace has no error spans — healthy execution"],
            )

        # Build span lookup table
        span_map: dict[str, dict] = {s["span_id"]: s for s in normalised}

        # Find the ROOT error: earliest error span with no error-parent
        root_span = self._find_root_error(error_spans, span_map)
        root_service = root_span["service_name"]

        # Build propagation path: BFS from root_span outward through error spans
        propagation_path = self._build_propagation_path(
            root_span, error_spans, span_map
        )

        # Confidence scoring
        confidence, notes = self._compute_confidence(
            root_span=root_span,
            error_spans=error_spans,
            propagation_path=propagation_path,
            span_map=span_map,
        )

        # Extract code location from OTel attributes if available
        attrs = root_span.get("attributes") or {}
        root_file = attrs.get("code.filepath") or attrs.get("code.namespace")
        root_func = attrs.get("code.function") or attrs.get("rpc.method")

        cascade = FaultCascade(
            trace_id=trace_id,
            root_service=root_service,
            root_span_id=root_span["span_id"],
            root_operation=root_span.get("operation_name"),
            propagation_path=propagation_path,
            affected_services=list({s["service_name"] for s in error_spans}),
            non_error_services=non_error_services,
            total_span_count=len(normalised),
            error_span_count=len(error_spans),
            root_error_message=root_span.get("status_message"),
            root_file_path=root_file,
            root_function=root_func,
            blame_confidence=confidence,
            analysis_notes=notes,
        )

        logger.info(
            "FaultPropagationMapper: trace=%s root=%s path=%s confidence=%.2f",
            trace_id[:12], root_service, " → ".join(propagation_path), confidence,
        )
        return cascade

    async def persist(self, cascade: FaultCascade, neo4j=None) -> None:
        """Persist the FaultCascade to Neo4j FailureCascade node."""
        if neo4j is None:
            from storage.neo4j_client import get_neo4j
            neo4j = get_neo4j()
        try:
            await neo4j.store_failure_cascade(
                trace_id=cascade.trace_id,
                root_service=cascade.root_service,
                affected_services=cascade.affected_services,
            )
        except Exception as e:
            logger.warning("FaultPropagationMapper: Neo4j persist failed: %s", e)

    # ── Private algorithm ──────────────────────────────────────

    def _find_root_error(self, error_spans: list[dict], span_map: dict) -> dict:
        """
        Find the root error span using two strategies:
        1. Walk parent chain: error span whose parent is NOT an error is root.
        2. Tiebreak: earliest started_at among candidates.
        """
        root_candidates = []
        error_span_ids = {s["span_id"] for s in error_spans}

        for span in error_spans:
            parent_id = span.get("parent_span_id")
            if not parent_id:
                # No parent → this is a root span AND it's erroring
                root_candidates.append(span)
            elif parent_id not in error_span_ids:
                # Parent exists but is NOT an error span → fault starts HERE
                root_candidates.append(span)
            # else: parent is also an error → fault propagated from parent

        if not root_candidates:
            # All error spans have error parents — cycle or incomplete trace
            # Fall back to earliest by timestamp
            root_candidates = error_spans

        # Tiebreak by timestamp (earliest = root)
        def to_dt(s: dict):
            raw = s.get("started_at", "")
            if isinstance(raw, datetime):
                return raw
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except Exception:
                return datetime.min

        return min(root_candidates, key=to_dt)

    def _build_propagation_path(
        self,
        root_span: dict,
        error_spans: list[dict],
        span_map: dict,
    ) -> list[str]:
        """
        Build an ordered propagation path starting from root_service.
        Uses topological sort based on parent→child relationships.
        """
        root_service = root_span["service_name"]
        path = [root_service]
        seen = {root_service}

        # Build child relationships among ERROR spans only
        error_ids = {s["span_id"]: s for s in error_spans}
        children: dict[str, list[dict]] = {}
        for s in error_spans:
            pid = s.get("parent_span_id", "")
            if pid:
                children.setdefault(pid, []).append(s)

        # BFS from root_span through error children
        queue = [root_span]
        while queue:
            current = queue.pop(0)
            for child in children.get(current["span_id"], []):
                child_svc = child["service_name"]
                if child_svc not in seen:
                    path.append(child_svc)
                    seen.add(child_svc)
                queue.append(child)

        # Append any error services we missed (orphan spans / incomplete traces)
        for s in error_spans:
            svc = s["service_name"]
            if svc not in seen:
                path.append(svc)
                seen.add(svc)

        return path

    def _compute_confidence(
        self,
        root_span: dict,
        error_spans: list[dict],
        propagation_path: list[str],
        span_map: dict,
    ) -> tuple[float, list[str]]:
        """
        Compute blame confidence (0.0–1.0) and narrative notes.
        Rules:
          +0.50  base confidence
          +0.20  root span has OTel code.filepath attribute
          +0.15  root span is only 1 hop from a non-error parent
          +0.10  propagation path fully connected (no orphan gaps)
          +0.05  root span has an explicit error message
          -0.20  trace is incomplete (parent_span_id refs not in trace)
        """
        confidence = 0.50
        notes: list[str] = []
        attrs = root_span.get("attributes") or {}

        if attrs.get("code.filepath"):
            confidence += 0.20
            notes.append(f"OTel code.filepath present: {attrs['code.filepath']}")

        parent_id = root_span.get("parent_span_id")
        if parent_id and parent_id in span_map:
            parent = span_map[parent_id]
            if parent.get("status_code") != "ERROR":
                confidence += 0.15
                notes.append(
                    f"Root span's parent [{parent.get('service_name')}] is healthy — "
                    "fault boundary is clear"
                )

        error_ids = {s["span_id"] for s in error_spans}
        orphans = [
            s for s in error_spans
            if s.get("parent_span_id")
            and s["parent_span_id"] not in span_map
            and s["span_id"] != root_span["span_id"]
        ]
        if not orphans:
            confidence += 0.10
            notes.append("Propagation path is fully connected — no orphan spans")
        else:
            confidence -= 0.20
            notes.append(
                f"Trace incomplete: {len(orphans)} error span(s) have unknown parents — "
                "confidence reduced"
            )

        if root_span.get("status_message"):
            confidence += 0.05
            notes.append(f"Error message: {root_span['status_message'][:80]}")

        confidence = round(max(0.0, min(1.0, confidence)), 2)
        return confidence, notes

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _to_dict(span) -> dict:
        """Convert NormalisedSpan dataclass OR plain dict to a consistent dict."""
        if isinstance(span, dict):
            return span
        return {
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "service_name": span.service_name,
            "operation_name": span.operation_name,
            "status_code": span.status_code,
            "status_message": span.status_message,
            "started_at": span.started_at,
            "attributes": span.attributes or {},
        }

    @staticmethod
    def _empty_cascade(trace_id: str, note: str) -> FaultCascade:
        return FaultCascade(
            trace_id=trace_id,
            root_service="unknown",
            root_span_id=None,
            root_operation=None,
            propagation_path=[],
            affected_services=[],
            non_error_services=[],
            total_span_count=0,
            error_span_count=0,
            root_error_message=None,
            root_file_path=None,
            root_function=None,
            blame_confidence=0.0,
            analysis_notes=[note],
        )
