"""
DualAdapter - 双模式运行：Sidecar + Legacy 并存
================================================================

Dual 模式：同时启用 sidecar 和 legacy，sidecar 失败时自动降级。
用于验证 sidecar 稳定性，或过渡期运行。

策略：
  1. 优先使用 sidecar
  2. sidecar 失败（网络错误、超时、5xx）→ 自动切换到 legacy
  3. legacy 也失败 → 降级为普通消息（不重试）
  
日志：
  - 记录每次降级的原因
  - 提供 metrics：sidecar_success / legacy_fallback 计数
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, List, Dict, Any

from .streaming_adapter import StreamingAdapter, CardContext, ToolCall, TokenUsage
from .sidecar_adapter import SidecarAdapter
from .legacy_adapter import LegacyAdapter

logger = logging.getLogger("feishu.streaming.dual")


class DualAdapter(StreamingAdapter):
    """
    双模式适配器：同时运行 sidecar 和 legacy，sidecar 优先。
    """
    
    def __init__(self, config: Dict[str, Any], hermes_dir: str, 
                 feishu_adapter: Any):
        super().__init__(config, hermes_dir)
        
        # 创建两个底层适配器
        self._sidecar = SidecarAdapter(config, hermes_dir)
        self._legacy = LegacyAdapter(config, hermes_dir, feishu_adapter)
        
        # 统计
        self._stats = {
            'sidecar_calls': 0,
            'legacy_fallbacks': 0,
            'sidecar_errors': 0,
        }
        
        # 熔断器：连续失败 N 次后暂时禁用 sidecar
        self._circuit_breaker = {
            'failures': 0,
            'threshold': 3,
            'timeout': 60,  # 秒
            'until': 0,
        }
    
    def _is_circuit_open(self) -> bool:
        """检查熔断器是否打开"""
        if asyncio.get_event_loop().time() < self._circuit_breaker['until']:
            return True
        return False
    
    def _record_success(self) -> None:
        """记录 sidecar 成功"""
        self._circuit_breaker['failures'] = 0
        self._circuit_breaker['until'] = 0
    
    def _record_failure(self) -> None:
        """记录 sidecar 失败"""
        self._circuit_breaker['failures'] += 1
        if self._circuit_breaker['failures'] >= self._circuit_breaker['threshold']:
            self._circuit_breaker['until'] = asyncio.get_event_loop().time() +                                              self._circuit_breaker['timeout']
            logger.warning(f"[Dual] Circuit breaker opened for {self._circuit_breaker['timeout']}s")
    
    async def initialize(self) -> None:
        """初始化两个适配器"""
        await self._sidecar.initialize()
        await self._legacy.initialize()
    
    async def shutdown(self) -> None:
        """关闭两个适配器"""
        await self._sidecar.shutdown()
        await self._legacy.shutdown()
    
    async def on_message_received(self, ctx: CardContext) -> str:
        """创建卡片：sidecar 优先，失败降级 legacy"""
        self._stats['sidecar_calls'] += 1
        
        # 尝试 sidecar
        if not self._is_circuit_open():
            try:
                card_id = await self._sidecar.on_message_received(ctx)
                self._record_success()
                logger.info(f"[Dual] Used sidecar for {ctx.chat_id}")
                return card_id
            except Exception as e:
                logger.warning(f"[Dual] Sidecar failed, falling back to legacy: {e}")
                self._record_failure()
                self._stats['legacy_fallbacks'] += 1
        
        # Sidecar 失败，使用 legacy
        logger.info(f"[Dual] Falling back to legacy for {ctx.chat_id}")
        return await self._legacy.on_message_received(ctx)
    
    async def on_thinking(self, chat_id: str, delta: str, 
                          tools: Optional[List[ToolCall]] = None) -> None:
        """思考更新：sidecar 非阻塞，失败丢弃"""
        try:
            if not self._is_circuit_open():
                asyncio.create_task(
                    self._safe_thinking(self._sidecar, chat_id, delta, tools)
                )
        except Exception:
            pass  # sidecar 失败不影响，不降级（thinking 非关键）
    
    async def _safe_thinking(self, adapter: StreamingAdapter, 
                            chat_id: str, delta: str, 
                            tools: Optional[List[ToolCall]]) -> None:
        """安全调用 thinking（异常不传播）"""
        try:
            await adapter.on_thinking(chat_id, delta, tools)
        except Exception as e:
            logger.debug(f"[Dual] Thinking error: {e}")
    
    async def on_tool_call(self, chat_id: str, tool: ToolCall) -> None:
        """工具调用：同时发送给 sidecar（非阻塞）"""
        try:
            if not self._is_circuit_open():
                asyncio.create_task(self._sidecar.on_tool_call(chat_id, tool))
        except Exception:
            pass
    
    async def on_finish(self, chat_id: str, content: str, 
                        tokens: TokenUsage, duration: float) -> None:
        """
        完成任务：先 sidecar，失败降级 legacy。
        finish 是关键操作，需要确保至少一个成功。
        """
        try:
            if not self._is_circuit_open():
                try:
                    await self._sidecar.on_finish(chat_id, content, tokens, duration)
                    self._record_success()
                    return
                except Exception as e:
                    logger.warning(f"[Dual] Sidecar finish failed, legacy fallback: {e}")
                    self._record_failure()
                    self._stats['legacy_fallbacks'] += 1
            
            # Sidecar 失败或熔断，使用 legacy
            await self._legacy.on_finish(chat_id, content, tokens, duration)
        except Exception as e:
            logger.error(f"[Dual] Both sidecar and legacy failed: {e}")
            # 两个都失败，至少清理状态
            await self.cleanup_chat(chat_id)
    
    async def on_error(self, chat_id: str, error: str) -> None:
        """错误：同时通知两边"""
        try:
            await self._sidecar.on_error(chat_id, error)
        except Exception:
            pass
        try:
            await self._legacy.on_error(chat_id, error)
        except Exception:
            pass
    
    async def cleanup_chat(self, chat_id: str) -> None:
        """清理两边状态"""
        await self._sidecar.cleanup_chat(chat_id)
        await self._legacy.cleanup_chat(chat_id)
    
    # ─── 统计接口 ───────────────────────────────────────────────
    
    def get_stats(self) -> Dict[str, Any]:
        """获取运行统计"""
        return {
            'mode': 'dual',
            'sidecar_calls': self._stats['sidecar_calls'],
            'legacy_fallbacks': self._stats['legacy_fallbacks'],
            'sidecar_errors': self._stats['sidecar_errors'],
            'circuit_breaker': self._circuit_breaker.copy(),
            'active_cards_sidecar': len(self._sidecar._cards),
            'active_cards_legacy': len(self._legacy._cards),
        }
