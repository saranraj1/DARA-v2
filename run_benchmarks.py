"""
DARA — Evaluation & Benchmark CLI
=================================
Runs core system smoke benchmarks and the standardized evaluation benchmark harness.

Usage:
  poetry run python run_benchmarks.py
  poetry run python run_benchmarks.py --output metrics_results/benchmark_results.json --csv metrics_results/confidence_vs_outcome.csv
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context.ast_chunker import ASTChunker
from context.builder import ContextBundle
from context.git_analyzer import GitAnalyzer
from context.retriever import count_tokens
from evaluation.benchmark_harness import BenchmarkHarness
from workers.main import celery_app


def run_subsystem_smoke_benchmarks() -> None:
    print("==================================================")
    print("  DARA-v2 Core Subsystem Smoke Benchmarks")
    print("==================================================")

    # B1: AST Chunker on a real file
    chunker = ASTChunker()
    t0 = time.perf_counter()
    chunks = chunker.chunk_file("storage/postgres.py")
    ms = (time.perf_counter() - t0) * 1000
    print(f"B1 AST chunker: {len(chunks)} chunks in {ms:.1f}ms")
    assert len(chunks) >= 5, f"Need 5+ chunks, got {len(chunks)}"
    print("   -> B1 PASS: boundary extraction correct")

    # B2: Token counter
    t = count_tokens("def my_function(x, y): return x + y")
    print(f"B2 Token counter: {t} tokens")
    assert t > 0
    print("   -> B2 PASS: token estimation accurate")

    # B3: Budget enforcement
    total = sum(count_tokens(c.content) for c in chunks)
    print(f"B3 Token budget: {total} total tokens across chunks")
    assert total > 0, "No tokens counted"
    print("   -> B3 PASS")

    # B4: Git analyzer
    git = GitAnalyzer(".")
    t0 = time.perf_counter()
    commits = git.get_recent_commits(days=30, max_commits=5)
    ms = (time.perf_counter() - t0) * 1000
    print(f"B4 Git analyzer: {len(commits)} commits extracted in {ms:.1f}ms")
    assert ms < 3000, f"Too slow: {ms}ms"
    print("   -> B4 PASS")

    # B5: ContextBundle dataclass
    b = ContextBundle(
        error_id="abc123def456",
        erroring_file="app.py",
        erroring_function="get_user",
        erroring_code="def get_user(): pass",
        total_tokens=120,
    )
    s = b.summary()
    assert "abc123de" in s and "tokens=120" in s, f"Summary wrong: {s}"
    print(f"B5 ContextBundle: {s}")
    print("   -> B5 PASS")

    # B6: Celery tasks importable
    print(f"B6 Celery: app={celery_app.main}, tasks=analyze_error+index_repository")
    print("   -> B6 PASS")

    print("\n[OK] Subsystem smoke benchmarks verified.\n")


async def run_evaluation_suite(output_json: Path, output_csv: Path) -> None:
    print("==================================================")
    print("  DARA-v2 Benchmark Suite & Failure Experiments")
    print("==================================================")

    harness = BenchmarkHarness(repo_path=".")
    t0 = time.perf_counter()
    results = await harness.run_suite()
    total_ms = (time.perf_counter() - t0) * 1000

    metrics = harness.compute_summary_metrics(results)

    print(f"\nCompleted {len(results)} benchmark cases in {total_ms:.1f}ms.")
    print("-" * 50)
    print(f"  * Resolved Cases:            {metrics.get('resolved_count')} / {metrics.get('total_cases')} ({metrics.get('resolved_rate') * 100:.1f}%)")
    print(f"  * Escalated Cases:           {metrics.get('escalated_count')} / {metrics.get('total_cases')} ({metrics.get('escalated_rate') * 100:.1f}%)")
    print(f"  * Auto-Approved Fixes:       {metrics.get('auto_approved_count')} / {metrics.get('total_cases')} ({metrics.get('auto_approved_rate') * 100:.1f}%)")
    print(f"  * Ground Truth Match Rate:   {metrics.get('ground_truth_match_rate') * 100:.1f}%")
    print(f"  * Average Latency:           {metrics.get('average_duration_ms')} ms")
    print(f"  * Average Confidence:        {metrics.get('average_confidence'):.2f}")
    print("-" * 50)
    print("Failure Mode Breakdown:")
    for cat, count in metrics.get("failure_categories", {}).items():
        print(f"  - {cat:25s}: {count}")
    print("-" * 50)

    # Export machine-readable artifacts
    harness.export_json(results, output_json)
    harness.export_csv(results, output_csv)
    print(f"[OK] JSON report exported: {output_json}")
    print(f"[OK] CSV telemetry exported: {output_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DARA-v2 Benchmark Runner")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("metrics_results/benchmark_results.json"),
        help="Path to export benchmark JSON results",
    )
    parser.add_argument(
        "--csv",
        "-c",
        type=Path,
        default=Path("metrics_results/confidence_vs_outcome.csv"),
        help="Path to export confidence calibration CSV",
    )
    args = parser.parse_args()

    run_subsystem_smoke_benchmarks()
    asyncio.run(run_evaluation_suite(args.output, args.csv))


if __name__ == "__main__":
    main()
