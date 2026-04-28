"""
hermes-feishu-streaming-card - Adapter 模式核心
===============================================

重构目标：将流式卡片逻辑从直接代码注入改为适配器模式，
实现 legacy（旧注入）和 sidecar（独立进程）两种实现。

架构：
  StreamingAdapter (ABC)
    ├── LegacyAdapter    - 封装原有 feishu_patch.py 逻辑
    └── SidecarAdapter   - 通过 HTTP 与独立 sidecar 进程通信

使用：
  from adapter.factory import create_streaming_adapter
  adapter = create_streaming_adapter(config, hermes_dir, feishu_adapter)
  await adapter.on_message_received(ctx)
  await adapter.on_thinking(chat_id, delta, tools)
  await adapter.on_finish(chat_id, content, tokens, duration)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime


@dataclass
class CardContext:
    """卡片创建上下文"""
    chat_id: str
    message_id: str
    user_id: str
    greeting: str
    model_name: str
    text: str = ""  # 用户原始输入
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """工具调用信息"""
    name: str
    status: str  # "pending" | "running" | "completed" | "failed"
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TokenUsage:
    """Token 统计"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    total_tokens: int = 0

    def __post_init__(self):
        self.total_tokens = self.input_tokens + self.output_tokens + self.cache_read


class StreamingAdapter(ABC):
    """
    流式卡片适配器抽象基类。
    
    所有具体适配器（Legacy/Sidecar）必须实现这些方法。
    设计原则：
      1. 异常隔离：adapter 内部异常不应影响主流程
      2. 非阻塞：关键路径上的调用应快速返回
      3. 可重入：同一 chat_id 可能并发调用
    """
    
    def __init__(self, config: Dict[str, Any], hermes_dir: str):
        """
        初始化适配器。
        
        Args:
            config: config.yaml 的完整配置字典
            hermes_dir: Hermes 根目录路径
        """
        self.config = config
        self.hermes_dir = hermes_dir
        self._cards: Dict[str, str] = {}  # chat_id -> card_id
        self._locks: Dict[str, asyncio.Lock] = {}  # per-chat locks
        self._global_lock = asyncio.Lock()  # 全局初始化锁
    
    # ─── 生命周期 ──────────────────────────────────────────────
    
    async def initialize(self) -> None:
        """
        适配器初始化（异步资源分配）。
        子类可重写此方法进行异步初始化（如连接 sidecar）。
        """
        pass
    
    async def shutdown(self) -> None:
        """
        适配器关闭（清理资源）。
        子类可重写此方法进行清理（如关闭 sidecar 连接）。
        """
        pass
    
    # ─── 核心 API ──────────────────────────────────────────────
    
    @abstractmethod
    async def on_message_received(self, ctx: CardContext) -> str:
        """
        收到用户消息时调用。
        应创建卡片并返回 card_id。
        
        Args:
            ctx: 卡片上下文
            
        Returns:
            card_id: 飞书卡片 ID
            
        Raises:
            Exception: 创建失败应抛出异常（会被捕获并降级为普通消息）
        """
        pass
    
    @abstractmethod
    async def on_thinking(self, chat_id: str, delta: str, 
                          tools: Optional[List[ToolCall]] = None) -> None:
        """
        思考过程增量更新。
        
        Args:
            chat_id: 聊天 ID
            delta: 新增文本（增量）
            tools: 当前活跃的工具调用列表
        """
        pass
    
    @abstractmethod
    async def on_tool_call(self, chat_id: str, tool: ToolCall) -> None:
        """
        工具调用状态更新。
        
        Args:
            chat_id: 聊天 ID
            tool: 工具调用信息
        """
        pass
    
    @abstractmethod
    async def on_finish(self, chat_id: str, content: str, 
                        tokens: TokenUsage, duration: float) -> None:
        """
        任务完成，卡片最终化。
        
        Args:
            chat_id: 聊天 ID
            content: 最终回复内容
            tokens: Token 使用统计
            duration: 总耗时（秒）
        """
        pass
    
    @abstractmethod
    async def on_error(self, chat_id: str, error: str) -> None:
        """
        发生错误时调用。
        
        Args:
            chat_id: 聊天 ID
            error: 错误信息
        """
        pass
    
    # ─── 辅助方法 ─────────────────────────────────────────────
    
    def get_card_id(self, chat_id: str) -> Optional[str]:
        """获取 chat_id 对应的 card_id"""
        return self._cards.get(chat_id)
    
    def set_card_id(self, chat_id: str, card_id: str) -> None:
        """记录 card_id 映射"""
        self._cards[chat_id] = card_id
    
    def get_lock(self, chat_id: str) -> asyncio.Lock:
        """
        获取该 chat_id 的锁（保证同一会话的更新顺序）。
        
        使用 per-chat lock 而非全局锁，提高并发性能。
        """
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]
    
    async def cleanup_chat(self, chat_id: str) -> None:
        """
        清理会话状态。
        在卡片完成后调用，释放锁和 card_id 映射。
        """
        self._cards.pop(chat_id, None)
        self._locks.pop(chat_id, None)
    
    # ─── 配置辅助 ─────────────────────────────────────────────
    
    def get_greeting(self) -> str:
        """从配置读取 greeting"""
        return (self.config.get('feishu_streaming_card', {})
                .get('greeting', '主人，苏菲为您服务！'))
    
    def get_model_name(self, gateway_model: Optional[str] = None) -> str:
        """
        获取模型名称用于显示。
        优先使用 gateway_model，其次从 config 读取。
        """
        if gateway_model:
            return gateway_model
        
        # 从 config 读取
        model_cfg = self.config.get('model', {})
        default_model = model_cfg.get('default', 'unknown')
        provider = model_cfg.get('provider', '')
        
        # 简化显示
        if provider == 'minimax-cn':
            return f"MiniMax {default_model}"
        elif provider == 'stepfun':
            return f"Step {default_model}"
        else:
            return default_model
    
    def is_enabled(self) -> bool:
        """检查是否启用流式卡片"""
        return self.config.get('feishu_streaming_card', {}).get('enabled', True)
    
    # ─── 上下文管理器 ─────────────────────────────────────────
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()
