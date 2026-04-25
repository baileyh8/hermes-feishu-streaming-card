from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid4
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
    original: str | None = None
    manifest_existed = manifest_path.exists()
    backup_existed = backup_path.exists()

    try:
        original = run_py.read_text(encoding="utf-8")
        _validate_existing_install_state(run_py, backup_path, manifest_path)
        patched = apply_patch(original)
        if not backup_existed:
            _atomic_write_text(backup_path, original)
        if patched != original:
            _atomic_write_text(run_py, patched)
        _write_manifest(manifest_path, run_py, backup_path)
    except (OSError, UnicodeError, ValueError) as exc:
        _rollback_install(
            run_py,
            original,
            backup_path,
            backup_existed,
            manifest_path,
            manifest_existed,
        )
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
    manifest_path = _manifest_path(hermes_root)
    if run_py.is_symlink():
        raise ValueError("gateway/run.py must not be a symlink")
    if backup_path.exists():
        manifest = _read_manifest(manifest_path)
        if manifest is None:
            backup_text = backup_path.read_text(encoding="utf-8")
            _validate_backup_contains_original(backup_text, "restore")
            if run_py.exists() and run_py.read_text(encoding="utf-8") == backup_text:
                _clear_install_state(backup_path, manifest_path)
                return

            patched_backup = apply_patch(backup_text)
            if not run_py.exists() or run_py.read_text(encoding="utf-8") != patched_backup:
                raise ValueError("run.py changed since install; refusing to restore")

            _atomic_write_text(run_py, backup_text)
            _clear_install_state(backup_path, manifest_path)
            return

        backup_text = _validate_restorable_install_state(
            run_py, backup_path, manifest, "restore"
        )
        _atomic_write_text(run_py, backup_text)
        _clear_install_state(backup_path, manifest_path)
        return
    if not run_py.exists():
        return

    current = run_py.read_text(encoding="utf-8")
    if manifest_path.exists() and remove_patch(current) == current:
        _clear_install_state(backup_path, manifest_path)
        return

    manifest = _read_manifest(manifest_path)
    if manifest is not None:
        patched_sha256 = manifest.get("patched_sha256")
        if not isinstance(patched_sha256, str) or not patched_sha256:
            if remove_patch(current) != current:
                raise ValueError("manifest missing patched run.py sha256")
        elif file_sha256(run_py) != patched_sha256:
            raise ValueError("run.py changed since install; refusing to restore")

    restored = _restore_by_removing_owned_patch(run_py, current)
    if restored or backup_path.exists() or manifest_path.exists():
        _clear_install_state(backup_path, manifest_path)


def _backup_path(run_py: Path) -> Path:
    return run_py.with_name(f"{run_py.name}{BACKUP_SUFFIX}")


def _manifest_path(hermes_root: Path) -> Path:
    return hermes_root / MANIFEST_NAME


def _clear_install_state(backup_path: Path, manifest_path: Path) -> None:
    backup_path.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)


def _write_manifest(manifest_path: Path, run_py: Path, backup_path: Path) -> None:
    manifest = {
        "run_py": str(run_py.relative_to(manifest_path.parent)),
        "patched_sha256": file_sha256(run_py),
        "backup": str(backup_path.relative_to(manifest_path.parent)),
        "backup_sha256": file_sha256(backup_path),
    }
    _atomic_write_text(manifest_path, json.dumps(manifest, sort_keys=True) + "\n")


def _rollback_install(
    run_py: Path,
    original: str | None,
    backup_path: Path,
    backup_existed: bool,
    manifest_path: Path,
    manifest_existed: bool,
) -> None:
    if original is not None:
        try:
            _atomic_write_text(run_py, original)
        except OSError:
            pass
    if not backup_existed:
        try:
            backup_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    if not manifest_existed:
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _validate_existing_install_state(
    run_py: Path, backup_path: Path, manifest_path: Path
) -> None:
    backup_exists = backup_path.exists()
    manifest_exists = manifest_path.exists()
    current = run_py.read_text(encoding="utf-8")

    if not backup_exists and not manifest_exists:
        if remove_patch(current) != current:
            raise ValueError(
                "install state incomplete; run.py already contains patch; "
                "restore or remove patch before installing"
            )
        return

    if backup_exists and not manifest_exists:
        raise ValueError(
            "install state incomplete; manifest missing; "
            "restore or remove patch before installing"
        )

    if not backup_exists:
        manifest = _read_manifest(manifest_path)
        _validate_manifest_matches_run_py(run_py, manifest)
        raise ValueError("install state incomplete; backup missing; refusing to install")

    manifest = _read_manifest(manifest_path)
    _validate_complete_install_state(run_py, backup_path, manifest, "install")


