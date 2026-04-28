"""
Sidecar Client - Gateway 端轻量级事件发送器
================================================================

在 Gateway 的 feishu.py 中实例化，用于向 sidecar 发送事件。
设计原则：
  1. 非阻塞：事件发送不阻塞主流程
  2. 失败静默：sidecar 不可用时自动降级
  3. 资源管理：自动管理 aiohttp session 生命周期
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Dict, Any, List

import aiohttp

logger = logging.getLogger("feishu.sidecar_client")


class SidecarClient:
    """
    Sidecar HTTP 客户端（轻量版）。
    
    仅负责发送事件，不维护卡片状态。
    状态由 sidecar 进程管理，gateway 无状态。
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化客户端。
        
        Args:
            config: Hermes 配置字典（feishu_streaming_card 部分）
        """
        fsc_cfg = config.get('feishu_streaming_card', {})
        sidecar_cfg = fsc_cfg.get('sidecar', {})
        
        self.base_url = sidecar_cfg.get('base_url') or                        f"http://{sidecar_cfg.get('host', 'localhost')}:"                        f"{sidecar_cfg.get('port', 8765)}"
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._connect_lock = asyncio.Lock()
        self._health_cache = {'last_check': 0, 'healthy': False}
        self._health_ttl = 10
        
        # 配置
        self._timeout = sidecar_cfg.get('timeout', 3)
        self._retry_count = sidecar_cfg.get('retry_count', 1)
    
    async def _ensure_session(self) -> None:
        """确保 HTTP session 可用"""
        if self._session is None or self._session.closed:
            async with self._connect_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=self._timeout)
                    self._session = aiohttp.ClientSession(timeout=timeout)
    
    async def _is_healthy(self) -> bool:
        """检查 sidecar 健康状态（带缓存）"""
        now = asyncio.get_event_loop().time()
        if now - self._health_cache['last_check'] < self._health_ttl:
            return self._health_cache['healthy']
        
        try:
            await self._ensure_session()
            async with self._session.get(
                f"{self.base_url}/health",
                timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                healthy = resp.status == 200
                self._health_cache = {'last_check': now, 'healthy': healthy}
                return healthy
        except Exception:
            self._health_cache = {'last_check': now, 'healthy': False}
            return False
    
    async def publish(self, event: Dict[str, Any]) -> bool:
        """
        发布事件到 sidecar。
        
        设计为"火燎"式：发送后不等待结果，失败不影响主流程。
        
        Args:
            event: 事件字典
            
        Returns:
            True 如果发送成功（或已入队），False 如果 sidecar 不可用
        """
        # 快速健康检查（非阻塞）
        if not await self._is_healthy():
            logger.debug("[SidecarClient] Sidecar unhealthy, skipping event")
            return False
        
        # 非阻塞发送（后台任务）
        asyncio.create_task(self._send_event(event))
        return True
    
    async def _send_event(self, event: Dict[str, Any]) -> None:
        """实际发送事件（后台任务）"""
        try:
            await self._ensure_session()
            async with self._session.post(
                f"{self.base_url}/events",
                json=event,
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            ) as resp:
                if resp.status >= 400:
                    logger.debug(f"[SidecarClient] Event failed: HTTP {resp.status}")
        except Exception as e:
            logger.debug(f"[SidecarClient] Send event error: {e}")
    
    async def publish_sync(self, event: Dict[str, Any]) -> bool:
        """
        同步发布事件（等待响应）。
        
        用于关键操作如 on_finish，确保 sidecar 处理完成。
        
        Args:
            event: 事件字典
            
        Returns:
            True 如果发送并收到成功响应
        """
        if not await self._is_healthy():
            return False
        
        try:
            await self._ensure_session()
            async with self._session.post(
                f"{self.base_url}/events",
                json=event,
                timeout=aiohttp.ClientTimeout(total=self._timeout * 2)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('ok', False)
                return False
        except Exception as e:
            logger.debug(f"[SidecarClient] Sync publish error: {e}")
            return False
    
    async def close(self) -> None:
        """关闭连接"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def __del__(self):
        """析构时确保连接关闭"""
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())
