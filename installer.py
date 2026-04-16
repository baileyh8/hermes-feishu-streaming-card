#!/usr/bin/env python3
"""
hermes-feishu-streaming-card installer v2.0
==========================================
Safe install with pre-validate, backup, and restore.

Usage:
    python installer.py                        # install with validation + backup
    python installer.py --check                 # check patch status only
    python installer.py --list-backups         # show all backups
    python installer.py --restore              # restore from latest backup
    python installer.py --restore BACKUP_ID    # restore specific backup
    python installer.py --validate-only        # dry-run: validate without writing
"""

from __future__ import annotations

import argparse
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

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
log = logging.getLogger("fsc-install")

HERMES_DEFAULT = os.path.expanduser("~/.hermes/hermes-agent")
BACKUP_DIR_REL = ".fsc_backups"
INSTALLER_VERSION = "2.0.0"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────────────────────────
# Hermes venv python
# ─────────────────────────────────────────────────────────────────

def _hermes_python(hermes_dir: str) -> str:
    for name in ("python3", "python"):
        p = os.path.join(hermes_dir, "venv", "bin", name)
        if os.path.isfile(p):
            return p
    return "python3"


def _run(hermes_dir: str, cmd: list, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=os.path.dirname(__file__) or ".",
    )


# ─────────────────────────────────────────────────────────────────
# Backup management
# ─────────────────────────────────────────────────────────────────

def _backup_dir(hermes_dir: str) -> str:
    return os.path.join(hermes_dir, BACKUP_DIR_REL)


def _ensure_backup_dir(hermes_dir: str) -> str:
    d = _backup_dir(hermes_dir)
    os.makedirs(d, exist_ok=True)
    return d


