"""
Sidecar HTTP Server - 独立进程主入口
================================================================

HTTP API 端点：
  GET  /health           - 健康检查
  GET  /metrics          - Prometheus 格式指标（可选）
  POST /events           - 接收事件（统一入口）
  POST /card             - 创建卡片（别名）
  POST /card/{card_id}/update  - 更新卡片
  POST /card/{card_id}/finalize - 完成卡片

启动方式：
  python -m sidecar.server
  或
  hermes sidecar start
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import aiohttp
from aiohttp import web

from .card_manager import CardManager
from .config import load_config

logger = logging.getLogger("sidecar.server")


class SidecarServer:
    """Sidecar HTTP 服务器"""
    
    def __init__(self, config_path: str, config: dict = None):
        self.config_path = config_path
        self.config = config if config is not None else load_config(config_path)
        self.card_manager: Optional[CardManager] = None
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        
        # 信号处理
        self._shutdown_event = asyncio.Event()
        
    async def start(self) -> None:
        """启动服务器"""
        # 初始化 CardManager
        self.card_manager = CardManager(self.config)
        await self.card_manager.start()
        
        # 创建 aiohttp 应用
        self.app = web.Application()
        self.app.router.add_get('/health', self.handle_health)
        self.app.router.add_get('/metrics', self.handle_metrics)
        self.app.router.add_post('/events', self.handle_events)
        self.app.router.add_post('/card', self.handle_create_card)
        self.app.router.add_post('/card/{card_id}/update', self.handle_update_card)
        self.app.router.add_post('/card/{card_id}/finalize', self.handle_finalize_card)
        
        # 启动服务器
        host = self.config['server']['host']
        port = self.config['server']['port']
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()
        
        logger.info(f"🚀 Sidecar server started on http://{host}:{port}")
        logger.info(f"   Health: http://{host}:{port}/health")
        if self.config['server'].get('enable_metrics'):
            logger.info(f"   Metrics: http://{host}:{port}/metrics")
        
        # 等待关闭信号
        await self._wait_for_shutdown()
    
    async def stop(self) -> None:
        """停止服务器"""
        logger.info("Stopping sidecar server...")
        if self.card_manager:
            await self.card_manager.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Sidecar server stopped")
    
    async def _wait_for_shutdown(self) -> None:
        """等待关闭信号"""
        loop = asyncio.get_event_loop()
        
        # 注册信号处理
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._signal_handler)
        
        await self._shutdown_event.wait()
        await self.stop()
    
    def _signal_handler(self) -> None:
        """信号处理"""
        logger.info("Received shutdown signal")
        self._shutdown_event.set()
    
    # ─── HTTP 处理器 ──────────────────────────────────────────────
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """
        健康检查端点。
        
        返回：
          {
            "status": "healthy" | "unhealthy",
            "active_cards": int,
            "uptime": float,
            "timestamp": str
          }
        """
        try:
            stats = self.card_manager.get_stats() if self.card_manager else {}
            return web.json_response({
                'status': 'healthy',
                'active_cards': stats.get('active_cards', 0),
                'uptime': stats.get('uptime', 0),
                'timestamp': asyncio.get_event_loop().time(),
            })
        except Exception as e:
            logger.error(f"Health check error: {e}")
            return web.json_response({
                'status': 'unhealthy',
                'error': str(e),
            }, status=503)
    
    async def handle_metrics(self, request: web.Request) -> web.Response:
        """
        Prometheus 格式指标（可选功能）。
        """
        if not self.config['server'].get('enable_metrics'):
            return web.Response(status=404, text='Not found')
        
        stats = self.card_manager.get_stats() if self.card_manager else {}
        
        lines = [
            '# HELP feishu_cards_created Total cards created',
            '# TYPE feishu_cards_created counter',
            f'feishu_cards_created {stats.get("cards_created", 0)}',
            '',
            '# HELP feishu_cards_finalized Total cards finalized',
            '# TYPE feishu_cards_finalized counter',
            f'feishu_cards_finalized {stats.get("cards_finalized", 0)}',
            '',
            '# HELP feishu_updates_sent Total updates sent',
            '# TYPE feishu_updates_sent counter',
            f'feishu_updates_sent {stats.get("updates_sent", 0)}',
            '',
            '# HELP feishu_active_cards Currently active cards',
            '# TYPE feishu_active_cards gauge',
            f'feishu_active_cards {stats.get("active_cards", 0)}',
        ]
        
        return web.Response(text='\n'.join(lines), content_type='text/plain')
    
    async def handle_events(self, request: web.Request) -> web.Response:
        """
        统一事件处理器（推荐）。
        
        事件格式：
          {
            "schema_version": "1.0",
            "event": "message_received" | "thinking" | "tool_call" | "finish" | "error",
            "data": { ... }
          }
        """
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        event = payload.get('event')
        data = payload.get('data', {})
        
        logger.info(f"[Sidecar] Received event: {event}, data_keys: {list(data.keys()) if data else []}")
        
        try:
            if event == 'message_received':
                card_id = await self.card_manager.on_message_received(
                    chat_id=data['chat_id'],
                    message_id=data.get('message_id', ''),
                    user_id=data.get('user_id', ''),
                    greeting=data.get('greeting') or self.config.get('feishu_streaming_card', {}).get('greeting', ''),
                    model=data.get('model', ''),
                    user_input=data.get('text', ''),
                )
                return web.json_response({'card_id': card_id})
            
            elif event == 'thinking' or event == 'update':
                await self.card_manager.on_thinking(
                    chat_id=data['chat_id'],
                    delta=data.get('delta', ''),
                    tools=data.get('tools'),
                )
                return web.json_response({'ok': True})
            
            elif event == 'tool_call':
                from .card_manager import ToolCall  # 延迟导入避免循环
                tool = ToolCall(
                    name=data['tool_name'],
                    status=data['status'],
                    result=data.get('result'),
                    error=data.get('error'),
                )
                await self.card_manager.on_tool_call(
                    chat_id=data['chat_id'],
                    tool=tool,
                )
                return web.json_response({'ok': True})
            
            elif event == 'finish':
                await self.card_manager.on_finish(
                    chat_id=data['chat_id'],
                    final_content=data.get('final_content', ''),
                    tokens=data.get('tokens', {}),
                    duration=data.get('duration', 0),
                    tool_calls=data.get('tool_calls', []),
                )
                return web.json_response({'ok': True})
            
            elif event == 'error':
                await self.card_manager.on_error(
                    chat_id=data['chat_id'],
                    error=data.get('error', ''),
                )
                return web.json_response({'ok': True})
            
            else:
                return web.json_response(
                    {'error': f'Unknown event: {event}'}, 
                    status=400
                )
                
        except Exception as e:
            logger.error(f"[Sidecar] Event handling error: {e}", exc_info=True)
            return web.json_response({'error': str(e)}, status=500)
    
    async def handle_create_card(self, request: web.Request) -> web.Response:
        """创建卡片（专用端点）"""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        try:
            card_id = await self.card_manager.on_message_received(
                chat_id=data['chat_id'],
                message_id=data.get('message_id', ''),
                user_id=data.get('user_id', ''),
                greeting=data.get('greeting') or self.config.get('feishu_streaming_card', {}).get('greeting', ''),
                model=data.get('model', ''),
                user_input=data.get('text', ''),
            )
            return web.json_response({'card_id': card_id})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)
    
    async def handle_update_card(self, request: web.Request) -> web.Response:
        """更新卡片"""
        card_id = request.match_info['card_id']
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        # 从 card_id 反查 chat_id
        chat_id = self.card_manager._by_card.get(card_id)
        if not chat_id:
            return web.json_response({'error': 'Card not found'}, status=404)
        
        try:
            await self.card_manager.on_thinking(
                chat_id=chat_id,
                delta=data.get('delta', ''),
                tools=data.get('tools'),
            )
            return web.json_response({'ok': True})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)
    
    async def handle_finalize_card(self, request: web.Request) -> web.Response:
        """完成卡片"""
        card_id = request.match_info['card_id']
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)
        
        chat_id = self.card_manager._by_card.get(card_id)
        if not chat_id:
            return web.json_response({'error': 'Card not found'}, status=404)
        
        try:
            await self.card_manager.on_finish(
                chat_id=chat_id,
                final_content=data.get('final_content', data.get('content', '')),
                tokens=data.get('tokens', {}),
                duration=data.get('duration', 0),
                tool_calls=data.get('tool_calls', []),
            )
            return web.json_response({'ok': True})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)


def load_config(config_path: str) -> Dict[str, Any]:
    """
    加载 sidecar 配置。
    
    搜索顺序：
      1. 显式指定的 config_path
      2. ~/.hermes/feishu-sidecar.yaml
      3. 项目根目录的 sidecar/config.yaml.example
    """
    import yaml
    
    paths = [
        Path(config_path).expanduser(),
        Path.home() / '.hermes' / 'feishu-sidecar.yaml',
        Path(__file__).parent.parent / 'config.yaml.example',
    ]
    
    for path in paths:
        if path.exists():
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
            logger.info(f"Loaded config from {path}")
            return cfg
    
    # 返回默认配置
    logger.warning("No config found, using defaults")
    return {
        'server': {
            'host': 'localhost',
            'port': 8765,
            'enable_metrics': True,
        },
        'cardkit': {},
        'logging': {'level': 'INFO'},
        'card': {
            'merge_window_ms': 100,
            'max_age_seconds': 3600,
        },
    }


def main():
    """主入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Hermes Feishu Streaming Sidecar')
    parser.add_argument('--config', '-c', default='~/.hermes/feishu-sidecar.yaml',
                       help='Sidecar 配置文件路径')
    parser.add_argument('--hermes-dir', default='~/.hermes',
                       help='Hermes 根目录（用于读取配置）')
    parser.add_argument('--port', type=int, 
                       help='覆盖配置文件的端口号')
    parser.add_argument('--host', default='localhost',
                       help='监听地址')
    parser.add_argument('--debug', action='store_true',
                       help='启用调试日志')
    
    args = parser.parse_args()
    
    # 日志配置
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    
    # 加载配置
    config = load_config(args.config)
    
    # 命令行覆盖
    if args.port:
        config.setdefault('server', {})['port'] = args.port
    if args.host:
        config.setdefault('server', {})['host'] = args.host
    
    # 注入 hermes_dir（供 CardKitClient 读取 .env）
    config['hermes_dir'] = Path(args.hermes_dir).expanduser()
    
    # 启动服务器
    server = SidecarServer(args.config, config=config)
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