def _validate_manifest_matches_run_py(
    run_py: Path, manifest: dict[str, object] | None
) -> None:
    if manifest is None:
        return
    patched_sha256 = manifest.get("patched_sha256")
    if not isinstance(patched_sha256, str) or not patched_sha256:
        raise ValueError("manifest missing patched run.py sha256")
    if file_sha256(run_py) != patched_sha256:
        raise ValueError("run.py changed since install; refusing to install")


def _validate_complete_install_state(
    run_py: Path,
    backup_path: Path,
    manifest: dict[str, object] | None,
    operation: str,
) -> str:
    if manifest is None:
        backup_text = backup_path.read_text(encoding="utf-8")
        _validate_backup_contains_original(backup_text, operation)
        patched_backup = apply_patch(backup_text)
        if not run_py.exists() or run_py.read_text(encoding="utf-8") != patched_backup:
            raise ValueError(f"run.py changed since install; refusing to {operation}")
        return backup_text

    patched_sha256 = manifest.get("patched_sha256")
    if not isinstance(patched_sha256, str) or not patched_sha256:
        raise ValueError("manifest missing patched run.py sha256")
    if file_sha256(run_py) != patched_sha256:
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    backup_sha256 = manifest.get("backup_sha256")
    if not isinstance(backup_sha256, str) or not backup_sha256:
        raise ValueError("manifest missing backup sha256")
    if file_sha256(backup_path) != backup_sha256:
        raise ValueError(f"backup changed since install; refusing to {operation}")

    current = run_py.read_text(encoding="utf-8")
    backup_text = backup_path.read_text(encoding="utf-8")
    _validate_backup_contains_original(backup_text, operation)
    try:
        patched_backup = apply_patch(backup_text)
    except ValueError as exc:
        raise ValueError(
            f"backup changed since install; refusing to {operation}"
        ) from exc
    if patched_backup != current:
        raise ValueError(f"backup changed since install; refusing to {operation}")
    return backup_text


def _validate_restorable_install_state(
    run_py: Path,
    backup_path: Path,
    manifest: dict[str, object],
    operation: str,
) -> str:
    backup_sha256 = manifest.get("backup_sha256")
    if not isinstance(backup_sha256, str) or not backup_sha256:
        raise ValueError("manifest missing backup sha256")
    if file_sha256(backup_path) != backup_sha256:
        raise ValueError(f"backup changed since install; refusing to {operation}")

    backup_text = backup_path.read_text(encoding="utf-8")
    _validate_backup_contains_original(backup_text, operation)
    if not run_py.exists():
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    current = run_py.read_text(encoding="utf-8")
    if current == backup_text:
        return backup_text

    patched_sha256 = manifest.get("patched_sha256")
    if not isinstance(patched_sha256, str) or not patched_sha256:
        raise ValueError("manifest missing patched run.py sha256")
    if file_sha256(run_py) != patched_sha256:
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    try:
        patched_backup = apply_patch(backup_text)
    except ValueError as exc:
        raise ValueError(
            f"backup changed since install; refusing to {operation}"
        ) from exc
    if patched_backup != current:
        raise ValueError(f"backup changed since install; refusing to {operation}")
    return backup_text


def _validate_backup_contains_original(backup_text: str, operation: str) -> None:
    if remove_patch(backup_text) != backup_text:
        raise ValueError(f"backup changed since install; refusing to {operation}")


def _read_manifest(manifest_path: Path) -> dict[str, object] | None:
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("manifest could not be parsed") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest could not be parsed")
    return manifest


def _atomic_write_text(path: Path, contents: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(contents, encoding="utf-8")
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _restore_by_removing_owned_patch(run_py: Path, current: str | None = None) -> bool:
    if not run_py.exists():
        return False
    if current is None:
        current = run_py.read_text(encoding="utf-8")
    restored = remove_patch(current)
    if restored == current:
        return False
    _atomic_write_text(run_py, restored)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