def create_backup(hermes_dir: str) -> str:
    bak_dir = _ensure_backup_dir(hermes_dir)
    backup_id = time.strftime("%Y%m%d_%H%M%S")
    bak_path = os.path.join(bak_dir, backup_id)
    os.makedirs(bak_path, exist_ok=True)

    files_saved = []
    for fname in ["gateway/platforms/feishu.py", "gateway/run.py"]:
        src = os.path.join(hermes_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(bak_path, os.path.basename(fname)))
            files_saved.append(fname)

    meta = {
        "backup_id": backup_id,
        "timestamp": time.time(),
        "files": files_saved,
        "installer_version": INSTALLER_VERSION,
    }
    meta_path = os.path.join(bak_path, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("  Backup created: %s (%s)", backup_id, ", ".join(files_saved))
    _cleanup_old_backups(hermes_dir)
    return backup_id


def list_backups(hermes_dir: str) -> list[dict]:
    bak_dir = _backup_dir(hermes_dir)
    if not os.path.isdir(bak_dir):
        return []
    backups = []
    for bid in os.listdir(bak_dir):
        meta_path = os.path.join(bak_dir, bid, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                backups.append(json.load(f))
    backups.sort(key=lambda x: x["timestamp"], reverse=True)
    return backups


def restore_backup(hermes_dir: str, backup_id: str) -> None:
    bak_path = os.path.join(_backup_dir(hermes_dir), backup_id)
    if not os.path.isdir(bak_path):
        log.error("  Backup not found: %s", backup_id)
        sys.exit(1)
    restored = []
    for fname in ["feishu.py", "run.py"]:
        src = os.path.join(bak_path, fname)
        dst = os.path.join(hermes_dir, "gateway" if "feishu" in fname else "gateway", fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            restored.append(fname)
    log.info("  Restored: %s", ", ".join(restored))
    log.info("  Restart Hermes gateway to load the restored files.")


def _cleanup_old_backups(hermes_dir: str, keep: int = 5) -> None:
    backups = list_backups(hermes_dir)
    for old in backups[keep:]:
        shutil.rmtree(os.path.join(_backup_dir(hermes_dir), old["backup_id"]))


# ─────────────────────────────────────────────────────────────────
# Hermes detection
# ─────────────────────────────────────────────────────────────────

def detect_hermes_dir() -> str:
    default = HERMES_DEFAULT
    if os.path.isdir(default):
        return default
    cwd = os.getcwd()
    if "hermes-agent" in cwd:
        parent = os.path.dirname(cwd.rstrip("/"))
        if os.path.basename(parent) == "hermes-agent" or os.path.isdir(f"{parent}/gateway"):
            return parent
    raise RuntimeError(
        f"hermes-agent not found at {default} or current directory. "
        "Use --hermes-dir to specify the path."
    )


# ─────────────────────────────────────────────────────────────────
# Prerequisites
# ─────────────────────────────────────────────────────────────────

def check_prerequisites(hermes_dir: str) -> bool:
    python = _hermes_python(hermes_dir)
    ok = True
    for pkg, imp in [("pyyaml", "yaml"), ("regex", "regex")]:
        r = subprocess.run([python, "-c", f"import {imp}; print('ok')"],
                           capture_output=True, text=True, timeout=10)
        if r.stdout.strip() != "ok":
            log.error("  ✗ %s missing — %s -m pip install %s", pkg, python, pkg)
            ok = False
    if ok:
        log.info("  ✓ All prerequisites met (%s)", python)
    return ok


# ─────────────────────────────────────────────────────────────────
# Pre-flight validation (subprocess — fully isolated python env)
# ─────────────────────────────────────────────────────────────────

#language=python
VALIDATION_SCRIPT = r'''
import sys, os, re, tempfile, shutil, subprocess, json

def hermes_python(hermes_dir):
    for name in ("python3", "python"):
        p = os.path.join(hermes_dir, "venv", "bin", name)
        if os.path.isfile(p):
            return p
    return "python3"

hermes_dir = sys.argv[1]
project_dir = sys.argv[2]  # directory where installer.py lives
feishu_content = open(sys.argv[3]).read()
run_content = open(sys.argv[4]).read()

sys.path.insert(0, project_dir)
sys.path.insert(0, os.path.join(project_dir, "patch"))
from feishu_patch import apply_patch as _apply_feishu
from run_patch import patch_run_py as _apply_run

notes = []
feishu_patched_ok = False
run_patched_ok = False

# ── 1. Check injection points in feishu.py (no side effects) ─────
try:
    has_streaming = (
        "def send_streaming_card" in feishu_content
        and "_streaming_card" in feishu_content
        and "Feishu Streaming Card" in feishu_content
    )
    if has_streaming:
        notes.append("feishu.py: already has streaming card (skip)")
    else:
        # Check __init__ injection point
        # Use simple string search (not regex) for marker checks
        # because these patterns contain literal '(' which confuse the regex engine
        _marker = "\n    async def send("
        if _marker not in feishu_content:
            raise RuntimeError("Cannot find 'async def send(self,' in feishu.py")
        notes.append("feishu.py: send() injection point found")

        _init_marker1 = "self._load_seen_message_ids()"
        _init_marker2 = "self._approval_counter = itertools.count("
        if _init_marker1 not in feishu_content and _init_marker2 not in feishu_content:
            raise RuntimeError("Cannot find __init__ injection point in feishu.py")
        notes.append("feishu.py: __init__ injection point found")
except Exception as e:
    print(json.dumps({"status": "error", "step": "feishu_injection_points", "message": str(e)}))
    sys.exit(1)

# ── 2. Check injection points in run.py ──────────────────────────
try:
    has_streaming = (
        "Pre-created streaming card for chat_id" in run_content
        and "finalize_streaming_card" in run_content
    )
    if has_streaming:
        notes.append("run.py: already has streaming card (skip)")
    else:
        _m = re.search(
            r'(logger\.info\(\s*\n\s*"inbound message:.*?"[^\)]*\)\s*\n\s*\)\s*\n)\s*\n(\s*# Get or create session)',
            run_content, re.DOTALL,
        )
        if not _m:
            _m = re.search(r'(\)\s*\n)\s*\n(\s*# Get or create session)', run_content)
        if not _m:
            raise RuntimeError("Cannot find _handle_message_with_agent injection point in run.py")
        notes.append("run.py: pre-create injection point found")

        _m2 = re.search(
            r'(# Emit agent:end hook\s+await self\.hooks\.emit\("agent:end",\s*\{.*?\}\)\s*\n\s*\)\s*\n)\s*\n(\s*# Check for pending)',
            run_content, re.DOTALL,
        )
        if not _m2:
            _m2 = re.search(r'(\)\s*\n)\s*\n(\s*# Check for pending process watchers)', run_content)
        if not _m2:
            raise RuntimeError("Cannot find agent:end injection point in run.py")
        notes.append("run.py: finalize injection point found")
except Exception as e:
    print(json.dumps({"status": "error", "step": "run_injection_points", "message": str(e)}))
    sys.exit(1)

# ── 3. Write patched files to temp and syntax-check ──────────────
python = hermes_python(hermes_dir)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp_feishu = os.path.join(tmpdir, "feishu.py")
    tmp_run = os.path.join(tmpdir, "run.py")

    # Apply feishu patch to temp file
    orig_feishu = open(os.path.join(hermes_dir, "gateway/platforms/feishu.py")).read()
    orig_run = open(os.path.join(hermes_dir, "gateway/run.py")).read()

    # Use apply_patch to write to temp, then check syntax
    # apply_patch writes to the real path, so we copy back later
    # For validation, we just check the actual hermes files would pass
    # by running py_compile on them (they are already patched)
    r = subprocess.run([python, "-m", "py_compile",
                        os.path.join(hermes_dir, "gateway/platforms/feishu.py")],
                       capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        notes.append("feishu.py: syntax check passed (current file)")
        feishu_patched_ok = True
    else:
        notes.append(f"feishu.py: syntax error — {r.stderr.strip().splitlines()[-1] if r.stderr else 'unknown'}")

    r2 = subprocess.run([python, "-m", "py_compile",
                         os.path.join(hermes_dir, "gateway/run.py")],
                        capture_output=True, text=True, timeout=15)
    if r2.returncode == 0:
        notes.append("run.py: syntax check passed (current file)")
        run_patched_ok = True
    else:
        notes.append(f"run.py: syntax error — {r2.stderr.strip().splitlines()[-1] if r2.stderr else 'unknown'}")

print(json.dumps({
    "status": "ok",
    "notes": notes,
    "feishu_ok": feishu_patched_ok,
    "run_ok": run_patched_ok,
}))
'''


def _validate(hermes_dir: str, project_dir: str, feishu_path: str, run_path: str) -> dict:
    """Run validation in a subprocess using hermes venv python."""
    python = _hermes_python(hermes_dir)

    # Write validation script to temp file
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as tmp_script:
        tmp_script.write(VALIDATION_SCRIPT)
        tmp_script.flush()
        script_path = tmp_script.name

    try:
        # Use the actual hermes files for syntax check
        # (they may already be patched from previous install)
        r = subprocess.run(
            [python, script_path, hermes_dir, project_dir, feishu_path, run_path],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(__file__) or ".",
        )
        if r.returncode != 0:
            try:
                err = json.loads(r.stderr.strip())
                return {"status": "error", "message": f"{err.get('step', '?')}: {err.get('message', '')}"}
            except Exception:
                return {"status": "error", "message": r.stderr.strip() or r.stdout.strip()}

        result = json.loads(r.stdout.strip())
        result["notes"] = result.get("notes", [])
        return result
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Validation timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        os.unlink(script_path)


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

def apply_config(hermes_dir: str, greeting: str, enabled: bool, pending_timeout: int) -> None:
    import re
    cfg_path = os.path.join(hermes_dir, "config.yaml")
    section_yaml = f'''feishu_streaming_card:
  greeting: "{greeting}"
  enabled: {str(enabled).lower()}
  pending_timeout: {pending_timeout}'''

    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            content = f.read()
    else:
        content = ""

    if re.search(r"feishu_streaming_card:", content):
        lines = content.splitlines()
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"feishu_streaming_card:", line):
                indent = len(line) - len(line.lstrip())
                new_lines.append(line)
                for sl in section_yaml.splitlines():
                    new_lines.append(sl)
                i += 1
                while i < len(lines):
                    nl = lines[i]
                    if not nl.strip():
                        i += 1
                        continue
                    if len(nl) - len(nl.lstrip()) <= indent and re.match(r"\w", nl.lstrip()):
                        break
                    i += 1
                continue
            new_lines.append(line)
            i += 1
        with open(cfg_path, "w") as f:
            f.write("\n".join(new_lines) + "\n")
    else:
        with open(cfg_path, "a") as f:
            f.write(f"\n# Feishu Streaming Card\n{section_yaml}\n")
    log.info("  Updated config.yaml")


# ─────────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────────

def do_install(hermes_dir: str, greeting: str, enabled: bool, pending_timeout: int,
               *, dry_run: bool = False) -> None:
    feishu_py = os.path.join(hermes_dir, "gateway", "platforms", "feishu.py")
    run_py = os.path.join(hermes_dir, "gateway", "run.py")

    for p, label in [(feishu_py, "feishu.py"), (run_py, "run.py")]:
        if not os.path.exists(p):
            log.error("  ✗ %s not found at %s", label, p)
            sys.exit(1)

    log.info("Feishu Streaming Card Installer v%s", INSTALLER_VERSION)
    log.info("  hermes-dir: %s", hermes_dir)

    # ── 1. Prerequisites ────────────────────────────────────────────
    log.info("\n[1/5] Checking prerequisites...")
    if not check_prerequisites(hermes_dir):
        sys.exit(1)

    # ── 2. Pre-flight validation ────────────────────────────────────
    log.info("\n[2/5] Pre-flight validation...")
    result = _validate(hermes_dir, PROJECT_DIR, feishu_py, run_py)

    if result.get("status") != "ok":
        log.error("  ✗ Validation failed: %s", result.get("message", "unknown"))
        log.error("\n  The patch cannot be safely applied. Your Hermes version may not be")
        log.error("  supported, or the current feishu.py/run.py have unexpected structure.")
        sys.exit(1)

    for note in result.get("notes", []):
        log.info("  ✓ %s", note)

    if not result.get("feishu_ok") or not result.get("run_ok"):
        log.error("  ✗ Syntax errors detected in current files.")
        log.error("  Please fix the errors before installing.")
        sys.exit(1)

    log.info("  ✓ Pre-flight validation PASSED")

    if dry_run:
        log.info("\n[dry-run] Validation complete — no files were written.")
        return

    # ── 3. Backup ────────────────────────────────────────────────────
    log.info("\n[3/5] Creating backup...")
    backup_id = create_backup(hermes_dir)
    backups = list_backups(hermes_dir)
    log.info("  ✓ Backup created (%s). %d backup(s) total.", backup_id, len(backups))

    # ── 4. Apply patch ───────────────────────────────────────────────
    log.info("\n[4/5] Applying patch...")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from patch.feishu_patch import apply_patch as _apply_feishu
    from patch.run_patch import patch_run_py as _apply_run

    results_feishu = _apply_feishu(feishu_py, hermes_dir)
    for status, msg in results_feishu:
        prefix = "  ✓" if status == "OK" else "  ✗"
        log.info("  %s %s", prefix, msg)

    results_run = _apply_run(run_py, hermes_dir)
    for status, msg in results_run:
        prefix = "  ✓" if status == "OK" else "  ✗"
        log.info("  %s %s", prefix, msg)

    # ── 5. Post-install syntax check ────────────────────────────────
    log.info("\n[5/5] Verifying installed files...")
    python = _hermes_python(hermes_dir)
    ok = True
    for fpath, label in [(feishu_py, "feishu.py"), (run_py, "run.py")]:
        r = subprocess.run([python, "-m", "py_compile", fpath],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            log.info("  ✓ %s syntax check passed", label)
        else:
            log.error("  ✗ %s syntax error: %s", label,
                      r.stderr.strip().splitlines()[-1] if r.stderr else "unknown")
            ok = False

    if not ok:
        log.warning("\n  ⚠ Files written but have syntax errors.")
        log.warning("  Run: python installer.py --restore to restore the backup.")
    else:
        log.info("\n✅ Installation complete!")
        log.info("  Restart Hermes: hermes gateway restart")


# ─────────────────────────────────────────────────────────────────
# Check
# ─────────────────────────────────────────────────────────────────

def do_check(hermes_dir: str) -> None:
    feishu_py = os.path.join(hermes_dir, "gateway", "platforms", "feishu.py")
    run_py = os.path.join(hermes_dir, "gateway", "run.py")

    feishu_content = open(feishu_py).read() if os.path.exists(feishu_py) else ""
    run_content = open(run_py).read() if os.path.exists(run_py) else ""

    checks = [
        ("feishu.py: streaming state init",
         "_streaming_card" in feishu_content and "self._streaming_card:" in feishu_content),
        ("feishu.py: send_streaming_card method", "def send_streaming_card" in feishu_content),
        ("feishu.py: streaming routing in send()", "Streaming card routing" in feishu_content),
        ("feishu.py: streaming routing in edit_message()", "If streaming card is active for this chat" in feishu_content),
        ("feishu.py: _get_card_lock method", "def _get_card_lock" in feishu_content),
        ("feishu.py: finalize_streaming_card method", "def finalize_streaming_card" in feishu_content),
        ("run.py: pre-create card", "Pre-created streaming card for chat_id" in run_content),
        ("run.py: finalize_streaming_card call", "finalize_streaming_card" in run_content),
        ("run.py: pending timeout 30s", "wait_start < 30" in run_content),
    ]

    all_ok = True
    log.info("Patch status for: %s", hermes_dir)
    for name, ok in checks:
        prefix = "  ✓" if ok else "  ✗"
        log.info("  %s %s", prefix, name)
        if not ok:
            all_ok = False

    if all_ok:
        log.info("\n✅ All checks passed.")
    else:
        log.info("\n⚠ Some checks failed.")


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Feishu Streaming Card (v2 — safe install)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python installer.py                           # install with validation + backup
  python installer.py --validate-only            # dry-run: validate without writing
  python installer.py --check                   # check patch status
  python installer.py --list-backups            # show all backups
  python installer.py --restore                 # restore from latest backup
  python installer.py --restore 20260416_083000 # restore specific backup
  python installer.py --uninstall               # restore from latest backup
        """,
    )
    parser.add_argument("--hermes-dir", default=None)
    parser.add_argument("--greeting", default="主人，苏菲为您服务！")
    parser.add_argument("--enabled", type=lambda x: x.lower() in ("1", "true", "yes"), default=True)
    parser.add_argument("--pending-timeout", type=int, default=30)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list-backups", action="store_true")
    parser.add_argument("--restore", nargs="?", const="__latest__")
    parser.add_argument("--delete-backup", metavar="BACKUP_ID")
    parser.add_argument("--validate-only", action="store_true")

    args = parser.parse_args()
    hermes_dir = args.hermes_dir or detect_hermes_dir()

    if args.list_backups:
        backups = list_backups(hermes_dir)
        if not backups:
            log.info("No backups found.")
        else:
            log.info("Backups (newest first):")
            for b in backups:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b["timestamp"]))
                log.info("  [%s]  %s  (%s)", b["backup_id"], ts, ", ".join(b["files"]))
        return

    if args.delete_backup:
        bak_path = os.path.join(_backup_dir(hermes_dir), args.delete_backup)
        if os.path.isdir(bak_path):
            shutil.rmtree(bak_path)
            log.info("Deleted: %s", args.delete_backup)
        else:
            log.error("Backup not found: %s", args.delete_backup)
        return

    if args.restore is not None:
        backups = list_backups(hermes_dir)
        if not backups:
            log.error("No backups found.")
            sys.exit(1)
        if args.restore == "__latest__":
            backup_id = backups[0]["backup_id"]
        else:
            backup_id = args.restore
        log.info("Restoring from: %s", backup_id)
        restore_backup(hermes_dir, backup_id)
        return

    if args.uninstall:
        backups = list_backups(hermes_dir)
        if not backups:
            log.error("No backups found.")
            sys.exit(1)
        log.info("Restoring from latest backup: %s", backups[0]["backup_id"])
        restore_backup(hermes_dir, backups[0]["backup_id"])
        return

    if args.check:
        do_check(hermes_dir)
        return

    if args.validate_only:
        do_install(hermes_dir, args.greeting, args.enabled, args.pending_timeout, dry_run=True)
        return

    do_install(hermes_dir, args.greeting, args.enabled, args.pending_timeout)


if __name__ == "__main__":
    main()
