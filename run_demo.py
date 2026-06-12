"""
DARA — Full-Stack Demo
======================
Demonstrates the complete autonomous bug-resolution pipeline.

Strategy:
  1. Pre-flight: probe Postgres, Redis, Qdrant (3 s timeout each)
  2. If ALL three are reachable  → FULL STACK MODE
       • Persists error to Postgres
       • Tracks progress in Redis
       • Stores embeddings in Qdrant
       • Runs real 9-stage pipeline: Normalise → Classify → AST →
         Debugger → Fixer → Validate → Reviewer → Persist → Summary
  3. If ANY service is unreachable → LIGHT MODE (in-memory, same agents)
       • Prints clear per-service status for the operator
       • Identical LLM pipeline — just no persistence

Usage:
    poetry run python run_demo.py          # auto-detect
    poetry run python run_demo.py --full   # fail if infra missing
    poetry run python run_demo.py --light  # skip infra probe, always in-memory
"""
from __future__ import annotations

import argparse
import asyncio
import io
import socket
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import Optional

# Force UTF-8 output on Windows — prevents cp1252 UnicodeEncodeError with
# box-drawing chars and emoji used in the demo output.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf_8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, ".")

# ─────────────────────────── Terminal colours ────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
MAGENTA = "\033[95m"
WHITE  = "\033[97m"

def _c(text: str, *codes: str) -> str:
    return "".join(codes) + str(text) + RESET

def banner(title: str) -> None:
    width = 68
    print()
    print(_c("╔" + "═" * (width - 2) + "╗", CYAN, BOLD))
    pad = (width - 2 - len(title)) // 2
    print(_c("║" + " " * pad + title + " " * (width - 2 - pad - len(title)) + "║", CYAN, BOLD))
    print(_c("╚" + "═" * (width - 2) + "╝", CYAN, BOLD))

def section(title: str, icon: str = "▶") -> None:
    print()
    print(_c(f"  {icon}  {title}", BLUE, BOLD))
    print(_c("  " + "─" * 62, DIM))

def ok(msg: str) -> None:
    print(_c(f"  ✓  {msg}", GREEN))

def warn(msg: str) -> None:
    print(_c(f"  ⚠  {msg}", YELLOW))

def fail(msg: str) -> None:
    print(_c(f"  ✗  {msg}", RED))

def info(msg: str) -> None:
    print(f"     {msg}")

def kv(key: str, val, indent: int = 5) -> None:
    val_s = str(val)
    if len(val_s) > 72:
        val_s = val_s[:72] + "…"
    print(f"{'':>{indent}}{_c(key + ':', DIM):<28} {val_s}")

def diff_block(diff: str, max_lines: int = 22) -> None:
    lines = diff.strip().splitlines()[:max_lines]
    for ln in lines:
        if ln.startswith("+") and not ln.startswith("+++"):
            print(_c(f"  {ln}", GREEN))
        elif ln.startswith("-") and not ln.startswith("---"):
            print(_c(f"  {ln}", RED))
        elif ln.startswith("@@"):
            print(_c(f"  {ln}", CYAN))
        else:
            print(f"  {ln}")
    remaining = len(diff.splitlines()) - max_lines
    if remaining > 0:
        print(_c(f"  … ({remaining} more lines)", DIM))

def status_badge(passed: bool) -> str:
    return _c(" PASS ", GREEN, BOLD) if passed else _c(" FAIL ", RED, BOLD)


# ─────────────────────────── Infrastructure probe ────────────────────────────

@dataclass
class InfraStatus:
    postgres: bool = False
    redis:    bool = False
    qdrant:   bool = False

    @property
    def full_stack(self) -> bool:
        return self.postgres and self.redis and self.qdrant

    @property
    def mode_label(self) -> str:
        return (_c("FULL STACK", GREEN, BOLD) if self.full_stack
                else _c("LIGHT (in-memory)", YELLOW, BOLD))


