"""
Adapter 模式 - Streaming Adapter 抽象层
================================================================

提供 Legacy 和 Sidecar 两种实现，统一接口。
"""

from __future__ import annotations

__version__ = "2.1.0"

__all__ = [
    # 核心抽象
    'StreamingAdapter',
    'CardContext',
    'ToolCall',
    'TokenUsage',
    
    # 具体适配器
    'LegacyAdapter',
    'SidecarAdapter',
    'DualAdapter',
    
    # 工厂
    'create_streaming_adapter',
    'get_adapter_class',
    
    # 网关端客户端（轻量）
    'SidecarClient',
]

from .streaming_adapter import (
    StreamingAdapter,
    CardContext,
    ToolCall,
    TokenUsage,
)
from .legacy_adapter import LegacyAdapter
from .sidecar_adapter import SidecarAdapter
from .dual_adapter import DualAdapter
from .factory import create_streaming_adapter, get_adapter_class
from .sidecar_client import SidecarClient
