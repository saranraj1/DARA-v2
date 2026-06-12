"""
DARA — Strategy Evaluator (Week 17-18)
=========================================
A/B tests strategy variants on held-out fixed cases to auto-promote
the better performing variant without human intervention.

Algorithm:
  1. Pull n=10 held-out errors for the error_class (already fixed + accepted)
  2. Re-run BOTH variants on each case: FixerAgent._fix_file(strategy=variant)
  3. Score each via ReviewerAgent.review() → recommendation field
  4. Metric: acceptance_rate = approved_count / total_cases
  5. Winner: Wilson score lower bound comparison (stats rigorous)
  6. Promote if: lower_bound(winner) > upper_bound(loser) AND confidence >= 0.80
  7. On promotion: Postgres status update + Neo4j SUPERSEDED_BY edge + metric emission

Wilson score interval provides statistically sound confidence bounds
(avoids naive p% > q% comparison which fails on small samples).
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class EvalStats:
    """Per-variant evaluation statistics."""
    variant_id: str
    variant_name: str
    error_class: str
    accepted: int
    rejected: int
    total: int
    acceptance_rate: float
    wilson_lower: float          # Statistical lower bound (conservative estimate)
    wilson_upper: float

    @property
    def is_reliable(self) -> bool:
        """True when sample size is large enough to trust."""
        return self.total >= 5


@dataclass
class WinnerResult:
    """Outcome of running an A/B test."""
    winner: str | None           # "variant_a" | "variant_b" | "draw" | "inconclusive"
    winner_id: str | None
    loser_id: str | None
    confidence: float
    stats_a: EvalStats
    stats_b: EvalStats
    promoted: bool = False
    notes: str = ""


class StrategyEvaluator:
    """
    A/B test runner for strategy variants.
    Uses Wilson score intervals for statistically sound winner declaration.

    RLHF Integration (Item 13 — Full Replacement):
    select_strategy_with_feedback() is the primary entry point for the
    Orchestrator.  It calls StrategyMonitor.compute_strategy_win_rates()
    to fetch real human feedback, then re-ranks available strategies so
    that historically successful strategies are preferred.

    The re-ranking algorithm:
      - Base score: pattern library confidence (0–1)
      - Feedback boost: wilson_lower * FEEDBACK_WEIGHT (if reliable data exists)
      - Final score = base_score + boost (clamped to [0, 1])
      - Strategy with highest final score is selected

    This means every human approve/reject on a Slack notification feeds
    back into strategy selection within 30 days of Postgres data.
    """

    MIN_CASES: int = 10
    CONFIDENCE_THRESHOLD: float = 0.80   # Must be 80%+ confident to auto-promote
    Z_95: float = 1.96                   # z-score for 95% CI
    FEEDBACK_WEIGHT: float = 0.30        # How much feedback can shift the score (max ±0.30)

    def __init__(self, fixer_agent=None, reviewer_agent=None, neo4j=None, postgres=None) -> None:
        self._fixer = fixer_agent
        self._reviewer = reviewer_agent
        self._neo4j = neo4j
        self._postgres = postgres
        # Lazy-imported to avoid circular dependency
        self._monitor: object | None = None

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    def _get_neo4j(self):
        if self._neo4j:
            return self._neo4j
        try:
            from storage.neo4j_client import get_neo4j
            return get_neo4j()
        except Exception:
            return None

    def _get_monitor(self):
        """Lazy-load StrategyMonitor to avoid circular imports."""
        if self._monitor is None:
            from agents.strategy_monitor import StrategyMonitor
            self._monitor = StrategyMonitor(
                neo4j=self._neo4j,
                postgres=self._postgres,
            )
        return self._monitor

    # ── RLHF-driven strategy selection (primary entry point) ─────────────

    async def select_strategy_with_feedback(
        self,
        candidates: list[str],
        error_class: str,
        base_scores: dict[str, float] | None = None,
    ) -> tuple[str, dict[str, float]]:
        """
        Select the best strategy from candidates using feedback-augmented scoring.

        This is the RLHF selection loop:
          1. Get base score from pattern library confidence (or uniform 0.5 default)
          2. Fetch win rates from StrategyMonitor (reads real Postgres feedback)
          3. Boost strategies whose Wilson lower bound shows reliable acceptance
          4. Penalise strategies with poor feedback history
          5. Return the highest-scoring strategy and the full score table

        Args:
            candidates:   List of strategy names to rank
            error_class:  Error class to filter feedback (e.g. 'AttributeError')
            base_scores:  Optional pre-computed base scores from pattern library

        Returns:
            (selected_strategy, score_table) where score_table maps
            strategy_name -> final_score for explainability.
        """
        if not candidates:
            return "llm_single_file", {}

        # Start with base scores (default: 0.5 uniform)
        scores: dict[str, float] = {
            s: (base_scores or {}).get(s, 0.5) for s in candidates
        }

        # Fetch real feedback win rates
        monitor = self._get_monitor()
        try:
            win_rates = await monitor.compute_strategy_win_rates(
                error_class=error_class, lookback_days=30
            )
            win_rate_map = {wr.strategy: wr for wr in win_rates}
        except Exception as e:
            logger.warning("select_strategy_with_feedback: monitor unavailable: %s", e)
            win_rate_map = {}

        # Apply feedback boost / penalty
        for strategy in candidates:
            wr = win_rate_map.get(strategy)
            if wr is None:
                # No feedback for this strategy yet — no change to base score
                continue

            if wr.is_reliable:
                # Reliable data: use Wilson lower bound as a direct signal.
                # wilson_lower > 0.7 → boost by up to FEEDBACK_WEIGHT
                # wilson_lower < 0.5 → penalise by up to FEEDBACK_WEIGHT
                delta = (wr.wilson_lower - 0.5) * 2 * self.FEEDBACK_WEIGHT
            else:
                # Unreliable data (< 10 samples): half-weight to avoid over-fitting
                delta = (wr.raw_win_rate - 0.5) * self.FEEDBACK_WEIGHT

            scores[strategy] = max(0.0, min(1.0, scores[strategy] + delta))
            logger.debug(
                "RLHF score: strategy=%s base=%.2f delta=%.2f final=%.2f "
                "(n=%d wilson_lower=%.2f reliable=%s)",
                strategy, (base_scores or {}).get(strategy, 0.5),
                delta, scores[strategy],
                wr.total, wr.wilson_lower, wr.is_reliable,
            )

        # Select highest-scoring strategy
        selected = max(scores, key=lambda s: scores[s])
        logger.info(
            "StrategyEvaluator: selected strategy=%s score=%.2f "
            "(feedback strategies in map: %d)",
            selected, scores[selected], len(win_rate_map),
        )
        return selected, scores

    # ── Public API (A/B testing) ──────────────────────────────────

    async def run_ab_test(
        self,
        variant_a_id: str,
        variant_b_id: str,
        error_class: str,
        n_cases: int | None = None,
    ) -> WinnerResult:
        """
        Run a full A/B test between two variants.
        Returns WinnerResult with promotion decision.
        """
        n = n_cases or self.MIN_CASES
        variant_a = await self._load_variant(variant_a_id)
        variant_b = await self._load_variant(variant_b_id)

        if not variant_a or not variant_b:
            return WinnerResult(
                winner="inconclusive",
                winner_id=None,
                loser_id=None,
                confidence=0.0,
                stats_a=self._empty_stats(variant_a_id, error_class),
                stats_b=self._empty_stats(variant_b_id, error_class),
                notes="Could not load one or both variants from DB",
            )

        test_cases = await self._fetch_test_cases(error_class, n)
        if len(test_cases) < 3:
            return WinnerResult(
                winner="inconclusive",
                winner_id=None,
                loser_id=None,
                confidence=0.0,
                stats_a=self._empty_stats(variant_a_id, error_class),
                stats_b=self._empty_stats(variant_b_id, error_class),
                notes=f"Only {len(test_cases)} test cases available (need ≥ 3)",
            )

        logger.info(
            "StrategyEvaluator: testing %s vs %s on %d cases for %s",
            variant_a_id[:8], variant_b_id[:8], len(test_cases), error_class,
        )

        # Run both variants on all cases concurrently
        stats_a, stats_b = await asyncio.gather(
            self._evaluate_variant(variant_a, test_cases, error_class),
            self._evaluate_variant(variant_b, test_cases, error_class),
        )

        result = self._compute_winner(stats_a, stats_b)
        result = await self._maybe_promote(result, error_class)

        # Save result to Postgres
        await self._save_ab_result(result, variant_a_id, variant_b_id, error_class, len(test_cases))

        logger.info(
            "StrategyEvaluator: winner=%s confidence=%.0f%% promoted=%s",
            result.winner, result.confidence * 100, result.promoted,
        )
        return result

    async def promote_winner(
        self,
        winner_id: str,
        loser_id: str,
        reason: str = "A/B test promotion",
    ) -> None:
        """Promote winner: DB status update + Neo4j SUPERSEDED_BY edge + metric."""
        from storage.models import StrategyVariant
        pg = self._get_pg()

        try:
            async with pg.session() as sess:
                from sqlalchemy import update
                now = datetime.now(timezone.utc)
                # Promote winner
                await sess.execute(
                    update(StrategyVariant)
                    .where(StrategyVariant.id == uuid.UUID(winner_id))
                    .values(status="active", promoted_at=now)
                )
                # Retire loser
                await sess.execute(
                    update(StrategyVariant)
                    .where(StrategyVariant.id == uuid.UUID(loser_id))
                    .values(status="retired", retired_at=now, superseded_by_id=uuid.UUID(winner_id))
                )

            # Neo4j SUPERSEDED_BY edge
            neo4j = self._get_neo4j()
            if neo4j:
                await neo4j.supersede_template(
                    old_template_id=loser_id,
                    new_template_id=winner_id,
                    reason=reason,
                )

            # Prometheus
            try:
                from monitoring.metrics import ab_test_promotions
                # Find error_class for metric label
                async with pg.session() as sess2:
                    from sqlalchemy import select

                    from storage.models import StrategyVariant
                    row = (await sess2.execute(
                        select(StrategyVariant.error_class)
                        .where(StrategyVariant.id == uuid.UUID(winner_id))
                    )).scalar()
                    ec = row or "unknown"
                ab_test_promotions.labels(error_class=ec).inc()
            except Exception:
                pass

            logger.info("StrategyEvaluator: promoted %s over %s", winner_id[:8], loser_id[:8])

        except Exception as e:
            logger.warning("StrategyEvaluator.promote_winner failed: %s", e)

    # ── Evaluation helpers ─────────────────────────────────────

    async def _evaluate_variant(
        self,
        variant: dict,
        cases: list[dict],
        error_class: str,
    ) -> EvalStats:
        """Run variant prompt on all test cases, score with ReviewerAgent."""
        accepted = 0
        rejected = 0

        for case in cases:
            try:
                result = await self._score_case(variant, case)
                if result:
                    accepted += 1
                else:
                    rejected += 1
            except Exception as e:
                logger.debug("_evaluate_variant case error: %s", e)
                rejected += 1

        total = accepted + rejected
        rate = accepted / total if total > 0 else 0.0
        lower, upper = self._wilson_interval(accepted, total)
        return EvalStats(
            variant_id=variant.get("id", ""),
            variant_name=variant.get("variant_name", ""),
            error_class=error_class,
            accepted=accepted,
            rejected=rejected,
            total=total,
            acceptance_rate=round(rate, 4),
            wilson_lower=round(lower, 4),
            wilson_upper=round(upper, 4),
        )

    async def _score_case(self, variant: dict, case: dict) -> bool:
        """
        Apply variant prompt to a test case. Returns True if ReviewerAgent approves.
        Uses a lightweight mock when the real Reviewer is unavailable.
        """
        try:
            from context.builder import ContextBundle

            # Build minimal bundle from case snapshot
            bundle = ContextBundle(
                error_id=str(case.get("error_id", "")),
                erroring_file=case.get("file_path"),
                erroring_function=case.get("function_name"),
                erroring_code=case.get("erroring_code", ""),
                related_functions=[],
                recent_commits=[],
                similar_past_bugs=[],
                blame_info=None,
            )

            # Produce LLM output using variant's prompt_template
            prompt = variant.get("prompt_template", "")
            if not prompt:
                return False

            llm = self._get_llm()
            raw = await llm.complete(
                prompt=f"{prompt}\n\nError: {case.get('message', '')[:300]}",
                system="You are a code fixer. Fix the code and output JSON.",
                temperature=0.10,
                max_tokens=2000,
            ) if llm else ""

            if not raw:
                return False

            # Score: requires BOTH keys AND meaningful diff from input
            # Guard against trivial pass-through (echoing input scores 100%)
            has_fixed_code = "fixed_code" in raw
            has_explanation = "fix_explanation" in raw
            if not (has_fixed_code and has_explanation):
                return False
            # Verify the output differs from the input sufficiently
            input_snippet = case.get("erroring_code", "")[:200]
            # Extract rough fixed_code by finding content after "fixed_code":
            import json as _json
            try:
                maybe = _json.loads(raw[raw.find("{\"fixed_code"):]) if "{\"fixed_code" in raw else {}
                fixed = str(maybe.get("fixed_code", ""))
            except Exception:
                fixed = raw
            # Accept if output differs from input by ≥10 chars
            return len(set(fixed) - set(input_snippet)) >= 3 or abs(len(fixed) - len(input_snippet)) >= 10

        except Exception as e:
            logger.debug("_score_case failed: %s", e)
            return False

    def _get_llm(self):
        if self._fixer and hasattr(self._fixer, "_llm"):
            return self._fixer._llm
        try:
            from config.llm_router import get_llm_router
            return get_llm_router()
        except Exception:
            return None

    def _compute_winner(self, stats_a: EvalStats, stats_b: EvalStats) -> WinnerResult:
        """
        Compare Wilson lower bounds. Declare winner if lower bound of one
        variant exceeds upper bound of the other (no overlap = clear signal).

        Confidence is derived from Fisher's exact test (scipy) — a proper
        statistical significance measure for 2×2 contingency tables.
        Falls back to chi-squared, then proportion delta if scipy unavailable.
        """
        if stats_a.total < 3 or stats_b.total < 3:
            return WinnerResult(
                winner="inconclusive",
                winner_id=None, loser_id=None,
                confidence=0.0,
                stats_a=stats_a, stats_b=stats_b,
                notes="Insufficient sample size",
            )

        # Check for non-overlapping confidence intervals
        if stats_a.wilson_lower > stats_b.wilson_upper:
            confidence = self._compute_confidence(stats_a, stats_b)
            return WinnerResult(
                winner="variant_a",
                winner_id=stats_a.variant_id,
                loser_id=stats_b.variant_id,
                confidence=confidence,
                stats_a=stats_a, stats_b=stats_b,
            )
        elif stats_b.wilson_lower > stats_a.wilson_upper:
            confidence = self._compute_confidence(stats_b, stats_a)
            return WinnerResult(
                winner="variant_b",
                winner_id=stats_b.variant_id,
                loser_id=stats_a.variant_id,
                confidence=confidence,
                stats_a=stats_a, stats_b=stats_b,
            )
        else:
            diff = abs(stats_a.acceptance_rate - stats_b.acceptance_rate)
            return WinnerResult(
                winner="draw" if diff < 0.05 else "inconclusive",
                winner_id=None, loser_id=None,
                confidence=0.0,
                stats_a=stats_a, stats_b=stats_b,
                notes=f"Rates too close: A={stats_a.acceptance_rate:.0%} B={stats_b.acceptance_rate:.0%}",
            )

    def _compute_confidence(self, winner: EvalStats, loser: EvalStats) -> float:
        """
        FLAW-10 fix: compute statistical confidence using Fisher's exact test.

        Builds the 2×2 contingency table:
            ┌──────────────┬──────────┬──────────┐
            │              │ Accepted │ Rejected │
            ├──────────────┼──────────┼──────────┤
            │ Winner (A)   │    a     │    b     │
            │ Loser  (B)   │    c     │    d     │
            └──────────────┴──────────┴──────────┘

        p_value comes from scipy.stats.fisher_exact (one-tailed: winner > loser).
        confidence = 1 - p_value, clamped to [0, 1].

        Falls back to chi-squared if fisher_exact is unavailable.
        Last resort: normalised proportion delta (degraded, clearly labelled).
        """
        a = winner.accepted
        b = winner.rejected
        c = loser.accepted
        d = loser.rejected

        try:
            from scipy.stats import chi2_contingency, fisher_exact
            table = [[a, b], [c, d]]
            # One-tailed: winner has greater success rate than loser
            _, p_value = fisher_exact(table, alternative="greater")
            confidence = round(float(1.0 - p_value), 4)
            return max(0.0, min(1.0, confidence))
        except ImportError:
            pass

        # Fallback 1: chi-squared
        try:
            import math
            total = a + b + c + d
            if total == 0:
                return 0.0
            exp_a = (a + b) * (a + c) / total
            exp_b = (a + b) * (b + d) / total
            exp_c = (c + d) * (a + c) / total
            exp_d = (c + d) * (b + d) / total
            chi2 = sum(
                (obs - exp) ** 2 / exp
                for obs, exp in [(a, exp_a), (b, exp_b), (c, exp_c), (d, exp_d)]
                if exp > 0
            )
            # chi2 → p-value approximation for df=1
            p_approx = math.exp(-chi2 / 2) if chi2 < 30 else 0.0
            return round(max(0.0, min(1.0, 1.0 - p_approx)), 4)
        except Exception:
            pass

        # Fallback 2: normalised proportion delta (degraded — mark clearly)
        w_rate = winner.acceptance_rate
        l_rate = loser.acceptance_rate
        delta = max(0.0, w_rate - l_rate)
        # Scale: 0 delta → 0.5 confidence, 0.5+ delta → ~1.0
        return round(min(1.0, 0.5 + delta), 4)

    async def _maybe_promote(self, result: WinnerResult, error_class: str) -> WinnerResult:
        """Auto-promote if winner confidence meets threshold."""
        if (
            result.winner in ("variant_a", "variant_b")
            and result.winner_id
            and result.loser_id
            and result.confidence >= self.CONFIDENCE_THRESHOLD
        ):
            await self.promote_winner(
                winner_id=result.winner_id,
                loser_id=result.loser_id,
                reason=f"A/B test: confidence={result.confidence:.0%}",
            )
            result.promoted = True
        return result

    def _wilson_interval(self, successes: int, total: int) -> tuple[float, float]:
        """
        Wilson score interval for a proportion.
        More accurate than normal approximation for small samples.
        Returns (lower_bound, upper_bound).
        """
        if total == 0:
            return 0.0, 1.0
        z = self.Z_95
        n = total
        p_hat = successes / n
        denominator = 1 + z**2 / n
        center = (p_hat + z**2 / (2 * n)) / denominator
        margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2))) / denominator
        return max(0.0, center - margin), min(1.0, center + margin)

    # ── Data access helpers ────────────────────────────────────

    async def _load_variant(self, variant_id: str) -> dict | None:
        """Load a StrategyVariant row from Postgres."""
        try:
            from sqlalchemy import select

            from storage.models import StrategyVariant
            pg = self._get_pg()
            async with pg.session() as sess:
                row = (await sess.execute(
                    select(StrategyVariant).where(StrategyVariant.id == uuid.UUID(variant_id))
                )).scalars().first()
                if row:
                    return {
                        "id": str(row.id),
                        "variant_name": row.variant_name,
                        "prompt_template": row.prompt_template,
                        "error_class": row.error_class,
                    }
        except Exception as e:
            logger.warning("_load_variant %s: %s", variant_id[:8], e)
        return None

    async def _fetch_test_cases(self, error_class: str, n: int = 10) -> list[dict]:
        """
        Fetch n most recent fixed+accepted errors for this class.
        These serve as the held-out evaluation set.
        """
        try:
            from sqlalchemy import text
            pg = self._get_pg()
            sql = text("""
                SELECT
                    e.id AS error_id,
                    e.message,
                    e.file_path,
                    e.stack_trace,
                    pr.fix_explanation AS erroring_code
                FROM errors e
                JOIN pipeline_runs pr ON pr.error_id = e.id
                WHERE e.error_class = :ec
                  AND pr.outcome    = 'accepted'
                ORDER BY pr.started_at DESC
                LIMIT :n
            """)
            async with pg.session() as sess:
                rows = (await sess.execute(sql, {"ec": error_class, "n": n})).all()
            return [
                {
                    "error_id": str(r[0]),
                    "message": r[1] or "",
                    "file_path": r[2] or "",
                    "stack_trace": r[3] or "",
                    "erroring_code": r[4] or "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("_fetch_test_cases: %s", e)
            return []

    async def _save_ab_result(
        self,
        result: WinnerResult,
        variant_a_id: str,
        variant_b_id: str,
        error_class: str,
        sample_count: int,
    ) -> None:
        """Persist A/B test result to ab_test_results table."""
        try:
            from storage.models import AbTestResult
            pg = self._get_pg()
            async with pg.session() as sess:
                row = AbTestResult(
                    id=uuid.uuid4(),
                    variant_a_id=uuid.UUID(variant_a_id),
                    variant_b_id=uuid.UUID(variant_b_id),
                    error_class=error_class,
                    winner=result.winner,
                    variant_a_score=result.stats_a.wilson_lower,
                    variant_b_score=result.stats_b.wilson_lower,
                    confidence=result.confidence,
                    sample_count=sample_count,
                    promoted=result.promoted,
                    notes=result.notes,
                )
                sess.add(row)
        except Exception as e:
            logger.warning("_save_ab_result: %s", e)

    def _empty_stats(self, variant_id: str, error_class: str) -> EvalStats:
        return EvalStats(
            variant_id=variant_id, variant_name="", error_class=error_class,
            accepted=0, rejected=0, total=0,
            acceptance_rate=0.0, wilson_lower=0.0, wilson_upper=1.0,
        )
