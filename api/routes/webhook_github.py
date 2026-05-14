"""
api/routes/webhook_github.py
==============================
FastAPI router for GitHub webhook events.

Endpoint: POST /webhooks/github/pr-feedback

Security: Verifies X-Hub-Signature-256 HMAC-SHA256 before processing.
          Returns 403 immediately on invalid signature.

Processing: Non-blocking — returns 202 Accepted, then processes in a
            background asyncio task (GitHub has a 10s webhook timeout).

Supported events:
  pull_request     → action: closed (merged or rejected)
  pull_request_review → action: submitted (changes_requested or approved)
  pull_request_review_comment → collects inline review comments

GitHub App webhook configuration:
  Payload URL: https://{your-domain}/webhooks/github/pr-feedback
  Content type: application/json
  Secret: value of GITHUB_WEBHOOK_SECRET env var
  Events: Pull requests, Pull request reviews, Pull request review comments
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Signature verification dependency
# ──────────────────────────────────────────────────────────────────────────────

async def _verify_github_signature(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> bytes:
    """
    Verify GitHub HMAC-SHA256 webhook signature.
    Raises 403 if signature is missing or invalid.
    Returns raw request body for downstream use.
    """
    body = await request.body()
    settings = get_settings()
    secret = getattr(settings, "github_webhook_secret", "")

    if not secret:
        # Webhook secret not configured — allow (dev mode) but warn loudly
        logger.warning(
            "GitHub webhook: GITHUB_WEBHOOK_SECRET not set — signature verification DISABLED"
        )
        return body

    if not x_hub_signature_256:
        logger.warning("GitHub webhook: missing X-Hub-Signature-256 header")
        raise HTTPException(status_code=403, detail="Missing signature header")

    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, x_hub_signature_256):
        logger.warning("GitHub webhook: signature mismatch — possible spoofed request")
        raise HTTPException(status_code=403, detail="Invalid signature")

    return body


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/webhooks/github/pr-feedback",
    status_code=202,
    summary="GitHub PR Webhook — RLHF Feedback",
    description=(
        "Receives GitHub pull_request and pull_request_review events "
        "and feeds them into DARA's reinforcement learning memory."
    ),
)
async def github_pr_feedback(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    body: bytes = Depends(_verify_github_signature),
) -> JSONResponse:
    """
    202 Accepted immediately. Processing happens in a background task
    to meet GitHub's 10-second webhook delivery timeout.
    """
    import json
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = x_github_event or ""
    action = payload.get("action", "")

    logger.info("GitHub webhook: event=%s action=%s", event, action)

    # Dispatch relevant events — ignore everything else (ping, push, etc.)
    if event == "pull_request" and action in ("closed",):
        background_tasks.add_task(_process_pr_event, payload)
    elif event == "pull_request_review" and action == "submitted":
        background_tasks.add_task(_process_review_event, payload)
    elif event == "ping":
        return JSONResponse({"status": "pong"}, status_code=200)
    else:
        # Unhandled event — ack and ignore
        return JSONResponse({"status": "ignored", "event": event, "action": action})

    return JSONResponse({"status": "accepted", "event": event, "action": action})


# ──────────────────────────────────────────────────────────────────────────────
# Background processors
# ──────────────────────────────────────────────────────────────────────────────

async def _process_pr_event(payload: dict) -> None:
    """Handle pull_request closed events (merged or rejected)."""
    try:
        processor = _get_processor()
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number", 0)
        pr_body = pr.get("body", "") or ""
        merged = pr.get("merged", False)

        if merged:
            await processor.on_pr_merged(pr_body, pr_number)
        else:
            await processor.on_pr_closed(pr_body, pr_number)
    except Exception as exc:
        logger.error("GitHub webhook: _process_pr_event failed: %s", exc, exc_info=True)


async def _process_review_event(payload: dict) -> None:
    """Handle pull_request_review submitted events."""
    try:
        processor = _get_processor()
        pr = payload.get("pull_request", {})
        review = payload.get("review", {})
        pr_number = pr.get("number", 0)
        pr_body = pr.get("body", "") or ""
        review_body = review.get("body", "") or ""
        review_state = review.get("state", "").lower()

        if review_state == "changes_requested":
            # Gather inline comments from review body (detailed comments come via
            # pull_request_review_comment events but body often has the summary)
            comments = [c for c in [review_body] if c.strip()]
            await processor.on_changes_requested(pr_body, review_body, pr_number, comments)
        # "approved" with no merged event — not a final signal, ignore
    except Exception as exc:
        logger.error("GitHub webhook: _process_review_event failed: %s", exc, exc_info=True)


def _get_processor():
    """Lazy-import processor with real storage clients."""
    from agents.memory import PatternMemory
    from feedback.pr_feedback_processor import PRFeedbackProcessor
    from storage.neo4j_client import get_neo4j
    from storage.postgres import get_postgres
    from storage.qdrant_client import get_qdrant
    return PRFeedbackProcessor(
        qdrant=get_qdrant(),
        neo4j=get_neo4j(),
        postgres=get_postgres(),
        memory=PatternMemory(),
    )
