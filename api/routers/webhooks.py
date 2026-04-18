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
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}
    return {"ok": True}


@router.post("/slack/interactions", summary="Slack interactive component handler")
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle Approve/Reject button clicks from Slack fix notifications.
    Approve  → apply patch (temp dir) → git commit → GitHub PR → record pattern
    Reject   → mark rejected → record failure pattern → Slack confirms
    Returns immediately (202) and runs pipeline in background.
    """
    # Verify Slack signing secret (replay attack prevention)
    if not await _verify_slack_signature(request):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    form = await request.form()
    payload_str = form.get("payload", "{}")
    payload = json.loads(str(payload_str))

    action = payload.get("actions", [{}])[0]
    action_id = action.get("action_id", "")
    fix_id = action.get("value", "")
    slack_user = payload.get("user", {}).get("username", "unknown")
    response_url = payload.get("response_url", "")

    if not fix_id:
        return {"ok": True}

    if action_id == "approve_fix":
        background_tasks.add_task(
            _run_approve_pipeline, fix_id, slack_user, response_url
        )
        return {"ok": True, "text": f"Processing approval for fix `{fix_id[:8]}`..."}

    elif action_id == "reject_fix":
        rejection_reason = action.get("value", "Rejected via Slack")
        background_tasks.add_task(
            _run_reject_pipeline, fix_id, slack_user, rejection_reason, response_url
        )
        return {"ok": True, "text": f"Fix `{fix_id[:8]}` marked as rejected."}

    return {"ok": True}


async def _run_approve_pipeline(
    fix_id: str, slack_user: str, response_url: str
) -> None:
    """
    Full approval pipeline:
    load fix → apply patch (temp dir) → git commit → github PR → record pattern → notify
    """
    from patch.applier import PatchApplier
    from patch.git_committer import GitCommitter
    from output.github_pr import GitHubPRCreator
    from output.slack_notifier import SlackNotifier
    from agents.memory import PatternMemory
    from config.llm_router import get_llm_router
    from storage.redis_client import get_redis
    from storage.audit import get_audit_logger
    from monitoring import metrics

    postgres = get_postgres()
    notifier = SlackNotifier()
    pr_creator = GitHubPRCreator()
    audit = get_audit_logger(postgres)

    try:
        # 1. Load fix from DB
        fix_row = await postgres.get_fix(fix_id)
        if not fix_row:
            logger.error("HITL approve: fix not found: %s", fix_id)
            return

        # Mark as being processed
        await postgres.update_fix_outcome(
            fix_id, "accepted",
            reviewer_notes=f"Approved by @{slack_user} via Slack"
        )

        # 2. Reconstruct PatchFile objects from DB patch data
        from api.models.agent_schemas import Fix, PatchFile
        patches_data = fix_row.get("patches", []) or []
        patch_objects = [
            PatchFile(
                file_path=p.get("file_path", ""),
                unified_diff=p.get("unified_diff", ""),
                lines_changed=p.get("lines_changed", 0),
                change_description=p.get("change_description", ""),
            )
            for p in patches_data if p.get("unified_diff")
        ]

        if not patch_objects:
            logger.warning("HITL approve: no patches in fix %s — cannot apply", fix_id)
            return

        # 3. Apply patch to temp directory
        applier = PatchApplier(repo_path=".")
        apply_result = await applier.apply(patch_objects)

        logger.info(
            "HITL approve: patch apply success=%s files=%s",
            apply_result.success, apply_result.files_modified,
        )

        pr_url = None
        branch = None

        if apply_result.success:
            # 4. Git commit from temp dir
            committer = GitCommitter(repo_path=".")
            commit_result = await committer.commit_and_push(
                fix_id=fix_id,
                error_class=fix_row.get("error_class", "error"),
                temp_dir=apply_result.temp_dir,
                files_modified=apply_result.files_modified,
                fix_explanation=fix_row.get("fix_explanation", ""),
            )

            # 5. Create GitHub PR
            if commit_result.success:
                branch = commit_result.branch
                repo_name = settings.github_repo_full_name if hasattr(settings, "github_repo_full_name") else ""
                if repo_name:
                    pr_result = await pr_creator.create_pr(
                        repo_full_name=repo_name,
                        fix_id=fix_id,
                        error_class=fix_row.get("error_class", "error"),
                        patches=[p.__dict__ if hasattr(p, '__dict__') else p for p in patch_objects],
                        fix_explanation=fix_row.get("fix_explanation", ""),
                        branch_name=branch,
                    )
                    if pr_result:
                        pr_url = pr_result.get("pr_url")
                        await postgres.update_fix_outcome(
                            fix_id, "accepted",
                            reviewer_notes=f"Approved @{slack_user} | PR: {pr_url}"
                        )

        # 6. Record in PatternMemory
        try:
            redis = get_redis()
            llm = get_llm_router(redis_client=redis.client)
            memory = PatternMemory(postgres=postgres, llm_router=llm)
            await memory.record_success(
                error_class=fix_row.get("error_class", ""),
                root_cause=fix_row.get("fix_explanation", ""),
                fix_summary=fix_row.get("fix_explanation", ""),
                fix_id=fix_id,
            )
        except Exception as mem_err:
            logger.warning("HITL approve: pattern memory failed: %s", mem_err)

        # Audit the approval
        await audit.record(
            action="fix_approved",
            actor=f"@{slack_user}",
            resource_type="fix",
            resource_id=fix_id,
            after_state={"pr_url": pr_url, "branch": branch, "patch_applied": apply_result.success},
        )
        metrics.hitl_decisions.labels(action="approve", channel="slack").inc()

        # 7. Send Slack confirmation
        msg = (
            f"Fix `{fix_id[:8]}` approved by @{slack_user}.\n"
            f"Patch applied to temp dir: `{apply_result.success}`\n"
        )
        if pr_url:
            msg += f"GitHub PR created: {pr_url}"
        elif branch:
            msg += f"Branch created: `{branch}` (PR creation skipped \u2014 no repo configured)"
        else:
            msg += "Git commit skipped (no remote configured in dev mode)"

        await notifier.send_simple(msg)
        logger.info("HITL approve: pipeline complete for fix %s | pr=%s", fix_id, pr_url)

    except Exception as e:
        logger.error("HITL approve pipeline failed for fix %s: %s", fix_id, e, exc_info=True)


async def _run_reject_pipeline(
    fix_id: str, slack_user: str, reason: str, response_url: str
) -> None:
    """Reject pipeline: update DB, record failure pattern, notify Slack."""
    from output.slack_notifier import SlackNotifier
    from agents.memory import PatternMemory
    from config.llm_router import get_llm_router
    from storage.redis_client import get_redis
    from storage.audit import get_audit_logger
    from monitoring import metrics

    postgres = get_postgres()
    notifier = SlackNotifier()

    try:
        await postgres.update_fix_outcome(
            fix_id, "rejected",
            reviewer_notes=f"Rejected by @{slack_user} via Slack: {reason}"
        )

        # Record failure pattern
        fix_row = await postgres.get_fix(fix_id)
        if fix_row:
            try:
                redis = get_redis()
                llm = get_llm_router(redis_client=redis.client)
                memory = PatternMemory(postgres=postgres, llm_router=llm)
                await memory.record_failure(
                    error_class=fix_row.get("error_class", ""),
                    root_cause=fix_row.get("fix_explanation", ""),
                    failure_reason=reason,
                    fix_id=fix_id,
                )
            except Exception as mem_err:
                logger.warning("HITL reject: pattern memory failed: %s", mem_err)

        await notifier.send_simple(
            f"Fix `{fix_id[:8]}` rejected by @{slack_user}. Reason: {reason}"
        )

        # Audit the rejection
        audit = get_audit_logger(postgres)
        await audit.record(
            action="fix_rejected",
            actor=f"@{slack_user}",
            resource_type="fix",
            resource_id=fix_id,
            after_state={"reason": reason},
        )
        metrics.hitl_decisions.labels(action="reject", channel="slack").inc()

        logger.info("HITL reject: fix %s rejected by %s", fix_id, slack_user)

    except Exception as e:
        logger.error("HITL reject failed for fix %s: %s", fix_id, e, exc_info=True)


async def _verify_slack_signature(request: Request) -> bool:
    """
    Verify Slack request signature using HMAC-SHA256.
    https://api.slack.com/authentication/verifying-requests-from-slack
    """
    sig_header = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")

    if not settings.slack_signing_secret:
        logger.warning("SLACK_SIGNING_SECRET not set — skipping verification")
        return True  # Allow in dev

    # Replay attack prevention: reject if timestamp > 5 minutes old
    import time
    try:
        if abs(time.time() - float(timestamp)) > 300:
            logger.warning("Slack request timestamp too old: %s", timestamp)
            return False
    except (ValueError, TypeError):
        return False

    body = await request.body()
    base_string = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(),
        base_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)
