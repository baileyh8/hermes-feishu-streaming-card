"""
Hermes Feishu Streaming Sidecar
================================================================

独立进程，接收 Gateway 事件并管理飞书流式卡片。

使用方法：
  1. 配置 Hermes config.yaml 的 feishu_streaming_card 部分
  2. 启动 sidecar: python -m sidecar.server
  3. 或通过 installer 自动管理

更多信息：https://github.com/baileyh8/hermes-feishu-streaming-card
"""

__version__ = "2.1.0"
__all__ = ['server', 'card_manager', 'cardkit_client', 'config']

from .config import load_config
from .card_manager import CardManager
from .server import SidecarServer

__all__ += ['load_config', 'CardManager', 'SidecarServer']
