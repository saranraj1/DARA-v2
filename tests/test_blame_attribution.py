"""
Tests: Week 9-10 — Blame Attribution Engine
Covers:
  - BlameResult dataclass: summary string, properties
  - BlameAttributionEngine: no deploy event → confidence 0
  - BlameAttributionEngine: deploy found → base confidence += 0.35
  - BlameAttributionEngine: recent deploy (< 24h) → confidence += 0.25
  - BlameAttributionEngine: old deploy (> 24h) → moderate boost only
  - BlameAttributionEngine: no GitHub token → skips API enrichment
  - BlameResult.summary includes author and confidence
"""
import sys

sys.path.insert(0, ".")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestBlameResult:

    def _make_result(self, **kwargs):
        from context.blame_attribution import BlameResult
        defaults = dict(
            service_name="payment-svc",
            commit_sha="abc123def456",
            commit_sha_short="abc123de",
            author_email="dev@example.com",
            author_name="Dev User",
            commit_message="feat: add payment retry",
            committed_at=None,
            deployed_at=None,
            hours_before_error=2.5,
            file_path="payment/charge.py",
            blame_line=42,
            blame_confidence=0.75,
            confidence_reasons=["Deploy < 24h before error"],
            deploy_found=True,
            blame_method="deploy_correlation",
        )
        defaults.update(kwargs)
        return BlameResult(**defaults)

    def test_summary_includes_service_and_confidence(self):
        result = self._make_result()
        summary = result.summary
        assert "payment-svc" in summary
        assert "75%" in summary

    def test_summary_includes_author(self):
        result = self._make_result()
        assert "Dev User" in result.summary or "abc123de" in result.summary

    def test_no_commit_summary(self):
        result = self._make_result(
            commit_sha=None, commit_sha_short=None,
            author_name=None, author_email=None,
            hours_before_error=None,
            deploy_found=False,
        )
        assert "No blame" in result.summary or "payment-svc" in result.summary


class TestBlameAttributionEngine:

    def _engine(self, pg=None):
        from context.blame_attribution import BlameAttributionEngine
        return BlameAttributionEngine(github_token=None, postgres=pg)

    @pytest.mark.asyncio
    async def test_no_deploy_events_returns_zero_confidence(self):
        mock_pg = MagicMock()
        mock_pg.get_latest_deploy_before = AsyncMock(return_value=None)
        engine = self._engine(pg=mock_pg)
        result = await engine.attribute(
            service_name="payment-svc",
            error_timestamp=datetime.now(timezone.utc),
        )
        assert result.blame_confidence == 0.0
        assert not result.deploy_found

    @pytest.mark.asyncio
    async def test_deploy_found_adds_base_confidence(self):
        now = datetime.now(timezone.utc)
        mock_pg = MagicMock()
        mock_pg.get_latest_deploy_before = AsyncMock(return_value={
            "commit_sha": "abc123",
            "deployed_at": (now - timedelta(hours=50)).isoformat(),
            "deployed_by": "github-actions",
        })
        engine = self._engine(pg=mock_pg)
        result = await engine.attribute(
            service_name="svc",
            error_timestamp=now,
        )
        assert result.blame_confidence >= 0.35
        assert result.deploy_found
        assert result.commit_sha == "abc123"

    @pytest.mark.asyncio
    async def test_recent_deploy_boosts_confidence(self):
        now = datetime.now(timezone.utc)
        mock_pg = MagicMock()
        mock_pg.get_latest_deploy_before = AsyncMock(return_value={
            "commit_sha": "xyz999",
            "deployed_at": (now - timedelta(hours=2)).isoformat(),
            "deployed_by": "cd-pipeline",
        })
        engine = self._engine(pg=mock_pg)
        result = await engine.attribute(
            service_name="svc",
            error_timestamp=now,
        )
        # Base 0.35 + recent 0.25 = 0.60
        assert result.blame_confidence >= 0.60
        assert result.hours_before_error is not None
        assert result.hours_before_error < 24

    @pytest.mark.asyncio
    async def test_old_deploy_lower_confidence_than_recent(self):
        now = datetime.now(timezone.utc)
        mock_pg = MagicMock()

        # Old deploy (50h)
        mock_pg.get_latest_deploy_before = AsyncMock(return_value={
            "commit_sha": "old111",
            "deployed_at": (now - timedelta(hours=50)).isoformat(),
            "deployed_by": "cd-pipeline",
        })
        engine_old = self._engine(pg=mock_pg)
        result_old = await engine_old.attribute("svc", now)

        # Recent deploy (2h)
        mock_pg.get_latest_deploy_before = AsyncMock(return_value={
            "commit_sha": "new222",
            "deployed_at": (now - timedelta(hours=2)).isoformat(),
            "deployed_by": "cd-pipeline",
        })
        engine_recent = self._engine(pg=mock_pg)
        result_recent = await engine_recent.attribute("svc", now)

        assert result_recent.blame_confidence > result_old.blame_confidence

    @pytest.mark.asyncio
    async def test_no_github_token_skips_enrichment(self):
        now = datetime.now(timezone.utc)
        mock_pg = MagicMock()
        mock_pg.get_latest_deploy_before = AsyncMock(return_value={
            "commit_sha": "abc123",
            "deployed_at": now.isoformat(),
            "deployed_by": "ci",
        })
        engine = self._engine(pg=mock_pg)
        # No token — GitHub enrichment skipped
        result = await engine.attribute("svc", now, repo_full_name="org/repo")
        assert result.author_email is None   # not enriched from GitHub
        assert result.commit_sha == "abc123"

    @pytest.mark.asyncio
    async def test_blame_method_set_correctly(self):
        now = datetime.now(timezone.utc)
        mock_pg = MagicMock()
        mock_pg.get_latest_deploy_before = AsyncMock(return_value={
            "commit_sha": "sha01",
            "deployed_at": now.isoformat(),
            "deployed_by": "ci",
        })
        engine = self._engine(pg=mock_pg)
        result = await engine.attribute("svc", now)
        assert result.blame_method == "deploy_correlation"
