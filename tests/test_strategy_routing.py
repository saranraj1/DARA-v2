"""
Tests: Week 11-12 — Strategy Routing
Covers:
  - StrategyRouter.get_strategy(): all 8 error classes route correctly
  - Unknown class falls back to LogicErrorStrategy
  - Each strategy.generate_fix_prompt() returns non-empty string
  - Each strategy.generate_fix_prompt() contains class-specific keywords
  - confidence_boost() returns 0.0 for non-matching code
  - NullReferenceStrategy.confidence_boost() positive when 'None' in code
  - NetworkTimeoutStrategy.confidence_boost() positive when 'timeout' in root cause
  - StrategyRouter.get_strategy_with_boost() returns (strategy, float)
"""
import sys

sys.path.insert(0, ".")

from unittest.mock import MagicMock


def _make_bundle(code="def foo(): pass", file="foo.py", function="foo"):
    b = MagicMock()
    b.erroring_code = code
    b.erroring_file = file
    b.erroring_function = function
    b.related_functions = []
    b.recent_commits = []
    b.similar_past_bugs = []
    b.blame_info = None
    return b


def _make_root_cause(root_cause="Bug found", confidence=0.5, strategy="llm_single_file"):
    rc = MagicMock()
    rc.root_cause = root_cause
    rc.immediate_cause = "Error"
    rc.confidence = confidence
    rc.suggested_strategy = strategy
    return rc


class TestStrategyRouter:

    def test_all_8_classes_route_to_unique_strategies(self):
        from agents.strategies.router import StrategyRouter
        classes = StrategyRouter.all_classes()
        assert len(classes) == 8
        strategies = {StrategyRouter.get_strategy(c).display_name for c in classes}
        assert len(strategies) == 8  # each maps to a distinct name

    def test_null_reference_routes_correctly(self):
        from agents.strategies.router import NullReferenceStrategy, StrategyRouter
        s = StrategyRouter.get_strategy("null_reference")
        assert isinstance(s, NullReferenceStrategy)

    def test_network_timeout_routes_correctly(self):
        from agents.strategies.router import NetworkTimeoutStrategy, StrategyRouter
        s = StrategyRouter.get_strategy("network_timeout")
        assert isinstance(s, NetworkTimeoutStrategy)

    def test_unknown_class_falls_back_to_logic_error(self):
        from agents.strategies.router import LogicErrorStrategy, StrategyRouter
        s = StrategyRouter.get_strategy("totally_unknown_error_type")
        assert isinstance(s, LogicErrorStrategy)

    def test_case_insensitive_routing(self):
        from agents.strategies.router import DatabaseErrorStrategy, StrategyRouter
        s = StrategyRouter.get_strategy("DATABASE_ERROR")
        assert isinstance(s, DatabaseErrorStrategy)


class TestStrategyPrompts:

    def test_all_strategies_generate_non_empty_prompt(self):
        from agents.strategies.router import StrategyRouter
        bundle = _make_bundle()
        rc = _make_root_cause()
        error = {"error_class": "test", "service": "svc", "message": "error", "line_number": 1}

        for cls in StrategyRouter.all_classes():
            strategy = StrategyRouter.get_strategy(cls)
            prompt = strategy.generate_fix_prompt(bundle, rc, error)
            assert isinstance(prompt, str)
            assert len(prompt) > 100, f"{cls} generated empty prompt"

    def test_null_reference_prompt_contains_null_keywords(self):
        from agents.strategies.router import NullReferenceStrategy
        s = NullReferenceStrategy()
        prompt = s.generate_fix_prompt(_make_bundle(), _make_root_cause(), {"error_class": "null_reference", "service": "x", "message": "", "line_number": 1})
        assert any(word in prompt.lower() for word in ["none", "null", "guard", "default"])

    def test_network_timeout_prompt_contains_timeout_keywords(self):
        from agents.strategies.router import NetworkTimeoutStrategy
        s = NetworkTimeoutStrategy()
        prompt = s.generate_fix_prompt(_make_bundle(), _make_root_cause(), {"error_class": "network_timeout", "service": "x", "message": "", "line_number": 1})
        assert "timeout" in prompt.lower() or "retry" in prompt.lower()


class TestConfidenceBoost:

    def test_null_reference_boost_with_none_in_code(self):
        from agents.strategies.router import NullReferenceStrategy
        s = NullReferenceStrategy()
        bundle = _make_bundle(code="if x is None: raise ValueError")
        rc = _make_root_cause()
        assert s.confidence_boost(rc, bundle) > 0.0

    def test_null_reference_no_boost_without_none(self):
        from agents.strategies.router import NullReferenceStrategy
        s = NullReferenceStrategy()
        bundle = _make_bundle(code="def add(a, b): return a + b")
        rc = _make_root_cause()
        assert s.confidence_boost(rc, bundle) == 0.0

    def test_network_timeout_boost_with_timeout_in_root_cause(self):
        from agents.strategies.router import NetworkTimeoutStrategy
        s = NetworkTimeoutStrategy()
        bundle = _make_bundle()
        rc = _make_root_cause(root_cause="connection timeout after 30s")
        assert s.confidence_boost(rc, bundle) > 0.0

    def test_get_strategy_with_boost_returns_tuple(self):
        from agents.strategies.router import StrategyRouter
        bundle = _make_bundle(code="x is None")
        rc = _make_root_cause()
        strategy, boost = StrategyRouter.get_strategy_with_boost("null_reference", rc, bundle)
        assert hasattr(strategy, "generate_fix_prompt")
        assert isinstance(boost, float)
        assert 0.0 <= boost <= 0.30
