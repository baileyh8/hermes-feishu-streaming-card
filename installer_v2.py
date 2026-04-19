#!/usr/bin/env python3
"""
installer.py — Hermes Feishu Streaming Card Installer v2.0

Features:
- Safe install with pre-validation, backup, restore
- Sidecar mode (new, recommended)
- Legacy mode (backward compatible)
- Multi-version management
- One-command recovery

Usage:
    python installer.py                      # Interactive install
    python installer.py --check              # Check patch status
    python installer.py --list-backups       # Show all backups
    python installer.py --restore            # Restore from latest backup
    python installer.py --mode sidecar       # Force sidecar mode
    python installer.py --mode legacy        # Use legacy injection
    python installer.py --uninstall          # Remove completely
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

# Paths
HERMES_DIR = Path.home() / ".hermes"
HERMES_AGENT_DIR = HERMES_DIR / "hermes-agent"
GATEWAY_DIR = HERMES_AGENT_DIR / "gateway"
PROJECT_ROOT = Path(__file__).parent.resolve()
CONFIG_SRC = PROJECT_ROOT / "config.yaml.example"
CONFIG_DEST = HERMES_DIR / "feishu-sidecar.yaml"
SIDECAR_DIR = PROJECT_ROOT / "sidecar"

# Colors
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_RED = '\033[91m'
C_RESET = '\033[0m'


def log(msg, color=C_GREEN):
    print(f"{color}{msg}{C_RESET}")


def error(msg):
    log(f"❌ {msg}", C_RED)
    sys.exit(1)


def info(msg):
    log(f"ℹ️  {msg}", C_YELLOW)


def success(msg):
    log(f"✅ {msg}", C_GREEN)


# ── Backup Management ─────────────────────────────────────────────────────────

BACKUP_DIR = HERMES_DIR / ".fsc_backups"


def ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def list_backups():
    """Return list of backup dicts sorted by date desc."""
    ensure_backup_dir()
    backups = []
    for f in BACKUP_DIR.glob("backup_*.json"):
        try:
            with open(f) as fh:
                backups.append(json.load(fh))
        except Exception:
            pass
    backups.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return backups


def create_backup(description: str = "") -> str:
    """Create backup of current state, return backup_id."""
    ensure_backup_dir()
    backup_id = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir = BACKUP_DIR / backup_id
    backup_dir.mkdir()

    backup_info = {
        "id": backup_id,
        "timestamp": datetime.now().isoformat(),
        "description": description,
        "files": []
    }

    # Files to backup
    files_to_backup = [
        HERMES_DIR / "config.yaml",
        GATEWAY_DIR / "run.py",
        HERMES_DIR / "feishu-sidecar.yaml",
    ]

    for filepath in files_to_backup:
        if filepath.exists():
            rel = filepath.relative_to(HERMES_DIR)
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(filepath, dest)
            backup_info["files"].append(str(rel))

    backup_info_path = backup_dir / "backup.json"
    with open(backup_info_path, 'w') as f:
        json.dump(backup_info, f, indent=2)

    success(f"Backup created: {backup_id} ({len(backup_info['files'])} files)")
    return backup_id


def restore_backup(backup_id: str):
    """Restore from a specific backup."""
    backup_dir = BACKUP_DIR / backup_id
    if not backup_dir.exists():
        error(f"Backup {backup_id} not found")

    info(f"Restoring from {backup_id}...")

    # Stop gateway
    info("Stopping gateway...")
    subprocess.run(["hermes", "gateway", "stop"], capture_output=True)

    # Restore files
    with open(backup_dir / "backup.json") as f:
        backup_info = json.load(f)

    for rel_path in backup_info["files"]:
        src = backup_dir / rel_path
        dst = HERMES_DIR / rel_path
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  ✓ Restored {rel_path}")

    success(f"Restored backup {backup_id}")
    info("Run: hermes gateway restart")


# ── Mode Detection ────────────────────────────────────────────────────────────

def detect_current_mode():
    """Detect currently installed mode."""
    # Check if sidecar is running
    result = subprocess.run(["pgrep", "-f", "sidecar.server"], capture_output=True)
    if result.returncode == 0:
        return "sidecar"

    # Check if legacy patch is applied
    run_py = GATEWAY_DIR / "run.py"
    if run_py.exists() and "Feishu Streaming Card Sidecar" in run_py.read_text():
        return "sidecar"  # Patched for sidecar mode

    # Check if legacy patch exists
    if run_py.exists() and "STREAMING_METHODS" in run_py.read_text():
        return "legacy"

    return "none"


# ── Installation ──────────────────────────────────────────────────────────────


def check_and_reserve_port(preferred_port=8765, max_attempts=10):
    """检查端口是否可用，如果被占用则自动选择其他端口。
    
    Returns:
        tuple: (port, was_available) — 被选中的端口和是否首选端口被占用
    """
    import socket
    
    for attempt in range(max_attempts):
        port = preferred_port + attempt
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.bind(('localhost', port))
                info(f"Port {port} is available")
                return port, (attempt == 0)
        except OSError as e:
            if e.args[0] == 48:  # Address already in use
                warning(f"Port {port} is in use, trying {port + 1}...")
                continue
            else:
                raise
    error(f"Could not find an available port after {max_attempts} attempts")
    return None, False

def check_requirements():
    """Verify system meets installation requirements."""
    info("Checking requirements...")

    # Check Python version
    py_ver = sys.version_info
    if py_ver < (3, 9):
        error(f"Python 3.9+ required, found {py_ver.major}.{py_ver.minor}")

    # Check Hermes installation
    if not HERMES_DIR.exists():
        error(f"Hermes directory not found: {HERMES_DIR}")

    if not (HERMES_DIR / "hermes-agent").exists():
        error(f"Hermes agent not found: {HERMES_DIR}/hermes-agent")

    # Check venv
    venv_python = HERMES_AGENT_DIR / "venv" / "bin" / "python3"
    if not venv_python.exists():
        error(f"Hermes venv not found: {venv_python}")

    # Check gateway
    if not GATEWAY_DIR.exists():
        error(f"Gateway directory not found: {GATEWAY_DIR}")

    success("Requirements check passed")


def install_sidecar_mode():
    """Install in sidecar mode (non-invasive)."""
    info("Installing in SIDECAR mode...")

    # 0. Check and reserve port
    port, was_available = check_and_reserve_port()
    if port is None:
        error("No available port found, aborting installation")
        return

    if not was_available:
        warning(f"Preferred port 8765 was in use. Using port {port} instead.")
        info(f"Please update ~/.hermes/feishu-sidecar.yaml if needed (server.port should be {port})")

    # 1. Backup current state
    backup_id = create_backup("pre-sidecar-install")

    # 2. Patch gateway run.py (add event forwarding)
    info("Patching gateway/run.py...")
    patch_script = PROJECT_ROOT / "gateway_run_patch.py"
    result = subprocess.run(
        [sys.executable, str(patch_script)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        error(f"Failed to patch run.py: {result.stderr}")

    # 3. Copy adapter modules to gateway/
    info("Copying adapter modules...")
    adapter_files = [
        "sidecar_client.py",
        "streaming_adapter.py",
    ]
    for f in adapter_files:
        src = PROJECT_ROOT / "adapter" / f
        dst = GATEWAY_DIR / f
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  ✓ {f}")

    # 4. Create feishu-sidecar.yaml config
    if not CONFIG_DEST.exists():
        shutil.copy(CONFIG_SRC, CONFIG_DEST)
        info(f"Config template copied to {CONFIG_DEST}")
        info("Please edit it with your Feishu App ID and Secret")
    else:
        info(f"Config already exists: {CONFIG_DEST}")

    # 5. Install sidecar Python deps
    info("Installing sidecar dependencies...")
    req_file = SIDECAR_DIR / "requirements.txt"
    result = subprocess.run(
        [str(HERMES_AGENT_DIR / "venv" / "bin" / "pip3"), "install", "-r", str(req_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        error(f"Failed to install dependencies: {result.stderr[:200]}")

    # 6. Create startup script if not exists
    startup_script = HERMES_DIR / "sidecar_start.sh"
    if not startup_script.exists():
        with open(startup_script, 'w') as f:
            f.write(f"""#!/bin/bash
