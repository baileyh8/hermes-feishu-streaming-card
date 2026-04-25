from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hermes_feishu_card.config import load_config
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.manifest import file_sha256
from hermes_feishu_card.install.patcher import apply_patch, remove_patch


BACKUP_SUFFIX = ".hermes_feishu_card.bak"
MANIFEST_NAME = ".hermes_feishu_card_manifest"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "status":
        return _run_status()
    if args.command == "install":
        return _run_install(args)
    if args.command == "restore":
        return _run_restore(args)
    if args.command == "uninstall":
        return _run_uninstall(args)

    parser.print_help()
    if argv == []:
        return 0
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-feishu-card")
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--config", required=True)
    doctor.add_argument("--skip-hermes", action="store_true")

    subparsers.add_parser("status")
    for command in ("install", "restore", "uninstall"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--hermes-dir", required=True)
        command_parser.add_argument("--yes", action="store_true", required=True)
    return parser


def _run_doctor(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    host = config["server"]["host"]
    port = config["server"]["port"]
    print("doctor: ok")
    print(f"sidecar: {host}:{port}")
    if args.skip_hermes:
        print("hermes: skipped")
    return 0


def _run_status() -> int:
    print("status: process management not implemented")
    return 0


def _run_install(args: argparse.Namespace) -> int:
    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(detection.reason, file=sys.stderr)
        return 1

    run_py = detection.run_py
    backup_path = _backup_path(run_py)
    manifest_path = _manifest_path(detection.root)

    try:
        original = run_py.read_text(encoding="utf-8")
        patched = apply_patch(original)
        if not backup_path.exists():
            backup_path.write_text(original, encoding="utf-8")
        if patched != original:
            run_py.write_text(patched, encoding="utf-8")
        _write_manifest(manifest_path, run_py, backup_path)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("install ok")
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    try:
        _restore(Path(args.hermes_dir))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("restore ok")
    return 0


def _run_uninstall(args: argparse.Namespace) -> int:
    try:
        _restore(Path(args.hermes_dir))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("uninstall ok")
    return 0


def _restore(hermes_root: Path) -> None:
    run_py = hermes_root / "gateway" / "run.py"
    backup_path = _backup_path(run_py)
    if backup_path.exists():
        run_py.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        return
    if not run_py.exists():
        return

    restored = remove_patch(run_py.read_text(encoding="utf-8"))
    run_py.write_text(restored, encoding="utf-8")


def _backup_path(run_py: Path) -> Path:
    return run_py.with_name(f"{run_py.name}{BACKUP_SUFFIX}")


def _manifest_path(hermes_root: Path) -> Path:
    return hermes_root / MANIFEST_NAME


def _write_manifest(manifest_path: Path, run_py: Path, backup_path: Path) -> None:
    manifest = {
        "run_py": str(run_py.relative_to(manifest_path.parent)),
        "patched_sha256": file_sha256(run_py),
        "backup": str(backup_path.relative_to(manifest_path.parent)),
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
