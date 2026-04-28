"""
SidecarAdapter - 通过 HTTP 与独立 sidecar 进程通信
================================================================

Sidecar 模式：所有卡片操作由独立 sidecar 进程处理。
Gateway 仅需转发事件，不直接调用 CardKit API。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, List, Dict, Any
from dataclasses import asdict

import aiohttp

from .streaming_adapter import (
    StreamingAdapter,
    CardContext,
    ToolCall,
    TokenUsage,
)

logger = logging.getLogger("feishu.streaming.sidecar")


class SidecarAdapter(StreamingAdapter):
    """Sidecar 模式适配器"""
    
    def __init__(self, config: Dict[str, Any], hermes_dir: str):
        super().__init__(config, hermes_dir)
        fsc_cfg = config.get('feishu_streaming_card', {})
        sidecar_cfg = fsc_cfg.get('sidecar', {})
        self.base_url = sidecar_cfg.get('base_url') or                        f"http://{sidecar_cfg.get('host', 'localhost')}:"                        f"{sidecar_cfg.get('port', 8765)}"
        self._session: Optional[aiohttp.ClientSession] = None
        self._connect_lock = asyncio.Lock()
        self._health_cache = {'last_check': 0, 'healthy': False}
        self._health_ttl = 10
    
    async def initialize(self) -> None:
        await self._ensure_session()
        await self._check_health()
    
    async def shutdown(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            async with self._connect_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=5)
                    self._session = aiohttp.ClientSession(timeout=timeout)
    
    async def _check_health(self) -> bool:
        now = asyncio.get_event_loop().time()
        if now - self._health_cache['last_check'] < self._health_ttl:
            return self._health_cache['healthy']
        try:
            await self._ensure_session()
            async with self._session.get(f"{self.base_url}/health",
                                        timeout=aiohttp.ClientTimeout(total=2)) as resp:
                if resp.status == 200:
                    self._health_cache = {'last_check': now, 'healthy': True}
                    return True
        except Exception as e:
            logger.debug(f"[Sidecar] Health check failed: {e}")
        self._health_cache = {'last_check': now, 'healthy': False}
        return False
    
    async def _publish_event(self, event: Dict[str, Any], 
                             endpoint: str = "/events") -> bool:
        try:
            await self._ensure_session()
            async with self._session.post(
                f"{self.base_url}{endpoint}",
                json=event,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status >= 400:
                    logger.debug(f"[Sidecar] Event failed: HTTP {resp.status}")
                    return False
                return True
        except aiohttp.ClientError as e:
            logger.debug(f"[Sidecar] Connection error: {e}")
            return False
        except asyncio.TimeoutError:
            logger.debug(f"[Sidecar] Event timeout")
            return False
        except Exception as e:
            logger.debug(f"[Sidecar] Unexpected error: {e}")
            return False
    
    async def on_message_received(self, ctx: CardContext) -> str:
        if not await self._check_health():
            raise RuntimeError("Sidecar unavailable")
        event = {
            "schema_version": "1.0",
            "event": "message_received",
            "data": {
                "chat_id": ctx.chat_id,
                "message_id": ctx.message_id,
                "user_id": ctx.user_id,
                "greeting": ctx.greeting or self.get_greeting(),
                "model": ctx.model_name or self.get_model_name(),
                "text": ctx.text[:500] if ctx.text else "",
                "timestamp": asyncio.get_event_loop().time(),
            }
        }
        success = await self._publish_event(event, "/events")
        if not success:
            raise RuntimeError("Failed to send message_received event")
        temp_card_id = f"sidecar_{ctx.chat_id}"
        self.set_card_id(ctx.chat_id, temp_card_id)
        return temp_card_id
    
    async def on_thinking(self, chat_id: str, delta: str, 
                          tools: Optional[List[ToolCall]] = None) -> None:
        card_id = self.get_card_id(chat_id)
        if not card_id:
            return
        asyncio.create_task(self._send_thinking(card_id, chat_id, delta, tools))
    
    async def _send_thinking(self, card_id: str, chat_id: str, 
                            delta: str, tools: Optional[List[ToolCall]]) -> None:
        try:
            event = {
                "schema_version": "1.0",
                "event": "thinking",
                "data": {
                    "card_id": card_id,
                    "chat_id": chat_id,
                    "delta": delta,
                    "tools": [asdict(t) if isinstance(t, ToolCall) else t 
                              for t in (tools or [])],
                    "timestamp": asyncio.get_event_loop().time(),
                }
            }
            await self._publish_event(event)
        except Exception as e:
            logger.debug(f"[Sidecar] Thinking event failed: {e}")
    
    async def on_tool_call(self, chat_id: str, tool: ToolCall) -> None:
        card_id = self.get_card_id(chat_id)
        if not card_id:
            return
        asyncio.create_task(self._send_tool_call(card_id, chat_id, tool))
    
    async def _send_tool_call(self, card_id: str, chat_id: str, 
                             tool: ToolCall) -> None:
        try:
            event = {
                "schema_version": "1.0",
                "event": "tool_call",
                "data": {
                    "card_id": card_id,
                    "chat_id": chat_id,
                    "tool_name": tool.name,
                    "status": tool.status,
                    "result": tool.result,
                    "error": tool.error,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            }
            await self._publish_event(event)
        except Exception as e:
            logger.debug(f"[Sidecar] Tool call event failed: {e}")
    
    async def on_finish(self, chat_id: str, content: str, 
                        tokens: TokenUsage, duration: float) -> None:
        card_id = self.get_card_id(chat_id)
        if not card_id:
            logger.warning(f"[Sidecar] on_finish: no card for {chat_id}")
            await self.cleanup_chat(chat_id)
            return
        try:
            event = {
                "schema_version": "1.0",
                "event": "finish",
                "data": {
                    "card_id": card_id,
                    "chat_id": chat_id,
                    "content": content,
                    "tokens": asdict(tokens),
                    "duration": duration,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            }
            success = await self._publish_event(event)
            if not success:
                logger.warning(f"[Sidecar] Finish event failed for {card_id}")
        except Exception as e:
            logger.error(f"[Sidecar] Finish event error: {e}")
        finally:
            await self.cleanup_chat(chat_id)
    
    async def on_error(self, chat_id: str, error: str) -> None:
        card_id = self.get_card_id(chat_id)
        if not card_id:
            return
        try:
            event = {
                "schema_version": "1.0",
                "event": "error",
                "data": {
                    "card_id": card_id,
                    "chat_id": chat_id,
                    "error": error,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            }
            await self._publish_event(event)
        except Exception as e:
            logger.debug(f"[Sidecar] Error event failed: {e}")
