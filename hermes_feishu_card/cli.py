from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from uuid import uuid4
from pathlib import Path
from typing import Any

import yaml

from hermes_feishu_card.config import load_config
from hermes_feishu_card.bots import BotRegistry
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.feishu_client import FeishuAPIError, FeishuClient, FeishuClientConfig
from hermes_feishu_card.install.detect import HermesDetection, detect_hermes
from hermes_feishu_card.install.manifest import file_sha256
from hermes_feishu_card.install.patcher import (
    apply_patch,
    apply_cron_patch,
    remove_patch,
    remove_cron_patch,
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
    if args.command == "setup":
        return _run_setup(args)
    if args.command == "start":
        return _run_start(args)
    if args.command == "stop":
        return _run_stop(args)
    if args.command == "status":
        return _run_status(args)
    if args.command == "smoke-feishu-card":
        return _run_smoke_feishu_card(args)
    if args.command == "bots":
        return _run_bots(args)
    if args.command == "install":
        return _run_install(args)
    if args.command == "repair":
        return _run_repair(args)
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
    doctor_output = doctor.add_mutually_exclusive_group()
    doctor_output.add_argument("--json", action="store_true", dest="json_output")
    doctor_output.add_argument("--explain", action="store_true")

    setup = subparsers.add_parser(
        "setup",
        help="run the guided all-in-one installer for ordinary users",
    )
    setup.add_argument("--hermes-dir", required=True, help="Hermes Agent root directory")
    setup.add_argument(
        "--config",
        default=str(Path.home() / ".hermes_feishu_card" / "config.yaml"),
        help="sidecar config path to create or reuse",
    )
    setup.add_argument(
        "--skip-start",
        action="store_true",
        help="install the Hermes hook but do not start the sidecar",
    )
    setup.add_argument(
        "--repair",
        action="store_true",
        help="repair known-safe Hermes hook install state before installing",
    )
    setup.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="confirm local Hermes hook installation",
    )

    for command in ("start", "stop", "status"):
        process_parser = subparsers.add_parser(command)
        process_parser.add_argument("--config", default="config.yaml.example")

    smoke = subparsers.add_parser("smoke-feishu-card")
    smoke.add_argument("--config", default="config.yaml.example")
    smoke.add_argument("--chat-id", required=True)
    smoke.add_argument("--profile-id")

    bots = subparsers.add_parser("bots")
    bot_subparsers = bots.add_subparsers(dest="bot_command")

    bots_list = bot_subparsers.add_parser("list")
    bots_list.add_argument("--config", required=True)

    bots_add = bot_subparsers.add_parser("add")
    bots_add.add_argument("bot_id")
    bots_add.add_argument("--config", required=True)

    bots_bind_chat = bot_subparsers.add_parser("bind-chat")
    bots_bind_chat.add_argument("chat_id")
    bots_bind_chat.add_argument("bot_id")
    bots_bind_chat.add_argument("--config", required=True)

    bots_unbind_chat = bot_subparsers.add_parser("unbind-chat")
    bots_unbind_chat.add_argument("chat_id")
    bots_unbind_chat.add_argument("--config", required=True)

    bots_test = bot_subparsers.add_parser("test")
    bots_test.add_argument("bot_id")
    bots_test.add_argument("--chat-id", required=True)
    bots_test.add_argument("--config", required=True)
    bots_test.add_argument("--profile-id")

    for command in ("install", "repair", "restore", "uninstall"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--hermes-dir", required=True)
        command_parser.add_argument("--yes", action="store_true", required=True)
    return parser


def _run_setup(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    try:
        created = _ensure_setup_config(config_path)
        config = load_config(config_path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"config: {'created' if created else 'existing'} {config_path}")
    if not _has_feishu_credentials(config):
        print(
            (
                "error: Feishu credentials are required before setup installs "
                "the Hermes hook. Set FEISHU_APP_ID and FEISHU_APP_SECRET, or "
                f"fill feishu.app_id and feishu.app_secret in {config_path}."
            ),
            file=sys.stderr,
        )
        return 1

    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1
    print("doctor: ok")
    print(_format_hermes_detection(detection))
    _print_hermes_streaming_guidance(Path(args.hermes_dir))

    if args.repair:
        repair_code = _run_repair(
            argparse.Namespace(hermes_dir=args.hermes_dir, yes=True)
        )
        if repair_code != 0:
            return repair_code

    install_code = _run_install(
        argparse.Namespace(hermes_dir=args.hermes_dir, yes=True)
    )
    if install_code != 0:
        return install_code

    if args.skip_start:
        print("start: skipped")
        print("setup ok")
        return 0

    try:
        start_result = start_sidecar(config_path, config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if start_result.startswith("failed:"):
        print(f"error: {start_result}", file=sys.stderr)
        return 1
    if start_result == "already running":
        print("start: already running")
    else:
        print("start ok")

    status = status_sidecar(config)
    if not status["running"]:
        print("error: sidecar did not report healthy status", file=sys.stderr)
        return 1
    print("status: running")
    print(f"pid: {status['pid'] or 'unknown'}")
    print("setup ok")
    return 0


def _has_feishu_credentials(config: dict[str, dict[str, object]]) -> bool:
    feishu = config.get("feishu", {})
    app_id = feishu.get("app_id", "")
    app_secret = feishu.get("app_secret", "")
    return bool(str(app_id).strip() and str(app_secret).strip())


def _ensure_setup_config(config_path: Path) -> bool:
    if config_path.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(config_path, _default_setup_config_text())
    return True


def _default_setup_config_text() -> str:
    return """# Hermes Feishu Streaming Card V3.2 configuration
# Prefer FEISHU_APP_ID and FEISHU_APP_SECRET environment variables in real deployments.

server:
  host: 127.0.0.1
  port: 8765

feishu:
  app_id: ""
  app_secret: ""
  base_url: https://open.feishu.cn/open-apis
  timeout_seconds: 30

# V3.2 Multi-bot configuration.
# For single-bot setups, leave `bots.items` empty and use `feishu.app_id`/`feishu.app_secret`.
# For multi-bot, define each bot under `bots.items` and map chat IDs in `bindings.chats`.
bots:
  default: default
  items: {}

bindings:
  fallback_bot: default
  chats: {}
  group_rules:
    enabled: false  # V3.2 does not filter group triggers

card:
  title: Hermes Agent
  max_wait_ms: 800
  max_chars: 240
  footer_fields:
    - duration
    - model
    - input_tokens
    - output_tokens
    - context
"""


def _run_doctor(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    try:
        config = load_config(config_path)
    except Exception as exc:
        if args.json_output:
            print(
                json.dumps(
                    _doctor_error_report(config_path, exc),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1
        if args.explain:
            print(_format_doctor_explanation(_doctor_error_report(config_path, exc)))
            return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json_output or args.explain:
        report = _build_doctor_report(config_path, config, args)
        if args.json_output:
            print(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(_format_doctor_explanation(report))
        return _doctor_exit_code(report)

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
        if detection.supported:
            runtime_import = _doctor_runtime_import_report(detection)
            print(
                "runtime_import: "
                f"{runtime_import['status']} - {runtime_import.get('message', '')}"
            )
            _print_hermes_streaming_guidance(Path(args.hermes_dir))
        return 0 if detection.supported else 1
    print("hermes: not checked")
    return 0


def _doctor_error_report(config_path: Path, exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "status": "error",
        "config": {
            "path": str(config_path),
            "loaded": False,
            "error": str(exc),
        },
        "sidecar": {"address": None},
        "hermes": {"checked": False, "status": "not_checked"},
        "streaming": {
            "status": "not_checked",
            "message": "Hermes streaming was not checked because config loading failed.",
        },
        "install_state": {
            "checked": False,
            "status": "skipped",
            "message": "Install state was not checked because config loading failed.",
        },
        "runtime_import": {
            "checked": False,
            "status": "skipped",
            "message": "Hermes runtime import was not checked because config loading failed.",
        },
        "recommendations": [
            {
                "severity": "error",
                "code": "config_load_failed",
                "message": f"Config could not be loaded: {exc}",
                "next_step": "Fix the sidecar config file and rerun doctor.",
            }
        ],
    }


def _build_doctor_report(
    config_path: Path,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    server = config["server"]
    host = str(server["host"])
    port = int(server["port"])
    recommendations: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "schema_version": "1",
        "status": "ok",
        "config": {
            "path": str(config_path),
            "loaded": True,
            "server": {"host": host, "port": port},
            "feishu_credentials": (
                "configured" if _has_feishu_credentials(config) else "missing"
            ),
            "profiles_enabled": _doctor_profile_count(config) > 0,
            "profile_count": _doctor_profile_count(config),
        },
        "sidecar": {"address": f"{host}:{port}"},
        "hermes": {"checked": False, "status": "not_checked"},
        "streaming": {
            "status": "not_checked",
            "message": "Hermes streaming config was not checked.",
        },
        "install_state": {
            "checked": False,
            "status": "skipped",
            "message": "Install state was not checked.",
        },
        "runtime_import": {
            "checked": False,
            "status": "not_checked",
            "message": "Hermes runtime import was not checked.",
        },
        "recommendations": recommendations,
    }

    if args.skip_hermes:
        report["hermes"] = {"checked": False, "status": "skipped"}
        report["streaming"] = {
            "status": "skipped",
            "message": "Hermes streaming config was skipped by request.",
        }
        report["install_state"] = {
            "checked": False,
            "status": "skipped",
            "message": "Install state was skipped by request.",
        }
        report["runtime_import"] = {
            "checked": False,
            "status": "skipped",
            "message": "Hermes runtime import was skipped by request.",
        }
        recommendations.append(
            {
                "severity": "info",
                "code": "hermes_check_skipped",
                "message": "Hermes detection was skipped.",
                "next_step": "Run doctor with --hermes-dir to check hook compatibility.",
            }
        )
        return _finalize_doctor_report(report)

    if not args.hermes_dir:
        recommendations.append(
            {
                "severity": "info",
                "code": "hermes_not_checked",
                "message": "Hermes detection was not checked.",
                "next_step": "Run doctor with --hermes-dir PATH to check hook compatibility.",
            }
        )
        return _finalize_doctor_report(report)

    detection = detect_hermes(args.hermes_dir)
    report["hermes"] = _doctor_hermes_report(detection)
    if not detection.supported:
        next_step = "Use a supported Hermes install before running install or setup."
        if detection.suggested_root is not None:
            next_step = (
                f"Use --hermes-dir {detection.suggested_root} "
                "and rerun doctor or install."
            )
        report["streaming"] = {
            "status": "skipped",
            "message": "Hermes streaming config was skipped because Hermes is unsupported.",
        }
        report["install_state"] = {
            "checked": False,
            "status": "skipped",
            "message": "Install state was skipped because Hermes is unsupported.",
        }
        report["runtime_import"] = {
            "checked": False,
            "status": "skipped",
            "message": "Hermes runtime import was skipped because Hermes is unsupported.",
        }
        recommendations.append(
            {
                "severity": "error",
                "code": "hermes_unsupported",
                "message": f"Hermes is unsupported: {detection.reason}",
                "next_step": next_step,
            }
        )
        return _finalize_doctor_report(report)

    if detection.compatibility != "full":
        recommendations.append(
            {
                "severity": "warning",
                "code": "hermes_compatibility_partial",
                "message": (
                    "Hermes is supported, but optional compatibility anchors are missing."
                ),
                "next_step": (
                    "Review the anchors section if streaming, cron, reply, or "
                    "attachment features do not behave as expected."
                ),
            }
        )

    runtime_import = _doctor_runtime_import_report(detection)
    report["runtime_import"] = runtime_import
    _append_runtime_import_recommendation(recommendations, runtime_import)

    streaming = _doctor_streaming_report(Path(args.hermes_dir))
    report["streaming"] = streaming
    if streaming["status"] == "disabled":
        recommendations.append(
            {
                "severity": "warning",
                "code": "streaming_disabled",
                "message": streaming["message"],
                "next_step": (
                    "Set streaming.enabled: true with streaming.transport: edit, "
                    "or set display.platforms.feishu.streaming: true."
                ),
            }
        )
    elif streaming["status"] == "not_detected":
        recommendations.append(
            {
                "severity": "warning",
                "code": "streaming_not_detected",
                "message": streaming["message"],
                "next_step": (
                    "If cards miss answer.delta updates, add Hermes streaming "
                    "config and rerun doctor."
                ),
            }
        )

    install_state = _diagnose_install_state(detection)
    report["install_state"] = install_state
    _append_install_state_recommendation(recommendations, install_state)
    return _finalize_doctor_report(report)


def _doctor_profile_count(config: dict[str, Any]) -> int:
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        return 0
    return len([key for key in profiles if str(key).strip()])


def _doctor_hermes_report(detection: HermesDetection) -> dict[str, Any]:
    return {
        "checked": True,
        "status": "supported" if detection.supported else "unsupported",
        "root": str(detection.root),
        "run_py": str(detection.run_py),
        "run_py_exists": detection.run_py_exists,
        "cron_py": str(detection.cron_py) if detection.cron_py is not None else None,
        "cron_py_exists": detection.cron_py_exists,
        "version_source": detection.version_source,
        "version": detection.version,
        "minimum_supported_version": detection.minimum_version,
        "hook_strategy": detection.hook_strategy,
        "cron_hook_strategy": detection.cron_hook_strategy,
        "compatibility": detection.compatibility,
        "anchors": dict(detection.capabilities),
        "reason": detection.reason,
        "suggested_root": (
            str(detection.suggested_root)
            if detection.suggested_root is not None
            else ""
        ),
        "suggestion_reason": detection.suggestion_reason,
    }


def _doctor_streaming_report(hermes_root: Path) -> dict[str, str]:
    config = _load_hermes_user_config(hermes_root)
    status = _detect_hermes_streaming_status(config)
    if status == "enabled":
        message = "Hermes Gateway streaming config appears enabled for Feishu."
    elif status == "disabled":
        message = (
            "Hermes Gateway streaming appears disabled for Feishu."
        )
    else:
        message = (
            "Hermes Gateway streaming config was not detected."
        )
    return {"status": status, "message": message}


def _doctor_runtime_import_report(detection: HermesDetection) -> dict[str, Any]:
    runtime_python = _detect_hermes_runtime_python(detection.root)
    if runtime_python is None:
        return {
            "checked": False,
            "status": "skipped",
            "python": None,
            "message": "Hermes runtime venv Python was not found.",
        }
    return _check_runtime_hook_import(runtime_python)


def _append_runtime_import_recommendation(
    recommendations: list[dict[str, str]],
    runtime_import: dict[str, Any],
) -> None:
    if runtime_import.get("status") != "failed":
        return
    recommendations.append(
        {
            "severity": "warning",
            "code": "runtime_import_failed",
            "message": runtime_import.get("message", "Hermes runtime import failed."),
            "next_step": (
                "Run setup/install again so hermes-feishu-streaming-card is "
                "installed into the Hermes Gateway venv Python."
            ),
        }
    )


def _detect_hermes_runtime_python(hermes_root: Path | str) -> Path | None:
    root = Path(hermes_root).expanduser()
    candidates = (
        root / "venv" / "bin" / "python",
        root / "venv" / "bin" / "python3",
        root / ".venv" / "bin" / "python",
        root / ".venv" / "bin" / "python3",
        root / "venv" / "Scripts" / "python.exe",
        root / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _check_runtime_hook_import(runtime_python: Path) -> dict[str, Any]:
    code = (
        "import hermes_feishu_card.hook_runtime as hook_runtime; "
        "print(getattr(hook_runtime, '__file__', ''))"
    )
    try:
        result = subprocess.run(
            [str(runtime_python), "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return {
            "checked": True,
            "status": "failed",
            "python": str(runtime_python),
            "message": "Hermes runtime hook_runtime import timed out.",
        }
    except OSError as exc:
        return {
            "checked": True,
            "status": "failed",
            "python": str(runtime_python),
            "message": (
                "Hermes runtime hook_runtime import could not start: "
                f"{exc.__class__.__name__}"
            ),
        }
    if result.returncode == 0:
        location = result.stdout.strip()
        suffix = f" from {location}" if location else ""
        return {
            "checked": True,
            "status": "ok",
            "python": str(runtime_python),
            "message": f"Hermes runtime can import hook_runtime{suffix}.",
        }
    detail = _summarize_process_output(result)
    return {
        "checked": True,
        "status": "failed",
        "python": str(runtime_python),
        "message": f"Hermes runtime cannot import hook_runtime: {detail}",
    }


def _ensure_hermes_runtime_package(detection: HermesDetection) -> None:
    runtime_python = _detect_hermes_runtime_python(detection.root)
    if runtime_python is None:
        print("runtime package: skipped (Hermes venv Python not found)")
        return
    report = _check_runtime_hook_import(runtime_python)
    if report["status"] == "ok":
        print(f"runtime package: import ok ({runtime_python})")
        return

    spec = _runtime_install_spec()
    if not spec:
        raise ValueError(
            "Hermes runtime Python cannot import hermes_feishu_card.hook_runtime, "
            "and no install spec is available. Run the one-line installer or set "
            "HFC_INSTALL_SPEC before install/setup."
        )

    pip_version = _run_runtime_pip(runtime_python, ["--version"], timeout=20)
    if pip_version.returncode != 0:
        raise ValueError(
            "Hermes runtime Python pip is unavailable: "
            f"{_summarize_process_output(pip_version)}"
        )
    install = _run_runtime_pip(
        runtime_python,
        ["install", "--upgrade", spec],
        timeout=180,
    )
    if install.returncode != 0:
        raise ValueError(
            "failed to install hermes-feishu-streaming-card into Hermes runtime "
            f"Python {runtime_python}: {_summarize_process_output(install)}"
        )
    report = _check_runtime_hook_import(runtime_python)
    if report["status"] != "ok":
        raise ValueError(report["message"])
    print(f"runtime package: installed into {runtime_python}")


def _run_runtime_pip(
    runtime_python: Path,
    args: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [str(runtime_python), "-m", "pip", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"Hermes runtime pip command timed out for {runtime_python}: {' '.join(args)}"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Hermes runtime pip command could not start for {runtime_python}: "
            f"{exc.__class__.__name__}"
        ) from exc
    if args == ["--version"] and result.returncode != 0:
        _run_runtime_ensurepip(runtime_python)
        result = subprocess.run(
            [str(runtime_python), "-m", "pip", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return result


def _run_runtime_ensurepip(runtime_python: Path) -> None:
    try:
        result = subprocess.run(
            [str(runtime_python), "-m", "ensurepip", "--upgrade"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"Hermes runtime ensurepip timed out for {runtime_python}"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Hermes runtime ensurepip could not start for {runtime_python}: "
            f"{exc.__class__.__name__}"
        ) from exc
    if result.returncode != 0:
        raise ValueError(
            "failed to bootstrap pip in Hermes runtime Python "
            f"{runtime_python}: {_summarize_process_output(result)}"
        )


def _runtime_install_spec() -> str | None:
    spec = os.environ.get("HFC_INSTALL_SPEC", "").strip()
    if spec:
        return spec
    root = Path(__file__).resolve().parents[1]
    if (root / "pyproject.toml").exists() and (root / "hermes_feishu_card").is_dir():
        return str(root)
    return None


def _summarize_process_output(result: subprocess.CompletedProcess[str]) -> str:
    combined = "\n".join(
        part.strip()
        for part in (result.stderr, result.stdout)
        if part and part.strip()
    )
    if not combined:
        combined = f"exit {result.returncode}"
    return combined[-800:]


def _diagnose_install_state(detection: HermesDetection) -> dict[str, Any]:
    run_py = detection.run_py
    backup_path = _backup_path(run_py)
    manifest_path = _manifest_path(detection.root)
    cron_py = detection.cron_py
    cron_backup_path = _backup_path(cron_py) if cron_py is not None else None
    backup_exists = backup_path.exists()
    manifest_exists = manifest_path.exists()
    cron_backup_exists = (
        cron_backup_path.exists() if cron_backup_path is not None else False
    )
    base: dict[str, Any] = {
        "checked": True,
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_exists,
        "backup_path": str(backup_path),
        "backup_exists": backup_exists,
        "cron_backup_path": (
            str(cron_backup_path) if cron_backup_path is not None else None
        ),
        "cron_backup_exists": cron_backup_exists,
        "automatic_repair_available": False,
    }

    try:
        _validate_existing_install_state(
            run_py,
            backup_path,
            manifest_path,
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
        )
    except ValueError as exc:
        message = str(exc)
        status = _install_state_status_from_error(message)
        automatic_repair_available = _automatic_repair_available(detection)
        return {
            **base,
            "status": status,
            "message": message,
            "manual_action_required": True,
            "automatic_repair_available": automatic_repair_available,
        }
    except (OSError, UnicodeError) as exc:
        return {
            **base,
            "status": "error",
            "message": f"install state could not be read: {exc.__class__.__name__}",
            "manual_action_required": True,
        }

    if backup_exists or manifest_exists or cron_backup_exists:
        return {
            **base,
            "status": "installed",
            "message": "Hermes Feishu hook install state is complete and consistent.",
            "manual_action_required": False,
        }
    return {
        **base,
        "status": "clean",
        "message": "No Hermes Feishu hook install state was found.",
        "manual_action_required": False,
    }


def _install_state_status_from_error(message: str) -> str:
    lowered = message.lower()
    if "changed since install" in lowered or "backup changed" in lowered:
        return "changed"
    if "incomplete" in lowered or "manifest" in lowered or "backup missing" in lowered:
        return "incomplete"
    return "error"


def _append_install_state_recommendation(
    recommendations: list[dict[str, str]],
    install_state: dict[str, Any],
) -> None:
    status = install_state["status"]
    if status == "clean":
        recommendations.append(
            {
                "severity": "info",
                "code": "install_state_clean",
                "message": "No existing Hermes Feishu hook install state was found.",
                "next_step": "Run install --hermes-dir PATH --yes when ready to patch Hermes.",
            }
        )
        return
    if status == "installed":
        recommendations.append(
            {
                "severity": "info",
                "code": "install_state_installed",
                "message": "Existing hook install state is complete and consistent.",
                "next_step": "No install-state action is required.",
            }
        )
        return
    code = "install_state_changed" if status == "changed" else "install_state_incomplete"
    if install_state.get("automatic_repair_available"):
        next_step = (
            "Run repair --hermes-dir PATH --yes to rebuild known-safe "
            "backup/manifest state, then rerun doctor."
        )
    else:
        next_step = (
            "Back up the Hermes directory, inspect gateway/run.py and the "
            "manifest, then restore or reinstall only after confirming the "
            "local edits are intentional."
        )
    recommendations.append(
        {
            "severity": "warning",
            "code": code,
            "message": install_state["message"],
            "next_step": next_step,
        }
    )


def _finalize_doctor_report(report: dict[str, Any]) -> dict[str, Any]:
    severities = {
        item.get("severity")
        for item in report.get("recommendations", [])
        if isinstance(item, dict)
    }
    if "error" in severities:
        report["status"] = "error"
    elif "warning" in severities:
        report["status"] = "warning"
    else:
        report["status"] = "ok"
    return report


def _doctor_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "error" else 0


def _format_doctor_explanation(report: dict[str, Any]) -> str:
    config = report["config"]
    lines = ["Doctor Summary"]
    if config.get("loaded"):
        lines.append(f"- Config: OK ({config['path']})")
        lines.append(f"- Sidecar: {report['sidecar']['address']}")
    else:
        lines.append(f"- Config: ERROR ({config['path']})")
        lines.append(f"- Error: {config.get('error', 'unknown')}")

    hermes = report["hermes"]
    hermes_status = hermes["status"]
    if hermes.get("checked"):
        details = []
        if hermes.get("version"):
            details.append(str(hermes["version"]))
        if hermes.get("hook_strategy"):
            details.append(str(hermes["hook_strategy"]))
        if hermes.get("compatibility"):
            details.append(f"compatibility {hermes['compatibility']}")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"- Hermes: {hermes_status}{suffix}")
    else:
        lines.append(f"- Hermes: {hermes_status}")

    runtime_import = report.get("runtime_import", {})
    if runtime_import.get("status"):
        lines.append(
            f"- Runtime import: {runtime_import['status']} - "
            f"{runtime_import.get('message', '')}"
        )

    streaming = report["streaming"]
    if streaming.get("status"):
        lines.append(
            f"- Streaming: {streaming['status']} - {streaming.get('message', '')}"
        )

    install_state = report["install_state"]
    if install_state.get("status"):
        lines.append(
            f"- Install state: {install_state['status']} - "
            f"{install_state.get('message', '')}"
        )

    lines.append("")
    lines.append("Next steps")
    recommendations = report.get("recommendations", [])
    if not recommendations:
        lines.append("- No action required.")
        return "\n".join(lines)
    for item in recommendations:
        severity = item.get("severity", "info")
        message = item.get("message", "")
        next_step = item.get("next_step", "")
        lines.append(f"- [{severity}] {message}")
        if next_step:
            lines.append(f"  Next: {next_step}")
    return "\n".join(lines)


def _format_hermes_detection(detection: HermesDetection) -> str:
    status = "supported" if detection.supported else "unsupported"
    run_py_exists = "yes" if detection.run_py_exists else "no"
    lines = [
        f"hermes: {status}",
        f"hermes_root: {detection.root}",
        f"run_py: {detection.run_py}",
        f"run_py_exists: {run_py_exists}",
        f"cron_py: {detection.cron_py}",
        f"cron_py_exists: {'yes' if detection.cron_py_exists else 'no'}",
        f"version_source: {detection.version_source}",
        f"version: {detection.version}",
        f"minimum_supported_version: {detection.minimum_version}",
        f"hook_strategy: {detection.hook_strategy}",
        f"cron_hook_strategy: {detection.cron_hook_strategy}",
        f"compatibility: {detection.compatibility}",
        f"suggested_root: {detection.suggested_root or ''}",
        f"suggestion_reason: {detection.suggestion_reason}",
        "anchors:",
    ]
    for capability, found in detection.capabilities.items():
        anchor_status = "found" if found else "missing"
        lines.append(f"  {capability}: {anchor_status}")
    lines.append(f"reason: {detection.reason}")
    return "\n".join(lines)


def _print_hermes_streaming_guidance(hermes_root: Path) -> None:
    config = _load_hermes_user_config(hermes_root)
    status = _detect_hermes_streaming_status(config)
    if status == "disabled":
        print(
            (
                "warning: Hermes Gateway streaming appears disabled for Feishu. "
                "Set streaming.enabled: true with streaming.transport: edit, "
                "or set display.platforms.feishu.streaming: true, so "
                "thinking.delta and answer.delta updates can reach the card."
            )
        )
    elif status == "not_detected":
        print(
            (
                "note: Hermes Gateway streaming config was not detected. If "
                "cards do not show answer.delta updates, set "
                "streaming.enabled: true and streaming.transport: edit in the "
                "Hermes config.yaml."
            )
        )


def _load_hermes_user_config(hermes_root: Path) -> dict[str, object]:
    for config_path in _candidate_hermes_config_paths(hermes_root):
        if not config_path.exists() or not config_path.is_file():
            continue
        try:
            with config_path.open("r", encoding="utf-8") as file:
                loaded = yaml.safe_load(file) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return {}


def _detect_hermes_streaming_status(config: dict[str, object]) -> str:
    feishu_streaming = _nested_get(
        config, ("display", "platforms", "feishu", "streaming")
    )
    if feishu_streaming is not None:
        return "enabled" if _truthy(feishu_streaming) else "disabled"

    streaming = config.get("streaming")
    if not isinstance(streaming, dict):
        return "not_detected"
    if _truthy(streaming.get("enabled")) and str(
        streaming.get("transport", "edit")
    ).strip().lower() != "off":
        return "enabled"
    return "disabled"


def _candidate_hermes_config_paths(hermes_root: Path) -> tuple[Path, ...]:
    return (
        hermes_root / "config.yaml",
        hermes_root / "config.yml",
        hermes_root / "configs" / "config.yaml",
        hermes_root / "configs" / "config.yml",
        Path.home() / ".hermes" / "config.yaml",
        Path.home() / ".hermes" / "config.yml",
    )


def _nested_get(config: dict[str, object], path: tuple[str, ...]) -> object:
    current: object = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
                "cron_cards_sent",
                "cron_fallbacks",
            ):
                value = metrics.get(name)
                if isinstance(value, int):
                    print(f"{name}: {value}")
        _print_status_routing(status["health"])
    else:
        print("status: stopped")
        if status["pid"] is not None:
            print(f"pid: {status['pid']} stale")
    return 0


def _print_status_routing(health: dict[str, Any]) -> None:
    routing = health.get("routing")
    if isinstance(routing, dict):
        bot_count = routing.get("bot_count")
        chat_binding_count = routing.get("chat_binding_count")
        if isinstance(bot_count, int):
            print(f"routing.bot_count: {bot_count}")
        if isinstance(chat_binding_count, int):
            print(f"routing.chat_binding_count: {chat_binding_count}")
        route = routing.get("last_route")
        if isinstance(route, dict) and route:
            profile_id = str(route.get("profile_id") or "").strip()
            bot_id = str(route.get("bot_id") or "").strip()
            reason = str(route.get("reason") or "").strip()
            profile_part = f"profile={profile_id} " if profile_id else ""
            print(f"routing.last_route: {profile_part}bot={bot_id} reason={reason}")
        last_route_error = routing.get("last_route_error")
        if isinstance(last_route_error, str) and last_route_error:
            print(f"routing.last_route_error: {last_route_error}")
    profiles = health.get("profile_diagnostics")
    if isinstance(profiles, dict):
        for profile_id in sorted(profiles):
            item = profiles[profile_id]
            if not isinstance(item, dict):
                continue
            events = item.get("events")
            if isinstance(events, int):
                print(f"profile.{profile_id}.events: {events}")
            source = item.get("last_profile_source")
            if isinstance(source, str) and source:
                print(f"profile.{profile_id}.last_profile_source: {source}")


def _run_smoke_feishu_card(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        config = _select_profile_config(config, args.profile_id)
        message_id = asyncio.run(_smoke_feishu_card(config, args.chat_id))
    except Exception as exc:
        print(f"error: {_sanitize_error(exc, config if 'config' in locals() else None)}", file=sys.stderr)
        return 1

    print("smoke ok")
    print(f"message_id: {message_id}")
    return 0


def _run_bots(args: argparse.Namespace) -> int:
    try:
        if args.bot_command == "list":
            config = load_config(args.config)
            registry = BotRegistry.from_config(config)
            for bot in registry.list_bots():
                print(f"{bot.bot_id}\t{bot.name}\t{bot.app_id}")
            return 0

        if args.bot_command == "add":
            data = _read_local_yaml(args.config)
            items = _ensure_mapping_path(data, "bots", "items")
            if args.bot_id in items:
                raise ValueError(f"bot {args.bot_id!r} already exists")
            config = load_config(args.config)
            if args.bot_id == "default" and _has_feishu_credentials(config):
                raise ValueError("bot 'default' already exists")
            items[args.bot_id] = {
                "name": args.bot_id,
                "app_id": "",
                "app_secret": "",
                "base_url": FeishuClientConfig.base_url,
                "timeout_seconds": FeishuClientConfig.timeout_seconds,
            }
            _write_local_yaml(args.config, data)
            print(f"bot added: {args.bot_id}")
            return 0

        if args.bot_command == "bind-chat":
            config = load_config(args.config)
            if not _config_has_bot(config, args.bot_id):
                raise KeyError(f"unknown bot: {args.bot_id}")
            data = _read_local_yaml(args.config)
            chats = _ensure_mapping_path(data, "bindings", "chats")
            chats[args.chat_id] = args.bot_id
            _write_local_yaml(args.config, data)
            print(f"bound: {args.chat_id} -> {args.bot_id}")
            return 0

        if args.bot_command == "unbind-chat":
            data = _read_local_yaml(args.config)
            chats = _ensure_mapping_path(data, "bindings", "chats")
            chats.pop(args.chat_id, None)
            _write_local_yaml(args.config, data)
            print(f"unbound: {args.chat_id}")
            return 0

        if args.bot_command == "test":
            config = load_config(args.config)
            config = _select_profile_config(config, args.profile_id)
            message_id = asyncio.run(
                _smoke_feishu_card_with_bot(config, args.bot_id, args.chat_id)
            )
            print("bot smoke ok")
            print(f"message_id: {message_id}")
            return 0
    except Exception as exc:
        print(f"error: {_sanitize_error(exc, locals().get('config'))}", file=sys.stderr)
        return 1

    print("error: bots command is required", file=sys.stderr)
    return 2


def _select_profile_config(config: dict[str, Any], profile_id: str | None) -> dict[str, Any]:
    if not profile_id:
        return config
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or profile_id not in profiles:
        raise KeyError(f"unknown profile: {profile_id}")
    profile_config = profiles[profile_id]
    if not isinstance(profile_config, dict):
        raise ValueError(f"profile {profile_id!r} must be a mapping")
    selected = dict(config)
    selected.update(profile_config)
    selected["profiles"] = {}
    return selected


def _read_local_yaml(path: str | Path) -> dict:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("Config top-level YAML value must be a mapping")
    return loaded


def _write_local_yaml(path: str | Path, data: dict) -> None:
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        config_path,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    )


def _ensure_mapping_path(data: dict, *path: str) -> dict:
    current = data
    for key in path:
        value = current.get(key)
        if value is None:
            value = {}
            current[key] = value
        if not isinstance(value, dict):
            raise ValueError(f"Config section {'.'.join(path)} must be a mapping")
        current = value
    return current


def _config_has_bot(config: dict, bot_id: str) -> bool:
    bots = config.get("bots")
    if isinstance(bots, dict):
        items = bots.get("items")
        if isinstance(items, dict) and bot_id in items:
            return True
    return bot_id == "default" and _has_feishu_credentials(config)


async def _smoke_feishu_card_with_bot(config: dict, bot_id: str, chat_id: str) -> str:
    registry = BotRegistry.from_config(config)
    bot = registry.get(bot_id)
    bot_config = dict(config)
    bot_config["feishu"] = {
        "app_id": bot.app_id,
        "app_secret": bot.app_secret,
        "base_url": bot.base_url,
        "timeout_seconds": bot.timeout_seconds,
    }
    return await _smoke_feishu_card(bot_config, chat_id)


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
    for secret in _secret_values(config):
        if secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "[redacted-auth]", message)
    message = re.sub(r"tenant-token-[A-Za-z0-9._~+/=-]+", "tenant-token-[redacted]", message)
    if isinstance(exc, FeishuAPIError):
        return message
    return message or exc.__class__.__name__


def _secret_values(config: dict | None) -> list[str]:
    if not isinstance(config, dict):
        return []
    secrets: list[str] = []
    feishu = config.get("feishu")
    if isinstance(feishu, dict) and isinstance(feishu.get("app_secret"), str):
        secrets.append(feishu["app_secret"])
    bots = config.get("bots")
    if isinstance(bots, dict):
        items = bots.get("items")
        if isinstance(items, dict):
            for value in items.values():
                if isinstance(value, dict) and isinstance(value.get("app_secret"), str):
                    secrets.append(value["app_secret"])
    return secrets


def _run_install(args: argparse.Namespace) -> int:
    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1

    try:
        _ensure_hermes_runtime_package(detection)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    run_py = detection.run_py
    backup_path = _backup_path(run_py)
    cron_py = detection.cron_py if detection.cron_py_exists else None
    cron_backup_path = _backup_path(cron_py) if cron_py is not None else None
    manifest_path = _manifest_path(detection.root)
    original: str | None = None
    cron_original: str | None = None
    manifest_existed = manifest_path.exists()
    backup_existed = backup_path.exists()
    cron_backup_existed = bool(cron_backup_path and cron_backup_path.exists())

    try:
        original = _read_text_preserve_newlines(run_py)
        cron_original = (
            _read_text_preserve_newlines(cron_py) if cron_py is not None else None
        )
        _validate_existing_install_state(
            run_py,
            backup_path,
            manifest_path,
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
        )
        patched = apply_patch(
            original, strategy=detection.hook_strategy or "legacy_gateway_run"
        )
        cron_patched = (
            apply_cron_patch(cron_original)
            if cron_py is not None and cron_original is not None
            else None
        )
        if not backup_existed:
            _atomic_write_text(backup_path, original)
        if cron_py is not None and cron_backup_path is not None and not cron_backup_existed:
            _atomic_write_text(cron_backup_path, cron_original or "")
        if patched != original:
            _atomic_write_text(run_py, patched)
        if (
            cron_py is not None
            and cron_patched is not None
            and cron_original is not None
            and cron_patched != cron_original
        ):
            _atomic_write_text(cron_py, cron_patched)
        _write_manifest(manifest_path, run_py, backup_path, cron_py, cron_backup_path)
    except (OSError, UnicodeError, ValueError) as exc:
        _rollback_install(
            run_py,
            original,
            backup_path,
            backup_existed,
            manifest_path,
            manifest_existed,
            cron_py=cron_py,
            cron_original=cron_original,
            cron_backup_path=cron_backup_path,
            cron_backup_existed=cron_backup_existed,
        )
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("install ok")
    return 0


def _run_repair(args: argparse.Namespace) -> int:
    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1
    try:
        actions = _repair_install_state(detection)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if actions:
        for action in actions:
            print(action)
    else:
        print("repair: no changes")
    print("repair ok")
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


def _automatic_repair_available(detection: HermesDetection) -> bool:
    try:
        return bool(_repair_install_state(detection, dry_run=True))
    except (OSError, UnicodeError, ValueError):
        return False


def _repair_install_state(
    detection: HermesDetection, *, dry_run: bool = False
) -> list[str]:
    run_py = detection.run_py
    backup_path = _backup_path(run_py)
    manifest_path = _manifest_path(detection.root)
    cron_py = detection.cron_py if detection.cron_py_exists else None
    cron_backup_path = _backup_path(cron_py) if cron_py is not None else None

    try:
        _validate_existing_install_state(
            run_py,
            backup_path,
            manifest_path,
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
        )
        return []
    except ValueError:
        pass

    current = _read_text_preserve_newlines(run_py)
    unpatched = _owned_unpatched_text(current, "run.py")
    has_owned_patch = unpatched != current
    if not has_owned_patch:
        raise ValueError("install state is inconsistent but run.py has no owned patch")

    backup_exists = backup_path.exists()
    manifest_exists = manifest_path.exists()
    backup_text = _read_text_preserve_newlines(backup_path) if backup_exists else None
    if backup_text is not None:
        _validate_backup_contains_original(backup_text, "repair")
        if backup_text != unpatched:
            raise ValueError("run.py changed since install; refusing to repair")

    manifest = _read_manifest_for_repair(manifest_path)
    if manifest is not None:
        _validate_repair_manifest_run_py(run_py, manifest)
        if backup_exists:
            _validate_repair_manifest_backup(backup_path, manifest)

    actions: list[str] = []
    should_write_backup = not backup_exists
    if should_write_backup:
        actions.append("backup: recreated")

    cron_actions = _repair_cron_state(
        cron_py,
        cron_backup_path,
        manifest,
        dry_run=dry_run,
    )
    should_write_manifest = (
        not manifest_exists
        or manifest is None
        or should_write_backup
        or bool(cron_actions)
    )
    if should_write_manifest:
        actions.append("manifest: rebuilt")

    if dry_run:
        return actions

    if should_write_backup:
        _atomic_write_text(backup_path, unpatched)
    for action in cron_actions:
        if action == "cron backup: recreated" and cron_py is not None and cron_backup_path is not None:
            cron_current = _read_text_preserve_newlines(cron_py)
            _atomic_write_text(
                cron_backup_path,
                _owned_cron_unpatched_text(cron_current, "cron/scheduler.py"),
            )
    if should_write_manifest:
        _write_manifest(manifest_path, run_py, backup_path, cron_py, cron_backup_path)
    _validate_existing_install_state(
        run_py,
        backup_path,
        manifest_path,
        cron_py=cron_py,
        cron_backup_path=cron_backup_path,
    )
    return actions + cron_actions


def _read_manifest_for_repair(manifest_path: Path) -> dict[str, object] | None:
    if not manifest_path.exists():
        return None
    try:
        return _read_manifest(manifest_path)
    except ValueError:
        return None


def _validate_repair_manifest_run_py(
    run_py: Path, manifest: dict[str, object]
) -> None:
    patched_sha256 = manifest.get("patched_sha256")
    if isinstance(patched_sha256, str) and patched_sha256:
        if file_sha256(run_py) != patched_sha256:
            raise ValueError("run.py changed since install; refusing to repair")


def _validate_repair_manifest_backup(
    backup_path: Path, manifest: dict[str, object]
) -> None:
    backup_sha256 = manifest.get("backup_sha256")
    if isinstance(backup_sha256, str) and backup_sha256:
        if file_sha256(backup_path) != backup_sha256:
            raise ValueError("backup changed since install; refusing to repair")


def _repair_cron_state(
    cron_py: Path | None,
    cron_backup_path: Path | None,
    manifest: dict[str, object] | None,
    *,
    dry_run: bool,
) -> list[str]:
    if cron_py is None or cron_backup_path is None or not cron_py.exists():
        return []
    cron_current = _read_text_preserve_newlines(cron_py)
    cron_unpatched = _owned_cron_unpatched_text(cron_current, "cron/scheduler.py")
    has_owned_cron_patch = cron_unpatched != cron_current
    cron_backup_exists = cron_backup_path.exists()
    if not has_owned_cron_patch:
        if cron_backup_exists:
            raise ValueError(
                "install state incomplete; cron backup exists without owned patch"
            )
        return []
    if cron_backup_exists:
        cron_backup_text = _read_text_preserve_newlines(cron_backup_path)
        if remove_cron_patch(cron_backup_text) != cron_backup_text:
            raise ValueError("cron backup changed since install; refusing to repair")
        if cron_backup_text != cron_unpatched:
            raise ValueError("cron scheduler changed since install; refusing to repair")
    if manifest is not None:
        cron_patched_sha256 = manifest.get("cron_patched_sha256")
        if isinstance(cron_patched_sha256, str) and cron_patched_sha256:
            if file_sha256(cron_py) != cron_patched_sha256:
                raise ValueError(
                    "cron scheduler changed since install; refusing to repair"
                )
        cron_backup_sha256 = manifest.get("cron_backup_sha256")
        if cron_backup_exists and isinstance(cron_backup_sha256, str) and cron_backup_sha256:
            if file_sha256(cron_backup_path) != cron_backup_sha256:
                raise ValueError("cron backup changed since install; refusing to repair")
    if cron_backup_exists:
        return []
    return ["cron backup: recreated"]


def _owned_unpatched_text(current: str, label: str) -> str:
    try:
        return remove_patch(current)
    except ValueError as exc:
        raise ValueError(f"{label} has corrupt patch markers; refusing to repair") from exc


def _owned_cron_unpatched_text(current: str, label: str) -> str:
    try:
        return remove_cron_patch(current)
    except ValueError as exc:
        raise ValueError(f"{label} has corrupt patch markers; refusing to repair") from exc


def _restore(hermes_root: Path) -> None:
    run_py = hermes_root / "gateway" / "run.py"
    cron_py = hermes_root / "cron" / "scheduler.py"
    backup_path = _backup_path(run_py)
    cron_backup_path = _backup_path(cron_py)
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
            run_py,
            backup_path,
            manifest,
            "restore",
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
        )
        cron_backup_text = (
            _read_text_preserve_newlines(cron_backup_path)
            if _manifest_has_cron(manifest) and cron_backup_path.exists()
            else None
        )
        _atomic_write_text(run_py, backup_text)
        if cron_backup_text is not None:
            _atomic_write_text(cron_py, cron_backup_text)
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
    cron_backup_path = backup_path.parent.parent / "cron" / f"scheduler.py{BACKUP_SUFFIX}"
    cron_backup_path.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)


def _write_manifest(
    manifest_path: Path,
    run_py: Path,
    backup_path: Path,
    cron_py: Path | None = None,
    cron_backup_path: Path | None = None,
) -> None:
    manifest = {
        "run_py": str(run_py.relative_to(manifest_path.parent)),
        "patched_sha256": file_sha256(run_py),
        "backup": str(backup_path.relative_to(manifest_path.parent)),
        "backup_sha256": file_sha256(backup_path),
    }
    if cron_py is not None and cron_backup_path is not None and cron_py.exists():
        manifest.update(
            {
                "cron_py": str(cron_py.relative_to(manifest_path.parent)),
                "cron_patched_sha256": file_sha256(cron_py),
                "cron_backup": str(cron_backup_path.relative_to(manifest_path.parent)),
                "cron_backup_sha256": file_sha256(cron_backup_path),
            }
        )
    _atomic_write_text(manifest_path, json.dumps(manifest, sort_keys=True) + "\n")


def _rollback_install(
    run_py: Path,
    original: str | None,
    backup_path: Path,
    backup_existed: bool,
    manifest_path: Path,
    manifest_existed: bool,
    *,
    cron_py: Path | None = None,
    cron_original: str | None = None,
    cron_backup_path: Path | None = None,
    cron_backup_existed: bool = False,
) -> None:
    if original is not None:
        try:
            _atomic_write_text(run_py, original)
        except OSError:
            pass
    if cron_py is not None and cron_original is not None:
        try:
            _atomic_write_text(cron_py, cron_original)
        except OSError:
            pass
    if not backup_existed:
        try:
            backup_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    if cron_backup_path is not None and not cron_backup_existed:
        try:
            cron_backup_path.unlink()
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
    run_py: Path,
    backup_path: Path,
    manifest_path: Path,
    *,
    cron_py: Path | None = None,
    cron_backup_path: Path | None = None,
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
        _validate_cron_install_state_without_manifest(cron_py, cron_backup_path)
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
        _validate_complete_install_state(
            run_py,
            backup_path,
            manifest,
            "install",
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
        )
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
    *,
    cron_py: Path | None = None,
    cron_backup_path: Path | None = None,
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
    _validate_complete_cron_install_state(
        cron_py, cron_backup_path, manifest, operation
    )
    return backup_text


def _validate_restorable_install_state(
    run_py: Path,
    backup_path: Path,
    manifest: dict[str, object],
    operation: str,
    *,
    cron_py: Path | None = None,
    cron_backup_path: Path | None = None,
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
    _validate_complete_cron_install_state(
        cron_py, cron_backup_path, manifest, operation
    )
    return backup_text


def _validate_cron_install_state_without_manifest(
    cron_py: Path | None, cron_backup_path: Path | None
) -> None:
    if cron_py is None:
        return
    if cron_backup_path is not None and cron_backup_path.exists():
        raise ValueError(
            "install state incomplete; cron backup exists without manifest; "
            "restore or remove patch before installing"
        )
    if cron_py.exists():
        current_cron = _read_text_preserve_newlines(cron_py)
        if remove_cron_patch(current_cron) == current_cron:
            return
        raise ValueError(
            "install state incomplete; cron scheduler already contains patch; "
            "restore or remove patch before installing"
        )


def _validate_complete_cron_install_state(
    cron_py: Path | None,
    cron_backup_path: Path | None,
    manifest: dict[str, object] | None,
    operation: str,
) -> None:
    if manifest is None or not _manifest_has_cron(manifest):
        return
    if cron_py is None or cron_backup_path is None:
        raise ValueError(f"cron scheduler changed since install; refusing to {operation}")
    if not cron_py.exists():
        raise ValueError(f"cron scheduler changed since install; refusing to {operation}")
    if not cron_backup_path.exists():
        raise ValueError(f"cron backup changed since install; refusing to {operation}")

    cron_patched_sha256 = manifest.get("cron_patched_sha256")
    if not isinstance(cron_patched_sha256, str) or not cron_patched_sha256:
        raise ValueError("manifest missing cron patched sha256")
    if file_sha256(cron_py) != cron_patched_sha256:
        raise ValueError(f"cron scheduler changed since install; refusing to {operation}")

    cron_backup_sha256 = manifest.get("cron_backup_sha256")
    if not isinstance(cron_backup_sha256, str) or not cron_backup_sha256:
        raise ValueError("manifest missing cron backup sha256")
    if file_sha256(cron_backup_path) != cron_backup_sha256:
        raise ValueError(f"cron backup changed since install; refusing to {operation}")

    cron_current = _read_text_preserve_newlines(cron_py)
    cron_backup_text = _read_text_preserve_newlines(cron_backup_path)
    if remove_cron_patch(cron_backup_text) != cron_backup_text:
        raise ValueError(f"cron backup changed since install; refusing to {operation}")
    try:
        restored_cron = remove_cron_patch(cron_current)
    except ValueError as exc:
        raise ValueError(
            f"cron scheduler changed since install; refusing to {operation}"
        ) from exc
    if restored_cron != cron_backup_text:
        raise ValueError(f"cron scheduler changed since install; refusing to {operation}")


def _manifest_has_cron(manifest: dict[str, object]) -> bool:
    return "cron_py" in manifest or "cron_patched_sha256" in manifest


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
