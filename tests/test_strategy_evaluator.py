"""
Tests: Week 17-18 — Strategy Evaluator (A/B Testing)
Covers:
  - Wilson interval for 10/10 successes → lower bound > 0.7
  - Wilson interval for 0/10 successes → lower bound ≈ 0.0
  - Wilson interval for 5/10 successes → symmetric about 0.5
  - _compute_winner: variant_a wins when A lower > B upper
  - _compute_winner: inconclusive when intervals overlap
  - _compute_winner: draw when rates within 5%
  - EvalStats.is_reliable is False for < 5 total
"""
import sys

sys.path.insert(0, ".")


class TestWilsonInterval:

    def _evaluator(self):
        from agents.strategy_evaluator import StrategyEvaluator
        return StrategyEvaluator()

    def test_all_successes_give_high_lower_bound(self):
        ev = self._evaluator()
        lower, upper = ev._wilson_interval(10, 10)
        assert lower > 0.70
        assert upper <= 1.01

    def test_no_successes_give_near_zero_lower_bound(self):
        ev = self._evaluator()
        lower, upper = ev._wilson_interval(0, 10)
        assert lower < 0.05

    def test_half_successes_symmetric(self):
        ev = self._evaluator()
        lower, upper = ev._wilson_interval(5, 10)
        assert lower < 0.5 < upper
        assert abs((upper + lower) / 2 - 0.5) < 0.10

    def test_empty_sample_returns_full_interval(self):
        ev = self._evaluator()
        lower, upper = ev._wilson_interval(0, 0)
        assert lower == 0.0
        assert upper == 1.0


class TestWinnerComputation:

    def _make_stats(self, vid, accepted, total):
        from agents.strategy_evaluator import EvalStats, StrategyEvaluator
        ev = StrategyEvaluator()
        lo, hi = ev._wilson_interval(accepted, total)
        rate = accepted / total if total > 0 else 0.0
        return EvalStats(
            variant_id=vid, variant_name=vid, error_class="test",
            accepted=accepted, rejected=total-accepted,
            total=total, acceptance_rate=rate,
            wilson_lower=lo, wilson_upper=hi,
        )

    def test_clear_winner_variant_a(self):
        from agents.strategy_evaluator import StrategyEvaluator
        ev = StrategyEvaluator()
        # A: 10/10 → high lower bound; B: 1/10 → low upper bound
        stats_a = self._make_stats("a", 10, 10)
        stats_b = self._make_stats("b", 1, 10)
        result = ev._compute_winner(stats_a, stats_b)
        assert result.winner == "variant_a"
        assert result.confidence > 0.80

    def test_clear_winner_variant_b(self):
        from agents.strategy_evaluator import StrategyEvaluator
        ev = StrategyEvaluator()
        stats_a = self._make_stats("a", 1, 10)
        stats_b = self._make_stats("b", 10, 10)
        result = ev._compute_winner(stats_a, stats_b)
        assert result.winner == "variant_b"

    def test_draw_when_very_close(self):
        from agents.strategy_evaluator import StrategyEvaluator
        ev = StrategyEvaluator()
        stats_a = self._make_stats("a", 5, 10)
        stats_b = self._make_stats("b", 5, 10)
        result = ev._compute_winner(stats_a, stats_b)
        assert result.winner in ("draw", "inconclusive")

    def test_inconclusive_below_min_sample(self):
        from agents.strategy_evaluator import StrategyEvaluator
        ev = StrategyEvaluator()
        stats_a = self._make_stats("a", 2, 2)
        stats_b = self._make_stats("b", 0, 2)
        result = ev._compute_winner(stats_a, stats_b)
        # 2 samples each is < 3 minimum
        assert result.winner == "inconclusive"


class TestEvalStats:

    def test_is_reliable_false_below_5(self):
        from agents.strategy_evaluator import EvalStats
        stats = EvalStats(
            variant_id="v1", variant_name="v1", error_class="test",
            accepted=2, rejected=2, total=4,
            acceptance_rate=0.5, wilson_lower=0.1, wilson_upper=0.9,
        )
        assert not stats.is_reliable

    def test_is_reliable_true_at_5(self):
        from agents.strategy_evaluator import EvalStats
        stats = EvalStats(
            variant_id="v1", variant_name="v1", error_class="test",
            accepted=3, rejected=2, total=5,
            acceptance_rate=0.6, wilson_lower=0.2, wilson_upper=0.9,
        )
        assert stats.is_reliable
