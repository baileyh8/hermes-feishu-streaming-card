#!/usr/bin/env python3
"""
recover_legacy.py — Recover from broken legacy injection

Usage:
    python recover_legacy.py [--backup-id BACKUP_ID]

This script:
1. Stops all Hermes processes (gateway, chat sessions)
2. Restores from the latest backup (or specified backup)
3. Cleans up partially injected code
4. Restarts gateway cleanly

适合场景：
- IndentationError 导致 gateway 无法启动
- 半注入状态（部分代码已注入但未完成）
- 升级 Hermes 后旧 patch 不兼容
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERMES_DIR = Path.home() / ".hermes"
GATEWAY_DIR = HERMES_DIR / "hermes-agent" / "gateway"
BACKUP_DIR = HERMES_DIR / ".fsc_backups"


def run_cmd(cmd, check=True):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        return False
    return result.returncode == 0


def stop_all_hermes():
    """Stop all Hermes processes."""
    print("🛑 Stopping all Hermes processes...")
    run_cmd("pkill -f 'hermes gateway'", check=False)
    run_cmd("pkill -f 'hermes chat'", check=False)
    run_cmd("pkill -f 'hermes --resume'", check=False)
    run_cmd("pkill -f 'sidecar.server'", check=False)
    time.sleep(2)
    print("  ✓ All Hermes processes stopped")


def list_backups():
    backups = []
    if BACKUP_DIR.exists():
        for f in BACKUP_DIR.glob("backup_*.json"):
            try:
                with open(f) as fh:
                    backups.append(json.load(fh))
            except Exception:
                pass
    backups.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return backups


def restore(backup_id=None):
    """Restore from backup."""
    if backup_id:
        backup_dir = BACKUP_DIR / backup_id
        if not backup_dir.exists():
            print(f"❌ Backup {backup_id} not found")
            return False
    else:
        backups = list_backups()
        if not backups:
            print("❌ No backups found")
            return False
        backup = backups[0]
        backup_dir = BACKUP_DIR / backup["id"]
        print(f"Using latest backup: {backup['id']} ({backup.get('timestamp','')})")

    # Restore files
    with open(backup_dir / "backup.json") as f:
        info = json.load(f)

    for rel_path in info["files"]:
        src = backup_dir / rel_path
        dst = HERMES_DIR / rel_path
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  ✓ Restored {rel_path}")

    return True


def clean_injection_artifacts():
    """Remove injected code from gateway/run.py."""
    run_py = GATEWAY_DIR / "run.py"
    if not run_py.exists():
        return

    content = run_py.read_text()

    # Check if patched
    if "Feishu Streaming Card Sidecar" not in content:
        print("  No sidecar injection found")
        return

    # Remove injected block
    # Find the injection and remove it
    lines = content.splitlines(keepends=True)
    new_lines = []
    skip = False
    for line in lines:
        if "Feishu Streaming Card Sidecar Event Forwarding" in line:
            skip = True
        elif skip and "───────────────────────" in line and len(line.strip()) > 0:
            # End of injection block
            skip = False
            continue
        elif not skip:
            new_lines.append(line)

    run_py.write_text(''.join(new_lines))
    print("  ✓ Removed sidecar injection from run.py")

    # Also remove copied adapter files
    for f in ["sidecar_adapter.py", "streaming_adapter.py"]:
        fp = GATEWAY_DIR / f
        if fp.exists():
            fp.unlink()
            print(f"  ✓ Removed {f}")


def main():
    parser = argparse.ArgumentParser(description="Recover from broken legacy injection")
    parser.add_argument("--backup-id", help="Specific backup ID to restore")
    args = parser.parse_args()

    print("═══════════════════════════════════════════════════════")
    print("  Feishu Streaming Card — Recovery Mode")
    print("═══════════════════════════════════════════════════════")
    print("")

    # 1. Stop everything
    stop_all_hermes()

    # 2. Clean artifacts
    print("🧹 Cleaning injected artifacts...")
    clean_injection_artifacts()

    # 3. Restore from backup
    print("📦 Restoring from backup...")
    if not restore(args.backup_id):
        print("⚠️  No backup available, skipping restore")

    # 4. Restart gateway
    print("🚀 Restarting gateway...")
    if run_cmd("hermes gateway run --replace", check=False):
        print("  ✓ Gateway restarted")
    else:
        print("  ⚠️  Gateway start failed, check logs")

    print("")
    print("✅ Recovery complete")
    print("")
    print("If gateway still fails:")
    print("  1. Check logs: tail -50 ~/.hermes/logs/gateway*.log")
    print("  2. Manually edit ~/.hermes/hermes-agent/gateway/run.py")
    print("  3. Open an issue: https://github.com/baileyh8/hermes-feishu-streaming-card")


if __name__ == "__main__":
    import time
    import shutil
    main()
