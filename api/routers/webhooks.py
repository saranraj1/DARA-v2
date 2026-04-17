"""
DARA — GitHub & Slack Webhook Router
Handles HMAC-verified webhook payloads from GitHub and Slack.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from config.settings import get_settings
from ingestion.normalizer import ErrorNormalizer
from ingestion.classifier import ErrorClassifier
from ingestion.sources.github import GitHubSourceParser
from storage.postgres import get_postgres
from storage.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

_normalizer = ErrorNormalizer()
_classifier = ErrorClassifier()
_gh_parser = GitHubSourceParser()


# ─── GitHub Webhook ──────────────────────────────────────────

@router.post(
    "/webhooks/github",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive GitHub App webhook events",
)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
) -> dict:
    body = await request.body()

    # Verify HMAC signature
    if not _verify_github_signature(body, x_hub_signature_256):
        logger.warning(
            "GitHub webhook HMAC verification failed",
            extra={"event": x_github_event},
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    logger.info("GitHub webhook received", extra={"event": x_github_event})

    # Route by event type
    raw_error: dict | None = None
    if x_github_event == "workflow_run":
        raw_error = _gh_parser.parse_workflow_run(payload)
    elif x_github_event == "push":
        raw_error = _gh_parser.parse_push_event(payload)
    elif x_github_event == "ping":
        return {"status": "pong"}

    if raw_error is None:
        return {"status": "ignored", "reason": "Non-failure event or succeeded"}

    # Ingest the parsed error
    background_tasks.add_task(_process_github_error, raw_error)
    return {"status": "accepted"}


async def _process_github_error(raw_error: dict) -> None:
    """Background task: normalize, classify, persist, queue."""
    try:
        normalized = _normalizer.normalize(raw_error, source="github_actions")
        classified = await _classifier.classify(normalized)
        postgres = get_postgres()
        redis = get_redis()
        error_id = await postgres.save_error(classified)
        await postgres.create_pipeline_run(error_id)
        await redis.push_task(
            "dara:ingestion",
            {"error_id": error_id, "priority": classified.get("priority", "P1")},
        )
        logger.info("GitHub error ingested", extra={"error_id": error_id})
    except Exception as e:
        logger.error("Failed to process GitHub webhook", extra={"error": str(e)}, exc_info=True)


def _verify_github_signature(body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub HMAC-SHA256 webhook signature."""
    if not settings.github_webhook_secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping verification")
        return True  # Allow in dev mode without secret
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ─── Slack Events & Interactions ──────────────────────────────

@router.post("/slack/events", summary="Slack URL verification and event handler")
async def slack_events(request: Request) -> dict:
    body = await request.json()
    # URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}
    return {"ok": True}


@router.post("/slack/interactions", summary="Slack interactive component handler")
async def slack_interactions(request: Request) -> dict:
    """Handle Approve/Reject button clicks from Slack fix notifications."""
    form = await request.form()
    payload_str = form.get("payload", "{}")
    payload = json.loads(str(payload_str))

    action = payload.get("actions", [{}])[0]
    action_id = action.get("action_id", "")
    fix_id = action.get("value", "")

    if not fix_id:
        return {"ok": True}

    postgres = get_postgres()
    if action_id == "approve_fix":
        await postgres.update_fix_outcome(fix_id, "accepted", reviewer_notes="Approved via Slack")
        logger.info("Fix approved via Slack", extra={"fix_id": fix_id})
    elif action_id == "reject_fix":
        await postgres.update_fix_outcome(fix_id, "rejected", reviewer_notes="Rejected via Slack")
        logger.info("Fix rejected via Slack", extra={"fix_id": fix_id})

    return {"ok": True}
