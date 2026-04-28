"""
Adapter Factory - 根据配置创建相应的 StreamingAdapter
================================================================

使用示例：
  from adapter.factory import create_streaming_adapter
  
  adapter = create_streaming_adapter(
      config=config_dict,
      hermes_dir="/Users/.../.hermes",
      feishu_adapter=feishu_adapter_instance  # legacy 模式需要
  )
  
  async with adapter:
      await adapter.on_message_received(ctx)
      ...
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from .streaming_adapter import StreamingAdapter
from .legacy_adapter import LegacyAdapter
from .sidecar_adapter import SidecarAdapter

logger = logging.getLogger(__name__)


def create_streaming_adapter(
    config: Dict[str, Any],
    hermes_dir: str,
    feishu_adapter: Optional[Any] = None,
) -> StreamingAdapter:
    """
    创建 StreamingAdapter 实例。
    
    Args:
        config: config.yaml 的完整配置字典
        hermes_dir: Hermes 根目录路径
        feishu_adapter: FeishuAdapter 实例（仅 legacy/dual 模式需要）
        
    Returns:
        StreamingAdapter 实例
        
    Raises:
        ValueError: 未知的 mode 或缺少必要参数
    """
    fsc_cfg = config.get('feishu_streaming_card', {})
    mode = fsc_cfg.get('mode', 'legacy')
    
    logger.info(f"[Factory] Creating streaming adapter: mode={mode}")
    
    if mode == 'legacy':
        # Legacy 模式：使用注入的代码
        if feishu_adapter is None:
            raise ValueError("feishu_adapter is required for legacy mode")
        return LegacyAdapter(config, hermes_dir, feishu_adapter)
    
    elif mode == 'sidecar':
        # Sidecar 模式：独立进程
        return SidecarAdapter(config, hermes_dir)
    
    elif mode in ('dual', 'migrating'):
        # 双模式：同时支持 legacy 和 sidecar
        # 实际使用：优先 sidecar，失败降级到 legacy
        if feishu_adapter is None:
            raise ValueError("feishu_adapter is required for dual/migrating mode")
        # 返回 DualAdapter（组合模式）
        from .dual_adapter import DualAdapter  # 稍后创建
        return DualAdapter(config, hermes_dir, feishu_adapter)
    
    else:
        raise ValueError(f"Unknown feishu_streaming_card.mode: {mode}")


def get_adapter_class(mode: str) -> type[StreamingAdapter]:
    """
    根据模式返回适配器类（不实例化）。
    
    Args:
        mode: 'legacy' | 'sidecar' | 'dual'
        
    Returns:
        StreamingAdapter 子类
    """
    mapping = {
        'legacy': LegacyAdapter,
        'sidecar': SidecarAdapter,
        'dual': None,  # DualAdapter 待实现
        'migrating': None,
    }
    cls = mapping.get(mode)
    if cls is None:
        raise ValueError(f"Mode {mode} not yet implemented")
    return cls