def _tcp_ping(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def probe_infra() -> InfraStatus:
    section("Infrastructure Pre-flight", "🔍")
    status = InfraStatus()

    checks = [
        ("PostgreSQL", "localhost", 5432, "postgres"),
        ("Redis",      "localhost", 6379, "redis"),
        ("Qdrant",     "localhost", 6333, "qdrant"),
    ]

    for label, host, port, attr in checks:
        result = await asyncio.to_thread(_tcp_ping, host, port)
        setattr(status, attr, result)
        if result:
            ok(f"{label} :{port} reachable")
        else:
            warn(f"{label} :{port} unreachable — running without persistence for this service")

    print()
    print(f"     Mode: {status.mode_label}")
    if not status.full_stack:
        warn("Start infrastructure with:  docker compose -f docker-compose.dev.yml up -d")
    return status


# ─────────────────────────── Full-stack pipeline ─────────────────────────────

async def run_full_stack(infra: InfraStatus, error_payload: dict) -> None:
    """Run the real Orchestrator pipeline with live DB persistence."""
    section("Full-Stack Pipeline  (Postgres + Redis + Qdrant + LLM)", "🚀")

    from storage.postgres import PostgresClient
    from storage.redis_client import RedisClient
    from storage.qdrant_client import QdrantClientWrapper
    from config.llm_router import get_llm_router
    from agents.orchestrator import Orchestrator

    # Initialise storage clients
    pg    = PostgresClient()
    redis = RedisClient()
    qdrant = QdrantClientWrapper()
    llm   = get_llm_router()

    # (a) Persist error to Postgres
    section("Stage 1 — Ingest error to PostgreSQL", "📥")
    t0 = time.perf_counter()
    error_id = await pg.save_error(error_payload)
    ms = (time.perf_counter() - t0) * 1000
    ok(f"Error persisted  id={error_id}  ({ms:.0f} ms)")
    kv("error_class", error_payload["error_class"])
    kv("service",     error_payload["service"])
    kv("severity",    error_payload["severity"])

    # (b) Track pipeline start in Redis
    section("Stage 2 — Register pipeline in Redis", "⚡")
    t0 = time.perf_counter()
    await redis.set_pipeline_state(str(error_id), "status", "started")
    ms = (time.perf_counter() - t0) * 1000
    ok(f"Pipeline state = 'started'  ({ms:.0f} ms)")

    # (c) Run orchestrator (stages 1–12)
    section("Stages 3–12 — Orchestrator (full 12-stage pipeline)", "🤖")
    print(_c("     This runs: Context → Debug → Fix → Validate → Review → PR", DIM))
    print()

    orchestrator = Orchestrator(
        postgres=pg,
        redis=redis,
        llm_router=llm,
        repo_path=".",
    )

    t_pipeline = time.perf_counter()
    result = await orchestrator.run(str(error_id))
    total_s = time.perf_counter() - t_pipeline

    # Print orchestrator result
    section("Pipeline Result", "📊")
    kv("Status",           result.status)
    kv("Stage reached",    result.stage_reached)
    kv("Sandbox iters",    result.sandbox_iterations)
    kv("Security retries", result.security_retries)
    kv("Blast risk",       result.blast_risk)
    kv("PR URL",           result.pr_url or "— (requires GitHub App config)")
    kv("Slack notified",   result.slack_sent)
    kv("Total time",       f"{total_s:.1f} s")

    if result.root_cause:
        print()
        kv("Root cause confidence", f"{result.root_cause.confidence:.0%}")
        kv("Root cause",            result.root_cause.root_cause[:100])

    if result.fix:
        print()
        kv("Files changed",  result.fix.total_files_changed)
        kv("Lines changed",  result.fix.total_lines_changed)
        kv("Regression risk", result.fix.regression_risk)
        if result.fix.patches:
            print()
            print(_c("  ┌─ Unified Diff ──────────────────────────────────────────┐", DIM))
            diff_block(result.fix.patches[0].unified_diff)
            print(_c("  └─────────────────────────────────────────────────────────┘", DIM))

    if result.review:
        print()
        rec = result.review.overall_recommendation
        colour = {
            "approve": GREEN,
            "approve_with_comments": YELLOW,
            "reject": RED,
        }.get(rec, "")
        kv("Quality score",     f"{result.review.quality_score:.0%}")
        kv("Recommendation",    _c(rec.replace('_', ' ').upper(), colour, BOLD))

    if result.validation:
        print()
        kv("Validation",   status_badge(result.validation.passed))
        if result.validation.blocking_issues:
            for issue in result.validation.blocking_issues[:3]:
                warn(f"Blocking: {issue}")

    # (d) Confirm final state in Redis
    state = await redis.get_pipeline_state(str(error_id))
    kv("Redis final state", state or "(not set)")

    await pg.close()
    await redis.close()
    await qdrant.close()


# ─────────────────────────── Light mode pipeline ─────────────────────────────

async def run_light_mode(error_payload: dict) -> None:
    """In-memory pipeline — same agents, no infrastructure required."""
    section("Light Mode Pipeline  (in-memory, no DB required)", "⚡")
    print(_c("     Same LLM agents as full-stack — persistence skipped.", DIM))

    # ── Stage 1: Normalisation ────────────────────────────────────────────────
    section("Stage 1 — Error Normalisation", "📋")
    from ingestion.normalizer import ErrorNormalizer
    t0 = time.perf_counter()
    normalizer = ErrorNormalizer()
    normalized = normalizer.normalize(error_payload, source="github_actions")
    ms = (time.perf_counter() - t0) * 1000
    ok(f"Normalised in {ms:.1f} ms")
    kv("fingerprint",      normalized.get("fingerprint", "—")[:24])
    kv("dedup_hash",       normalized.get("dedup_hash", "—")[:24])

    # ── Stage 2: Classification ───────────────────────────────────────────────
    section("Stage 2 — Error Classification", "🏷️")
    from ingestion.classifier import ErrorClassifier
    t0 = time.perf_counter()
    classifier = ErrorClassifier()
    classification = await classifier.classify(normalized)
    ms = (time.perf_counter() - t0) * 1000
    error_class_label = (
        classification.get("error_class", "unknown")
        if isinstance(classification, dict)
        else str(classification)
    )
    ok(f"Classified in {ms:.1f} ms  (rule-based, no LLM cost)")
    kv("error_class",  error_class_label)
    kv("auto_fixable", classification.get("auto_fixable", "?") if isinstance(classification, dict) else "?")

    # ── Stage 3: AST Chunking ─────────────────────────────────────────────────
    section("Stage 3 — AST-Aware Code Chunking (tree-sitter)", "🌳")
    from context.ast_chunker import ASTChunker
    from context.retriever import count_tokens
    t0 = time.perf_counter()
    chunker = ASTChunker()
    chunks  = chunker.chunk_file("storage/postgres.py", service="auth-service")
    ms      = (time.perf_counter() - t0) * 1000
    total_tokens = sum(count_tokens(c.content) for c in chunks)
    ok(f"{len(chunks)} chunks  |  {total_tokens} tokens  |  {ms:.1f} ms")
    print()
    print(f"     {_c('Range', DIM):<20} {_c('Function', DIM):<36} {_c('Tokens', DIM)}")
    for c in chunks[:8]:
        name   = c.display_name[:34]
        tokens = count_tokens(c.content)
        print(f"     [{c.line_start:3d}–{c.line_end:3d}]          {name:<36} {tokens}")
    if len(chunks) > 8:
        print(_c(f"     … and {len(chunks) - 8} more", DIM))

    # ── Stage 4: Context Bundle ───────────────────────────────────────────────
    section("Stage 4 — Context Bundle Assembly", "📦")
    from context.builder import ContextBundle
    from context.git_analyzer import GitAnalyzer
    git     = GitAnalyzer(".")
    commits = git.get_recent_commits(days=30, max_commits=5)
    erroring_chunk = chunks[0] if chunks else None
    bundle = ContextBundle(
        error_id="demo-attr-001",
        erroring_file="storage/postgres.py",
        erroring_function=erroring_chunk.display_name if erroring_chunk else "unknown",
        erroring_code=erroring_chunk.content if erroring_chunk else "",
        related_functions=[],
        recent_commits=commits,
        similar_past_bugs=[],
        total_tokens=count_tokens(erroring_chunk.content) if erroring_chunk else 0,
    )
    ok(f"Bundle ready  —  {bundle.total_tokens} tokens  |  {len(commits)} recent commits")
    if commits:
        kv("latest commit", f"[{commits[0].get('sha','')[:7]}] {commits[0].get('message','')[:55]}")

    # ── Stage 5: DebuggerAgent ────────────────────────────────────────────────
    section("Stage 5 — DebuggerAgent  (Groq  temp=0.1)", "🔬")
    from agents.debugger import DebuggerAgent
    from config.llm_router import get_llm_router
    llm      = get_llm_router()
    debugger = DebuggerAgent(llm_router=llm)
    error_dict = {
        "id": "demo-attr-001",
        **{k: error_payload[k] for k in
           ("error_class", "message", "stack_trace", "file_path",
            "line_number", "service", "severity", "commit_sha", "branch")},
        "trace_id": None,
    }
    print(_c("     Sending to Groq…", DIM), flush=True)
    t0 = time.perf_counter()
    root_cause = await debugger.analyze(bundle, error_dict)
    ms = (time.perf_counter() - t0) * 1000
    ok(f"Analysis complete  ({ms:.0f} ms)")
    print()
    kv("confidence",      f"{root_cause.confidence:.0%}")
    kv("evidence_quality",root_cause.evidence_quality)
    kv("strategy",        root_cause.suggested_strategy)
    kv("files_to_change", root_cause.files_to_change)
    print()
    print(f"     {_c('ROOT CAUSE', BOLD, WHITE)}")
    for line in textwrap.wrap(root_cause.root_cause, 64):
        print(f"     {line}")
    if root_cause.contributing_factors:
        print(f"\n     {_c('CONTRIBUTING FACTORS', BOLD, WHITE)}")
        for f in root_cause.contributing_factors[:4]:
            print(f"       • {f}")

    # ── Stage 6: FixerAgent ───────────────────────────────────────────────────
    section("Stage 6 — FixerAgent  (Groq  temp=0.15)", "🔧")
    from agents.fixer import FixerAgent
    fixer = FixerAgent(llm_router=llm)
    print(_c(f"     Generating minimal patch for: {root_cause.files_to_change}", DIM), flush=True)
    t0  = time.perf_counter()
    fix = await fixer.generate(root_cause, bundle, error_dict)
    ms  = (time.perf_counter() - t0) * 1000
    ok(f"Patch generated  ({ms:.0f} ms)")
    kv("files_changed",   fix.total_files_changed)
    kv("lines_changed",   fix.total_lines_changed)
    kv("regression_risk", fix.regression_risk)
    kv("confidence",      f"{fix.confidence_retained:.0%}")
    kv("strategy",        fix.strategy)
    print()
    print(f"     {_c('EXPLANATION', BOLD, WHITE)}")
    for line in textwrap.wrap(fix.fix_explanation[:200], 64):
        print(f"     {line}")
    if fix.patches:
        print()
        print(_c("  ┌─ Unified Diff ──────────────────────────────────────────────┐", DIM))
        diff_block(fix.patches[0].unified_diff, max_lines=28)
        print(_c("  └────────────────────────────────────────────────────────────┘", DIM))
        ok(f"Diff: {fix.patches[0].lines_changed} lines changed in {fix.patches[0].file_path}")
    else:
        warn("No diff generated — LLM needs more context (Qdrant unavailable in light mode)")

    # ── Stage 7: Validation Engine ────────────────────────────────────────────
    section("Stage 7 — ValidationEngine  (Ruff + Test Runner)", "🧪")
    from validation.engine import ValidationEngine
    validator  = ValidationEngine(repo_path=".")
    t0         = time.perf_counter()
    val_report = await validator.validate(fix, "demo-attr-001")
    ms         = (time.perf_counter() - t0) * 1000
    print(f"     Validation: {status_badge(val_report.passed)}   ({ms:.0f} ms)")
    if val_report.static_analysis:
        sa = val_report.static_analysis
        kv("static_analysis", f"{sa.tool}  errors={sa.error_count}  warnings={sa.warning_count}")
    if val_report.blocking_issues:
        for issue in val_report.blocking_issues[:3]:
            warn(f"Blocking: {issue}")
    else:
        ok("No blocking issues")

    # ── Stage 8: ReviewerAgent ────────────────────────────────────────────────
    section("Stage 8 — ReviewerAgent  (Groq  temp=0.0)", "🔍")
    from agents.reviewer import ReviewerAgent
    reviewer = ReviewerAgent(llm_router=llm)
    print(_c("     Reviewing patch…", DIM), flush=True)
    t0     = time.perf_counter()
    review = await reviewer.review(fix, root_cause, error_dict)
    ms     = (time.perf_counter() - t0) * 1000
    ok(f"Review complete  ({ms:.0f} ms)")
    rec    = review.overall_recommendation
    colour = {"approve": GREEN, "approve_with_comments": YELLOW, "reject": RED}.get(rec, "")
    kv("quality_score",    f"{review.quality_score:.0%}")
    kv("correctness",      _c("PASS", GREEN) if review.correctness_passes else _c("FAIL", RED))
    kv("security",         _c("PASS", GREEN) if review.security_passes else _c("FAIL", RED))
    kv("recommendation",   _c(rec.replace("_", " ").upper(), colour, BOLD))
    if review.rejection_reason:
        kv("rejection_reason", review.rejection_reason[:80])
    if review.issues:
        print(f"\n     {_c('ISSUES', BOLD, WHITE)}")
        for issue in review.issues[:4]:
            print(f"       • {issue}")
    print()
    for line in textwrap.wrap(review.reviewer_notes[:160], 64):
        print(f"     {DIM}{line}{RESET}")

    # ── Auto-merge eligibility ────────────────────────────────────────────────
    auto_eligible = (
        rec == "approve"
        and fix.confidence_retained >= 0.88
        and fix.regression_risk == "low"
        and val_report.passed
    )
    print()
    if auto_eligible:
        print(_c("  🚀  AUTO-MERGE ELIGIBLE — would be committed without human review", GREEN, BOLD))
    else:
        print(_c("  👤  Requires human review — Slack notification would be sent", YELLOW))

    return (root_cause, fix, val_report, review, error_class_label, chunks, commits, bundle)


# ─────────────────────────── Summary table ───────────────────────────────────

def print_summary(
    mode: str,
    error_class_label: str,
    chunks: list,
    commits: list,
    bundle,
    root_cause,
    fix,
    val_report,
    review,
    t_start: float,
) -> None:
    banner("DARA PIPELINE — EXECUTION SUMMARY")
    total_ms = (time.perf_counter() - t_start) * 1000

    rows = [
        ("Mode",                mode,                                        "—"),
        ("1. Normalise",        "fingerprint + dedup hash",                  "<1 ms"),
        ("2. Classify",         str(error_class_label),                      "<1 ms"),
        ("3. AST Chunk",        f"{len(chunks)} functions extracted",        "fast"),
        ("4. Git Analyse",      f"{len(commits)} recent commits",            "fast"),
        ("5. Context Bundle",   f"{bundle.total_tokens} tokens assembled",   "fast"),
        ("6. DebuggerAgent",    f"{root_cause.confidence*100:.0f}% conf, {root_cause.suggested_strategy}", "LLM"),
        ("7. FixerAgent",       f"{fix.total_lines_changed} lines, risk={fix.regression_risk}", "LLM"),
        ("8. Validation",       "PASS" if val_report.passed else "ISSUES",   "fast"),
        ("9. ReviewerAgent",    review.overall_recommendation.replace("_", " "), "LLM"),
        ("Total LLM calls",     "3 (Debugger + Fixer + Reviewer)",           f"{total_ms/1000:.1f} s"),
    ]

    print()
    print(f"     {_c('Stage', BOLD):<28} {_c('Result', BOLD):<38} {_c('Cost', BOLD)}")
    print(_c("     " + "─" * 70, DIM))
    for stage, result_s, timing in rows:
        colour = DIM if stage in ("Mode", "Total LLM calls") else ""
        print(f"     {_c(stage, colour):<28} {result_s:<38} {_c(timing, DIM)}")

    rec = review.overall_recommendation
    colour = {"approve": GREEN, "approve_with_comments": YELLOW, "reject": RED}.get(rec, "")
    print()
    print(_c("  ┌─ OUTCOME ───────────────────────────────────────────────────────────┐", CYAN))
    print(_c("  │", CYAN) + f"  Fix for AttributeError → {_c(rec.replace('_', ' ').upper(), colour, BOLD)}")
    print(_c("  │", CYAN) + f"  Quality: {review.quality_score:.0%}   Confidence: {fix.confidence_retained:.0%}   Risk: {fix.regression_risk}")
    print(_c("  └────────────────────────────────────────────────────────────────────┘", CYAN))
    print()


# ─────────────────────────── Entry point ─────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(description="DARA full-stack demo")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--full",  action="store_true", help="Fail if infra not running")
    group.add_argument("--light", action="store_true", help="Always run in-memory mode")
    args = parser.parse_args()

    banner("DARA  —  Autonomous Bug Resolution System  |  Demo")
    print()
    print(f"  {_c('Ingest → Normalise → Classify → AST → Debug → Fix → Validate → Review', DIM)}")

    # ── Sample error ──────────────────────────────────────────────────────────
    error_payload = {
        "source":      "github_actions",
        "job":         "run-tests",
        "run_id":      "13872401",
        "service":     "auth-service",
        "error_class": "AttributeError",
        "message":     "NoneType object has no attribute get",
        "stack_trace": textwrap.dedent("""
            Traceback (most recent call last):
              File "auth/service.py", line 87, in authenticate_user
                user_data = session.get_user(token)
              File "auth/session.py", line 44, in get_user
                return self._cache.get('user_id')
              File "auth/cache.py", line 12, in get
                return self._store.get(key)
            AttributeError: 'NoneType' object has no attribute 'get'
        """).strip(),
        "file_path":   "auth/session.py",
        "line_number": 44,
        "commit_sha":  "3f8a1b2",
        "branch":      "feature/token-refresh",
        "severity":    "critical",
    }

    t_start = time.perf_counter()

    # ── Infra probe ───────────────────────────────────────────────────────────
    if args.light:
        infra = InfraStatus()
    else:
        infra = await probe_infra()
        if args.full and not infra.full_stack:
            fail("--full flag set but infrastructure is not fully running.")
            fail("Start it with:  docker compose -f docker-compose.dev.yml up -d")
            return 1

    # ── Run pipeline ──────────────────────────────────────────────────────────
    if infra.full_stack:
        try:
            await run_full_stack(infra, error_payload)
            banner("FULL-STACK PIPELINE COMPLETE")
            print()
            ok("Error persisted to PostgreSQL")
            ok("Pipeline state tracked in Redis")
            ok("Embeddings stored in Qdrant (for future similar-bug retrieval)")
            ok("Slack notification sent (if configured)")
            ok("GitHub PR created (if GitHub App is configured)")
            print()
            return 0
        except Exception as exc:
            warn(f"Full-stack mode error: {exc}")
            warn("Falling back to light mode…")

    # Light mode — same agents, no persistence
    result = await run_light_mode(error_payload)
    if result:
        root_cause, fix, val_report, review, error_class_label, chunks, commits, bundle = result
        print_summary(
            mode="LIGHT (in-memory)",
            error_class_label=error_class_label,
            chunks=chunks,
            commits=commits,
            bundle=bundle,
            root_cause=root_cause,
            fix=fix,
            val_report=val_report,
            review=review,
            t_start=t_start,
        )
        print()
        if not infra.full_stack:
            print(_c("  💡  Run with full infra for DB persistence + Slack + GitHub PR:", DIM))
            print(_c("      docker compose -f docker-compose.dev.yml up -d", CYAN))
            print(_c("      poetry run python run_demo.py", CYAN))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
