#!/usr/bin/env python3
"""Hermes 飞书流式卡片安装器与管理器

职责：
- 安装/卸载 sidecar 独立进程
- 向 gateway/platforms/feishu.py 注入事件转发补丁
- 管理运行模式（legacy | sidecar | dual）
- 备份与恢复
"""
import sys
import os
import json
import shutil
import subprocess
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

HERMES_DIR = Path.home() / '.hermes'
GATEWAY_DIR = HERMES_DIR / 'hermes-agent'
PROJECT_DIR = Path(__file__).parent.parent.resolve()
SIDECAR_SRC = PROJECT_DIR / 'sidecar'
SIDECAR_DST = HERMES_DIR / 'sidecar'
PATCH_MODULE = 'gateway.platforms.feishu_forward'
MARKER_FILE = HERMES_DIR / '.fsc_installed'
BACKUP_DIR = HERMES_DIR / 'backups' / 'feishu_patches'

def log(msg, level="INFO"):
    print(f"[{level}] {msg}")

def backup_gateway_file():
    """备份原始 feishu.py"""
    feishu_path = GATEWAY_DIR / 'gateway' / 'platforms' / 'feishu.py'
    if not feishu_path.exists():
        raise FileNotFoundError(f"Gateway file not found: {feishu_path}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = BACKUP_DIR / f'feishu_{ts}.py'
    shutil.copy2(feishu_path, backup_path)
    log(f"Backup created: {backup_path}")
    return backup_path

def install_sidecar():
    """复制 sidecar 到 Hermes 目录"""
    if not SIDECAR_SRC.exists():
        raise FileNotFoundError(f"Sidecar source not found: {SIDECAR_SRC}")
    if SIDECAR_DST.exists():
        log(f"Sidecar already installed at {SIDECAR_DST}, replacing...")
        shutil.rmtree(SIDECAR_DST)
    shutil.copytree(SIDECAR_SRC, SIDECAR_DST)
    log(f"Sidecar installed to {SIDECAR_DST}")

def patch_gateway():
    """注入事件转发模块到 gateway"""
    # 复制 feishu_forward.py
    forward_src = PROJECT_DIR / 'gateway' / 'platforms' / 'feishu_forward.py'
    forward_dst = GATEWAY_DIR / 'gateway' / 'platforms' / 'feishu_forward.py'
    if not forward_src.exists():
        raise FileNotFoundError(f"Patch module not found: {forward_src}")
    shutil.copy2(forward_src, forward_dst)
    log(f"Patched gateway: {forward_dst}")

    # 标记已安装
    marker_data = {
        'mode': 'sidecar',
        'installed_at': datetime.now().isoformat(),
        'gateway_patched': True,
        'sidecar_installed': True
    }
    with open(MARKER_FILE, 'w') as f:
        json.dump(marker_data, f, indent=2)
    log(f"Marker written: {MARKER_FILE}")

def set_mode(mode: str):
    """设置运行模式 (legacy | sidecar | dual)"""
    if mode not in ('legacy', 'sidecar', 'dual'):
        raise ValueError(f"Invalid mode: {mode}")
    config_path = HERMES_DIR / 'config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    config['feishu_streaming_card'] = {'mode': mode}
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    log(f"Mode set to '{mode}' in config.yaml")

def status():
    """查看安装状态"""
    print("=" * 60)
    print("Hermes Feishu Streaming Card - Status")
    print("=" * 60)
    # 检查 marker
    if MARKER_FILE.exists():
        with open(MARKER_FILE) as f:
            data = json.load(f)
        print(f"  Installed: Yes")
        print(f"  Mode: {data.get('mode', 'unknown')}")
        print(f"  Installed at: {data.get('installed_at', 'unknown')}")
    else:
        print("  Installed: No")
    # 检查 sidecar
    print(f"  Sidecar files: {'Present' if SIDECAR_DST.exists() else 'Missing'}")
    # 检查补丁
    forward_dst = GATEWAY_DIR / 'gateway' / 'platforms' / 'feishu_forward.py'
    print(f"  Gateway patch: {'Applied' if forward_dst.exists() else 'Not applied'}")
    # 检查配置
    config_path = HERMES_DIR / 'config.yaml'
    if config_path.exists():
        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)
        mode = config.get('feishu_streaming_card', {}).get('mode', 'not set')
        print(f"  Config mode: {mode}")
    # 检查进程
    try:
        result = subprocess.run(['pgrep', '-f', 'sidecar.server'], capture_output=True, text=True)
        print(f"  Sidecar running: {'Yes' if result.returncode == 0 else 'No'}")
    except:
        pass
    print("=" * 60)

