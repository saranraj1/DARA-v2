"""DARA — SSE event stream router."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.events.broadcaster import get_broadcaster

router = APIRouter()


@router.get(
    "/events/stream",
    summary="Server-Sent Events live feed",
    response_class=StreamingResponse,
    tags=["events"],
)
async def event_stream(request: Request):
    """
    Long-lived SSE connection. Emits:
      - error.new      — when a new error is ingested
      - fix.new        — when a fix is generated
      - fix.approved   — when a fix is approved
      - fix.rejected   — when a fix is rejected
      - anomaly.new    — when an anomaly is detected

    Heartbeat (`: heartbeat`) emitted every 20 seconds.
    """
    broadcaster = get_broadcaster()

    async def _generate():
        async for chunk in broadcaster.subscribe():
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )
