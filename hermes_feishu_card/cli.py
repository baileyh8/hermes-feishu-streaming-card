from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from uuid import uuid4
from pathlib import Path

from hermes_feishu_card.config import load_config
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.feishu_client import FeishuAPIError, FeishuClient, FeishuClientConfig
from hermes_feishu_card.install.detect import HermesDetection, detect_hermes
from hermes_feishu_card.install.manifest import file_sha256
from hermes_feishu_card.install.patcher import (
    apply_patch,
    remove_patch,
    remove_patch_lenient,
)
from hermes_feishu_card.process import start_sidecar, status_sidecar, stop_sidecar
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


BACKUP_SUFFIX = ".hermes_feishu_card.bak"
MANIFEST_NAME = ".hermes_feishu_card_manifest"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "start":
        return _run_start(args)
    if args.command == "stop":
        return _run_stop(args)
    if args.command == "status":
        return _run_status(args)
    if args.command == "smoke-feishu-card":
        return _run_smoke_feishu_card(args)
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
    doctor.add_argument("--hermes-dir")
    doctor.add_argument("--skip-hermes", action="store_true")

    for command in ("start", "stop", "status"):
        process_parser = subparsers.add_parser(command)
        process_parser.add_argument("--config", default="config.yaml.example")

    smoke = subparsers.add_parser("smoke-feishu-card")
    smoke.add_argument("--config", default="config.yaml.example")
    smoke.add_argument("--chat-id", required=True)

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
    if args.hermes_dir:
        detection = detect_hermes(args.hermes_dir)
        print(_format_hermes_detection(detection))
        return 0 if detection.supported else 1
    print("hermes: not checked")
    return 0


def _format_hermes_detection(detection: HermesDetection) -> str:
    status = "supported" if detection.supported else "unsupported"
    run_py_exists = "yes" if detection.run_py_exists else "no"
    return "\n".join(
        [
            f"hermes: {status}",
            f"hermes_root: {detection.root}",
            f"run_py: {detection.run_py}",
            f"run_py_exists: {run_py_exists}",
            f"version_source: {detection.version_source}",
            f"version: {detection.version}",
            f"minimum_supported_version: {detection.minimum_version}",
            f"reason: {detection.reason}",
        ]
    )


def _run_start(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        result = start_sidecar(args.config, config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.startswith("failed:"):
        print(f"error: {result}", file=sys.stderr)
        return 1
    if result == "already running":
        print("start: already running")
        return 0
    print("start ok")
    return 0


def _run_stop(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        result = stop_sidecar(config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.startswith("failed:"):
        print(f"error: {result}", file=sys.stderr)
        return 1
    if result == "not running":
        print("stop: not running")
        return 0
    print("stop ok")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    status = status_sidecar(config)
    if status["running"]:
        print("status: running")
        print(f"pid: {status['pid'] or 'unknown'}")
        print(f"active_sessions: {status['health'].get('active_sessions', 0)}")
        metrics = status["health"].get("metrics", {})
        if isinstance(metrics, dict):
            for name in (
                "events_received",
                "events_applied",
                "events_ignored",
                "events_rejected",
                "feishu_send_attempts",
                "feishu_send_successes",
                "feishu_send_failures",
                "feishu_update_attempts",
                "feishu_update_successes",
                "feishu_update_failures",
                "feishu_update_retries",
            ):
                value = metrics.get(name)
                if isinstance(value, int):
                    print(f"{name}: {value}")
    else:
        print("status: stopped")
        if status["pid"] is not None:
            print(f"pid: {status['pid']} stale")
    return 0


def _run_smoke_feishu_card(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        message_id = asyncio.run(_smoke_feishu_card(config, args.chat_id))
    except Exception as exc:
        print(f"error: {_sanitize_error(exc, config if 'config' in locals() else None)}", file=sys.stderr)
        return 1

    print("smoke ok")
    print(f"message_id: {message_id}")
    return 0


async def _smoke_feishu_card(config: dict, chat_id: str) -> str:
    feishu = config.get("feishu", {})
    app_id = feishu.get("app_id", "")
    app_secret = feishu.get("app_secret", "")
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("chat_id is required")
    if not app_id or not app_secret:
        raise ValueError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

    client = FeishuClient(
        FeishuClientConfig(
            app_id=app_id,
            app_secret=app_secret,
            base_url=feishu.get("base_url", FeishuClientConfig.base_url),
            timeout_seconds=feishu.get(
                "timeout_seconds",
                FeishuClientConfig.timeout_seconds,
            ),
        )
    )
    session = CardSession(
        conversation_id="smoke",
        message_id=f"smoke-{uuid4().hex}",
        chat_id=chat_id,
        thinking_text="飞书卡片 smoke test 正在运行。",
    )
    card_config = config.get("card", {})
    title = card_config.get("title", "Hermes Agent")
    footer_fields = card_config.get("footer_fields")
    if not isinstance(footer_fields, list):
        footer_fields = None
    message_id = await client.send_card(
        chat_id,
        render_card(session, footer_fields=footer_fields, title=title),
    )

    completed = SidecarEvent(
        schema_version="1",
        event="message.completed",
        conversation_id=session.conversation_id,
        message_id=session.message_id,
        chat_id=session.chat_id,
        platform="feishu",
        sequence=0,
        created_at=time.time(),
        data={
            "answer": "飞书卡片 smoke test 已完成。",
            "duration": 0.1,
            "tokens": {"input_tokens": 0, "output_tokens": 0},
        },
    )
    session.apply(completed)
    await client.update_card_message(
        message_id,
        render_card(session, footer_fields=footer_fields, title=title),
    )
    return message_id


def _sanitize_error(exc: Exception, config: dict | None) -> str:
    message = str(exc)
    if config is not None:
        secret = config.get("feishu", {}).get("app_secret", "")
        if isinstance(secret, str) and secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "[redacted-auth]", message)
    message = re.sub(r"tenant-token-[A-Za-z0-9._~+/=-]+", "tenant-token-[redacted]", message)
    if isinstance(exc, FeishuAPIError):
        return message
    return message or exc.__class__.__name__


def _run_install(args: argparse.Namespace) -> int:
    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1

    run_py = detection.run_py
    backup_path = _backup_path(run_py)
    manifest_path = _manifest_path(detection.root)
    original: str | None = None
    manifest_existed = manifest_path.exists()
    backup_existed = backup_path.exists()

    try:
        original = _read_text_preserve_newlines(run_py)
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
            backup_text = _read_text_preserve_newlines(backup_path)
            _validate_backup_contains_original(backup_text, "restore")
            if run_py.exists() and _read_text_preserve_newlines(run_py) == backup_text:
                _clear_install_state(backup_path, manifest_path)
                return

            current = _read_text_preserve_newlines(run_py) if run_py.exists() else ""
            try:
                if run_py.exists() and remove_patch(current) == backup_text:
                    _atomic_write_text(run_py, backup_text)
                    _clear_install_state(backup_path, manifest_path)
                    return
            except ValueError:
                pass

            patched_backup = apply_patch(backup_text)
            if not run_py.exists() or current != patched_backup:
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

    current = _read_text_preserve_newlines(run_py)
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
    current = _read_text_preserve_newlines(run_py)

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
    try:
        _validate_complete_install_state(run_py, backup_path, manifest, "install")
    except ValueError as exc:
        if "run.py changed since install" not in str(
            exc
        ) or not _current_matches_backup_lenient(run_py, backup_path):
            raise


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
        backup_text = _read_text_preserve_newlines(backup_path)
        _validate_backup_contains_original(backup_text, operation)
        if not run_py.exists():
            raise ValueError(f"run.py changed since install; refusing to {operation}")
        _validate_current_matches_backup(_read_text_preserve_newlines(run_py), backup_text, operation)
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

    current = _read_text_preserve_newlines(run_py)
    backup_text = _read_text_preserve_newlines(backup_path)
    _validate_backup_contains_original(backup_text, operation)
    _validate_current_matches_backup(current, backup_text, operation)
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

    backup_text = _read_text_preserve_newlines(backup_path)
    _validate_backup_contains_original(backup_text, operation)
    if not run_py.exists():
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    current = _read_text_preserve_newlines(run_py)
    if current == backup_text:
        return backup_text

    patched_sha256 = manifest.get("patched_sha256")
    if not isinstance(patched_sha256, str) or not patched_sha256:
        raise ValueError("manifest missing patched run.py sha256")
    if file_sha256(run_py) != patched_sha256:
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    _validate_current_matches_backup(current, backup_text, operation)
    return backup_text


def _validate_backup_contains_original(backup_text: str, operation: str) -> None:
    if remove_patch(backup_text) != backup_text:
        raise ValueError(f"backup changed since install; refusing to {operation}")


def _validate_current_matches_backup(
    current: str, backup_text: str, operation: str
) -> None:
    try:
        restored_current = remove_patch(current)
    except ValueError as exc:
        raise ValueError(
            f"run.py changed since install; refusing to {operation}"
        ) from exc
    if restored_current != backup_text:
        raise ValueError(f"run.py changed since install; refusing to {operation}")


def _current_matches_backup_lenient(run_py: Path, backup_path: Path) -> bool:
    try:
        current = _read_text_preserve_newlines(run_py)
        backup_text = _read_text_preserve_newlines(backup_path)
        _validate_backup_contains_original(backup_text, "install")
        return remove_patch_lenient(current) == backup_text
    except (OSError, UnicodeError, ValueError):
        return False


def _read_manifest(manifest_path: Path) -> dict[str, object] | None:
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(_read_text_preserve_newlines(manifest_path))
    except json.JSONDecodeError as exc:
        raise ValueError("manifest could not be parsed") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest could not be parsed")
    return manifest


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _atomic_write_text(path: Path, contents: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(contents)
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
        current = _read_text_preserve_newlines(run_py)
    restored = remove_patch(current)
    if restored == current:
        return False
    _atomic_write_text(run_py, restored)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
