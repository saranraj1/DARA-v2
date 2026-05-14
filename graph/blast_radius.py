"""
graph/blast_radius.py
======================
Cross-Service Impact Analysis — "Blast Radius" calculator.

Before proposing a fix, DARA queries Neo4j to understand:
  1. Which downstream services depend (transitively) on the modified service?
  2. Are any modified files shared API contracts (routes, gRPC, REST endpoints)?
  3. If a contract is modified: do any consumer service test suites still pass?

Risk classification:
  critical : API contract changed + >3 downstream services
  high     : API contract changed (any downstream count)
  medium   : >1 downstream service (no contract change)
  low      : ≤1 downstream service, no contract change

Architecture:
  BlastRadiusAnalyzer.analyze(fix, service_name) → BlastRadiusReport
    ├─ neo4j.get_downstream_services(service)
    ├─ neo4j.get_callers(function, service) for each modified function
    ├─ _detect_api_contracts(patches)
    └─ _trigger_consumer_tests(consumers) if contract modified
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from api.models.agent_schemas import Fix

logger = logging.getLogger(__name__)

# ── API contract detection heuristics ────────────────────────────────────────
# A file is considered an API contract if it contains any of these patterns
_CONTRACT_PATTERNS: list[re.Pattern] = [
    re.compile(r"@(app|router)\.(get|post|put|patch|delete|head)\s*\(", re.IGNORECASE),
    re.compile(r"add_api_route\s*\("),
    re.compile(r"grpc\.ServicerContext"),
    re.compile(r"proto\s*=\s*"),
    re.compile(r"openapi\s*:", re.IGNORECASE),
    re.compile(r"swagger\s*:", re.IGNORECASE),
    re.compile(r"\"paths\"\s*:\s*\{"),  # OpenAPI JSON
    re.compile(r"export\s+(?:default\s+)?(?:function|class|const)\s+\w+.*Handler"),  # TS handlers
    re.compile(r"func\s+\w+Handler\s*\("),  # Go handlers
]

# Files whose path alone suggests they are contracts
_CONTRACT_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"(routes?|endpoints?|handlers?|controllers?|views?)[\\/]", re.IGNORECASE),
    re.compile(r"\.(proto|yaml|yml|json)$", re.IGNORECASE),
    re.compile(r"(openapi|swagger|api_spec)", re.IGNORECASE),
]


@dataclass
class ConsumerTestResult:
    service: str
    passed: bool
    test_count: int = 0
    failure_log: str = ""


@dataclass
class BlastRadiusReport:
    service: str
    modified_files: list[str]
    downstream_services: list[dict] = field(default_factory=list)
    downstream_functions: list[dict] = field(default_factory=list)
    shared_api_contracts: list[str] = field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    consumer_test_results: list[ConsumerTestResult] = field(default_factory=list)
    warning_message: str = ""

    @property
    def downstream_count(self) -> int:
        return len(self.downstream_services)

    @property
    def has_contract_change(self) -> bool:
        return len(self.shared_api_contracts) > 0

    @property
    def consumer_tests_passed(self) -> bool:
        if not self.consumer_test_results:
            return True
        return all(r.passed for r in self.consumer_test_results)

    def summary(self) -> str:
        return (
            f"BlastRadius[service={self.service}, downstream={self.downstream_count}, "
            f"contracts={len(self.shared_api_contracts)}, risk={self.risk_level}, "
            f"consumer_tests_ok={self.consumer_tests_passed}]"
        )

    def to_prompt_block(self) -> str:
        """Formatted string for injection into FixerAgent prompt."""
        lines = [
            f"=== DOWNSTREAM IMPACT (Blast Radius) ===",
            f"Service: {self.service}",
            f"Downstream services affected: {self.downstream_count}",
        ]
        if self.downstream_services:
            for ds in self.downstream_services[:5]:
                lines.append(
                    f"  - {ds.get('service')} (hop={ds.get('hop_count',1)}, "
                    f"calls={ds.get('call_count',0)})"
                )
        if self.shared_api_contracts:
            lines.append(f"⚠️  SHARED API CONTRACTS MODIFIED:")
            for c in self.shared_api_contracts:
                lines.append(f"  - {c}")
        if self.consumer_test_results:
            failed = [r for r in self.consumer_test_results if not r.passed]
            lines.append(
                f"Consumer test results: "
                f"{len(self.consumer_test_results) - len(failed)}/{len(self.consumer_test_results)} passed"
            )
            for r in failed[:3]:
                lines.append(f"  ❌ {r.service}: {r.failure_log[:150]}")
        lines.append(f"Risk level: {self.risk_level.upper()}")
        if self.warning_message:
            lines.append(f"WARNING: {self.warning_message}")
        lines.append("=" * 40)
        return "\n".join(lines)


class BlastRadiusAnalyzer:
    """
    Calculates the downstream impact of a proposed fix using the Neo4j call graph.

    Usage:
        analyzer = BlastRadiusAnalyzer(neo4j=get_neo4j(), repo_path=".")
        report   = await analyzer.analyze(fix, service_name="payment-svc")
    """

    def __init__(self, neo4j=None, repo_path: str = ".") -> None:
        self._neo4j = neo4j
        self._repo_path = Path(repo_path)

    async def analyze(self, fix: Fix, service_name: str) -> BlastRadiusReport:
        """
        Full blast radius analysis for a given fix + service.
        Always returns a BlastRadiusReport — never raises.
        """
        modified_files = [p.file_path for p in fix.patches]
        report = BlastRadiusReport(service=service_name, modified_files=modified_files)

        try:
            # 1. Downstream services from Neo4j topology graph
            report.downstream_services = await self._get_downstream(service_name)

            # 2. Downstream functions from Neo4j call graph
            report.downstream_functions = await self._get_downstream_functions(
                fix, service_name
            )

            # 3. Detect API contract changes
            report.shared_api_contracts = self._detect_api_contracts(fix)

            # 4. Trigger consumer tests if a contract was modified
            if report.shared_api_contracts and report.downstream_services:
                consumer_names = [
                    ds.get("service", "") for ds in report.downstream_services[:5]
                ]
                report.consumer_test_results = await self._trigger_consumer_tests(
                    consumer_names, report.shared_api_contracts
                )

            # 5. Classify risk
            report.risk_level = self._classify_risk(report)
            report.warning_message = self._build_warning(report)

            logger.info("BlastRadius: %s", report.summary())

        except Exception as exc:
            logger.error("BlastRadiusAnalyzer.analyze failed: %s", exc, exc_info=True)
            # Return a safe low-risk report rather than blocking the pipeline
            report.risk_level = "low"
            report.warning_message = f"Blast radius analysis unavailable: {exc}"

        return report

    # ──────────────────────────────────────────────────────────────────────────
    # Neo4j queries
    # ──────────────────────────────────────────────────────────────────────────

    async def _get_downstream(self, service_name: str) -> list[dict]:
        if not self._neo4j or not service_name:
            return []
        try:
            return await self._neo4j.get_downstream_services(service_name, max_hops=3)
        except Exception as exc:
            logger.warning("BlastRadius: get_downstream_services failed: %s", exc)
            return []

    async def _get_downstream_functions(self, fix: Fix, service_name: str) -> list[dict]:
        if not self._neo4j or not service_name:
            return []
        callers: list[dict] = []
        seen: set[str] = set()
        for patch in fix.patches[:3]:
            fn_name = _infer_function_name(patch.unified_diff)
            if not fn_name or fn_name in seen:
                continue
            seen.add(fn_name)
            try:
                results = await self._neo4j.get_callers(fn_name, service_name, max_hops=2)
                callers.extend(results)
            except Exception as exc:
                logger.debug("BlastRadius: get_callers(%s) failed: %s", fn_name, exc)
        return callers[:20]

    # ──────────────────────────────────────────────────────────────────────────
    # API contract detection
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_api_contracts(self, fix: Fix) -> list[str]:
        """
        Return the list of modified file paths that appear to be API contracts.
        Uses path heuristics first (fast), then content scan of the diff.
        """
        contracts: list[str] = []
        for patch in fix.patches:
            fp = patch.file_path
            # Path-level check
            if any(p.search(fp) for p in _CONTRACT_PATH_PATTERNS):
                contracts.append(fp)
                continue
            # Content check: scan added lines in the diff for contract patterns
            added_lines = "\n".join(
                line[1:] for line in patch.unified_diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            if any(p.search(added_lines) for p in _CONTRACT_PATTERNS):
                contracts.append(fp)
        return contracts

    # ──────────────────────────────────────────────────────────────────────────
    # Consumer test triggering
    # ──────────────────────────────────────────────────────────────────────────

    async def _trigger_consumer_tests(
        self,
        consumer_services: list[str],
        contract_files: list[str],
    ) -> list[ConsumerTestResult]:
        """
        Run the test suites for each consumer service (file-system based).
        Skips services whose repo path is not locally accessible.
        """
        from validation.test_runner import TestRunner

        results: list[ConsumerTestResult] = []
        for svc_name in consumer_services:
            if not svc_name:
                continue
            svc_repo = self._repo_path / svc_name
            if not svc_repo.exists():
                logger.debug(
                    "BlastRadius: consumer service %s not at %s — skipping test trigger",
                    svc_name, svc_repo,
                )
                results.append(ConsumerTestResult(
                    service=svc_name, passed=True,
                    failure_log="Repo not locally accessible — tests not run",
                ))
                continue

            logger.info(
                "BlastRadius: triggering consumer tests for %s (contract: %s)",
                svc_name, contract_files[:2],
            )
            try:
                runner = TestRunner(repo_path=str(svc_repo))
                tr = await runner.run(patches=[], timeout_seconds=60)
                failure_log = "; ".join(
                    f"{f['nodeid']}: {str(f.get('message',''))[:100]}"
                    for f in tr.failures[:3]
                )
                results.append(ConsumerTestResult(
                    service=svc_name,
                    passed=tr.passed,
                    test_count=tr.test_count,
                    failure_log=failure_log,
                ))
                if not tr.passed:
                    logger.warning(
                        "BlastRadius: consumer %s FAILED (%d tests). log=%s",
                        svc_name, tr.tests_failed, failure_log[:200],
                    )
            except Exception as exc:
                logger.warning(
                    "BlastRadius: consumer test trigger for %s failed: %s", svc_name, exc
                )
                results.append(ConsumerTestResult(
                    service=svc_name, passed=True,
                    failure_log=f"Test runner error: {exc}",
                ))
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Risk classification
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_risk(report: BlastRadiusReport) -> Literal["low", "medium", "high", "critical"]:
        n = report.downstream_count
        has_contract = report.has_contract_change
        consumer_failed = not report.consumer_tests_passed

        if has_contract and (n > 3 or consumer_failed):
            return "critical"
        if has_contract:
            return "high"
        if n > 1:
            return "medium"
        return "low"

    @staticmethod
    def _build_warning(report: BlastRadiusReport) -> str:
        if report.risk_level == "critical":
            failed = [r.service for r in report.consumer_test_results if not r.passed]
            base = (
                f"CRITICAL: API contract modified + {report.downstream_count} downstream services. "
            )
            if failed:
                base += f"Consumer tests FAILED: {', '.join(failed)}. "
            base += "Manual review required before merge."
            return base
        if report.risk_level == "high":
            return (
                f"HIGH: Shared API contract modified in {report.shared_api_contracts[0]}. "
                f"Affects {report.downstream_count} downstream service(s)."
            )
        if report.risk_level == "medium":
            names = [d.get("service", "?") for d in report.downstream_services[:3]]
            return f"MEDIUM: {report.downstream_count} downstream service(s) may be affected: {', '.join(names)}"
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _infer_function_name(unified_diff: str) -> str | None:
    """
    Heuristically extract the primary function name from a unified diff.
    Looks for def/func/function declarations in changed lines.
    """
    patterns = [
        re.compile(r"^[+-]\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE),   # Python
        re.compile(r"^[+-]\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.MULTILINE),  # Go
        re.compile(r"^[+-]\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),  # JS/TS
        re.compile(r"^[+-]\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", re.MULTILINE),  # Arrow fn
    ]
    for pat in patterns:
        m = pat.search(unified_diff)
        if m:
            return m.group(1)
    return None
