"""
DARA — Server-Sent Events broadcaster.
New errors and pipeline events fan-out to all connected SSE subscribers
via a global in-process pubsub bus.

Architecture:
  EventBroadcaster (singleton) holds a set of asyncio.Queue instances,
  one per connected client. When an event is published, it is placed into
  every active queue. Disconnected clients are pruned automatically.

Usage:
  # Publish (from any router after DB write):
  await get_broadcaster().publish({"type": "error.new", "data": {...}})

  # Subscribe (SSE endpoint):
  async for chunk in get_broadcaster().subscribe():
      yield chunk
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Thread-safe, async fan-out broadcaster."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def publish(self, payload: dict) -> None:
        """Push an event to all connected clients."""
        payload["_ts"] = datetime.now(timezone.utc).isoformat()
        msg = f"data: {json.dumps(payload)}\n\n"
        async with self._lock:
            dead: set[asyncio.Queue] = set()
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    dead.add(q)
            self._subscribers -= dead

    async def subscribe(self):
        """Async generator yielding SSE-formatted event strings."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=128)
        async with self._lock:
            self._subscribers.add(q)
        logger.info("SSE client connected (total=%d)", len(self._subscribers))
        try:
            # Send immediate hello heartbeat
            yield f"data: {json.dumps({'type': 'connected', 'subscribers': len(self._subscribers)})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield msg
                except asyncio.TimeoutError:
                    # Heartbeat — keeps the connection alive through proxies/firewalls
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            async with self._lock:
                self._subscribers.discard(q)
            logger.info("SSE client disconnected (total=%d)", len(self._subscribers))


# Singleton
_broadcaster: EventBroadcaster | None = None


def get_broadcaster() -> EventBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = EventBroadcaster()
    return _broadcaster
