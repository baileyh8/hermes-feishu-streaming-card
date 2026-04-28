"""
LegacyAdapter - 封装原有 feishu_patch.py 的流式卡片逻辑
================================================================

Legacy 模式：通过代码注入到 gateway 中运行。
此适配器作为一层薄包装器，调用 FeishuAdapter 中已注入的方法。

注意：此模块仅在 legacy/dual 模式下使用。
sidecar 模式下不使用此代码。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, List, Dict, Any

from .streaming_adapter import (
    StreamingAdapter,
    CardContext,
    ToolCall,
    TokenUsage,
)

logger = logging.getLogger("feishu.streaming.legacy")


class LegacyAdapter(StreamingAdapter):
    """
    传统注入模式适配器。
    
    此适配器假设 FeishuAdapter 已经被注入了以下方法（来自 feishu_patch.py）：
      - _get_card_lock(chat_id) -> asyncio.Lock
      - _get_tenant_access_token() -> str
      - _build_streaming_card(greeting, subtitle) -> dict
      - send_streaming_card(chat_id, ...)  # 创建并发送卡片
      - _update_card_element(card_id, element_id, content)
      - finalize_streaming_card(chat_id, final_content, tokens, duration, ...)
    """
    
    def __init__(self, config: Dict[str, Any], hermes_dir: str, 
                 feishu_adapter: Any):
        super().__init__(config, hermes_dir)
        self._feishu = feishu_adapter
        
        # 验证必要方法
        required = ['_get_card_lock', 'send_streaming_card', 
                    '_update_card_element', 'finalize_streaming_card',
                    'is_streaming_pending', 'set_streaming_pending',
                    'clear_streaming_pending', 'clear_streaming_card']
        missing = [m for m in required if not hasattr(self._feishu, m)]
        if missing:
            logger.warning(f"[LegacyAdapter] FeishuAdapter 缺少方法: {missing}")
        
        self._pending_chats: set[str] = set()
    
    async def on_message_received(self, ctx: CardContext) -> str:
        """Pre-create 卡片"""
        chat_id = ctx.chat_id
        lock = self._feishu._get_card_lock(chat_id)
        
        async with lock:
            try:
                if self._feishu.is_streaming_pending(chat_id):
                    logger.debug(f"[Legacy] Chat {chat_id} already pending")
                    return self.get_card_id(chat_id) or ""
                
                self._feishu.set_streaming_pending(chat_id)
                self._pending_chats.add(chat_id)
                
                greeting = ctx.greeting or self.get_greeting()
                model_name = ctx.model_name or self.get_model_name()
                
                card_id = await self._feishu.send_streaming_card(
                    chat_id=chat_id,
                    message_id=ctx.message_id,
                    user_id=ctx.user_id,
                    greeting=greeting,
                    model=model_name,
                    user_input=ctx.text[:500] if ctx.text else "",
                )
                
                self.set_card_id(chat_id, card_id)
                logger.info(f"[Legacy] Card created: {card_id}")
                return card_id
                
            except Exception as e:
                self._feishu.clear_streaming_pending(chat_id)
                self._pending_chats.discard(chat_id)
                logger.error(f"[Legacy] Pre-create failed: {e}")
                raise
    
    async def on_thinking(self, chat_id: str, delta: str, 
                          tools: Optional[List[ToolCall]] = None) -> None:
        """更新思考过程"""
        card_id = self.get_card_id(chat_id)
        if not card_id:
            return
        
        lock = self._feishu._get_card_lock(chat_id)
        async with lock:
            try:
                if not self._feishu.is_streaming_pending(chat_id):
                    return
                
                # 获取当前内容并追加
                current = ""
                if chat_id in getattr(self._feishu, '_streaming_card', {}):
                    current = self._feishu._streaming_card[chat_id].get('thinking_content', '')
                
                new_content = current + delta
                
                await self._feishu._update_card_element(
                    card_id=card_id,
                    element_id='thinking_content',
                    content=new_content,
                )
                
                # 更新状态
                if hasattr(self._feishu, '_streaming_card'):
                    if chat_id not in self._feishu._streaming_card:
                        self._feishu._streaming_card[chat_id] = {}
                    self._feishu._streaming_card[chat_id]['thinking_content'] = new_content
                
                logger.debug(f"[Legacy] Updated card {card_id}")
            except Exception as e:
                logger.debug(f"[Legacy] Update failed: {e}")
    
    async def on_tool_call(self, chat_id: str, tool: ToolCall) -> None:
        """工具调用状态"""
        try:
            status_line = f"

**[{tool.status.upper()}]** `{tool.name}`"
            if tool.result:
                status_line += f": {tool.result[:100]}"
            if tool.error:
                status_line += f"
❌ {tool.error}"
            await self.on_thinking(chat_id, status_line)
        except Exception as e:
            logger.debug(f"[Legacy] Tool call failed: {e}")
    
    async def on_finish(self, chat_id: str, content: str, 
                        tokens: TokenUsage, duration: float) -> None:
        """完成卡片"""
        card_id = self.get_card_id(chat_id)
        if not card_id:
            logger.warning(f"[Legacy] No card for {chat_id} on finish")
            return
        
        lock = self._feishu._get_card_lock(chat_id)
        async with lock:
            try:
                await self._feishu.finalize_streaming_card(
                    chat_id=chat_id,
                    final_content=content,
                    tokens={
                        'input': tokens.input_tokens,
                        'output': tokens.output_tokens,
                        'cache_read': tokens.cache_read,
                    },
                    duration=duration,
                    thinking_start=None,
                )
                logger.info(f"[Legacy] Finalized card {card_id}")
            except Exception as e:
                logger.error(f"[Legacy] Finalize failed: {e}")
            finally:
                await self.cleanup_chat(chat_id)
    
    async def on_error(self, chat_id: str, error: str) -> None:
        """错误处理"""
        try:
            card_id = self.get_card_id(chat_id)
            if card_id:
                await self.on_thinking(chat_id, f"

❌ **错误**: {error}")
        except Exception as e:
            logger.debug(f"[Legacy] Error handling failed: {e}")
    
    async def cleanup_chat(self, chat_id: str) -> None:
        """清理状态"""
        self._feishu.clear_streaming_pending(chat_id)
        self._feishu.clear_streaming_card(chat_id)
        self._pending_chats.discard(chat_id)
        super().cleanup_chat(chat_id)
    
    async def shutdown(self) -> None:
        """关闭时清理所有 pending 状态"""
        for chat_id in list(self._pending_chats):
            try:
                await self.cleanup_chat(chat_id)
            except Exception:
                pass
        await super().shutdown()
