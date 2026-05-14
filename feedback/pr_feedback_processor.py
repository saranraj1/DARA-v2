"""
feedback/pr_feedback_processor.py
===================================
RLHF: Translates GitHub Pull Request lifecycle events into memory signals.

Event → Action mapping
───────────────────────
PR merged          → Qdrant: outcome="highly_reliable", reliability_score=1.0
                   → Neo4j:  increment_pattern_acceptance (FixTemplate.success_rate++)
                   → PatternMemory.record_success

PR changes_requested → Qdrant: outcome="needs_revision", human_feedback=<comments>
                     → Neo4j:  (no counter change — wait for final outcome)
                     → PatternMemory.record_failure with reviewer comments

PR closed (not merged) → Qdrant: outcome="negative_example", reliability_score=-1.0
                       → Neo4j:  increment_pattern_rejection (needs_refresh may flip)
                       → PatternMemory.record_failure

Robustness notes:
  - All methods are fire-and-forget safe (swallow exceptions, log, never re-raise)
  - Fix ID is extracted via regex from PR body: **Fix ID:** `{fix_id}`
  - If fix_id not found the event is silently skipped (not a DARA-managed PR)
  - error_id / error_class are loaded from Postgres via fix_id
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Regex to extract fix_id embedded by GitHubPRCreator in PR body
_FIX_ID_RE = re.compile(r"\*\*Fix ID:\*\*\s*`([^`]+)`", re.IGNORECASE)
# Regex to extract signature_hash stored in PR body (optional, best-effort)
_SIG_HASH_RE = re.compile(r"\*\*Signature:\*\*\s*`([^`]+)`", re.IGNORECASE)


@dataclass
class FeedbackResult:
    fix_id: str
    event_type: str          # "merged" | "changes_requested" | "closed"
    qdrant_updated: bool = False
    neo4j_updated: bool = False
    memory_updated: bool = False
    error: str = ""


class PRFeedbackProcessor:
    """
    Maps GitHub PR webhook payloads to Qdrant + Neo4j + PatternMemory signals.

    Usage (from webhook handler):
        processor = PRFeedbackProcessor(qdrant, neo4j, postgres, memory)
        await processor.on_pr_merged(pr_body, pr_number)
        await processor.on_changes_requested(pr_body, review_body, pr_number)
        await processor.on_pr_closed(pr_body, pr_number)
    """

    def __init__(self, qdrant=None, neo4j=None, postgres=None, memory=None) -> None:
        self._qdrant = qdrant
        self._neo4j = neo4j
        self._postgres = postgres
        self._memory = memory

    # ──────────────────────────────────────────────────────────────────────────
    # PR Merged → "Highly Reliable"
    # ──────────────────────────────────────────────────────────────────────────

    async def on_pr_merged(self, pr_body: str, pr_number: int) -> FeedbackResult:
        """
        A human merged the PR — the fix is validated by production humans.
        Mark solution in Qdrant as highly_reliable and increment Neo4j acceptance.
        """
        fix_id = _extract_fix_id(pr_body)
        result = FeedbackResult(fix_id=fix_id or "", event_type="merged")

        if not fix_id:
            logger.debug("PRFeedback: merged PR #%d — not a DARA PR, skipping", pr_number)
            return result

        logger.info("PRFeedback: MERGED PR #%d fix_id=%s → highly_reliable", pr_number, fix_id)

        # 1. Load error_id from Postgres
        error_id, error_class, sig_hash = await self._load_fix_context(fix_id)

        # 2. Qdrant: mark highly_reliable
        if error_id and self._qdrant:
            try:
                await self._qdrant.set_payload_fields(error_id, {
                    "outcome": "highly_reliable",
                    "reliability_score": 1.0,
                    "human_merged": True,
                    "fix_id": fix_id,
                })
                result.qdrant_updated = True
                logger.info("PRFeedback: Qdrant outcome=highly_reliable error_id=%s", error_id)
            except Exception as exc:
                logger.warning("PRFeedback: Qdrant update failed: %s", exc)

        # 3. Neo4j: increment acceptance on the error pattern
        if sig_hash and self._neo4j:
            try:
                await self._neo4j.increment_pattern_acceptance(sig_hash)
                result.neo4j_updated = True
                logger.info("PRFeedback: Neo4j acceptance++ sig=%s", sig_hash[:16])
            except Exception as exc:
                logger.warning("PRFeedback: Neo4j acceptance failed: %s", exc)

        # 4. PatternMemory: reinforce success signal
        if error_class and self._memory:
            try:
                await self._memory.record_success(
                    error_class=error_class,
                    fix_id=fix_id,
                    fix_summary="Human-merged PR — highly reliable",
                )
                result.memory_updated = True
            except Exception as exc:
                logger.warning("PRFeedback: PatternMemory.record_success failed: %s", exc)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # PR Changes Requested → "Needs Revision"
    # ──────────────────────────────────────────────────────────────────────────

    async def on_changes_requested(
        self,
        pr_body: str,
        review_body: str,
        pr_number: int,
        reviewer_comments: list[str] | None = None,
    ) -> FeedbackResult:
        """
        A human requested changes — feed the review comments back into memory.
        Does NOT count as a hard rejection (outcome: needs_revision, not negative_example).
        """
        fix_id = _extract_fix_id(pr_body)
        result = FeedbackResult(fix_id=fix_id or "", event_type="changes_requested")

        if not fix_id:
            return result

        # Consolidate all human feedback into one string
        all_comments = [c for c in [review_body] + (reviewer_comments or []) if c and c.strip()]
        combined_feedback = "\n\n".join(all_comments)[:2000]

        logger.info(
            "PRFeedback: CHANGES_REQUESTED PR #%d fix_id=%s — feeding %d comment(s) back",
            pr_number, fix_id, len(all_comments),
        )

        error_id, error_class, sig_hash = await self._load_fix_context(fix_id)

        # 1. Qdrant: store human feedback payload (outcome stays "accepted" until closed)
        if error_id and self._qdrant:
            try:
                await self._qdrant.set_payload_fields(error_id, {
                    "outcome": "needs_revision",
                    "human_feedback": combined_feedback,
                    "fix_id": fix_id,
                })
                result.qdrant_updated = True
                logger.info("PRFeedback: Qdrant human_feedback stored error_id=%s", error_id)
            except Exception as exc:
                logger.warning("PRFeedback: Qdrant feedback store failed: %s", exc)

        # 2. PatternMemory: record failure with reviewer text so future FixerAgent
        #    prompts can include "why previous fixes failed"
        if error_class and self._memory:
            try:
                await self._memory.record_failure(
                    error_class=error_class,
                    root_cause="Human reviewer requested changes",
                    failure_reason=combined_feedback[:500] or "No review comment provided",
                    fix_id=fix_id,
                )
                result.memory_updated = True
            except Exception as exc:
                logger.warning("PRFeedback: PatternMemory.record_failure failed: %s", exc)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # PR Closed (not merged) → "Negative Example"
    # ──────────────────────────────────────────────────────────────────────────

    async def on_pr_closed(self, pr_body: str, pr_number: int) -> FeedbackResult:
        """
        A human closed/rejected the PR without merging.
        Mark as negative_example — suppresses this pattern in future retrieval.
        """
        fix_id = _extract_fix_id(pr_body)
        result = FeedbackResult(fix_id=fix_id or "", event_type="closed")

        if not fix_id:
            return result

        logger.warning(
            "PRFeedback: PR #%d CLOSED/REJECTED fix_id=%s → negative_example",
            pr_number, fix_id,
        )

        error_id, error_class, sig_hash = await self._load_fix_context(fix_id)

        # 1. Qdrant: mark as negative_example with reliability_score = -1.0
        #    search_similar_errors() filters outcome="accepted"; negative_example is excluded
        if error_id and self._qdrant:
            try:
                await self._qdrant.set_payload_fields(error_id, {
                    "outcome": "negative_example",
                    "reliability_score": -1.0,
                    "human_rejected": True,
                    "fix_id": fix_id,
                })
                result.qdrant_updated = True
                logger.info("PRFeedback: Qdrant outcome=negative_example error_id=%s", error_id)
            except Exception as exc:
                logger.warning("PRFeedback: Qdrant rejection mark failed: %s", exc)

        # 2. Neo4j: increment rejection counter — may flip needs_refresh=True
        if sig_hash and self._neo4j:
            try:
                await self._neo4j.increment_pattern_rejection(sig_hash, threshold=0.4)
                result.neo4j_updated = True
                logger.info(
                    "PRFeedback: Neo4j rejection++ sig=%s (needs_refresh may flip)", sig_hash[:16]
                )
            except Exception as exc:
                logger.warning("PRFeedback: Neo4j rejection failed: %s", exc)

        # 3. PatternMemory: record failure to lower success_rate
        if error_class and self._memory:
            try:
                await self._memory.record_failure(
                    error_class=error_class,
                    root_cause="Fix rejected by human reviewer (PR closed)",
                    failure_reason="PR closed without merge",
                    fix_id=fix_id,
                )
                result.memory_updated = True
            except Exception as exc:
                logger.warning("PRFeedback: PatternMemory rejection failed: %s", exc)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _load_fix_context(self, fix_id: str) -> tuple[str | None, str | None, str | None]:
        """
        Load (error_id, error_class, signature_hash) from Postgres for a given fix_id.
        Returns (None, None, None) if Postgres is unavailable or fix not found.
        """
        if not self._postgres:
            return None, None, None
        try:
            fix_row = await self._postgres.get_fix_by_id(fix_id)
            if not fix_row:
                logger.debug("PRFeedback: fix_id=%s not found in DB", fix_id)
                return None, None, None
            error_id = str(getattr(fix_row, "error_id", "") or "")
            error_class = ""
            sig_hash = ""
            if error_id:
                err_row = await self._postgres.get_error(error_id)
                if err_row:
                    error_class = getattr(err_row, "error_class", "") or ""
                    # Derive signature_hash from error_class (same as PatternMemory)
                    import hashlib
                    sig_hash = hashlib.sha256(error_class.encode()).hexdigest()[:16]
            return error_id or None, error_class or None, sig_hash or None
        except Exception as exc:
            logger.warning("PRFeedback: _load_fix_context failed: %s", exc)
            return None, None, None


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _extract_fix_id(pr_body: str) -> str | None:
    """Extract fix_id from DARA PR body. Returns None for non-DARA PRs."""
    if not pr_body:
        return None
    m = _FIX_ID_RE.search(pr_body)
    return m.group(1).strip() if m else None
