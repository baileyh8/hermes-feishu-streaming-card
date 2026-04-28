#!/usr/bin/env python3
"""
Hermes Feishu Streaming Card — 一键恢复脚本
============================================

自动检测半安装状态，从备份恢复，清理残留文件。
用法: python scripts/recover.py [--dry-run]
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('fsc.recover')

HERMES_DIR = Path.home() / '.hermes'
HERMES_AGENT = HERMES_DIR / 'hermes-agent'
GATEWAY_PLATFORMS = HERMES_AGENT / 'gateway' / 'platforms'
FEISHU_PY = GATEWAY_PLATFORMS / 'feishu.py'
BACKUP_DIR = HERMES_DIR / '.fsc_backups'


def find_latest_backup() -> Optional[Path]:
    if not BACKUP_DIR.exists():
        return None
    backups = sorted(BACKUP_DIR.iterdir(),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    return backups[0] if backups else None


def detect_half_installed() -> dict:
    """检测是否是半安装状态（备份存在但注入不存在等）"""
    issues = []

    # 1. feishu.py 被修改但没有备份
    if FEISHU_PY.exists():
        with open(FEISHU_PY) as f:
            content = f.read()
        has_injection = 'Feishu Streaming Card' in content or '_sidecar_client' in content
        if has_injection and not BACKUP_DIR.exists():
            issues.append("feishu.py 有注入但没有备份记录 — 可能是手动安装后未创建备份")

    # 2. 有备份但 feishu.py 与备份不一致
    latest = find_latest_backup()
    if latest:
        feishu_backup = latest / 'feishu.py'
        if feishu_backup.exists() and FEISHU_PY.exists():
            with open(FEISHU_PY) as f:
                current = f.read()
            with open(feishu_backup) as f:
                backed = f.read()
            if current != backed:
                # 检查是否真的被修改过（而非正常升级）
                has_both_markers = ('Feishu Streaming Card' in current and
                                  'Feishu Streaming Card' not in backed)
                if has_both_markers:
                    issues.append(f"feishu.py 已被修改（版本不同），最新备份: {latest.name}")

    # 3. sidecar 目录存在但配置缺失
    sidecar_dir = HERMES_DIR / 'feishu-sidecar'
    config_path = HERMES_DIR / 'feishu-sidecar.yaml'
    if sidecar_dir.exists() and not config_path.exists():
        issues.append("sidecar 目录存在但配置文件缺失 — 安装可能未完成")

    # 4. sidecar 未运行但 config 说应该是 sidecar 模式
    config_path_main = HERMES_DIR / 'config.yaml'
    if config_path_main.exists():
        try:
            import yaml
            with open(config_path_main) as f:
                config = yaml.safe_load(f) or {}
            mode = config.get('feishu_streaming_card', {}).get('mode', 'legacy')
            if mode in ('sidecar', 'dual'):
                pid_file = HERMES_DIR / 'feishu-sidecar.pid'
                if pid_file.exists():
                    try:
                        with open(pid_file) as f:
                            pid = json.load(f)['pid']
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        issues.append(f"配置为 {mode} 模式但 sidecar 进程已退出")
                else:
                    issues.append(f"配置为 {mode} 模式但 sidecar 未启动")
        except Exception:
            pass

    return {
        'half_installed': len(issues) > 0,
        'issues': issues,
        'latest_backup': latest.name if latest else None,
    }


def restore_from_backup(backup_path: Path, dry_run: bool = False) -> None:
    """从指定备份恢复"""
    logger.info(f"从备份恢复: {backup_path.name}")

    files_to_restore = {
        'feishu.py': FEISHU_PY,
        'run.py': HERMES_AGENT / 'gateway' / 'run.py',
    }

    for fname, target in files_to_restore.items():
        src = backup_path / fname
        if src.exists():
            if dry_run:
                logger.info(f"  [dry-run] 恢复: {target.name}")
            else:
                shutil.copy2(src, target)
                logger.info(f"  ✓ 恢复: {target.name}")

    # 清理 sidecar
    sidecar_dir = HERMES_DIR / 'feishu-sidecar'
    if sidecar_dir.exists():
        if dry_run:
            logger.info(f"  [dry-run] 删除: {sidecar_dir}")
        else:
            shutil.rmtree(sidecar_dir)
            logger.info(f"  ✓ 删除: {sidecar_dir}")

    # 恢复 config.yaml
    config_path = HERMES_DIR / 'config.yaml'
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            changed = False
            if 'feishu_streaming_card' in config:
                config['feishu_streaming_card'].pop('mode', None)
                config['feishu_streaming_card'].pop('sidecar', None)
                if not config['feishu_streaming_card']:
                    del config['feishu_streaming_card']
                changed = True
            if changed:
                if dry_run:
                    logger.info(f"  [dry-run] 清理 config.yaml")
                else:
                    with open(config_path, 'w') as f:
                        yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)
                    logger.info(f"  ✓ 清理 config.yaml")
        except Exception as e:
            logger.warning(f"  ! config.yaml 清理失败: {e}")


def main():
    parser = argparse.ArgumentParser(description='Feishu Streaming Card 恢复工具')
    parser.add_argument('--dry-run', action='store_true', help='只检测不修改')
    parser.add_argument('--force', action='store_true', help='强制从最新备份恢复')
    args = parser.parse_args()

    print("=" * 60)
    print("Feishu Streaming Card — 恢复工具")
    print("=" * 60)

    # 检测状态
    state = detect_half_installed()

    if state['half_installed']:
        print("\n检测到以下问题：")
        for issue in state['issues']:
            print(f"  ✗ {issue}")

        if state['latest_backup']:
            print(f"\n最新备份: {state['latest_backup']}")

        if args.dry_run:
            print("\n[dry-run 模式] 未执行任何修改")
            return 0

        if args.force or input("\n是否从最新备份恢复? (y/N): ").strip().lower() == 'y':
            latest = find_latest_backup()
            if latest:
                restore_from_backup(latest)
                print("\n✓ 恢复完成。请重启 Hermes Gateway:")
                print("  hermes-gateway restart")
            else:
                logger.error("未找到备份")
                return 1
        else:
            print("已取消")
            return 0
    else:
        print("\n✓ 未检测到半安装状态，无需恢复")
        print("\n如需手动恢复，可使用以下命令之一：")
        print("  python installer_sidecar.py recover")
        print("  python scripts/recover.py --force")

    return 0

if __name__ == '__main__':
    sys.exit(main())
