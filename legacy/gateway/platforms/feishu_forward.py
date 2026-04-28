"""
gateway/platforms/feishu_forward.py
===================================

Feishu event forwarder for the streaming card sidecar.

Provides get_emitter() which returns a SidecarEmitter that forwards
events to the sidecar HTTP server in a non-blocking way.

Usage in feishu.py:
    from gateway.platforms.feishu_forward import get_emitter
    self._event_emitter = get_emitter(self)

    # Then emit events:
    asyncio.create_task(self._event_emitter.emit('message_received', {...}))
"""

import asyncio
import logging
from typing import Optional, Any, Dict

logger = logging.getLogger("feishu.forward")


class SidecarEmitter:
    """
    Event emitter that forwards events to the sidecar HTTP server.

    Non-blocking: events are sent via fire-and-forget tasks.
    Failure to send does not affect the main gateway flow.
    """

    def __init__(self, adapter, sidecar_url: str = "http://localhost:8765"):
        self.adapter = adapter
        self.sidecar_url = sidecar_url
        self._session: Optional[Any] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    import aiohttp
                    timeout = aiohttp.ClientTimeout(total=3)
                    self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def emit(self, event_name: str, data: Dict[str, Any]) -> None:
        """
        Emit an event to the sidecar.

        Args:
            event_name: Name of the event (message_received, thinking, finish)
            data: Event payload dict
        """
        payload = {
            "event": event_name,
            "data": data
        }

        # Fire-and-forget: don't block the gateway
        asyncio.create_task(self._send_event(payload))

    async def _send_event(self, payload: Dict[str, Any]) -> None:
        """Send event to sidecar (runs in background task)."""
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.sidecar_url}/events",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status >= 400:
                    logger.debug(f"[SidecarEmitter] HTTP {resp.status}")
        except Exception as e:
            logger.debug(f"[SidecarEmitter] Send error: {e}")

    async def emit_sync(self, event_name: str, data: Dict[str, Any]) -> bool:
        """
        Emit an event and wait for acknowledgment.

        Use for critical events like 'finish' where we want to ensure
        the sidecar processes the event before returning.
        """
        payload = {
            "event": event_name,
            "data": data
        }

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.sidecar_url}/events",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("ok", False)
                return False
        except Exception as e:
            logger.debug(f"[SidecarEmitter] Sync send error: {e}")
            return False

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()


class LegacyEmitter:
    """
    Legacy emitter that logs events instead of sending to sidecar.

    Used when sidecar mode is not enabled or sidecar is unavailable.
    """

    def __init__(self, adapter):
        self.adapter = adapter

    async def emit(self, event_name: str, data: Dict[str, Any]) -> None:
        logger.debug(f"[LegacyEmitter] {event_name}: {data}")

    async def emit_sync(self, event_name: str, data: Dict[str, Any]) -> bool:
        logger.debug(f"[LegacyEmitter] {event_name}: {data}")
        return True


def get_emitter(adapter, mode: str = "sidecar", sidecar_url: str = "http://localhost:8765"):
    """
    Factory function to get an event emitter.

    Args:
        adapter: The feishu adapter instance
        mode: "sidecar" or "legacy"
        sidecar_url: URL of the sidecar server

    Returns:
        SidecarEmitter or LegacyEmitter instance
    """
    if mode == "sidecar":
        return SidecarEmitter(adapter, sidecar_url)
    else:
        return LegacyEmitter(adapter)