# Auto-generated by hermes-feishu-streaming-card installer
VENV_PYTHON="{HERMES_AGENT_DIR}/venv/bin/python"
SIDECAR_DIR="{PROJECT_ROOT}"

export PYTHONPATH="$SIDECAR_DIR:$HERMES_AGENT_DIR:$PYTHONPATH"
exec "$VENV_PYTHON" -m sidecar.server "$@"
""")
        startup_script.chmod(0o755)
        info(f"Created startup script: {startup_script}")

    success("Sidecar mode installation complete!")
    info("\nNext steps:")
    info("1. Edit ~/.hermes/feishu-sidecar.yaml with your Feishu credentials")
    info("2. Restart gateway: hermes gateway restart")
    info("3. Start sidecar (new terminal): ~/.hermes/sidecar_start.sh")
    info("4. Test: hermes chat → send a message")


def install_legacy_mode():
    """Install using legacy injection (not recommended)."""
    info("Installing in LEGACY mode (direct code injection)...")
    info("This method is risky and may break on Hermes updates.")

    # Run old installer
    installer_legacy = PROJECT_ROOT / "installer.py"
    if installer_legacy.exists():
        result = subprocess.run(
            [sys.executable, str(installer_legacy)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            success("Legacy installation complete")
        else:
            error(f"Legacy installer failed: {result.stderr[:200]}")
    else:
        error("Legacy installer not found")


def uninstall():
    """Remove all traces of the plugin."""
    info("Uninstalling...")

    # Restore from backup if exists
    backups = list_backups()
    if backups:
        info(f"Found {len(backups)} backup(s). Restoring latest...")
        restore_backup(backups[0]["id"])

    # Remove sidecar config
    if CONFIG_DEST.exists():
        CONFIG_DEST.unlink()
        info("Removed feishu-sidecar.yaml")

    # Remove gateway patches
    if GATEWAY_DIR.exists():
        # Remove copied adapter files
        for f in ["sidecar_adapter.py", "streaming_adapter.py"]:
            fp = GATEWAY_DIR / f
            if fp.exists():
                fp.unlink()
                print(f"  Removed {f}")

    success("Uninstallation complete")


def check_status():
    """Check installation status."""
    print("═══════════════════════════════════════════════════════")
    print("  Feishu Streaming Card — Status Check")
    print("═══════════════════════════════════════════════════════")

    mode = detect_current_mode()
    print(f"  Current mode : {mode}")

    # Config
    if CONFIG_DEST.exists():
        print(f"  Config       : ✅ {CONFIG_DEST}")
    else:
        print(f"  Config       : ❌ Not found")

    # Sidecar process
    result = subprocess.run(["pgrep", "-f", "sidecar.server"], capture_output=True)
    if result.returncode == 0:
        print(f"  Sidecar      : ✅ Running (PID {result.stdout.strip().decode()})")
    else:
        print(f"  Sidecar      : ⚠️  Not running")

    # Gateway patch
    run_py = GATEWAY_DIR / "run.py"
    if run_py.exists():
        content = run_py.read_text()
        if "Feishu Streaming Card Sidecar" in content:
            print(f"  Gateway patch: ✅ Applied")
        else:
            print(f"  Gateway patch: ❌ Not patched")

    # Backups
    backups = list_backups()
    print(f"  Backups      : {len(backups)} available")

    print("")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Hermes Feishu Streaming Card Installer")
    parser.add_argument("--check", action="store_true", help="Check installation status")
    parser.add_argument("--list-backups", action="store_true", help="List all backups")
    parser.add_argument("--restore", action="store_true", help="Restore from latest backup")
    parser.add_argument("--mode", choices=["sidecar", "legacy"], help="Installation mode")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall completely")
    args = parser.parse_args()

    # Change to project directory
    os.chdir(PROJECT_ROOT)

    if args.check:
        check_status()
        sys.exit(0)

    if args.list_backups:
        backups = list_backups()
        if not backups:
            print("No backups found")
        else:
            print("Available backups:")
            for b in backups[:10]:
                print(f"  {b['id']} — {b['timestamp']} — {b.get('description','')}")
        sys.exit(0)

    if args.restore:
        backups = list_backups()
        if not backups:
            error("No backups available")
        restore_backup(backups[0]["id"])
        sys.exit(0)

    if args.uninstall:
        uninstall()
        sys.exit(0)

    # Interactive install
    print("═══════════════════════════════════════════════════════")
    print("  Feishu Streaming Card v2.0 — Installer")
    print("═══════════════════════════════════════════════════════")
    print("")
    print("Select installation mode:")
    print("  1) Sidecar mode (recommended) — Safe, isolated, no gateway injection")
    print("  2) Legacy mode — Direct injection (risky, may break)")
    print("  3) Uninstall")
    print("")

    current_mode = detect_current_mode()
    if current_mode != "none":
        print(f"Current mode: {current_mode}")

    choice = input("Enter choice [1/2/3]: ").strip()

    if choice == "1":
        check_requirements()
        install_sidecar_mode()
    elif choice == "2":
        check_requirements()
        install_legacy_mode()
    elif choice == "3":
        uninstall()
    else:
        error("Invalid choice")


if __name__ == "__main__":
    main()
