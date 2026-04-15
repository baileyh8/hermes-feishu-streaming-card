#!/usr/bin/env python3
"""
hermes-feishu-streaming-card installer.
=======================================
Applies the Feishu streaming card patch to an existing hermes-agent installation.

Usage:
    python installer.py [--hermes-dir DIR] [--uninstall]

Options:
    --hermes-dir DIR   Path to hermes-agent (default: auto-detect ~/.hermes/hermes-agent)
    --uninstall        Revert patches and restore backups
    --check            Only check patch status, don't apply
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("installer")


HERMES_DEFAULT = os.path.expanduser("~/.hermes/hermes-agent")


def detect_hermes_dir() -> str:
    """Find hermes-agent directory."""
    default = HERMES_DEFAULT
    if os.path.isdir(default):
        return default
    # Try current directory parent
    cwd = os.getcwd()
    if "hermes-agent" in cwd:
        parent = os.path.dirname(cwd.rstrip("/"))
        if os.path.basename(parent) == "hermes-agent" or os.path.isdir(f"{parent}/gateway"):
            return parent
    raise RuntimeError(
        f"hermes-agent not found at {default} or current directory. "
        "Use --hermes-dir to specify the path."
    )


def check_prerequisites() -> bool:
    """Check that required files exist in hermes-agent."""
    errors = []
    try:
        result = subprocess.run(
            ["python3", "-c", "import yaml; print('ok')"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip() != "ok":
            errors.append("python package 'pyyaml' is required: pip install pyyaml")
    except Exception:
        errors.append("python3 is required")

    try:
        result = subprocess.run(
            ["python3", "-c", "import regex; print('ok')"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip() != "ok":
            errors.append("python package 'regex' is required: pip install regex")
    except Exception:
        errors.append("python package 'regex' is required (for emoji detection)")

    if errors:
        for e in errors:
            log.error("  ✗ %s", e)
        return False
    return True


def apply_config(hermes_dir: str, greeting: str, enabled: bool = True, pending_timeout: int = 30) -> None:
    """Add or update feishu_streaming_card section in config.yaml.

    If the section already exists, updates it in-place.
    If not, appends it at the end of the file.
    """
    import re

    cfg_path = f"{hermes_dir}/config.yaml"

    section_yaml = f'''feishu_streaming_card:
  greeting: "{greeting}"
  enabled: {str(enabled).lower()}
  pending_timeout: {pending_timeout}'''

    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            content = f.read()
    else:
        content = ""

    # Check if section already exists
    if re.search(r"feishu_streaming_card:", content):
        # Replace existing section (find start/end of the section)
        new_lines = []
        lines = content.splitlines()
        in_section = False
        section_indent = None
        skip_next_blank = False

        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"feishu_streaming_card:", line):
                # Found the section start — replace until next top-level key
                new_lines.append(line)
                section_indent = len(line) - len(line.lstrip())
                in_section = True
                # Replace with new section content
                for sec_line in section_yaml.splitlines():
                    new_lines.append(sec_line)
                # Skip original section lines
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if not next_line.strip():
                        i += 1
                        continue
                    indent = len(next_line) - len(next_line.lstrip())
                    if indent <= section_indent and re.match(r"\w", next_line.lstrip()):
                        break  # next section
                    i += 1
                continue
            else:
                new_lines.append(line)
            i += 1

        with open(cfg_path, "w") as f:
            f.write("\n".join(new_lines) + "\n")
    else:
        with open(cfg_path, "a") as f:
            f.write(f"\n# Feishu Streaming Card\n{section_yaml}\n")

    log.info("  ✓ Updated config.yaml")


def do_install(hermes_dir: str, greeting: str, enabled: bool, pending_timeout: int) -> None:
    """Apply all patches."""
    feishu_py = f"{hermes_dir}/gateway/platforms/feishu.py"
    run_py = f"{hermes_dir}/gateway/run.py"

    if not os.path.exists(feishu_py):
        log.error("  ✗ feishu.py not found at %s", feishu_py)
        sys.exit(1)
    if not os.path.exists(run_py):
        log.error("  ✗ run.py not found at %s", run_py)
        sys.exit(1)

    log.info("Installing Feishu Streaming Card to:")
    log.info("  hermes-dir: %s", hermes_dir)
    log.info("  greeting:   %s", greeting)

    # Check prerequisites
    log.info("\nChecking prerequisites...")
    if not check_prerequisites():
        log.error("\nPrerequisites not met. Install required packages and retry.")
        sys.exit(1)
    log.info("  ✓ All prerequisites met")

    # Patch feishu.py
    log.info("\nPatching feishu.py...")
    sys.path.insert(0, os.path.dirname(__file__))
    from patch.feishu_patch import apply_patch as apply_feishu_patch
    from patch.run_patch import patch_run_py

    results = apply_feishu_patch(feishu_py, hermes_dir)
    for status, msg in results:
        prefix = "  ✓" if status == "OK" else "  ✗"
        log.info("  %s %s", prefix, msg)

    # Patch run.py
    log.info("\nPatching run.py...")
    results = patch_run_py(run_py, hermes_dir)
    for status, msg in results:
        prefix = "  ✓" if status == "OK" else "  ✗"
        log.info("  %s %s", prefix, msg)

    # Update config
    log.info("\nUpdating config.yaml...")
    try:
        apply_config(hermes_dir, greeting, enabled, pending_timeout)
    except Exception as e:
        log.warning("  ⚠ Could not update config.yaml: %s", e)
        log.info("    Please add this to config.yaml manually:")
        log.info("    feishu_streaming_card:")
        log.info("      greeting: '%s'", greeting)
        log.info("      enabled: true", )
        log.info("      pending_timeout: 30")

    log.info("\n✅ Installation complete!")
    log.info("\nNext steps:")
    log.info("  1. Restart hermes: hermes gateway restart")
    log.info("  2. Send a message to your Feishu bot")
    log.info("  3. A streaming card with typewriter effect will appear")
    log.info("\nTo customize the greeting, edit config.yaml:")
    log.info("  feishu_streaming_card:")
    log.info("    greeting: 'Your custom greeting here'")


def do_uninstall(hermes_dir: str) -> None:
    """Restore backups."""
    feishu_py = f"{hermes_dir}/gateway/platforms/feishu.py"
    run_py = f"{hermes_dir}/gateway/run.py"

    restored = []
    for path in [feishu_py, run_py]:
        bak = path + ".fscbak"
        if os.path.exists(bak):
            shutil.copy2(bak, path)
            os.remove(bak)
            restored.append(path)
            log.info("  ✓ Restored %s", path)
        else:
            bak2 = path + ".bak"
            if os.path.exists(bak2):
                shutil.copy2(bak2, path)
                os.remove(bak2)
                restored.append(path)
                log.info("  ✓ Restored %s", path)
            else:
                log.info("  ℹ No backup found for %s (may not be patched)", os.path.basename(path))

    log.info("\n✅ Uninstallation complete! Restored %d file(s).", len(restored))
    log.info("Restart hermes to see changes.")


def do_check(hermes_dir: str) -> None:
    """Check patch status."""
    feishu_py = f"{hermes_dir}/gateway/platforms/feishu.py"
    run_py = f"{hermes_dir}/gateway/run.py"

    with open(feishu_py) as f:
        feishu_content = f.read()
    with open(run_py) as f:
        run_content = f.read()

    checks = [
        ("feishu.py: streaming state init", "_streaming_card" in feishu_content and "self._streaming_card: dict = {}" in feishu_content),
        ("feishu.py: send_streaming_card method", "def send_streaming_card" in feishu_content),
        ("feishu.py: streaming routing in send()", "Feishu Streaming Card — routing" in feishu_content),
        ("feishu.py: streaming routing in edit_message()", "If streaming card is active for this chat" in feishu_content),
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
        log.info("\n✅ All checks passed — patch is installed correctly.")
    else:
        log.info("\n⚠ Some checks failed — patch may be incomplete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Feishu Streaming Card for hermes-agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python installer.py                                    # auto-detect hermes-agent
  python installer.py --hermes-dir /path/to/hermes-agent # specify path
  python installer.py --check                            # check patch status
  python installer.py --uninstall                       # revert to original
        """,
    )
    parser.add_argument(
        "--hermes-dir", default=None,
        help=f"Path to hermes-agent (default: {HERMES_DEFAULT})",
    )
    parser.add_argument(
        "--greeting", default="主人，苏菲为您服务！",
        help="Card header greeting text (default: 主人，苏菲为您服务！)",
    )
    parser.add_argument(
        "--enabled", type=lambda x: x.lower() in ("1", "true", "yes"), default=True,
        help="Enable streaming card (default: true)",
    )
    parser.add_argument(
        "--pending-timeout", type=int, default=30,
        help="send_progress timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Revert patches using backups",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Only check patch status, don't install",
    )

    args = parser.parse_args()

    hermes_dir = args.hermes_dir or detect_hermes_dir()

    if args.uninstall:
        do_uninstall(hermes_dir)
    elif args.check:
        do_check(hermes_dir)
    else:
        do_install(hermes_dir, args.greeting, args.enabled, args.pending_timeout)


if __name__ == "__main__":
    main()