def uninstall(full=False):
    """卸载"""
    print("Uninstalling...")
    # 移除补丁
    forward_dst = GATEWAY_DIR / 'gateway' / 'platforms' / 'feishu_forward.py'
    if forward_dst.exists():
        forward_dst.unlink()
        log("Removed feishu_forward.py")
    # 移除 marker
    if MARKER_FILE.exists():
        MARKER_FILE.unlink()
        log("Removed marker")
    # 完全卸载时移除 sidecar
    if full and SIDECAR_DST.exists():
        shutil.rmtree(SIDECAR_DST)
        log("Removed sidecar directory")
    print("✓ Uninstall complete")

def main():
    parser = argparse.ArgumentParser(
        description='Hermes Feishu Streaming Card Installer & Manager'
    )
    subparsers = parser.add_subparsers(dest='command', help='Command')

    # install
    subparsers.add_parser('install', help='Install sidecar and patch gateway')
    # uninstall
    subparsers.add_parser('uninstall', help='Uninstall (keeps sidecar files)')
    subparsers.add_parser('uninstall-all', help='Full uninstall (removes sidecar)')
    # mode
    mode_parser = subparsers.add_parser('mode', help='Set running mode')
    mode_parser.add_argument('mode', choices=['legacy', 'sidecar', 'dual'])
    # status
    subparsers.add_parser('status', help='Show installation status')
    # start/stop/restart
    subparsers.add_parser('start', help='Start sidecar service')
    subparsers.add_parser('stop', help='Stop sidecar service')
    subparsers.add_parser('restart', help='Restart sidecar')
    # check-env
    subparsers.add_parser('check-env', help='Check environment prerequisites')
    # recover
    subparsers.add_parser('recover', help='Interactive recovery tool')

    args = parser.parse_args()

    if args.command == 'install':
        backup_gateway_file()
        install_sidecar()
        patch_gateway()
        set_mode('sidecar')
        print("✓ Installation complete. Next: hermes sidecar check-env")
    elif args.command == 'uninstall':
        uninstall(full=False)
    elif args.command == 'uninstall-all':
        uninstall(full=True)
    elif args.command == 'mode':
        set_mode(args.mode)
        print(f"✓ Mode set to {args.mode}")
    elif args.command == 'status':
        status()
    elif args.command == 'check-env':
        # 调用 scripts/check_env.py
        check_script = PROJECT_DIR / 'scripts' / 'check_env.py'
        if check_script.exists():
            os.execv(sys.executable, [sys.executable, str(check_script)])
        else:
            print("Error: scripts/check_env.py not found")
            sys.exit(1)
    elif args.command == 'recover':
        recover_script = PROJECT_DIR / 'scripts' / 'recover.py'
        if recover_script.exists():
            os.execv(sys.executable, [sys.executable, str(recover_script)])
        else:
            print("Error: scripts/recover.py not found")
            sys.exit(1)
    elif args.command == 'start':
        start_sidecar()
    elif args.command == 'stop':
        stop_sidecar()
    elif args.command == 'restart':
        stop_sidecar()
        start_sidecar()
    else:
        parser.print_help()

def start_sidecar():
    """启动 sidecar 服务"""
    sidecar_script = HERMES_DIR / 'sidecar_start.sh'
    if not sidecar_script.exists():
        # 使用 python -m 方式
        cmd = [sys.executable, '-m', 'sidecar.server']
        cwd = str(SIDECAR_DST)
    else:
        os.execv(str(sidecar_script), [str(sidecar_script)] + sys.argv[2:])
        return
    log(f"Starting sidecar: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True
    )
    print(f"✓ Sidecar started (PID {proc.pid})")
    print(f"  Health: http://127.0.0.1:8765/health")

def stop_sidecar():
    """停止 sidecar 服务"""
    try:
        result = subprocess.run(['pkill', '-f', 'sidecar.server'], capture_output=True)
        if result.returncode == 0:
            print("✓ Sidecar stopped")
        else:
            print("No sidecar process found")
    except Exception as e:
        print(f"Error stopping sidecar: {e}")

if __name__ == '__main__':
    main()
