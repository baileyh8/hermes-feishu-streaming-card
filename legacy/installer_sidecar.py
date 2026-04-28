"""
Hermes Feishu Streaming Card — Sidecar 模式安装器
====================================================

提供 sidecar 模式的安装、升级、回滚功能。
不破坏现有 legacy 部署，支持双模式并行。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("fsc.sidecar")

# Hermes 目录
HERMES_DIR = Path.home() / '.hermes'
HERMES_AGENT = HERMES_DIR / 'hermes-agent'
GATEWAY_PLATFORMS = HERMES_AGENT / 'gateway' / 'platforms'
FEISHU_PY = GATEWAY_PLATFORMS / 'feishu.py'
RUN_PY = HERMES_AGENT / 'gateway' / 'run.py'

# Sidecar 文件列表
SIDECAR_FILES = [
    'sidecar/__init__.py',
    'sidecar/__main__.py',
    'sidecar/config.py',
    'sidecar/cardkit_client.py',
    'sidecar/card_manager.py',
    'sidecar/server.py',
    'sidecar/requirements.txt',
]

ADAPTER_FILES = [
    'adapter/__init__.py',
    'adapter/streaming_adapter.py',
    'adapter/legacy_adapter.py',
    'adapter/sidecar_adapter.py',
    'adapter/dual_adapter.py',
    'adapter/factory.py',
]


class SidecarInstaller:
    """Sidecar 模式安装器"""

    def __init__(self, hermes_dir: Path = None):
        self.hermes_dir = Path(hermes_dir or HERMES_DIR)
        self.backup_dir = self.hermes_dir / '.fsc_backups'
        self.sidecar_dir = self.hermes_dir / 'feishu-sidecar'

    # ─── 状态检测 ────────────────────────────────────────────────

    def detect_current_state(self) -> Dict:
        """
        检测当前安装状态。
        返回: {
            'mode': 'legacy' | 'sidecar' | 'dual' | 'none',
            'version': '2.0.0' 等,
            'injected': bool,
            'backups_available': int,
            'sidecar_installed': bool,
            'sidecar_running': bool,
        }
        """
        state = {
            'mode': 'none',
            'version': 'unknown',
            'injected': False,
            'backups_available': 0,
            'sidecar_installed': False,
            'sidecar_running': False,
        }

        # 1. 检查 sidecar 目录
        if self.sidecar_dir.exists():
            state['sidecar_installed'] = True

        # 2. 检查 sidecar 是否运行
        pid_file = self.hermes_dir / 'feishu-sidecar.pid'
        if pid_file.exists():
            try:
                with open(pid_file) as f:
                    pid_data = json.load(f)
                pid = pid_data.get('pid')
                if pid:
                    os.kill(pid, 0)
                    state['sidecar_running'] = True
            except Exception:
                pass

        # 3. 检查 legacy 注入
        if FEISHU_PY.exists():
            with open(FEISHU_PY) as f:
                feishu_content = f.read()

            if 'Feishu Streaming Card' in feishu_content:
                state['injected'] = True
                state['mode'] = 'legacy'

                ver_match = re.search(r'feishu-streaming-card v([\d.]+)', feishu_content)
                if ver_match:
                    state['version'] = ver_match.group(1)

        # 4. 检查 backups
        if self.backup_dir.exists():
            backups = sorted(self.backup_dir.iterdir(),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            state['backups_available'] = len(backups)

        # 5. 检查配置中的 mode
        config_path = self.hermes_dir / 'config.yaml'
        if config_path.exists():
            try:
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
                mode = config.get('feishu_streaming_card', {}).get('mode', 'legacy')
                if mode in ('sidecar', 'dual', 'migrating'):
                    state['mode'] = mode
            except Exception:
                pass

        return state

    # ─── 安装 ─────────────────────────────────────────────────────

    def install_sidecar(self, mode: str = 'dual') -> None:
        """
        安装 sidecar（不修改 legacy 代码）。
        mode: 'dual' | 'sidecar'
        """
        logger.info(f"Installing sidecar in {mode} mode")

        # 1. 备份
        self._backup_current()

        # 2. 复制 sidecar 和 adapter 文件
        self._copy_sidecar_files()
        self._copy_adapter_files()

        # 3. 在 feishu.py 中添加事件转发（最小化注入）
        self._patch_feishu_add_forwarding()

        # 4. 更新 config.yaml
        self._update_config(mode)

        # 5. 生成 sidecar 配置
        self._generate_sidecar_config()

        # 6. 安装依赖
        self._install_dependencies()

        logger.info("Sidecar installed successfully")

    def _backup_current(self) -> None:
        """备份现有文件"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_id = time.strftime('%Y%m%d_%H%M%S')
        backup_path = self.backup_dir / backup_id
        backup_path.mkdir()

        for f in [FEISHU_PY, RUN_PY]:
            if f.exists():
                shutil.copy2(f, backup_path / f.name)

        with open(backup_path / 'metadata.json', 'w') as f:
            json.dump({
                'backup_id': backup_id,
                'timestamp': time.time(),
                'files': [f.name for f in backup_path.iterdir()],
            }, f, indent=2)

        logger.info(f"Backup created: {backup_id}")

    def _copy_sidecar_files(self) -> None:
        """复制 sidecar 模块文件"""
        src_dir = Path(__file__).parent / 'sidecar'
        dst_dir = self.sidecar_dir / 'sidecar'
        dst_dir.mkdir(parents=True, exist_ok=True)

        for file in SIDECAR_FILES:
            src = src_dir / file
            if src.exists():
                dst = dst_dir.parent / file
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        logger.info("Sidecar files copied")

    def _copy_adapter_files(self) -> None:
        """复制 adapter 模块文件"""
        src_dir = Path(__file__).parent / 'adapter'
        dst_dir = self.sidecar_dir / 'adapter'
        dst_dir.mkdir(parents=True, exist_ok=True)

        for file in ADAPTER_FILES:
            src = src_dir / file
            if src.exists():
                dst = dst_dir.parent / file
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        logger.info("Adapter files copied")

    def _patch_feishu_add_forwarding(self) -> None:
        """
        在 feishu.py 中注入事件转发代码。
        这是唯一的 gateway 修改，仅 ~40 行，非阻塞。
        """
        if not FEISHU_PY.exists():
            raise FileNotFoundError(f"feishu.py not found: {FEISHU_PY}")

        with open(FEISHU_PY) as f:
            content = f.read()

        # 检查是否已经注入
        if '_sidecar_client' in content:
            logger.warning("Sidecar forwarding already patched, skipping")
            return

        # ── 注入: 顶部 import 添加 ───────────────────────────────
        import_addition = '''
# ─── Feishu Streaming Sidecar Forwarder ─────────────────────────────
import asyncio as _asyncio
import json as _json
import logging as _logging
from typing import Optional, Dict, Any
# ────────────────────────────────────────────────────────────────────
'''
        # 在第一个 import 之后添加
        first_import_end = content.find('\n')
        if first_import_end > 0:
            content = content[:first_import_end] + import_addition + content[first_import_end:]

        # ── 注入: FeishuAdapter.__init__ 末尾添加 sidecar client ──
        init_patch = '''
        # ── Feishu Streaming Sidecar: init client (non-blocking) ──
        self._streaming_mode = (self.config.extra or {}).get('feishu_streaming_card', {}).get('mode', 'legacy')
        if self._streaming_mode in ('sidecar', 'dual', 'migrating'):
            self._sidecar_client = _SidecarForwarder(
                (self.config.extra or {}).get('feishu_streaming_card', {}).get('sidecar', {}).get('host', 'localhost'),
                (self.config.extra or {}).get('feishu_streaming_card', {}).get('sidecar', {}).get('port', 8765),
            )
            _logging.getLogger('hermes.feishu').info(f"[Feishu] Streaming mode: {self._streaming_mode}")
        else:
            self._sidecar_client = None
'''

        # 找到 __init__ 的结尾位置
        init_start = content.find('def __init__')
        if init_start == -1:
            logger.error("Cannot find __init__ in feishu.py")
            return

        # 找到下一个方法定义（缩进更少）
        # __init__ 通常在类的前面部分
        search_from = init_start + 100
        next_method = re.search(r'\n    async def |\n    def ', content[search_from:])
        if next_method:
            insert_pos = search_from + next_method.start()
            content = content[:insert_pos] + init_patch + content[insert_pos:]

        # ── 注入: _SidecarForwarder 类定义（在文件末尾或类定义之前）────
        forwarder_class = '''

# ══════════════════════════════════════════════════════════════════════
# Feishu Streaming Sidecar — Minimal Event Forwarder
# Non-blocking HTTP client, cannot crash gateway
# ══════════════════════════════════════════════════════════════════════
class _SidecarForwarder:
    """Lightweight async HTTP client for forwarding events to sidecar."""

    def __init__(self, host: str = 'localhost', port: int = 8765):
        self._base_url = f"http://{host}:{port}"
        self._timeout = 5.0
        self._session = None

    async def _post(self, endpoint: str, payload: Dict[str, Any]) -> None:
        """Fire-and-forget POST, never blocks gateway."""
        try:
            import aiohttp
            if self._session is None:
                self._session = aiohttp.ClientSession()
            async with self._session.post(
                f"{self._base_url}{endpoint}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            ) as resp:
                resp.release()
        except Exception:
            pass  # Non-blocking, swallow all errors

    async def publish_message_received(self, chat_id: str, message_id: str,
                                       user_id: str, greeting: str, model: str,
                                       user_input: str) -> None:
        await self._post("/event", {
            "event": "message_received",
            "chat_id": chat_id,
            "message_id": message_id,
            "user_id": user_id,
            "greeting": greeting,
            "model": model,
            "user_input": user_input,
        })

    async def publish_thinking(self, chat_id: str, message_id: str,
                               thinking: str) -> None:
        await self._post("/event", {
            "event": "thinking",
            "chat_id": chat_id,
            "message_id": message_id,
            "thinking": thinking[-2000:],
        })

    async def publish_tool_call(self, chat_id: str, message_id: str,
                                tool_name: str, tool_input: str) -> None:
        await self._post("/event", {
            "event": "tool_call",
            "chat_id": chat_id,
            "message_id": message_id,
            "tool_name": tool_name,
            "tool_input": tool_input[-1000:],
        })

    async def publish_finish(self, chat_id: str, message_id: str,
                             result: str) -> None:
        await self._post("/event", {
            "event": "finish",
            "chat_id": chat_id,
            "message_id": message_id,
            "result": result[-2000:],
        })

    async def publish_error(self, chat_id: str, message_id: str,
                            error: str) -> None:
        await self._post("/event", {
            "event": "error",
            "chat_id": chat_id,
            "message_id": message_id,
            "error": error[-500:],
        })
'''
        # 在文件末尾添加 forwarder 类
        content = content.rstrip() + forwarder_class

        with open(FEISHU_PY, 'w') as f:
            f.write(content)

        logger.info("Patched feishu.py with sidecar forwarding (~40 lines)")

    def _update_config(self, mode: str) -> None:
        """更新 config.yaml"""
        config_path = self.hermes_dir / 'config.yaml'
        if not config_path.exists():
            logger.warning("config.yaml not found")
            return

        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        if 'feishu_streaming_card' not in config:
            config['feishu_streaming_card'] = {}

        config['feishu_streaming_card'].update({
            'enabled': True,
            'mode': mode,
            'greeting': '主人，苏菲为您服务！',
            'sidecar': {
                'host': 'localhost',
                'port': 8765,
            }
        })

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)

        logger.info(f"Config updated: mode={mode}")

    def _generate_sidecar_config(self) -> None:
        """生成 sidecar 配置文件"""
        config_content = '''server:
  host: "localhost"
  port: 8765
  enable_metrics: true

cardkit:
  base_url: "https://open.feishu.cn/open-apis/cardkit/v1"
  timeout: 30
  max_retries: 3

card:
  merge_window_ms: 100
  max_age_seconds: 3600

logging:
  level: "INFO"
  file: ""
'''
        config_path = self.hermes_dir / 'feishu-sidecar.yaml'
        with open(config_path, 'w') as f:
            f.write(config_content)
        logger.info(f"Sidecar config: {config_path}")

    def _install_dependencies(self) -> None:
        """安装 aiohttp 依赖"""
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', 'aiohttp>=3.9.0'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            logger.info("aiohttp installed")
        else:
            logger.warning(f"aiohttp install: {result.stderr[:200]}")

    # ─── 模式切换 ─────────────────────────────────────────────────

    def switch_mode(self, mode: str) -> None:
        """切换运行模式: legacy | sidecar | dual"""
        valid = ['legacy', 'sidecar', 'dual']
        if mode not in valid:
            raise ValueError(f"Invalid mode: {mode}")

        config_path = self.hermes_dir / 'config.yaml'
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        config.setdefault('feishu_streaming_card', {})['mode'] = mode
        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)

        if mode == 'legacy':
            self.stop_sidecar()
        elif mode in ('sidecar', 'dual'):
            self.start_sidecar()

        logger.info(f"Mode switched to: {mode}")

    # ─── Sidecar 进程管理 ────────────────────────────────────────

    def start_sidecar(self) -> None:
        """启动 sidecar 进程"""
        pid_file = self.hermes_dir / 'feishu-sidecar.pid'
        if pid_file.exists():
            try:
                with open(pid_file) as f:
                    pid = json.load(f)['pid']
                os.kill(pid, 0)
                logger.info(f"Sidecar already running (PID {pid})")
                return
            except Exception:
                pass

        log_dir = self.hermes_dir / 'logs'
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / 'sidecar.log'

        cmd = [sys.executable, '-m', 'sidecar', '--config', str(self.hermes_dir / 'feishu-sidecar.yaml')]
        with open(log_file, 'w') as f:
            f.write(f"Starting sidecar at {time.ctime()}\\n")
        result = subprocess.run(
            f"cd {self.hermes_dir} && nohup {' '.join(cmd)} >> {log_file} 2>&1 & echo $!",
            shell=True, capture_output=True, text=True
        )

        pid_str = result.stdout.strip()
        if pid_str.isdigit():
            with open(pid_file, 'w') as f:
                json.dump({'pid': int(pid_str), 'started': time.time()}, f)
            logger.info(f"Sidecar started (PID {pid_str})")
        else:
            logger.error(f"Sidecar start failed: {result.stderr[:200]}")

    def stop_sidecar(self) -> None:
        """停止 sidecar 进程"""
        pid_file = self.hermes_dir / 'feishu-sidecar.pid'
        if not pid_file.exists():
            return

        try:
            with open(pid_file) as f:
                pid = json.load(f)['pid']
            import signal
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            pid_file.unlink(missing_ok=True)
            logger.info("Sidecar stopped")
        except Exception as e:
            logger.error(f"Stop sidecar failed: {e}")

    # ─── 恢复 ─────────────────────────────────────────────────────

    def recover(self) -> None:
        """从最新备份恢复"""
        if not self.backup_dir.exists():
            logger.error("No backups found")
            return

        backups = sorted(self.backup_dir.iterdir(),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        latest = backups[0]
        logger.info(f"Recovering from: {latest.name}")

        self.stop_sidecar()

        for backup_file in latest.iterdir():
            if backup_file.name == 'metadata.json':
                continue
            target = None
            if backup_file.name == 'feishu.py':
                target = FEISHU_PY
            elif backup_file.name == 'run.py':
                target = RUN_PY
            if target:
                shutil.copy2(backup_file, target)
                logger.info(f"Restored: {target.name}")

        if self.sidecar_dir.exists():
            shutil.rmtree(self.sidecar_dir)

        config_path = self.hermes_dir / 'config.yaml'
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            if 'feishu_streaming_card' in config:
                config['feishu_streaming_card'].pop('mode', None)
                config['feishu_streaming_card'].pop('sidecar', None)
                with open(config_path, 'w') as f:
                    yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)

        logger.info("Recovery complete. Restart gateway.")

    def uninstall(self, full: bool = False) -> None:
        """卸载 sidecar"""
        logger.info("Uninstalling sidecar...")

        self.stop_sidecar()

        if self.sidecar_dir.exists():
            shutil.rmtree(self.sidecar_dir)

        config_path = self.hermes_dir / 'config.yaml'
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            if 'feishu_streaming_card' in config:
                config['feishu_streaming_card'].pop('mode', None)
                config['feishu_streaming_card'].pop('sidecar', None)
                if not config['feishu_streaming_card']:
                    del config['feishu_streaming_card']
                with open(config_path, 'w') as f:
                    yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)

        if full:
            self.recover()

        logger.info("Uninstall complete")


# ─── CLI 入口 ──────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.INFO, format='%(name)s %(levelname)s %(message)s')

    parser = argparse.ArgumentParser(description='Feishu Streaming Card Sidecar Installer')
    parser.add_argument('command', choices=['install', 'uninstall', 'recover', 'status', 'start', 'stop', 'switch'],
                        help='Command to execute')
    parser.add_argument('--mode', default='dual', choices=['legacy', 'sidecar', 'dual'],
                        help='Mode for install/switch commands')
    parser.add_argument('--full', action='store_true', help='Full uninstall (includes legacy)')

    args = parser.parse_args()

    installer = SidecarInstaller()

    if args.command == 'status':
        state = installer.detect_current_state()
        print(json.dumps(state, indent=2))

    elif args.command == 'install':
        installer.install_sidecar(args.mode)

    elif args.command == 'uninstall':
        installer.uninstall(full=args.full)

    elif args.command == 'recover':
        installer.recover()

    elif args.command == 'start':
        installer.start_sidecar()

    elif args.command == 'stop':
        installer.stop_sidecar()

    elif args.command == 'switch':
        installer.switch_mode(args.mode)
