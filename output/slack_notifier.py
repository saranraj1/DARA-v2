from __future__ import annotations
import logging
from config.settings import get_settings

logger = logging.getLogger(__name__)


class SlackNotifier:
    """
    Sends Block Kit messages to Slack with Approve/Reject buttons.
    Falls back gracefully if Slack token not configured.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = None
        self._channel = self._settings.slack_alert_channel
        if self._settings.slack_bot_token:
            try:
                from slack_sdk.web.async_client import AsyncWebClient
                self._client = AsyncWebClient(token=self._settings.slack_bot_token)
            except ImportError:
                logger.warning("slack_sdk not installed")

    async def notify_fix_ready(
        self,
        error_id: str,
        fix_id: str,
        error_class: str,
        message: str,
        service: str | None,
        confidence: float,
        recommendation: str,
        quality_score: float,
        files_changed: list[str],
    ) -> bool:
        if not self._client or not self._channel:
            logger.warning("Slack not configured, skipping notification")
            return False

        emoji = {"approve": ":white_check_mark:", "approve_with_comments": ":warning:",
                 "reject": ":x:"}.get(recommendation, ":question:")
        color = {"approve": "#36a64f", "approve_with_comments": "#daa038", "reject": "#cc3232"}.get(recommendation, "#cccccc")
        files_text = "\n".join(f"- `{f}`" for f in files_changed[:5]) or "No files"

        blocks = [
            {"type": "header", "text": {"type": "plain_text",
             "text": f"{emoji} DARA Fix Ready: {error_class}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n{service or 'unknown'}"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
                {"type": "mrkdwn", "text": f"*Quality Score:*\n{quality_score:.0%}"},
                {"type": "mrkdwn", "text": f"*Reviewer:*\n{recommendation.replace('_',' ').title()}"},
            ]},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*Error:* `{message[:150]}`"}},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*Files Changed:*\n{files_text}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Approve Fix"},
                 "style": "primary", "action_id": "approve_fix", "value": fix_id},
                {"type": "button", "text": {"type": "plain_text", "text": "Reject Fix"},
                 "style": "danger", "action_id": "reject_fix", "value": fix_id},
                {"type": "button", "text": {"type": "plain_text", "text": "View Details"},
                 "url": f"http://localhost:8000/api/v1/fixes/{fix_id}", "action_id": "view_fix"},
            ]},
            {"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"error_id: `{error_id[:12]}` | fix_id: `{fix_id[:12]}`"}
            ]},
        ]

        try:
            resp = await self._client.chat_postMessage(
                channel=self._channel,
                blocks=blocks,
                text=f"DARA Fix Ready: {error_class} in {service}",
            )
            logger.info("Slack notification sent: ts=%s", resp.get("ts",""))
            return True
        except Exception as e:
            logger.error("Slack notification failed: %s", e)
            return False

    async def notify_escalation(self, error_id: str, error_class: str,
                                 service: str | None, reason: str) -> bool:
        if not self._client or not self._channel:
            return False
        blocks = [
            {"type": "header", "text": {"type": "plain_text",
             "text": f":sos: DARA Escalation: {error_class}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n{service or 'unknown'}"},
                {"type": "mrkdwn", "text": f"*Error ID:*\n`{error_id[:12]}`"},
            ]},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*Reason:* {reason[:300]}"}},
        ]
        try:
            await self._client.chat_postMessage(channel=self._channel, blocks=blocks,
                                                text=f"DARA Escalation: {error_class}")
            return True
        except Exception as e:
            logger.error("Slack escalation failed: %s", e)
            return False

    async def send_simple(self, text: str, channel: str | None = None) -> bool:
        """Send a plain-text message to Slack. Used for HITL confirmations."""
        if not self._client:
            import logging as _l; _l.getLogger(__name__).info("Slack send_simple (no client): %s", text[:100])
            return False
        ch = channel or self._channel
        if not ch:
            return False
        try:
            await self._client.chat_postMessage(channel=ch, text=text)
            return True
        except Exception as e:
            import logging as _l; _l.getLogger(__name__).error("Slack send_simple failed: %s", e)
            return False
