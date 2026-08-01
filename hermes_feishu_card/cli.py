from __future__ import annotations

import argparse
import asyncio
import hashlib
from ipaddress import ip_address
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from pathlib import Path
from typing import Any, Callable

import yaml

from hermes_feishu_card import __version__ as PACKAGE_VERSION
from hermes_feishu_card.config import load_config
from hermes_feishu_card.delivery_policy import normalize_native_chats
from hermes_feishu_card.bots import BotRegistry, RoutingContext
from hermes_feishu_card.diagnostics import (
    DiagnosticReport,
    build_diagnostic_report,
    build_route_diagnostics,
    format_diagnostic_text,
    safe_event_endpoint_for_output,
)
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.feishu_client import FeishuAPIError, FeishuClient, FeishuClientConfig
from hermes_feishu_card.install.detect import HermesDetection, detect_hermes
from hermes_feishu_card.install.envfile import (
    read_hfc_env,
    render_hfc_env,
    update_hfc_env,
)
from hermes_feishu_card.install.manifest import (
    BASE_INSTALL_MANIFEST_FIELDS,
    CRON_INSTALL_MANIFEST_FIELDS,
    CURRENT_INSTALL_MANIFEST_VERSION,
    file_sha256,
    validate_install_manifest,
)
from hermes_feishu_card.install.integrity import (
    IntegrityRepairRefused,
    build_integrity_provenance,
    plan_integrity_repair,
    render_integrity_manifest_migration,
)
from hermes_feishu_card.install.recovery import (
    RecoveryRefused,
    _first_refusal,
    execute_recovery,
    plan_recovery,
)
from hermes_feishu_card.install.patcher import (
    apply_base_patch,
    apply_patch,
    apply_cron_patch,
    remove_base_patch,
    remove_patch,
    remove_cron_patch,
    remove_patch_lenient,
)
from hermes_feishu_card.integrity import (
    build_runtime_integrity_fence_binding,
    sanitize_integrity_snapshot,
)
from hermes_feishu_card.maintenance_process import (
    inspect_runtime,
    launch_job,
    provision_runtime,
)
from hermes_feishu_card.maintenance_store import (
    MaintenanceRefused,
    load_job,
    load_verified_artifact,
    maintenance_paths,
    stage_job_credentials,
    stage_wheel_artifact,
)
from hermes_feishu_card.runtime_control import (
    acknowledge_runtime_integrity_review,
    inspect_runtime_integrity_review,
)
from hermes_feishu_card.process import (
    PIDFILE_NAME,
    fetch_health,
    start_sidecar,
    status_sidecar,
    stop_sidecar,
)
from hermes_feishu_card.render import render_card
from hermes_feishu_card.server import python_executable_identity
from hermes_feishu_card.session import CardSession


BACKUP_SUFFIX = ".hermes_feishu_card.bak"
MANIFEST_NAME = ".hermes_feishu_card_manifest"
INSTALL_MANIFEST_VERSION = CURRENT_INSTALL_MANIFEST_VERSION
_BASE_MANIFEST_FIELDS = BASE_INSTALL_MANIFEST_FIELDS
_CRON_MANIFEST_FIELDS = CRON_INSTALL_MANIFEST_FIELDS
DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
COMPOSE_HOST_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
FEISHU_SDK_INSTALL_SPEC = "lark-oapi==1.6.8"
FEISHU_SDK_REQUIRED_PARAMETER = "extra_ua_tags"
_RestoreIdentity = tuple[int, int]
_RestoreEvidenceSnapshot = tuple[int, int, str]
_CLI_SNAPSHOT_UNSET = object()
OFFICIAL_INSTALLER_COMMAND = (
    "bash <(curl -fsSL "
    "https://raw.githubusercontent.com/baileyh8/"
    "hermes-feishu-streaming-card/main/install.sh)"
)


class _CliTargetBinding:
    def __init__(
        self,
        path: Path,
        parent_fd: int,
        parent_identity: _RestoreIdentity,
        initial_snapshot: _RestoreEvidenceSnapshot | None,
        initial_bytes: bytes | None,
        initial_mode: int | None,
    ) -> None:
        self.path = path
        self.parent_fd = parent_fd
        self.parent_identity = parent_identity
        self.initial_snapshot = initial_snapshot
        self.initial_bytes = initial_bytes
        self.initial_mode = initial_mode

    @property
    def basename(self) -> str:
        return self.path.name

    def close(self) -> None:
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


class _CliStagedText:
    def __init__(
        self,
        basename: str,
        identity: _RestoreIdentity,
        digest: str,
        mode: int,
    ) -> None:
        self.basename = basename
        self.identity = identity
        self.digest = digest
        self.mode = mode
        self.consumed = False


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
    if args.command == "integrity":
        return _run_integrity(args)
    if args.command == "chats":
        return _run_chats(args)
    if args.command == "maintenance":
        return _run_maintenance(args)
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
    doctor.add_argument("--profile-id")
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
    setup.add_argument("--env-file")
    setup.add_argument("--profile-id")
    setup.add_argument("--event-url")
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
        "--no-repair",
        action="store_true",
        help="do not automatically repair known-safe Hermes hook install state",
    )
    setup.add_argument(
        "--accept-hermes-upgrade",
        action="store_true",
        help=(
            "accept a supported unpatched Hermes source replacement and clear "
            "only verified stale HFC install state"
        ),
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
        process_parser.add_argument("--env-file")
        if command in {"start", "status"}:
            process_parser.add_argument("--hermes-dir")

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

    integrity = subparsers.add_parser(
        "integrity",
        help="inspect or explicitly migrate runtime integrity controls",
    )
    integrity_subparsers = integrity.add_subparsers(dest="integrity_command")
    integrity_migrate = integrity_subparsers.add_parser("migrate-safe")
    integrity_migrate.add_argument("--config", required=True)
    integrity_migrate.add_argument("--hermes-dir", required=True)
    integrity_migrate.add_argument("--env-file")
    integrity_migrate.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="confirm provenance migration and safe-mode activation",
    )
    integrity_acknowledge = integrity_subparsers.add_parser("acknowledge-review")
    integrity_acknowledge.add_argument("--config", required=True)
    integrity_acknowledge.add_argument("--hermes-dir", required=True)
    integrity_acknowledge.add_argument(
        "--env-file",
        help=(
            "configuration loading only; the state directory must be provided "
            "with --state-dir"
        ),
    )
    integrity_acknowledge.add_argument(
        "--state-dir",
        required=True,
        help=(
            "explicit sidecar state directory; this is never inferred from "
            "--env-file"
        ),
    )
    integrity_acknowledge.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="confirm clearing only the verified manual-review fence",
    )

    chats = subparsers.add_parser("chats")
    chat_subparsers = chats.add_subparsers(dest="chat_command")
    for chat_command in ("use-native", "use-card"):
        command_parser = chat_subparsers.add_parser(chat_command)
        command_parser.add_argument("chat_id")
        command_parser.add_argument("--config", required=True)
        command_parser.add_argument("--profile-id")
    chats_list = chat_subparsers.add_parser("list")
    chats_list.add_argument("--config", required=True)

    maintenance = subparsers.add_parser(
        "maintenance",
        help="provision or inspect the independent Hermes update runtime",
    )
    maintenance_subparsers = maintenance.add_subparsers(dest="maintenance_command")
    maintenance_provision = maintenance_subparsers.add_parser("provision")
    maintenance_provision.add_argument("--hermes-dir", required=True)
    maintenance_provision.add_argument("--wheel", required=True)
    maintenance_provision.add_argument("--root")
    maintenance_status = maintenance_subparsers.add_parser("status")
    maintenance_status.add_argument(
        "--hermes-dir",
        default=str(Path.home() / ".hermes" / "hermes-agent"),
    )
    maintenance_status.add_argument("--root")
    for command in ("run", "resume"):
        maintenance_run = maintenance_subparsers.add_parser(command)
        maintenance_run.add_argument("--job", required=True)
    chats_list.add_argument("--profile-id")

    for command in ("install", "repair", "restore", "uninstall"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--hermes-dir", required=True)
        command_parser.add_argument("--yes", action="store_true", required=True)
        if command == "install":
            command_parser.add_argument("--no-repair", action="store_true")
        if command in {"install", "repair"}:
            command_parser.add_argument(
                "--accept-hermes-upgrade",
                action="store_true",
                help=(
                    "accept a supported unpatched Hermes source replacement "
                    "and clear only verified stale HFC install state"
                ),
            )
    return parser


def _run_setup(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    try:
        route_settings = _resolve_route_settings(args, config_path)
        update_hfc_env(
            route_settings["env_path"],
            {
                "HERMES_FEISHU_CARD_PROFILE_ID": route_settings["profile_id"],
                "HERMES_FEISHU_CARD_EVENT_URL": route_settings["event_url"],
                "HERMES_DIR": str(Path(args.hermes_dir).expanduser()),
            },
        )
        created = _ensure_setup_config(config_path)
        selected_env_path = route_settings["env_path"]
        config = (
            load_config(config_path, env_file=selected_env_path)
            if selected_env_path != config_path.parent / ".env"
            else load_config(config_path)
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"config: {'created' if created else 'existing'} {config_path}")
    detection = detect_hermes(args.hermes_dir)
    diagnostic_args = argparse.Namespace(
        hermes_dir=args.hermes_dir,
        skip_hermes=False,
        _profile_id=route_settings["profile_id"],
        _profile_source=route_settings["profile_source"],
        _event_url=route_settings["event_url"],
    )
    report = _build_doctor_report(config_path, config, diagnostic_args)
    if isinstance(report, DiagnosticReport):
        print(_format_route_chain(report))

    profile_id = route_settings["profile_id"]
    if not _profile_exists(config, profile_id):
        print(
            "error: profile_unknown: selected profile is not present in config",
            file=sys.stderr,
        )
        return 1
    if not _has_feishu_credentials(config, profile_id):
        print(
            (
                "error: profile_credentials_missing: Feishu credentials are required before setup installs "
                "the Hermes hook. Set FEISHU_APP_ID and FEISHU_APP_SECRET, or "
                f"fill feishu.app_id and feishu.app_secret in {config_path}."
            ),
            file=sys.stderr,
        )
        return 1

    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1
    try:
        verified_hermes_root = _verified_explicit_hermes_root(
            args.hermes_dir,
            detection=detection,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("doctor: ok")
    print(_format_hermes_detection(detection))
    _print_hermes_streaming_guidance(Path(args.hermes_dir))

    if args.repair and not args.no_repair:
        repair_code = _run_repair(
            argparse.Namespace(
                hermes_dir=args.hermes_dir,
                yes=True,
                accept_hermes_upgrade=args.accept_hermes_upgrade,
            )
        )
        if repair_code != 0:
            return repair_code

    install_code = _run_install(
        argparse.Namespace(
            hermes_dir=args.hermes_dir,
            yes=True,
            no_repair=args.no_repair,
            accept_hermes_upgrade=args.accept_hermes_upgrade,
        )
    )
    if install_code != 0:
        return install_code

    _provision_setup_maintenance(Path(args.hermes_dir).expanduser())

    try:
        runtime_python, runtime_identity = _resolve_start_runtime_identity(
            verified_hermes_root
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.skip_start:
        print("start: skipped")
        print("setup ok")
        return 0

    try:
        default_env_path = config_path.parent / ".env"
        start_kwargs: dict[str, Any] = {
            "hermes_dir": verified_hermes_root,
            "python_executable": runtime_python,
            "expected_package_version": PACKAGE_VERSION,
            "expected_python_identity": runtime_identity,
        }
        if route_settings["env_path"] != default_env_path:
            start_kwargs["env_file"] = route_settings["env_path"]
        start_result = start_sidecar(config_path, config, **start_kwargs)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if start_result.startswith("failed:"):
        _print_sidecar_start_failure(start_result)
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
    print(f"manager: {status.get('manager', 'unknown')}")
    print("setup ok")
    return 0


def _run_maintenance(args: argparse.Namespace) -> int:
    command = str(getattr(args, "maintenance_command", "") or "")
    try:
        if command == "provision":
            paths = maintenance_paths(
                Path(args.root).expanduser() if args.root else None
            )
            artifact = stage_wheel_artifact(
                paths,
                Path(args.wheel).expanduser(),
                expected_version=PACKAGE_VERSION,
                source_kind="cli_provision",
            )
            status = provision_runtime(
                paths,
                artifact,
                hermes_root=Path(args.hermes_dir).expanduser(),
            )
            print(
                "maintenance: "
                + ("ready" if status.available else status.reason_code)
            )
            if status.available:
                print(f"version: {status.package_version}")
                print(f"python: {status.python_path}")
                return 0
            return 1
        if command == "status":
            paths = maintenance_paths(
                Path(args.root).expanduser() if args.root else None
            )
            artifact = load_verified_artifact(
                paths, expected_version=PACKAGE_VERSION
            )
            status = inspect_runtime(
                paths,
                artifact,
                hermes_root=Path(args.hermes_dir).expanduser(),
            )
            print(
                "maintenance: "
                + ("ready" if status.available else status.reason_code)
            )
            print(f"version: {status.package_version or 'unavailable'}")
            print(f"python: {status.python_path}")
            return 0 if status.available else 1
        if command in {"run", "resume"}:
            job = load_job(Path(args.job).expanduser())
            paths = maintenance_paths(job.path.parent.parent)
            artifact = load_verified_artifact(
                paths,
                expected_version=job.artifact_version,
            )
            status = inspect_runtime(
                paths,
                artifact,
                hermes_root=job.hermes_root,
            )
            if not status.available:
                print(f"maintenance: unavailable ({status.reason_code})")
                return 1
            if all(
                str(os.environ.get(key) or "").strip()
                for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET")
            ):
                stage_job_credentials(
                    paths,
                    job_id=job.job_id,
                    environment=os.environ,
                )
            launched = launch_job(status, job)
            print(
                "maintenance: "
                + ("started" if launched.started else launched.reason_code)
            )
            print(f"manager: {launched.manager}")
            return 0 if launched.started else 1
    except (MaintenanceRefused, OSError, ValueError) as exc:
        print(f"maintenance: unavailable ({exc})", file=sys.stderr)
        return 1
    print("maintenance command is required", file=sys.stderr)
    return 2


def _provision_setup_maintenance(hermes_root: Path) -> bool:
    spec = _runtime_install_spec()
    if not spec:
        print("maintenance: unavailable (exact install spec missing)")
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="hfc-maintenance-wheel-") as temp:
            destination = Path(temp)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--wheel-dir",
                    str(destination),
                    spec,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
            wheels = sorted(destination.glob("*.whl"))
            if completed.returncode != 0 or len(wheels) != 1:
                print("maintenance: unavailable (wheel preparation failed)")
                return False
            paths = maintenance_paths()
            artifact = stage_wheel_artifact(
                paths,
                wheels[0],
                expected_version=PACKAGE_VERSION,
                source_kind="setup_install_spec",
            )
            status = provision_runtime(
                paths,
                artifact,
                hermes_root=hermes_root,
            )
            if not status.available:
                print(f"maintenance: unavailable ({status.reason_code})")
                return False
            print("maintenance: ready")
            return True
    except (MaintenanceRefused, OSError, subprocess.SubprocessError, ValueError):
        print("maintenance: unavailable (provisioning failed)")
        return False


def _has_feishu_credentials(
    config: dict[str, Any], profile_id: str = ""
) -> bool:
    selected = _profile_config(config, profile_id)
    feishu = selected.get("feishu", {})
    if not isinstance(feishu, dict):
        return False
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

# New installations verify that the patched Hermes runtime stays active.
# Existing configs without this section remain notification-only after upgrade.
integrity:
  mode: safe

service:
  manager: auto

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
  # Optional roles: body, reasoning, tool, notice, footer.
  # card width/height are controlled by the Feishu/Lark client.
  # text_sizes:
  #   body: normal
  #   footer:
  #     default: x-small
  #     pc: x-small
  #     mobile: notation
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
        route_settings = _resolve_route_settings(args, config_path)
        args._profile_id = route_settings["profile_id"]
        args._profile_source = route_settings["profile_source"]
        args._event_url = route_settings["event_url"]
        config = load_config(config_path)
    except Exception as exc:
        if args.json_output:
            print(
                json.dumps(
                    _doctor_json_output_payload(_doctor_error_report(config_path, exc)),
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
        payload = report.to_dict() if isinstance(report, DiagnosticReport) else report
        if args.json_output:
            print(
                json.dumps(
                    _doctor_json_output_payload(payload),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            if isinstance(report, DiagnosticReport):
                print(
                    f"{format_diagnostic_text(report, explain=True)}\n\n"
                    f"{_format_route_chain(report)}"
                )
            else:
                explanation = _format_doctor_explanation(report)
                if isinstance(report.get("routing"), dict):
                    explanation = f"{explanation}\n\n{_format_route_chain(report)}"
                print(explanation)
        return _doctor_exit_code(payload)

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
            feishu_sdk = _doctor_feishu_sdk_report(detection)
            print(
                "feishu_sdk: "
                f"{feishu_sdk['status']} - {feishu_sdk.get('message', '')}"
            )
            _print_hermes_streaming_guidance(Path(args.hermes_dir))
        return 0 if detection.supported else 1
    print("hermes: not checked")
    return 0


def _doctor_json_output_payload(payload: dict[str, Any]) -> dict[str, Any]:
    output = _redact_doctor_json_paths(payload)
    routing = output.get("routing")
    if not isinstance(routing, dict):
        return output
    output_routing = dict(routing)
    endpoint = output_routing.get("event_endpoint")
    if isinstance(endpoint, str):
        output_routing["event_endpoint"] = safe_event_endpoint_for_output(endpoint)
    output["routing"] = output_routing
    return output


_DOCTOR_JSON_PATH_KEYS = frozenset(
    {
        "backup_path",
        "config_path",
        "cron_backup_path",
        "cron_py",
        "base_backup_path",
        "base_py",
        "manifest_path",
        "path",
        "python",
        "root",
        "run_py",
        "suggested_root",
    }
)


def _redact_doctor_json_paths(value: Any, key: str = "") -> Any:
    if key in _DOCTOR_JSON_PATH_KEYS and isinstance(value, str):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            child_key: _redact_doctor_json_paths(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_doctor_json_paths(item) for item in value]
    if isinstance(value, str):
        return _redact_absolute_paths_in_text(value)
    return value


_DOCTOR_JSON_ABSOLUTE_PATH_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:"
    r"/[^\s\"'<>/\\]+/"
    r"|[A-Za-z]:\\"
    r"|\\\\[^\s\"'<>/\\]+\\[^\s\"'<>/\\]+"
    r")"
)


def _redact_absolute_paths_in_text(value: str) -> str:
    if not _DOCTOR_JSON_ABSOLUTE_PATH_PREFIX_RE.search(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"[redacted-path-text:{digest}]"


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
        "feishu_sdk": {
            "checked": False,
            "status": "skipped",
            "message": "Hermes Feishu SDK was not checked because config loading failed.",
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
) -> dict[str, Any] | DiagnosticReport:
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
                "configured"
                if _has_feishu_credentials(config, getattr(args, "_profile_id", ""))
                else "missing"
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
        "feishu_sdk": {
            "checked": False,
            "status": "not_checked",
            "message": "Hermes Feishu SDK was not checked.",
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
        report["feishu_sdk"] = {
            "checked": False,
            "status": "skipped",
            "message": "Hermes Feishu SDK was skipped by request.",
        }
        recommendations.append(
            {
                "severity": "info",
                "code": "hermes_check_skipped",
                "message": "Hermes detection was skipped.",
                "next_step": "Run doctor with --hermes-dir to check hook compatibility.",
            }
        )
        return _finalize_doctor_report(_attach_route_diagnostics(report, config, args))

    if not args.hermes_dir:
        recommendations.append(
            {
                "severity": "info",
                "code": "hermes_not_checked",
                "message": "Hermes detection was not checked.",
                "next_step": "Run doctor with --hermes-dir PATH to check hook compatibility.",
            }
        )
        return _finalize_doctor_report(_attach_route_diagnostics(report, config, args))

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
        report["feishu_sdk"] = {
            "checked": False,
            "status": "skipped",
            "message": "Hermes Feishu SDK was skipped because Hermes is unsupported.",
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
        status_callback_missing = detection.capabilities.get("status_callback") is False
        recommendations.append(
            {
                "severity": "warning",
                "code": "hermes_compatibility_partial",
                "message": (
                    "Hermes is supported, but optional compatibility anchors are missing."
                ),
                "next_step": (
                    "Review anchors.status_callback before relying on "
                    "context-compaction visibility."
                    if status_callback_missing
                    else "Review the anchors section if streaming, cron, reply, or "
                    "attachment features do not behave as expected."
                ),
            }
        )

    runtime_import = _doctor_runtime_import_report(detection)
    report["runtime_import"] = runtime_import
    _append_runtime_import_recommendation(recommendations, runtime_import)
    feishu_sdk = _doctor_feishu_sdk_report(detection)
    report["feishu_sdk"] = feishu_sdk

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
    recovery_plan = plan_recovery(detection)
    profile_id = str(getattr(args, "_profile_id", "") or "")
    route = _diagnostic_route(config, profile_id)
    try:
        sidecar_status = status_sidecar(config)
    except (OSError, RuntimeError, ValueError):
        sidecar_status = {}
    live_health = sidecar_status.get("health")
    health: dict[str, object] = (
        dict(live_health) if isinstance(live_health, dict) else {}
    )
    health.update({
        "streaming": streaming,
        "runtime_import": runtime_import,
        "feishu_sdk": feishu_sdk,
        "install_state": install_state,
    })
    if route is not None:
        health["routing"] = {"last_route": route}
    return build_diagnostic_report(
        config_path,
        config,
        detection,
        recovery_plan,
        health=health,
        profile_id=profile_id,
        profile_source=str(getattr(args, "_profile_source", "") or ""),
        event_url=str(getattr(args, "_event_url", "") or ""),
    )


def _resolve_route_settings(
    args: argparse.Namespace, config_path: Path
) -> dict[str, Any]:
    explicit_env_path = getattr(args, "env_file", None)
    raw_env_path = explicit_env_path or os.environ.get("HFC_ENV_FILE")
    env_path = (
        Path(raw_env_path).expanduser()
        if raw_env_path
        else config_path.parent / ".env"
    )
    env_values = read_hfc_env(env_path)
    profile_id, profile_source = _resolve_route_value(
        getattr(args, "profile_id", None),
        "HERMES_FEISHU_CARD_PROFILE_ID",
        env_values,
        "default",
        "fallback_default",
    )
    event_url, event_source = _resolve_route_value(
        getattr(args, "event_url", None),
        "HERMES_FEISHU_CARD_EVENT_URL",
        env_values,
        DEFAULT_EVENT_URL,
        "default",
    )
    profile_id = _validate_profile_id(profile_id)
    event_url = _validate_event_url(event_url)
    return {
        "env_path": env_path,
        "profile_id": profile_id,
        "profile_source": profile_source,
        "event_url": event_url,
        "event_source": event_source,
    }


def _resolve_route_value(
    explicit: str | None,
    env_key: str,
    env_values: dict[str, str],
    default: str,
    default_source: str,
) -> tuple[str, str]:
    if explicit is not None:
        return str(explicit), "argument"
    process_value = os.environ.get(env_key)
    if process_value is not None and process_value.strip():
        return process_value, "env"
    file_value = env_values.get(env_key)
    if file_value is not None and file_value.strip():
        return file_value, "env_file"
    return default, default_source


def _validate_profile_id(value: str) -> str:
    profile_id = str(value).strip()
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("invalid profile id; use 1-64 letters, digits, '.', '_', or '-'")
    return profile_id


def _validate_event_url(value: str) -> str:
    text = str(value).strip()
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid event URL") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/events")
        or not _allowed_event_host(parsed.hostname)
    ):
        raise ValueError("invalid event URL")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def _allowed_event_host(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host in {"localhost", "host.docker.internal"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return bool(COMPOSE_HOST_PATTERN.fullmatch(host))


def _profile_exists(config: dict[str, Any], profile_id: str) -> bool:
    profiles = config.get("profiles")
    if isinstance(profiles, dict) and profiles:
        return profile_id in profiles
    return profile_id == "default"


def _profile_config(config: dict[str, Any], profile_id: str) -> dict[str, Any]:
    profiles = config.get("profiles")
    if isinstance(profiles, dict) and profiles:
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict):
            return {}
        selected = dict(config)
        selected.update(profile)
        selected["profiles"] = {}
        return selected
    return config


def _diagnostic_route(
    config: dict[str, Any], profile_id: str
) -> dict[str, object] | None:
    if not _profile_exists(config, profile_id):
        return None
    try:
        registry = BotRegistry.from_config(_profile_config(config, profile_id))
        route = registry.resolve(RoutingContext(chat_id="", profile_id=profile_id))
    except (KeyError, TypeError, ValueError):
        return None
    return {"bot_id": route.bot_id, "reason": route.reason}


def _attach_route_diagnostics(
    report: dict[str, Any], config: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    if getattr(args, "profile_id", None) is None:
        return report
    profile_id = str(getattr(args, "_profile_id", "") or "")
    routing, findings = build_route_diagnostics(
        config,
        profile_id=profile_id,
        profile_source=str(getattr(args, "_profile_source", "") or ""),
        event_url=str(getattr(args, "_event_url", "") or ""),
        route=_diagnostic_route(config, profile_id),
    )
    report["routing"] = routing
    recommendations = report.setdefault("recommendations", [])
    recommendations.extend(
        {
            "severity": finding.severity,
            "code": finding.code,
            "message": finding.message,
            "next_step": finding.actions[0] if finding.actions else "",
        }
        for finding in findings
    )
    return report


def _format_route_chain(report: DiagnosticReport | dict[str, Any]) -> str:
    if isinstance(report, DiagnosticReport):
        routing = report.routing
        finding_codes = [finding.code for finding in report.findings]
    else:
        routing = report.get("routing", {})
        recommendations = report.get("recommendations", [])
        finding_codes = [
            str(item.get("code") or "")
            for item in recommendations
            if isinstance(item, dict)
        ]
    profile_id = str(routing.get("profile_id") or "")
    profile_exists = bool(routing.get("profile_exists"))
    endpoint = safe_event_endpoint_for_output(
        str(routing.get("event_endpoint") or "")
    )
    lines = [
        "Route Chain",
        f"- identity_source: {routing.get('profile_source') or 'unknown'}",
        f"- profile_id: {profile_id or 'missing'}",
        f"- event_endpoint: {endpoint or 'missing'}",
        f"- config_profile: {profile_id if profile_exists else 'missing'}",
        f"- bot_id: {routing.get('bot_id') or 'missing'}",
        f"- route_reason: {routing.get('route_reason') or 'missing'}",
    ]
    route_codes = {
        "profile_identity_missing",
        "profile_unknown",
        "profile_credentials_missing",
        "event_endpoint_mismatch",
        "bot_unknown",
        "route_fallback",
    }
    findings = [code for code in finding_codes if code in route_codes]
    if findings:
        lines.append(f"- findings: {', '.join(findings)}")
    return "\n".join(lines)


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
        "base_py": str(detection.base_py) if detection.base_py is not None else None,
        "base_py_exists": detection.base_py_exists,
        "base_required": detection.base_required,
        "base_hook_strategy": detection.base_hook_strategy,
        "exact_delivery_contract": _exact_delivery_contract_status(detection),
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


def _exact_delivery_contract_status(detection: HermesDetection) -> str:
    if not detection.base_required:
        return "not_required"
    if detection.capabilities.get("exact_base_delivery") is True:
        return "ready"
    return "missing_or_unsupported"


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


def _doctor_feishu_sdk_report(detection: HermesDetection) -> dict[str, Any]:
    if not _hermes_requires_feishu_sdk_capability(detection.root):
        return {
            "checked": False,
            "status": "not_required",
            "version": None,
            "supports_extra_ua_tags": None,
            "message": (
                "Hermes Feishu adapter does not require the extra_ua_tags SDK capability."
            ),
        }
    runtime_python = _detect_hermes_runtime_python(detection.root)
    if runtime_python is None:
        return {
            "checked": False,
            "status": "skipped",
            "version": None,
            "supports_extra_ua_tags": None,
            "message": "Hermes runtime venv Python was not found.",
        }
    return _check_runtime_feishu_sdk(runtime_python)


def _hermes_requires_feishu_sdk_capability(hermes_root: Path | str) -> bool:
    adapter = (
        Path(hermes_root).expanduser()
        / "plugins"
        / "platforms"
        / "feishu"
        / "adapter.py"
    )
    try:
        return FEISHU_SDK_REQUIRED_PARAMETER in adapter.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False


def _check_runtime_feishu_sdk(runtime_python: Path) -> dict[str, Any]:
    code = (
        "import inspect, json; "
        "from importlib.metadata import version; "
        "from lark_oapi.ws import Client; "
        "print(json.dumps({"
        "'version': version('lark-oapi'), "
        "'supports_extra_ua_tags': "
        "'extra_ua_tags' in inspect.signature(Client.__init__).parameters"
        "}))"
    )
    cwd = _hermes_runtime_cwd(runtime_python)
    try:
        result = subprocess.run(
            [str(runtime_python), "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(cwd) if cwd is not None else None,
        )
    except subprocess.TimeoutExpired:
        return {
            "checked": True,
            "status": "failed",
            "python": str(runtime_python),
            "version": None,
            "supports_extra_ua_tags": False,
            "message": "Hermes Feishu SDK compatibility check timed out.",
        }
    except OSError as exc:
        return {
            "checked": True,
            "status": "failed",
            "python": str(runtime_python),
            "version": None,
            "supports_extra_ua_tags": False,
            "message": (
                "Hermes Feishu SDK compatibility check could not start: "
                f"{exc.__class__.__name__}"
            ),
        }
    if result.returncode != 0:
        return {
            "checked": True,
            "status": "failed",
            "python": str(runtime_python),
            "version": None,
            "supports_extra_ua_tags": False,
            "message": (
                "Hermes runtime cannot load a compatible lark-oapi SDK: "
                f"{_summarize_process_output(result)}"
            ),
        }
    try:
        metadata = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {
            "checked": True,
            "status": "failed",
            "python": str(runtime_python),
            "version": None,
            "supports_extra_ua_tags": False,
            "message": "Hermes runtime returned invalid Feishu SDK metadata.",
        }
    version = str(metadata.get("version") or "").strip() or None
    supported = metadata.get("supports_extra_ua_tags") is True
    if supported:
        return {
            "checked": True,
            "status": "ok",
            "python": str(runtime_python),
            "version": version,
            "supports_extra_ua_tags": True,
            "message": (
                f"lark-oapi {version or 'unknown'} supports extra_ua_tags."
            ),
        }
    return {
        "checked": True,
        "status": "failed",
        "python": str(runtime_python),
        "version": version,
        "supports_extra_ua_tags": False,
        "message": (
            f"lark-oapi {version or 'unknown'} does not support extra_ua_tags."
        ),
    }


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
        root / "gateway" / "venv" / "bin" / "python",
        root / "gateway" / "venv" / "bin" / "python3",
        root / "gateway" / ".venv" / "bin" / "python",
        root / "gateway" / ".venv" / "bin" / "python3",
        root / "gateway" / "venv" / "Scripts" / "python.exe",
        root / "gateway" / ".venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                return candidate.parent.resolve(strict=True) / candidate.name
            except (OSError, RuntimeError):
                continue
    return None


def _resolve_start_runtime_identity(
    hermes_root: Path | str | None = None,
) -> tuple[Path, str]:
    if hermes_root is None:
        candidate = Path(sys.executable).expanduser()
        try:
            runtime_python = (
                candidate.parent.resolve(strict=True) / candidate.name
                if candidate.is_file()
                else None
            )
        except (OSError, RuntimeError):
            runtime_python = None
    else:
        runtime_python = _detect_hermes_runtime_python(hermes_root)
    if runtime_python is None:
        raise ValueError(
            "Sidecar runtime Python could not be verified. Rerun the official "
            f"installer: {OFFICIAL_INSTALLER_COMMAND}"
        )
    report = _check_runtime_hook_import(runtime_python)
    if report.get("status") != "ok" or report.get("version") != PACKAGE_VERSION:
        raise ValueError(
            "Sidecar runtime package does not match this CLI release. Rerun the "
            f"official installer: {OFFICIAL_INSTALLER_COMMAND}"
        )
    if hermes_root is not None and not _runtime_report_uses_installed_package(
        report, runtime_python
    ):
        raise ValueError(
            "Hermes runtime package is not an isolated site-packages install. "
            f"Rerun the official installer: {OFFICIAL_INSTALLER_COMMAND}"
        )
    return runtime_python, python_executable_identity(runtime_python)


def _verified_explicit_hermes_root(
    hermes_root: Path | str,
    *,
    detection: HermesDetection | None = None,
) -> Path:
    verified = detection if detection is not None else detect_hermes(hermes_root)
    if not verified.supported:
        raise ValueError("Explicit Hermes root could not be verified")
    try:
        return Path(verified.root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Explicit Hermes root could not be canonicalized") from exc


def _runtime_report_uses_installed_package(
    report: dict[str, Any], runtime_python: Path
) -> bool:
    raw_location = str(report.get("location") or "").strip()
    raw_prefix = str(report.get("prefix") or "").strip()
    raw_roots = {
        str(report.get(name) or "").strip()
        for name in ("purelib", "platlib")
    }
    if not raw_location or not raw_prefix:
        return False
    try:
        location = Path(raw_location).resolve(strict=True)
        prefix = Path(raw_prefix).resolve(strict=True)
        expected_prefix = runtime_python.parent.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if prefix != expected_prefix:
        return False
    for raw_root in raw_roots:
        if not raw_root:
            continue
        try:
            root = Path(raw_root).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        root_belongs_to_runtime = root == prefix or prefix in root.parents
        if root_belongs_to_runtime and (location == root or root in location.parents):
            return True
    return False


def _check_runtime_hook_import(runtime_python: Path) -> dict[str, Any]:
    code = (
        "import json, sys, sysconfig; "
        "import hermes_feishu_card as package; "
        "import hermes_feishu_card.hook_runtime; "
        "print(json.dumps({"
        "'version': getattr(package, '__version__', ''), "
        "'location': getattr(package, '__file__', ''), "
        "'prefix': sys.prefix, "
        "'purelib': sysconfig.get_path('purelib') or '', "
        "'platlib': sysconfig.get_path('platlib') or ''"
        "}))"
    )
    cwd = _hermes_runtime_cwd(runtime_python)
    try:
        result = subprocess.run(
            [str(runtime_python), "-I", "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            cwd=str(cwd) if cwd is not None else None,
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
        try:
            metadata = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {
                "checked": True,
                "status": "failed",
                "python": str(runtime_python),
                "message": "Hermes runtime returned invalid package metadata.",
            }
        version = str(metadata.get("version", "")).strip()
        location = str(metadata.get("location", "")).strip()
        prefix = str(metadata.get("prefix", "")).strip()
        purelib = str(metadata.get("purelib", "")).strip()
        platlib = str(metadata.get("platlib", "")).strip()
        suffix = f" from {location}" if location else ""
        return {
            "checked": True,
            "status": "ok",
            "python": str(runtime_python),
            "version": version,
            "location": location,
            "prefix": prefix,
            "purelib": purelib,
            "platlib": platlib,
            "message": f"Hermes runtime can import hook_runtime{suffix}.",
        }
    detail = _summarize_process_output(result)
    return {
        "checked": True,
        "status": "failed",
        "python": str(runtime_python),
        "message": f"Hermes runtime cannot import hook_runtime: {detail}",
    }


def _hermes_runtime_cwd(runtime_python: Path) -> Path | None:
    try:
        root = runtime_python.resolve().parents[2]
    except (OSError, IndexError):
        return None
    return root if root.exists() else None


def _ensure_hermes_runtime_package(detection: HermesDetection) -> None:
    runtime_python = _detect_hermes_runtime_python(detection.root)
    if runtime_python is None:
        print("runtime package: skipped (Hermes venv Python not found)")
        return
    report = _check_runtime_hook_import(runtime_python)
    if report["status"] == "ok" and report.get("version") == PACKAGE_VERSION:
        print(f"runtime package: {PACKAGE_VERSION} import ok ({runtime_python})")
        return

    previous_version = report.get("version") if report["status"] == "ok" else None

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
    if report.get("version") != PACKAGE_VERSION:
        actual = report.get("version") or "unknown"
        raise ValueError(
            "Hermes runtime package version mismatch after install: "
            f"expected {PACKAGE_VERSION}, got {actual} from "
            f"{report.get('location') or runtime_python}."
        )
    if previous_version:
        print(
            f"runtime package: upgraded {previous_version} -> {PACKAGE_VERSION} "
            f"in {runtime_python}"
        )
    else:
        print(f"runtime package: installed into {runtime_python}")


def _ensure_hermes_feishu_sdk(detection: HermesDetection) -> None:
    if not _hermes_requires_feishu_sdk_capability(detection.root):
        return
    runtime_python = _detect_hermes_runtime_python(detection.root)
    if runtime_python is None:
        print("feishu sdk: skipped (Hermes venv Python not found)")
        return
    report = _check_runtime_feishu_sdk(runtime_python)
    if report["status"] == "ok":
        print(
            "feishu sdk: "
            f"lark-oapi {report.get('version') or 'unknown'} capability ok "
            f"({runtime_python})"
        )
        return

    previous_version = report.get("version")
    pip_version = _run_runtime_pip(runtime_python, ["--version"], timeout=20)
    if pip_version.returncode != 0:
        raise ValueError(
            "Hermes runtime Python pip is unavailable: "
            f"{_summarize_process_output(pip_version)}"
        )
    install = _run_runtime_pip(
        runtime_python,
        ["install", "--upgrade", FEISHU_SDK_INSTALL_SPEC],
        timeout=180,
    )
    if install.returncode != 0:
        raise ValueError(
            "failed to install a compatible Hermes Feishu SDK into runtime "
            f"Python {runtime_python}: {_summarize_process_output(install)}"
        )
    report = _check_runtime_feishu_sdk(runtime_python)
    if report["status"] != "ok":
        raise ValueError(report["message"])
    installed_version = report.get("version") or "unknown"
    if previous_version:
        print(f"feishu sdk: upgraded {previous_version} -> {installed_version}")
    else:
        print(f"feishu sdk: installed lark-oapi {installed_version}")


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
    base_py = detection.base_py if detection.base_required else None
    base_backup_path = _backup_path(base_py) if base_py is not None else None
    backup_exists = backup_path.exists()
    manifest_exists = manifest_path.exists()
    cron_backup_exists = (
        cron_backup_path.exists() if cron_backup_path is not None else False
    )
    base_backup_exists = (
        base_backup_path.exists() if base_backup_path is not None else False
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
        "base_backup_path": (
            str(base_backup_path) if base_backup_path is not None else None
        ),
        "base_backup_exists": base_backup_exists,
        "automatic_repair_available": False,
    }

    try:
        _validate_existing_install_state(
            run_py,
            backup_path,
            manifest_path,
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
            base_py=base_py,
            base_backup_path=base_backup_path,
            require_base_manifest=detection.base_required,
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

    if backup_exists or manifest_exists or cron_backup_exists or base_backup_exists:
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
        exact_contract = hermes.get("exact_delivery_contract")
        if exact_contract:
            lines.append(f"- Exact delivery contract: {exact_contract}")
    else:
        lines.append(f"- Hermes: {hermes_status}")

    runtime_import = report.get("runtime_import", {})
    if runtime_import.get("status"):
        lines.append(
            f"- Runtime import: {runtime_import['status']} - "
            f"{runtime_import.get('message', '')}"
        )

    feishu_sdk = report.get("feishu_sdk", {})
    if isinstance(feishu_sdk, dict) and feishu_sdk.get("status"):
        lines.append(
            f"- Feishu SDK: {feishu_sdk['status']} - "
            f"{feishu_sdk.get('message', '')}"
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
    version = detection.version
    if (
        detection.supported
        and version == "unknown"
        and detection.version_source == "gateway anchors"
    ):
        version = "unknown (source-stripped metadata)"
    lines = [
        f"hermes: {status}",
        f"hermes_root: {detection.root}",
        f"run_py: {detection.run_py}",
        f"run_py_exists: {run_py_exists}",
        f"cron_py: {detection.cron_py}",
        f"cron_py_exists: {'yes' if detection.cron_py_exists else 'no'}",
        f"version_source: {detection.version_source}",
        f"version: {version}",
        f"minimum_supported_version: {detection.minimum_version}",
        f"hook_strategy: {detection.hook_strategy}",
        f"cron_hook_strategy: {detection.cron_hook_strategy}",
        f"base_py: {detection.base_py}",
        f"base_py_exists: {'yes' if detection.base_py_exists else 'no'}",
        f"base_required: {'yes' if detection.base_required else 'no'}",
        f"base_hook_strategy: {detection.base_hook_strategy}",
        f"exact_delivery_contract: {_exact_delivery_contract_status(detection)}",
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
        hermes_root.parent / "config.yaml",
        hermes_root.parent / "config.yml",
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


def _configured_lifecycle_hermes_root(args: argparse.Namespace) -> Path | None:
    explicit = getattr(args, "hermes_dir", None)
    if explicit:
        return Path(explicit).expanduser()

    raw_env_path = getattr(args, "env_file", None) or os.environ.get("HFC_ENV_FILE")
    env_paths = []
    if raw_env_path:
        env_paths.append(Path(raw_env_path).expanduser())
    env_paths.append(Path(args.config).expanduser().parent / ".env")
    for env_path in env_paths:
        value = read_hfc_env(env_path).get("HERMES_DIR", "").strip()
        if value:
            return Path(value).expanduser()

    for name in ("HERMES_DIR", "HFC_HERMES_DIR", "HERMES_AGENT_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value).expanduser()
    return None


def _lifecycle_hook_check(args: argparse.Namespace) -> dict[str, object] | None:
    hermes_root = _configured_lifecycle_hermes_root(args)
    if hermes_root is None:
        return None

    detection = detect_hermes(hermes_root)
    if not detection.supported:
        return {
            "status": "manual_review_required",
            "blocking": True,
            "root": hermes_root,
        }
    try:
        verified_root = _verified_explicit_hermes_root(
            hermes_root,
            detection=detection,
        )
    except ValueError:
        return {
            "status": "manual_review_required",
            "blocking": True,
            "root": hermes_root,
        }

    plan = plan_recovery(detection)
    if plan.state == "installed" and not plan.actions:
        return {"status": "installed", "blocking": False, "root": verified_root}
    if plan.state == "clean":
        return {"status": "not_installed", "blocking": False, "root": verified_root}
    if plan.state == "stale_unpatched":
        accepted = plan_recovery(detection, accept_hermes_upgrade=True)
        if accepted.executable:
            return {
                "status": "upgrade_repair_required",
                "blocking": True,
                "root": verified_root,
            }
    return {
        "status": "manual_review_required",
        "blocking": True,
        "root": verified_root,
    }


def _print_lifecycle_hook_check(
    check: dict[str, object], *, file: Any = None
) -> None:
    output = sys.stdout if file is None else file
    status = str(check["status"])
    hermes_root = Path(check["root"])
    print(f"hook.status: {status}", file=output)
    if status == "upgrade_repair_required":
        repair_command = shlex.join(
            [
                "hermes-feishu-card",
                "install",
                "--hermes-dir",
                str(hermes_root),
                "--accept-hermes-upgrade",
                "--yes",
            ]
        )
        print(f"hook.next: {repair_command}", file=output)
        print("hook.restart: hermes gateway start", file=output)
    elif status == "manual_review_required":
        doctor_command = shlex.join(
            [
                "hermes-feishu-card",
                "doctor",
                "--config",
                str(Path(check.get("config", "config.yaml.example"))),
                "--hermes-dir",
                str(hermes_root),
                "--explain",
            ]
        )
        print(f"hook.next: {doctor_command}", file=output)


def _print_sidecar_start_failure(result: str) -> None:
    lowered = result.lower()
    pidfileless = (
        "no verified pidfile" in lowered
        or "has no pidfile" in lowered
        or "pidfile-less" in lowered
    )
    if pidfileless:
        print(
            "error: a running sidecar cannot be managed safely without a "
            "verified pidfile",
            file=sys.stderr,
        )
        print(
            "next: stop the old sidecar service manually, then rerun the "
            f"official installer: {OFFICIAL_INSTALLER_COMMAND}",
            file=sys.stderr,
        )
        return
    print(f"error: {result}", file=sys.stderr)


def _run_start(args: argparse.Namespace) -> int:
    try:
        config = (
            load_config(args.config, env_file=args.env_file)
            if args.env_file is not None
            else load_config(args.config)
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    hook_check = _lifecycle_hook_check(args)
    if hook_check is not None and bool(hook_check["blocking"]):
        hook_check["config"] = args.config
        _print_lifecycle_hook_check(hook_check, file=sys.stderr)
        return 1

    start_kwargs: dict[str, Any] = {}
    if args.env_file is not None:
        start_kwargs["env_file"] = args.env_file
    verified_hermes_root: Path | None = None
    if args.hermes_dir is not None:
        try:
            verified_hermes_root = (
                Path(hook_check["root"]).expanduser().resolve(strict=True)
                if hook_check is not None
                else _verified_explicit_hermes_root(args.hermes_dir)
            )
        except (OSError, RuntimeError, ValueError):
            print(
                "error: Explicit Hermes root could not be verified. Rerun the "
                f"official installer: {OFFICIAL_INSTALLER_COMMAND}",
                file=sys.stderr,
            )
            return 1
    try:
        runtime_python, runtime_identity = _resolve_start_runtime_identity(
            verified_hermes_root
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    start_kwargs.update(
        {
            "python_executable": runtime_python,
            "expected_package_version": PACKAGE_VERSION,
            "expected_python_identity": runtime_identity,
        }
    )
    if verified_hermes_root is not None:
        start_kwargs["hermes_dir"] = verified_hermes_root

    try:
        result = start_sidecar(args.config, config, **start_kwargs)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.startswith("failed:"):
        _print_sidecar_start_failure(result)
        return 1
    if result == "already running":
        print("start: already running")
        return 0
    print("start ok")
    return 0


def _run_stop(args: argparse.Namespace) -> int:
    try:
        config = (
            load_config(args.config, env_file=args.env_file)
            if args.env_file is not None
            else load_config(args.config)
        )
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


def _run_integrity(args: argparse.Namespace) -> int:
    if getattr(args, "integrity_command", None) == "acknowledge-review":
        return _run_integrity_acknowledge_review(args)
    if getattr(args, "integrity_command", None) != "migrate-safe":
        print("error: an integrity subcommand is required", file=sys.stderr)
        return 2
    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1
    config_path = Path(args.config).expanduser()
    env_path = (
        Path(args.env_file).expanduser()
        if args.env_file is not None
        else config_path.parent / ".env"
    )
    manifest_path = detection.root / MANIFEST_NAME
    bindings: list[_CliTargetBinding] = []
    try:
        if not _cli_dirfd_binding_supported():
            raise ValueError(
                "secure integrity migration requires directory-relative "
                "filesystem operations on this platform"
            )
        manifest_binding = _bind_cli_target(manifest_path)
        bindings.append(manifest_binding)
        if manifest_binding.initial_snapshot is None:
            raise ValueError("integrity migration requires a manifest")
        env_binding = _bind_cli_target(env_path)
        bindings.append(env_binding)
        manifest_text = (manifest_binding.initial_bytes or b"").decode("utf-8")
        _provenance, manifest_contents = render_integrity_manifest_migration(
            detection,
            manifest_text,
        )
        env_text = (env_binding.initial_bytes or b"").decode("utf-8")
        env_contents = render_hfc_env(
            env_text,
            {"HERMES_FEISHU_CARD_INTEGRITY_MODE": "safe"},
        )
        _write_targets_transactionally(
            [
                (manifest_path, manifest_contents),
                (env_path, env_contents),
            ],
            expected_identities={
                manifest_path: manifest_binding.initial_snapshot,
                env_path: env_binding.initial_snapshot,
            },
            expected_directories={
                manifest_path.parent: manifest_binding.parent_identity,
                env_path.parent: env_binding.parent_identity,
            },
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        for binding in bindings:
            try:
                binding.close()
            except OSError:
                pass
    print("integrity migration: verified")
    print("integrity mode: safe")
    print("sidecar.restart_required: true")
    print("gateway.restart_required: false")
    return 0


def _run_integrity_acknowledge_review(args: argparse.Namespace) -> int:
    try:
        first_binding = _verified_integrity_acknowledgement_binding(
            args.hermes_dir
        )
        config = (
            load_config(args.config, env_file=args.env_file)
            if args.env_file is not None
            else load_config(args.config)
        )
        target_state = _integrity_state_directory(args)
        review = inspect_runtime_integrity_review(target_state)
        if not _integrity_acknowledgement_process_stopped(target_state, config):
            print(
                "error: stop the sidecar before acknowledging manual review",
                file=sys.stderr,
            )
            return 1
        second_binding = _verified_integrity_acknowledgement_binding(
            args.hermes_dir
        )
        if second_binding != first_binding:
            raise ValueError("runtime integrity acknowledgement binding changed")
        if not _integrity_acknowledgement_process_stopped(target_state, config):
            print(
                "error: stop the sidecar before acknowledging manual review",
                file=sys.stderr,
            )
            return 1
        acknowledgement_options = {
            "expected_state_token": review.state_token,
            "expected_binding": first_binding,
            "allow_legacy_unbound_empty_restart": bool(
                args.yes is True and review.legacy_unbound_empty_restart
            ),
        }
        if (
            args.yes is True
            and review.binding is not None
            and review.binding != first_binding
            and review.binding.target_identity == first_binding.target_identity
        ):
            acknowledgement_options["allow_same_target_plan_transition"] = True
        changed = acknowledge_runtime_integrity_review(
            target_state,
            **acknowledgement_options,
        )
    except (OSError, RuntimeError, ValueError):
        print(
            "error: manual review fence could not be acknowledged safely",
            file=sys.stderr,
        )
        return 1
    print(
        "integrity manual review: acknowledged"
        if changed
        else "integrity manual review: no pending fence"
    )
    print("next: restart sidecar and Hermes Gateway")
    return 0


def _verified_integrity_acknowledgement_binding(
    hermes_dir: str | Path,
):
    detection = detect_hermes(hermes_dir)
    if not detection.supported:
        raise ValueError("Hermes install could not be verified")
    recovery_plan = plan_recovery(detection)
    if recovery_plan.state != "installed" or recovery_plan.actions:
        raise ValueError("Hermes installed plan could not be verified")
    integrity_plan = plan_integrity_repair(detection)
    if (
        integrity_plan.state != "installed"
        or integrity_plan.executable
        or integrity_plan.reason != "recovery_not_required"
    ):
        raise ValueError("Hermes integrity plan could not be verified")
    return build_runtime_integrity_fence_binding(
        detection.root,
        integrity_plan.fingerprint,
    )


def _integrity_acknowledgement_process_stopped(target_state: Path, config) -> bool:
    return bool(
        _integrity_pidfile_absent(target_state)
        and fetch_health(config) is None
    )


def _integrity_state_directory(args: argparse.Namespace) -> Path:
    return Path(args.state_dir).expanduser()


def _integrity_pidfile_absent(target_state: Path) -> bool:
    try:
        os.lstat(target_state / PIDFILE_NAME)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _run_status(args: argparse.Namespace) -> int:
    try:
        config = (
            load_config(args.config, env_file=args.env_file)
            if args.env_file is not None
            else load_config(args.config)
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    status = status_sidecar(config)
    status_error = status.get("error")
    if isinstance(status_error, str) and status_error:
        print(f"error: {status_error}", file=sys.stderr)
        return 1
    readiness_degraded = False
    native_handoff_manual_review = False
    if status["running"]:
        print("status: running")
        print(f"pid: {status['pid'] or 'unknown'}")
        print(f"manager: {status.get('manager', 'unknown')}")
        health_status = status["health"].get("status")
        if health_status == "degraded":
            print("health: degraded")
        delivery = status["health"].get("delivery")
        if isinstance(delivery, dict) and delivery.get("mode") == "noop":
            print("delivery.mode: noop")
        readiness = status["health"].get("readiness")
        if isinstance(readiness, dict):
            readiness_status = str(readiness.get("status") or "unknown")
            if readiness_status not in {"ready", "starting", "degraded", "disabled"}:
                readiness_status = "unknown"
            readiness_reason = str(readiness.get("reason") or "unknown")
            if readiness_reason not in {
                "runtime_ready",
                "runtime_heartbeat_waiting",
                "runtime_heartbeat_missing",
                "runtime_heartbeat_stale",
                "gateway_restart_required",
                "manual_review_required",
                "control_auth_unavailable",
                "integrity_disabled",
            }:
                readiness_reason = "unknown"
            integrity_mode = str(readiness.get("integrity_mode") or "unknown")
            if integrity_mode not in {"safe", "notify", "off"}:
                integrity_mode = "unknown"
            restart_required = readiness.get("restart_required") is True
            print(f"readiness: {readiness_status}")
            print(f"readiness.reason: {readiness_reason}")
            print(f"integrity.mode: {integrity_mode}")
            print(
                "gateway.restart_required: "
                f"{'true' if restart_required else 'false'}"
            )
            readiness_degraded = readiness_status == "degraded"
        integrity = status["health"].get("integrity")
        if isinstance(integrity, dict):
            _print_status_integrity(integrity)
        print(f"active_sessions: {status['health'].get('active_sessions', 0)}")
        metrics = status["health"].get("metrics", {})
        if isinstance(metrics, dict):
            for name in (
                "events_received",
                "events_applied",
                "events_ignored",
                "events_rejected",
                "event_auth_rejections",
                "feishu_send_attempts",
                "feishu_noop_attempts",
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
        native_handoff_manual_review = _print_status_native_handoffs(
            status["health"]
        )
        _print_status_routing(status["health"])
    else:
        print("status: stopped")
        if status["pid"] is not None:
            print(f"pid: {status['pid']} stale")
    hook_check = _lifecycle_hook_check(args)
    if hook_check is None:
        return 1 if readiness_degraded or native_handoff_manual_review else 0
    hook_check["config"] = args.config
    _print_lifecycle_hook_check(hook_check)
    return (
        1
        if readiness_degraded
        or native_handoff_manual_review
        or bool(hook_check["blocking"])
        else 0
    )


def _print_status_native_handoffs(health: dict[str, Any]) -> bool:
    snapshot = health.get("native_handoffs")
    if not isinstance(snapshot, dict):
        return False
    delivery_states = snapshot.get("delivery_states")
    if not isinstance(delivery_states, dict):
        delivery_states = {}

    def safe_count(value: Any) -> int:
        return value if type(value) is int and value >= 0 else 0

    records = safe_count(snapshot.get("records"))
    pending = safe_count(delivery_states.get("pending"))
    acked = safe_count(delivery_states.get("acked"))
    uncertain = safe_count(delivery_states.get("uncertain"))
    manual_review_required = (
        snapshot.get("manual_review_required") is True or uncertain > 0
    )
    if not (records or pending or uncertain or manual_review_required):
        return False

    print(f"native_handoff.records: {records}")
    print(f"native_handoff.pending: {pending}")
    print(f"native_handoff.acked: {acked}")
    print(f"native_handoff.uncertain: {uncertain}")
    if manual_review_required:
        print("native_handoff.manual_review_required: true")
        print(
            "native_handoff.next_action: verify native conversation delivery "
            "and the Hermes delivery ledger before manually retrying; do not "
            "delete handoff state or retry automatically"
        )
    return manual_review_required


def _print_status_integrity(snapshot: dict[str, Any]) -> None:
    integrity = sanitize_integrity_snapshot(snapshot)
    print(f"integrity.status: {integrity['last_status']}")
    print(f"integrity.reason: {integrity['last_reason']}")
    for name in ("repair_attempts", "repair_successes", "repair_refusals"):
        print(f"integrity.{name}: {integrity[name]}")
    action = {
        "repair_available": (
            "review doctor evidence; run integrity migrate-safe with explicit "
            "config and Hermes paths; then restart sidecar"
        ),
        "manual_review_required": (
            "run doctor --explain and review evidence; do not force repair"
        ),
        "restart_required": (
            "restart Hermes Gateway manually when idle, then recheck"
        ),
        "repaired": "restart Hermes Gateway manually when idle, then recheck",
    }.get(str(integrity["last_status"]))
    if action:
        print(f"integrity.next_action: {action}")


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
    print(f"message_id: {_masked_message_id(message_id)}")
    return 0


def _run_chats(args: argparse.Namespace) -> int:
    if getattr(args, "chat_command", None) not in {
        "list",
        "use-native",
        "use-card",
    }:
        print("error: chats command is required", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
        data = _read_local_yaml(args.config)
        bindings = _native_chat_bindings_scope(
            config,
            data,
            profile_id=args.profile_id,
        )
        native_chats = normalize_native_chats(bindings.get("native_chats", []))
        if args.chat_command == "list":
            for chat_id in native_chats:
                print(_masked_chat_id(chat_id))
            return 0
        chat_id = normalize_native_chats([args.chat_id])[0]
        if args.chat_command == "use-native":
            if chat_id not in native_chats:
                native_chats.append(chat_id)
            disposition = "native"
        else:
            native_chats = [item for item in native_chats if item != chat_id]
            disposition = "card"
        bindings["native_chats"] = native_chats
        _write_local_yaml(args.config, data)
        print(
            f"{disposition}: {_masked_chat_id(chat_id)} "
            "(applies to the next new message)"
        )
        return 0
    except Exception as exc:
        print(
            f"error: {_sanitize_error(exc, locals().get('config'))}",
            file=sys.stderr,
        )
        return 1


def _native_chat_bindings_scope(
    config: dict[str, Any],
    data: dict[str, Any],
    *,
    profile_id: str | None,
) -> dict[str, Any]:
    profiles = config.get("profiles")
    if isinstance(profiles, dict) and profiles:
        if not profile_id:
            raise ValueError("--profile-id is required in multi-profile mode")
        if profile_id not in profiles:
            raise KeyError("unknown profile")
        raw_profiles = _ensure_mapping_path(data, "profiles")
        raw_profile = raw_profiles.get(profile_id)
        if raw_profile is None:
            raw_profile = {}
            raw_profiles[profile_id] = raw_profile
        if not isinstance(raw_profile, dict):
            raise ValueError("profile must be a mapping")
        return _ensure_mapping_path(raw_profile, "bindings")
    if profile_id:
        raise KeyError("unknown profile")
    return _ensure_mapping_path(data, "bindings")


def _masked_chat_id(chat_id: str) -> str:
    digest = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:10]
    return f"chat#{digest}"


def _masked_message_id(message_id: str) -> str:
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:10]
    return f"message#{digest}"


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
            print(f"bound: {_masked_chat_id(args.chat_id)} -> {args.bot_id}")
            return 0

        if args.bot_command == "unbind-chat":
            data = _read_local_yaml(args.config)
            chats = _ensure_mapping_path(data, "bindings", "chats")
            chats.pop(args.chat_id, None)
            _write_local_yaml(args.config, data)
            print(f"unbound: {_masked_chat_id(args.chat_id)}")
            return 0

        if args.bot_command == "test":
            config = load_config(args.config)
            config = _select_profile_config(config, args.profile_id)
            message_id = asyncio.run(
                _smoke_feishu_card_with_bot(config, args.bot_id, args.chat_id)
            )
            print("bot smoke ok")
            print(f"message_id: {_masked_message_id(message_id)}")
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
    text_sizes = card_config.get("text_sizes")
    if not isinstance(text_sizes, dict):
        text_sizes = None
    message_id = await client.send_card(
        chat_id,
        render_card(
            session,
            footer_fields=footer_fields,
            title=title,
            text_sizes=text_sizes,
        ),
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
        render_card(
            session,
            footer_fields=footer_fields,
            title=title,
            text_sizes=text_sizes,
        ),
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
        _read_manifest(_manifest_path(detection.root))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        _ensure_hermes_runtime_package(detection)
        _ensure_hermes_feishu_sdk(detection)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    accept_hermes_upgrade = bool(
        getattr(args, "accept_hermes_upgrade", False)
    )
    no_repair = bool(getattr(args, "no_repair", False))
    run_py = detection.run_py
    backup_path = _backup_path(run_py)
    cron_evidence_py = detection.cron_py
    cron_evidence_backup_path = (
        _backup_path(cron_evidence_py) if cron_evidence_py is not None else None
    )
    cron_py = detection.cron_py if detection.cron_py_exists else None
    cron_backup_path = _backup_path(cron_py) if cron_py is not None else None
    base_evidence_py = detection.base_py
    base_evidence_backup_path = (
        _backup_path(base_evidence_py) if base_evidence_py is not None else None
    )
    base_py = detection.base_py if detection.base_required else None
    base_backup_path = _backup_path(base_py) if base_py is not None else None
    manifest_path = _manifest_path(detection.root)
    manifestless_owned_upgrade = False
    if backup_path.exists() and not manifest_path.exists() and not no_repair:
        try:
            _validate_existing_install_state(
                run_py,
                backup_path,
                manifest_path,
                cron_py=cron_evidence_py,
                cron_backup_path=cron_evidence_backup_path,
                base_py=base_evidence_py,
                base_backup_path=base_evidence_backup_path,
                allow_manifestless_owned_upgrade=True,
            )
            if (
                not detection.base_required
                and base_evidence_py is not None
                and base_evidence_backup_path is not None
                and _managed_restore_evidence_exists(
                    base_evidence_py,
                    base_evidence_backup_path,
                    remove_owned_patch=remove_base_patch,
                )
            ):
                raise ValueError(
                    "install state incomplete; unexpected exact Base ownership "
                    "evidence without manifest"
                )
        except (OSError, UnicodeError, ValueError):
            pass
        else:
            manifestless_owned_upgrade = True

    recovery_plan = plan_recovery(
        detection,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    if recovery_plan.actions and not manifestless_owned_upgrade:
        if not recovery_plan.executable:
            print(
                "error: "
                + _recovery_refusal_message(
                    recovery_plan,
                    accept_hermes_upgrade=accept_hermes_upgrade,
                ),
                file=sys.stderr,
            )
            return 1
        if not no_repair:
            try:
                recovery_result = execute_recovery(
                    detection,
                    expected_fingerprint=recovery_plan.fingerprint,
                    accept_hermes_upgrade=accept_hermes_upgrade,
                )
            except (OSError, UnicodeError, RecoveryRefused) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            for action in recovery_result.actions:
                print(action)

    original: str | None = None
    cron_original: str | None = None
    base_original: str | None = None
    manifest_existed = False
    backup_existed = False
    cron_backup_existed = False
    base_backup_existed = False
    gateway_restart_required = False
    transactional_install = _cli_dirfd_binding_supported()

    try:
        install_paths = {
            "run.py": run_py,
            "run.py backup": backup_path,
            "install manifest": manifest_path,
        }
        if cron_py is not None and cron_backup_path is not None:
            install_paths["cron scheduler"] = cron_py
            install_paths["cron backup"] = cron_backup_path
        elif (
            cron_evidence_py is not None
            and cron_evidence_backup_path is not None
        ):
            install_paths["cron scheduler"] = cron_evidence_py
            install_paths["cron backup"] = cron_evidence_backup_path
        if base_py is not None and base_backup_path is not None:
            install_paths["exact Base"] = base_py
            install_paths["exact Base backup"] = base_backup_path
        elif (
            base_evidence_py is not None
            and base_evidence_backup_path is not None
        ):
            install_paths["exact Base"] = base_evidence_py
            install_paths["exact Base backup"] = base_evidence_backup_path
        install_identities, install_directory_identities = _snapshot_restore_evidence(
            detection.root, install_paths
        )
        original = _read_restore_text(run_py, install_identities.get(run_py))
        cron_original = (
            _read_restore_text(cron_py, install_identities.get(cron_py))
            if cron_py is not None
            else None
        )
        base_original = (
            _read_restore_text(base_py, install_identities.get(base_py))
            if base_py is not None
            else None
        )
        manifest_existed = install_identities.get(manifest_path) is not None
        backup_existed = install_identities.get(backup_path) is not None
        cron_backup_existed = bool(
            cron_backup_path is not None
            and install_identities.get(cron_backup_path) is not None
        )
        base_backup_existed = bool(
            base_backup_path is not None
            and install_identities.get(base_backup_path) is not None
        )
        _validate_existing_install_state(
            run_py,
            backup_path,
            manifest_path,
            cron_py=cron_evidence_py,
            cron_backup_path=cron_evidence_backup_path,
            base_py=base_evidence_py,
            base_backup_path=base_evidence_backup_path,
            allow_manifestless_owned_upgrade=manifestless_owned_upgrade,
        )
        run_backup_source = (
            _read_restore_text(
                backup_path, install_identities.get(backup_path)
            )
            if backup_existed
            else original
        )
        cron_backup_source = (
            _read_restore_text(
                cron_backup_path, install_identities.get(cron_backup_path)
            )
            if cron_backup_path is not None and cron_backup_existed
            else cron_original
        )
        base_backup_source = (
            _read_restore_text(
                base_backup_path, install_identities.get(base_backup_path)
            )
            if base_backup_path is not None and base_backup_existed
            else base_original
        )
        patched = apply_patch(
            original, strategy=detection.hook_strategy or "legacy_gateway_run"
        )
        cron_patched = (
            apply_cron_patch(cron_original)
            if cron_py is not None and cron_original is not None
            else None
        )
        base_patched = (
            apply_base_patch(base_original)
            if base_py is not None and base_original is not None
            else None
        )
        gateway_restart_required = bool(
            patched != original
            or (
                cron_patched is not None
                and cron_original is not None
                and cron_patched != cron_original
            )
            or (
                base_patched is not None
                and base_original is not None
                and base_patched != base_original
            )
        )
        manifest_contents = _render_install_manifest(
            manifest_path,
            run_py=run_py,
            run_contents=patched,
            backup_path=backup_path,
            run_source=run_backup_source,
            cron_py=cron_py,
            cron_contents=cron_patched,
            cron_backup_path=cron_backup_path,
            cron_source=cron_backup_source,
            base_py=base_py,
            base_contents=base_patched,
            base_backup_path=base_backup_path,
            base_source=base_backup_source,
        )
        changes: list[tuple[Path, str]] = []
        if (
            base_py is not None
            and base_backup_path is not None
            and not base_backup_existed
        ):
            changes.append((base_backup_path, base_backup_source or ""))
        if not backup_existed:
            changes.append((backup_path, run_backup_source))
        if (
            cron_py is not None
            and cron_backup_path is not None
            and not cron_backup_existed
        ):
            changes.append((cron_backup_path, cron_backup_source or ""))
        # The Base contract must exist before run.py can stage exact completion.
        if (
            base_py is not None
            and base_patched is not None
            and base_original is not None
            and base_patched != base_original
        ):
            changes.append((base_py, base_patched))
        if patched != original:
            changes.append((run_py, patched))
        if (
            cron_py is not None
            and cron_patched is not None
            and cron_original is not None
            and cron_patched != cron_original
        ):
            changes.append((cron_py, cron_patched))
        changes.append((manifest_path, manifest_contents))
        if transactional_install:
            _write_targets_transactionally(
                changes,
                expected_identities=install_identities,
                expected_directories=install_directory_identities,
                preserve_earlier_writes_on_rollback_failure=True,
            )
        else:
            _write_targets_portably(
                changes,
                expected_identities=install_identities,
                expected_directories=install_directory_identities,
                preserve_earlier_writes_on_rollback_failure=True,
            )
    except (OSError, UnicodeError, ValueError) as exc:
        message = str(exc)
        if message.startswith("restore transaction rollback failed"):
            message = message.replace(
                "restore transaction rollback failed",
                "install rollback failed",
                1,
            )
        print(f"error: {message}", file=sys.stderr)
        return 1

    if manifestless_owned_upgrade:
        print("manifest: rebuilt")
    print("install ok")
    if gateway_restart_required:
        print("gateway.restart_required: hermes gateway start")
    return 0


def _run_repair(args: argparse.Namespace) -> int:
    detection = detect_hermes(args.hermes_dir)
    if not detection.supported:
        print(_format_hermes_detection(detection), file=sys.stderr)
        return 1
    try:
        _read_manifest(_manifest_path(detection.root))
        actions = _repair_install_state(
            detection,
            accept_hermes_upgrade=bool(
                getattr(args, "accept_hermes_upgrade", False)
            ),
        )
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
    plan = plan_recovery(detection)
    return bool(plan.actions and plan.executable)


def _repair_install_state(
    detection: HermesDetection,
    *,
    dry_run: bool = False,
    accept_hermes_upgrade: bool = False,
) -> list[str]:
    plan = plan_recovery(
        detection,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    if not plan.actions:
        healthy_noop = bool(
            plan.state in {"clean", "installed"}
            and not any(finding.severity == "error" for finding in plan.findings)
        )
        if healthy_noop:
            return []
        raise RecoveryRefused(
            _recovery_refusal_message(
                plan,
                accept_hermes_upgrade=accept_hermes_upgrade,
            )
        )
    if not plan.executable:
        raise RecoveryRefused(
            _recovery_refusal_message(
                plan,
                accept_hermes_upgrade=accept_hermes_upgrade,
            )
        )
    if dry_run:
        return [_repair_action_message(action) for action in plan.actions]
    return list(
        execute_recovery(
            detection,
            expected_fingerprint=plan.fingerprint,
            accept_hermes_upgrade=accept_hermes_upgrade,
        ).actions
    )


def _recovery_refusal_message(
    plan,
    *,
    accept_hermes_upgrade: bool,
) -> str:
    message = _first_refusal(plan)
    if plan.state == "stale_unpatched" and not accept_hermes_upgrade:
        message += (
            " If Hermes was intentionally upgraded, rerun with "
            "--accept-hermes-upgrade --yes."
        )
    return message


def _repair_action_message(action: str) -> str:
    messages = {
        "restore_verified_backup": "run.py: restored verified backup",
        "reapply_current_hook": "run.py: reapplied current hook",
        "rebuild_backup": "backup: recreated",
        "rebuild_manifest": "manifest: rebuilt",
        "restore_verified_cron_backup": "cron scheduler: restored verified backup",
        "reapply_current_cron_hook": "cron scheduler: reapplied current hook",
        "rebuild_cron_backup": "cron backup: recreated",
        "restore_verified_base_backup": "exact Base: restored verified backup",
        "reapply_current_base_hook": "exact Base: reapplied current hook",
        "rebuild_base_backup": "exact Base backup: recreated",
        "clear_stale_install_state": "install state: cleared stale unpatched state",
    }
    return messages[action]


def _restore(hermes_root: Path) -> None:
    run_py = hermes_root / "gateway" / "run.py"
    cron_py = hermes_root / "cron" / "scheduler.py"
    base_py = hermes_root / "gateway" / "platforms" / "base.py"
    backup_path = _backup_path(run_py)
    cron_backup_path = _backup_path(cron_py)
    base_backup_path = _backup_path(base_py)
    manifest_path = _manifest_path(hermes_root)
    restore_identities, restore_directory_identities = _snapshot_restore_evidence(
        hermes_root,
        {
            "gateway/run.py": run_py,
            "gateway/run.py backup": backup_path,
            "cron scheduler": cron_py,
            "cron backup": cron_backup_path,
            "exact Base": base_py,
            "exact Base backup": base_backup_path,
            "manifest": manifest_path,
        },
    )
    read_restore_text = lambda path: _read_restore_text(
        path, restore_identities[path]
    )
    restore_file_hash = lambda path: hashlib.sha256(
        read_restore_text(path).encode("utf-8")
    ).hexdigest()
    manifest = _read_restore_manifest(manifest_path, read_restore_text)
    if manifest is not None:
        if not _manifest_has_cron(
            manifest
        ) and _legacy_managed_evidence_requires_refusal(
            cron_py,
            cron_backup_path,
            remove_owned_patch=remove_cron_patch,
            apply_owned_patch=apply_cron_patch,
            read_text=read_restore_text,
        ):
            raise ValueError(
                "install state incomplete; cron evidence exists but manifest "
                "ownership is missing; refusing to restore"
            )
        if not _manifest_has_base(
            manifest
        ) and _legacy_managed_evidence_requires_refusal(
            base_py,
            base_backup_path,
            remove_owned_patch=remove_base_patch,
            apply_owned_patch=apply_base_patch,
            read_text=read_restore_text,
        ):
            raise ValueError(
                "install state incomplete; exact Base evidence exists but manifest "
                "ownership is missing; refusing to restore"
            )
    if backup_path.exists():
        if manifest is None:
            if _managed_restore_evidence_exists(
                cron_py,
                cron_backup_path,
                remove_owned_patch=remove_cron_patch,
                read_text=read_restore_text,
            ):
                raise ValueError(
                    "install state incomplete; cron evidence exists without "
                    "manifest; refusing to restore"
                )
            if _managed_restore_evidence_exists(
                base_py,
                base_backup_path,
                remove_owned_patch=remove_base_patch,
                read_text=read_restore_text,
            ):
                raise ValueError(
                    "install state incomplete; exact Base evidence exists without "
                    "manifest; refusing to restore"
                )
            backup_text = read_restore_text(backup_path)
            _validate_backup_contains_original(backup_text, "restore")
            if run_py.exists() and read_restore_text(run_py) == backup_text:
                _assert_restore_evidence_set_unchanged(restore_identities)
                _clear_install_state(
                    backup_path,
                    manifest_path,
                    manifest=None,
                    restore_identities=restore_identities,
                    restore_directory_identities=restore_directory_identities,
                )
                return

            current = read_restore_text(run_py) if run_py.exists() else ""
            try:
                if run_py.exists() and remove_patch(current) == backup_text:
                    _assert_restore_evidence_set_unchanged(restore_identities)
                    _write_targets_transactionally(
                        [(run_py, backup_text)],
                        expected_identities=restore_identities,
                        expected_directories=restore_directory_identities,
                    )
                    _clear_install_state(
                        backup_path,
                        manifest_path,
                        manifest=None,
                        restore_identities=restore_identities,
                        restore_directory_identities=restore_directory_identities,
                    )
                    return
            except ValueError:
                pass

            patched_backup = apply_patch(backup_text)
            if not run_py.exists() or current != patched_backup:
                raise ValueError("run.py changed since install; refusing to restore")

            _assert_restore_evidence_set_unchanged(restore_identities)
            _write_targets_transactionally(
                [(run_py, backup_text)],
                expected_identities=restore_identities,
                expected_directories=restore_directory_identities,
            )
            _clear_install_state(
                backup_path,
                manifest_path,
                manifest=None,
                restore_identities=restore_identities,
                restore_directory_identities=restore_directory_identities,
            )
            return

        backup_text = _validate_restorable_install_state(
            run_py,
            backup_path,
            manifest,
            "restore",
            cron_py=cron_py,
            cron_backup_path=cron_backup_path,
            base_py=base_py,
            base_backup_path=base_backup_path,
            read_text=read_restore_text,
            file_hash=restore_file_hash,
        )
        cron_backup_text = (
            read_restore_text(cron_backup_path)
            if _manifest_has_cron(manifest) and cron_backup_path.exists()
            else None
        )
        base_backup_text = (
            read_restore_text(base_backup_path)
            if _manifest_has_base(manifest) and base_backup_path.exists()
            else None
        )
        target_changes = []
        if base_backup_text is not None:
            target_changes.append((base_py, base_backup_text))
        target_changes.append((run_py, backup_text))
        if cron_backup_text is not None:
            target_changes.append((cron_py, cron_backup_text))
        _assert_restore_evidence_set_unchanged(restore_identities)
        _write_targets_transactionally(
            target_changes,
            expected_identities=restore_identities,
            expected_directories=restore_directory_identities,
        )
        _clear_install_state(
            backup_path,
            manifest_path,
            manifest=manifest,
            restore_identities=restore_identities,
            restore_directory_identities=restore_directory_identities,
        )
        return

    if manifest is not None and _manifest_has_cron(manifest):
        raise ValueError(
            "install state incomplete; owned cron state but run.py backup is "
            "missing; refusing to restore"
        )
    if manifest is not None and _manifest_has_base(manifest):
        raise ValueError(
            "install state incomplete; owned exact Base state but run.py backup is "
            "missing; refusing to restore"
        )
    if _managed_restore_evidence_exists(
        cron_py,
        cron_backup_path,
        remove_owned_patch=remove_cron_patch,
        read_text=read_restore_text,
    ):
        raise ValueError(
            "install state incomplete; cron evidence exists without restorable "
            "run.py state; refusing to restore"
        )
    if _managed_restore_evidence_exists(
        base_py,
        base_backup_path,
        remove_owned_patch=remove_base_patch,
        read_text=read_restore_text,
    ):
        raise ValueError(
            "install state incomplete; exact Base evidence exists without restorable "
            "run.py state; refusing to restore"
        )
    if not run_py.exists():
        return

    current = read_restore_text(run_py)
    if manifest_path.exists() and remove_patch(current) == current:
        if manifest is not None and (_manifest_has_cron(manifest) or _manifest_has_base(manifest)):
            raise ValueError(
                "install state incomplete; owned target backup missing; refusing to restore"
            )
        _assert_restore_evidence_set_unchanged(restore_identities)
        _clear_install_state(
            backup_path,
            manifest_path,
            manifest=manifest,
            restore_identities=restore_identities,
            restore_directory_identities=restore_directory_identities,
        )
        return

    if manifest is not None:
        patched_sha256 = manifest.get("patched_sha256")
        if not isinstance(patched_sha256, str) or not patched_sha256:
            if remove_patch(current) != current:
                raise ValueError("manifest missing patched run.py sha256")
        elif restore_file_hash(run_py) != patched_sha256:
            raise ValueError("run.py changed since install; refusing to restore")

    restored_contents = remove_patch(current)
    restored = restored_contents != current
    if restored:
        _assert_restore_evidence_set_unchanged(restore_identities)
        _write_targets_transactionally(
            [(run_py, restored_contents)],
            expected_identities=restore_identities,
            expected_directories=restore_directory_identities,
        )
    if restored or backup_path.exists() or manifest_path.exists():
        _clear_install_state(
            backup_path,
            manifest_path,
            manifest=manifest,
            restore_identities=restore_identities,
            restore_directory_identities=restore_directory_identities,
        )


def _backup_path(run_py: Path) -> Path:
    return run_py.with_name(f"{run_py.name}{BACKUP_SUFFIX}")


def _snapshot_restore_evidence(
    hermes_root: Path, evidence: dict[str, Path]
) -> tuple[
    dict[Path, _RestoreEvidenceSnapshot | None],
    dict[Path, _RestoreIdentity | None],
]:
    directory_identities: dict[Path, _RestoreIdentity | None] = {}
    for label, path in (
        ("Hermes directory", hermes_root),
        ("gateway directory", hermes_root / "gateway"),
        ("cron directory", hermes_root / "cron"),
        ("gateway/platforms directory", hermes_root / "gateway" / "platforms"),
    ):
        try:
            snapshot = path.lstat()
        except FileNotFoundError:
            directory_identities[path] = None
            continue
        if stat.S_ISLNK(snapshot.st_mode):
            raise ValueError(f"{label} must not be a symlink")
        if not stat.S_ISDIR(snapshot.st_mode):
            raise ValueError(f"{label} must be a directory")
        directory_identities[path] = (snapshot.st_dev, snapshot.st_ino)

    identities: dict[Path, _RestoreEvidenceSnapshot | None] = {}
    for label, path in evidence.items():
        try:
            snapshot = path.lstat()
        except FileNotFoundError:
            identities[path] = None
            continue
        if stat.S_ISLNK(snapshot.st_mode):
            raise ValueError(f"{label} must not be a symlink")
        if not stat.S_ISREG(snapshot.st_mode):
            raise ValueError(f"{label} must be a regular file")
        identity = (snapshot.st_dev, snapshot.st_ino)
        contents = _read_restore_text(path, identity)
        identities[path] = (
            snapshot.st_dev,
            snapshot.st_ino,
            _restore_text_sha256(contents),
        )
    return identities, directory_identities


def _restore_text_sha256(contents: str) -> str:
    return hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _read_restore_text(
    path: Path,
    expected_snapshot: _RestoreIdentity | _RestoreEvidenceSnapshot | None,
) -> str:
    if expected_snapshot is None:
        raise ValueError(f"{path.name} is missing; refusing to restore")
    expected_identity = expected_snapshot[:2]
    expected_digest = expected_snapshot[2] if len(expected_snapshot) == 3 else None
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != expected_identity
        ):
            raise ValueError(f"{path.name} changed during restore; refusing to restore")
        if not nofollow:
            current = path.lstat()
            if (
                stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != expected_identity
            ):
                raise ValueError(
                    f"{path.name} changed during restore; refusing to restore"
                )
        with os.fdopen(fd, "r", encoding="utf-8", newline="") as handle:
            fd = None
            contents = handle.read()
        if not nofollow:
            current = path.lstat()
            if (
                stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != expected_identity
            ):
                raise ValueError(
                    f"{path.name} changed during restore; refusing to restore"
                )
        if (
            expected_digest is not None
            and _restore_text_sha256(contents) != expected_digest
        ):
            raise ValueError(f"{path.name} changed during restore; refusing to restore")
        return contents
    finally:
        if fd is not None:
            os.close(fd)


def _read_restore_manifest(
    manifest_path: Path, read_text: Callable[[Path], str]
) -> dict[str, object] | None:
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(read_text(manifest_path))
    except json.JSONDecodeError as exc:
        raise ValueError("manifest could not be parsed") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest could not be parsed")
    validate_install_manifest(manifest)
    return manifest


def _legacy_managed_evidence_requires_refusal(
    target_py: Path,
    target_backup_path: Path,
    *,
    remove_owned_patch: Callable[[str], str],
    apply_owned_patch: Callable[[str], str],
    read_text: Callable[[Path], str] | None = None,
) -> bool:
    if read_text is None:
        read_text = _read_text_preserve_newlines
    target_exists = target_py.exists()
    backup_exists = target_backup_path.exists()
    try:
        if target_exists:
            current = read_text(target_py)
            if remove_owned_patch(current) != current:
                return True
        if not backup_exists:
            return False
        backup = read_text(target_backup_path)
        if remove_owned_patch(backup) != backup:
            return True
        try:
            patched_backup = apply_owned_patch(backup)
        except ValueError:
            return False
        return (
            patched_backup != backup
            and remove_owned_patch(patched_backup) == backup
        )
    except (OSError, UnicodeError, ValueError):
        return True


def _managed_restore_evidence_exists(
    target_py: Path,
    target_backup_path: Path,
    *,
    remove_owned_patch,
    read_text: Callable[[Path], str] | None = None,
) -> bool:
    if read_text is None:
        read_text = _read_text_preserve_newlines
    if target_backup_path.is_symlink() or target_backup_path.exists():
        return True
    if target_py.is_symlink():
        return True
    if not target_py.exists():
        return False
    try:
        current = read_text(target_py)
        return remove_owned_patch(current) != current
    except (OSError, UnicodeError, ValueError):
        return True


def _manifest_path(hermes_root: Path) -> Path:
    return hermes_root / MANIFEST_NAME


def _clear_install_state(
    backup_path: Path,
    manifest_path: Path,
    *,
    manifest: dict[str, object] | None,
    restore_identities: dict[Path, _RestoreEvidenceSnapshot | None] | None = None,
    restore_directory_identities: dict[Path, _RestoreIdentity | None] | None = None,
) -> None:
    paths_to_remove = [backup_path]
    if manifest is not None and _manifest_has_cron(manifest):
        cron_backup_path = (
            backup_path.parent.parent / "cron" / f"scheduler.py{BACKUP_SUFFIX}"
        )
        paths_to_remove.append(cron_backup_path)
    if manifest is not None and _manifest_has_base(manifest):
        base_backup_path = (
            backup_path.parent / "platforms" / f"base.py{BACKUP_SUFFIX}"
        )
        paths_to_remove.append(base_backup_path)
    paths_to_remove.append(manifest_path)
    if restore_identities is None:
        for path in paths_to_remove:
            path.unlink(missing_ok=True)
        return
    if not _cli_dirfd_binding_supported():
        raise ValueError(
            "secure restore cleanup requires directory-relative filesystem "
            "operations on this platform"
        )
    bindings: dict[Path, _CliTargetBinding] = {}
    try:
        for path in paths_to_remove:
            if restore_directory_identities is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, restore_directory_identities
                )
            binding = _bind_cli_target(path)
            bindings[path] = binding
            expected_snapshot = restore_identities.get(path)
            if binding.initial_snapshot != expected_snapshot:
                raise ValueError(
                    f"{path.name} changed during restore; refusing to clean up"
                )
            if restore_directory_identities is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, restore_directory_identities
                )
                expected_parent = restore_directory_identities.get(path.parent)
                if (
                    expected_parent is not None
                    and binding.parent_identity != expected_parent
                ):
                    raise ValueError(
                        f"{path.parent.name} directory changed during restore; "
                        "refusing to restore"
                    )
        for path in paths_to_remove:
            _safe_unlink_restore_evidence(
                bindings[path], restore_identities.get(path)
            )
    finally:
        for binding in bindings.values():
            try:
                binding.close()
            except OSError:
                pass


def _assert_restore_evidence_unchanged(
    path: Path, expected_snapshot: _RestoreEvidenceSnapshot | None
) -> None:
    if expected_snapshot is not None:
        try:
            _read_restore_text(path, expected_snapshot)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"{path.name} changed during restore; refusing to clean up"
            ) from exc
        return
    try:
        current = path.lstat()
    except FileNotFoundError:
        if expected_snapshot is None:
            return
        raise ValueError(f"{path.name} changed during restore; refusing to clean up")
    if (
        expected_snapshot is None
        or stat.S_ISLNK(current.st_mode)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise ValueError(f"{path.name} changed during restore; refusing to clean up")


def _assert_restore_evidence_set_unchanged(
    restore_identities: dict[Path, _RestoreEvidenceSnapshot | None]
) -> None:
    for path, expected_snapshot in restore_identities.items():
        _assert_restore_evidence_unchanged(path, expected_snapshot)


def _safe_unlink_restore_evidence(
    binding: _CliTargetBinding,
    expected_snapshot: _RestoreEvidenceSnapshot | None,
) -> None:
    if expected_snapshot is not None:
        _unlink_bound_cli_target(binding, expected_snapshot)
    elif _read_bound_cli_target(binding.parent_fd, binding.basename) is not None:
        raise ValueError(
            f"{binding.basename} changed during restore; refusing to clean up"
        )


def _assert_restore_directory_ancestry_unchanged(
    path: Path,
    expected_directories: dict[Path, _RestoreIdentity | None],
) -> None:
    for directory, expected_identity in expected_directories.items():
        if directory != path and directory not in path.parents:
            continue
        try:
            current = directory.lstat()
        except FileNotFoundError:
            if expected_identity is None:
                continue
            raise ValueError(
                f"{directory.name} directory changed during restore; "
                "refusing to restore"
            )
        if (
            expected_identity is None
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != expected_identity
        ):
            raise ValueError(
                f"{directory.name} directory changed during restore; "
                "refusing to restore"
            )


def _write_targets_transactionally(
    changes: list[tuple[Path, str]],
    *,
    expected_identities: dict[Path, _RestoreEvidenceSnapshot | None] | None = None,
    expected_directories: dict[Path, _RestoreIdentity | None] | None = None,
    preserve_earlier_writes_on_rollback_failure: bool = False,
) -> None:
    if not _cli_dirfd_binding_supported():
        raise ValueError(
            "secure restore transaction requires directory-relative "
            "filesystem operations on this platform"
        )
    bindings: dict[Path, _CliTargetBinding] = {}
    snapshots: list[tuple[Path, str | None]] = []
    try:
        for path, _contents in changes:
            if expected_directories is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, expected_directories
                )
            binding = _bind_cli_target(path)
            bindings[path] = binding
            if expected_directories is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, expected_directories
                )
                expected_parent = expected_directories.get(path.parent)
                if (
                    expected_parent is not None
                    and binding.parent_identity != expected_parent
                ):
                    raise ValueError(
                        f"{path.parent.name} directory changed during restore; "
                        "refusing to restore"
                    )
            if expected_identities is not None:
                expected_snapshot = expected_identities.get(path)
                if binding.initial_snapshot != expected_snapshot:
                    raise ValueError(
                        f"{path.name} changed during restore; refusing to clean up"
                    )
            snapshots.append(
                (
                    path,
                    binding.initial_bytes.decode("utf-8")
                    if binding.initial_bytes is not None
                    else None,
                )
            )

        written: list[Path] = []
        post_write_snapshots: dict[Path, _RestoreEvidenceSnapshot] = {}
        try:
            for path, contents in changes:
                binding = bindings[path]
                post_write_snapshots[path] = _atomic_write_text(
                    path,
                    contents,
                    _binding=binding,
                    _expected_before=binding.initial_snapshot,
                )
                written.append(path)
            for path in written:
                if expected_directories is not None:
                    _assert_restore_directory_ancestry_unchanged(
                        path, expected_directories
                    )
                binding = bindings[path]
                _assert_cli_path_parent_bound(binding)
                if (
                    _read_bound_cli_target(binding.parent_fd, binding.basename)
                    != post_write_snapshots[path]
                ):
                    raise ValueError("restore transaction lost write ownership")
        except (OSError, UnicodeError, ValueError) as exc:
            rollback_failed = False
            snapshot_by_path = dict(snapshots)
            for path in reversed(written):
                previous = snapshot_by_path[path]
                try:
                    post_write_snapshot = post_write_snapshots.get(path)
                    if post_write_snapshot is None:
                        raise ValueError("restore transaction lost write ownership")
                    binding = bindings[path]
                    if previous is None:
                        _unlink_bound_cli_target(binding, post_write_snapshot)
                    else:
                        _atomic_write_text(
                            path,
                            previous,
                            _binding=binding,
                            _expected_before=post_write_snapshot,
                            _enforce_path_parent=False,
                        )
                except (OSError, UnicodeError, ValueError):
                    rollback_failed = True
                    if preserve_earlier_writes_on_rollback_failure:
                        break
            if rollback_failed:
                raise ValueError(
                    "restore transaction rollback failed; manual review required"
                ) from exc
            raise
    finally:
        for binding in bindings.values():
            try:
                binding.close()
            except OSError:
                pass


def _portable_target_state(
    path: Path,
) -> tuple[_RestoreEvidenceSnapshot | None, str | None, int | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, None, None
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("refusing to replace a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("refusing to replace a non-regular file")
    identity = (metadata.st_dev, metadata.st_ino)
    contents = _read_restore_text(path, identity)
    return (
        metadata.st_dev,
        metadata.st_ino,
        _restore_text_sha256(contents),
    ), contents, stat.S_IMODE(metadata.st_mode)


def _write_targets_portably(
    changes: list[tuple[Path, str]],
    *,
    expected_identities: dict[Path, _RestoreEvidenceSnapshot | None],
    expected_directories: dict[Path, _RestoreIdentity | None],
    preserve_earlier_writes_on_rollback_failure: bool = False,
) -> None:
    current_identities = dict(expected_identities)
    previous_states: dict[Path, tuple[str | None, int | None]] = {}

    def assert_directories_unchanged(path: Path) -> None:
        _assert_restore_directory_ancestry_unchanged(
            path, expected_directories
        )

    _assert_restore_evidence_set_unchanged(current_identities)
    for path, _contents in changes:
        assert_directories_unchanged(path)
        snapshot, previous, previous_mode = _portable_target_state(path)
        if snapshot != current_identities.get(path):
            raise ValueError("refusing to replace a changed target")
        previous_states[path] = previous, previous_mode

    written: list[Path] = []
    post_write_snapshots: dict[Path, _RestoreEvidenceSnapshot] = {}
    try:
        for path, contents in changes:
            _assert_restore_evidence_set_unchanged(current_identities)
            assert_directories_unchanged(path)
            post_write = _atomic_write_text_portable(
                path,
                contents,
                expected_before=current_identities.get(path),
            )
            current_identities[path] = post_write
            post_write_snapshots[path] = post_write
            written.append(path)
            assert_directories_unchanged(path)
        _assert_restore_evidence_set_unchanged(current_identities)
    except (OSError, UnicodeError, ValueError) as exc:
        rollback_failed = False
        for path in reversed(written):
            previous, previous_mode = previous_states[path]
            post_write = post_write_snapshots[path]
            try:
                assert_directories_unchanged(path)
                if previous is None:
                    _assert_restore_evidence_unchanged(path, post_write)
                    # Without directory-relative handles, deleting a newly
                    # created path cannot be bound to the verified inode.
                    # Preserve owned evidence for manual recovery instead of
                    # risking deletion of a concurrent replacement.
                    raise ValueError(
                        "portable rollback preserved newly created evidence"
                    )
                else:
                    restored = _atomic_write_text_portable(
                        path,
                        previous,
                        mode=previous_mode,
                        expected_before=post_write,
                    )
                    current_identities[path] = restored
                assert_directories_unchanged(path)
            except (OSError, UnicodeError, ValueError):
                rollback_failed = True
                if preserve_earlier_writes_on_rollback_failure:
                    break
        if rollback_failed:
            raise ValueError(
                "restore transaction rollback failed; manual review required"
            ) from exc
        raise


def _snapshot_transaction_write(
    path: Path, expected_contents: str
) -> _RestoreEvidenceSnapshot:
    try:
        snapshot = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("restore transaction lost write ownership") from exc
    if not stat.S_ISREG(snapshot.st_mode):
        raise ValueError("restore transaction lost write ownership")
    identity = (snapshot.st_dev, snapshot.st_ino)
    contents = _read_restore_text(path, identity)
    digest = _restore_text_sha256(contents)
    if digest != _restore_text_sha256(expected_contents):
        raise ValueError("restore transaction could not verify committed write")
    return snapshot.st_dev, snapshot.st_ino, digest


def _write_manifest(
    manifest_path: Path,
    run_py: Path,
    backup_path: Path,
    cron_py: Path | None = None,
    cron_backup_path: Path | None = None,
    base_py: Path | None = None,
    base_backup_path: Path | None = None,
) -> None:
    run_contents = _read_text_preserve_newlines(run_py)
    run_source = _read_text_preserve_newlines(backup_path)
    cron_contents = (
        _read_text_preserve_newlines(cron_py)
        if cron_py is not None and cron_backup_path is not None and cron_py.exists()
        else None
    )
    cron_source = (
        _read_text_preserve_newlines(cron_backup_path)
        if cron_contents is not None
        and cron_backup_path is not None
        and cron_backup_path.exists()
        else None
    )
    base_contents = (
        _read_text_preserve_newlines(base_py)
        if base_py is not None and base_backup_path is not None and base_py.exists()
        else None
    )
    if base_contents is not None and (
        base_backup_path is None or not base_backup_path.exists()
    ):
        raise ValueError("exact Base backup missing; refusing to write manifest")
    base_source = (
        _read_text_preserve_newlines(base_backup_path)
        if base_contents is not None and base_backup_path is not None
        else None
    )
    rendered = _render_install_manifest(
        manifest_path,
        run_py=run_py,
        run_contents=run_contents,
        backup_path=backup_path,
        run_source=run_source,
        cron_py=cron_py,
        cron_contents=cron_contents,
        cron_backup_path=cron_backup_path,
        cron_source=cron_source,
        base_py=base_py,
        base_contents=base_contents,
        base_backup_path=base_backup_path,
        base_source=base_source,
    )
    _atomic_write_text(manifest_path, rendered)


def _render_install_manifest(
    manifest_path: Path,
    *,
    run_py: Path,
    run_contents: str,
    backup_path: Path,
    run_source: str,
    cron_py: Path | None = None,
    cron_contents: str | None = None,
    cron_backup_path: Path | None = None,
    cron_source: str | None = None,
    base_py: Path | None = None,
    base_contents: str | None = None,
    base_backup_path: Path | None = None,
    base_source: str | None = None,
) -> str:
    manifest = {
        "manifest_version": INSTALL_MANIFEST_VERSION,
        "run_py": run_py.relative_to(manifest_path.parent).as_posix(),
        "patched_sha256": _restore_text_sha256(run_contents),
        "backup": backup_path.relative_to(manifest_path.parent).as_posix(),
        "backup_sha256": _restore_text_sha256(run_source),
    }
    if (
        cron_py is not None
        and cron_contents is not None
        and cron_backup_path is not None
        and cron_source is not None
    ):
        manifest.update(
            {
                "cron_py": cron_py.relative_to(manifest_path.parent).as_posix(),
                "cron_patched_sha256": _restore_text_sha256(cron_contents),
                "cron_backup": cron_backup_path.relative_to(
                    manifest_path.parent
                ).as_posix(),
                "cron_backup_sha256": _restore_text_sha256(cron_source),
            }
        )
    if (
        base_py is not None
        and base_contents is not None
        and base_backup_path is not None
        and base_source is not None
    ):
        manifest.update(
            {
                "base_py": base_py.relative_to(manifest_path.parent).as_posix(),
                "base_patched_sha256": _restore_text_sha256(base_contents),
                "base_backup": base_backup_path.relative_to(
                    manifest_path.parent
                ).as_posix(),
                "base_backup_sha256": _restore_text_sha256(base_source),
            }
        )
    try:
        manifest["integrity"] = build_integrity_provenance(
            manifest_path.parent,
            run_py=run_py,
            run_source=run_source,
            cron_py=(cron_py if cron_source is not None else None),
            cron_source=cron_source,
            base_py=(base_py if base_source is not None else None),
            base_source=base_source,
        )
    except IntegrityRepairRefused:
        # Source-stripped/container installs remain supported, but cannot use
        # automatic safe repair until provenance is explicitly available.
        pass
    return json.dumps(manifest, sort_keys=True) + "\n"


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
    base_py: Path | None = None,
    base_original: str | None = None,
    base_backup_path: Path | None = None,
    base_backup_existed: bool = False,
    created_evidence_bindings: dict[Path, _CliTargetBinding] | None = None,
    created_evidence_snapshots: dict[Path, _RestoreEvidenceSnapshot] | None = None,
) -> None:
    rollback_failures: list[tuple[str, Exception]] = []

    def restore_target(label: str, path: Path, contents: str | None) -> None:
        if contents is None:
            return
        try:
            if path.exists() and _read_text_preserve_newlines(path) == contents:
                return
        except (OSError, UnicodeError):
            pass
        try:
            _atomic_write_text(path, contents)
        except (OSError, UnicodeError, ValueError) as exc:
            rollback_failures.append((label, exc))

    if base_py is not None:
        restore_target("exact Base", base_py, base_original)
    restore_target("run.py", run_py, original)
    if cron_py is not None:
        restore_target("cron scheduler", cron_py, cron_original)

    # If any source cannot be restored, keep every backup and manifest as
    # forensic/recovery evidence. Deleting ownership evidence here would turn
    # a recoverable partial install into an ambiguous user-owned state.
    if rollback_failures:
        labels = ", ".join(label for label, _exc in rollback_failures)
        raise ValueError(
            "install rollback failed; manual review required; preserved ownership "
            f"evidence for: {labels}"
        ) from rollback_failures[0][1]

    cleanup_failures: list[tuple[str, Exception]] = []
    owned_bindings = created_evidence_bindings or {}
    owned_snapshots = created_evidence_snapshots or {}

    def remove_created_evidence(label: str, path: Path | None, existed: bool) -> None:
        if path is None or existed:
            return
        if not _cli_dirfd_binding_supported():
            try:
                path.unlink(missing_ok=True)
            except (OSError, UnicodeError, ValueError) as exc:
                cleanup_failures.append((label, exc))
            return
        expected_snapshot = owned_snapshots.get(path)
        binding = owned_bindings.get(path)
        if expected_snapshot is None or binding is None:
            if path.exists() or path.is_symlink():
                cleanup_failures.append(
                    (
                        label,
                        ValueError(
                            "created ownership evidence lacks a bound write snapshot"
                        ),
                    )
                )
            return
        try:
            _unlink_bound_cli_target(binding, expected_snapshot)
        except (OSError, UnicodeError, ValueError) as exc:
            cleanup_failures.append((label, exc))

    remove_created_evidence("run.py backup", backup_path, backup_existed)
    remove_created_evidence(
        "cron backup", cron_backup_path, cron_backup_existed
    )
    remove_created_evidence(
        "exact Base backup", base_backup_path, base_backup_existed
    )
    remove_created_evidence("install manifest", manifest_path, manifest_existed)

    if cleanup_failures:
        labels = ", ".join(label for label, _exc in cleanup_failures)
        raise ValueError(
            "install rollback failed; manual review required; ownership evidence "
            f"cleanup failed for: {labels}"
        ) from cleanup_failures[0][1]


def _validate_existing_install_state(
    run_py: Path,
    backup_path: Path,
    manifest_path: Path,
    *,
    cron_py: Path | None = None,
    cron_backup_path: Path | None = None,
    base_py: Path | None = None,
    base_backup_path: Path | None = None,
    require_base_manifest: bool = False,
    allow_manifestless_owned_upgrade: bool = False,
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
        _validate_base_install_state_without_manifest(base_py, base_backup_path)
        return

    if backup_exists and not manifest_exists:
        if not allow_manifestless_owned_upgrade:
            raise ValueError(
                "install state incomplete; manifest missing; "
                "restore or remove patch before installing"
            )
        if run_py.is_symlink() or backup_path.is_symlink():
            raise ValueError(
                "install state incomplete; manifest missing; "
                "restore or remove patch before installing"
            )
        backup_text = _read_text_preserve_newlines(backup_path)
        _validate_backup_contains_original(backup_text, "install")
        if not _current_matches_backup_lenient(run_py, backup_path):
            raise ValueError(
                "install state incomplete; manifest missing; "
                "restore or remove patch before installing"
            )
        _validate_cron_install_state_without_manifest(
            cron_py,
            cron_backup_path,
            allow_owned_backup=True,
        )
        _validate_base_install_state_without_manifest(
            base_py,
            base_backup_path,
            allow_owned_backup=True,
        )
        return

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
            base_py=base_py,
            base_backup_path=base_backup_path,
        )
        if manifest is not None and not _manifest_has_base(manifest):
            if require_base_manifest and base_py is not None:
                raise ValueError(
                    "install state incomplete; exact Base ownership missing "
                    "from manifest"
                )
            _validate_base_install_state_without_manifest(
                base_py, base_backup_path
            )
    except ValueError as exc:
        lenient_owned_gateway_upgrade = bool(
            "run.py changed since install" in str(exc)
            and _current_matches_backup_lenient(run_py, backup_path)
        )
        if not lenient_owned_gateway_upgrade:
            raise
        _validate_complete_cron_install_state(
            cron_py, cron_backup_path, manifest, "install"
        )
        if (
            require_base_manifest
            and base_py is not None
            and manifest is not None
            and not _manifest_has_base(manifest)
        ):
            raise ValueError(
                "install state incomplete; exact Base ownership missing from manifest"
            )
        _validate_complete_base_install_state(
            base_py, base_backup_path, manifest, "install"
        )


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
    base_py: Path | None = None,
    base_backup_path: Path | None = None,
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
    _validate_complete_base_install_state(
        base_py, base_backup_path, manifest, operation
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
    base_py: Path | None = None,
    base_backup_path: Path | None = None,
    read_text: Callable[[Path], str] | None = None,
    file_hash: Callable[[Path], str] | None = None,
) -> str:
    if read_text is None:
        read_text = _read_text_preserve_newlines
    if file_hash is None:
        file_hash = file_sha256
    backup_sha256 = manifest.get("backup_sha256")
    if not isinstance(backup_sha256, str) or not backup_sha256:
        raise ValueError("manifest missing backup sha256")
    if file_hash(backup_path) != backup_sha256:
        raise ValueError(f"backup changed since install; refusing to {operation}")

    backup_text = read_text(backup_path)
    _validate_backup_contains_original(backup_text, operation)
    if not run_py.exists():
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    current = read_text(run_py)
    if current == backup_text:
        _validate_complete_cron_install_state(
            cron_py,
            cron_backup_path,
            manifest,
            operation,
            read_text=read_text,
            file_hash=file_hash,
        )
        _validate_complete_base_install_state(
            base_py,
            base_backup_path,
            manifest,
            operation,
            read_text=read_text,
            file_hash=file_hash,
        )
        return backup_text

    patched_sha256 = manifest.get("patched_sha256")
    if not isinstance(patched_sha256, str) or not patched_sha256:
        raise ValueError("manifest missing patched run.py sha256")
    if file_hash(run_py) != patched_sha256:
        raise ValueError(f"run.py changed since install; refusing to {operation}")

    _validate_current_matches_backup(current, backup_text, operation)
    _validate_complete_cron_install_state(
        cron_py,
        cron_backup_path,
        manifest,
        operation,
        read_text=read_text,
        file_hash=file_hash,
    )
    _validate_complete_base_install_state(
        base_py,
        base_backup_path,
        manifest,
        operation,
        read_text=read_text,
        file_hash=file_hash,
    )
    return backup_text


def _validate_cron_install_state_without_manifest(
    cron_py: Path | None,
    cron_backup_path: Path | None,
    *,
    allow_owned_backup: bool = False,
) -> None:
    if cron_py is None:
        return
    if cron_backup_path is not None and cron_backup_path.exists():
        if (
            not allow_owned_backup
            or cron_py.is_symlink()
            or cron_backup_path.is_symlink()
            or not cron_py.exists()
        ):
            raise ValueError(
                "install state incomplete; cron backup exists without manifest; "
                "restore or remove patch before installing"
            )
        current_cron = _read_text_preserve_newlines(cron_py)
        backup_cron = _read_text_preserve_newlines(cron_backup_path)
        try:
            backup_clean = remove_cron_patch(backup_cron) == backup_cron
            current_matches = remove_cron_patch(current_cron) == backup_cron
        except ValueError:
            backup_clean = False
            current_matches = False
        if backup_clean and current_matches:
            return
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


def _validate_base_install_state_without_manifest(
    base_py: Path | None,
    base_backup_path: Path | None,
    *,
    allow_owned_backup: bool = False,
) -> None:
    if base_py is None:
        return
    if base_backup_path is not None and base_backup_path.exists():
        if (
            not allow_owned_backup
            or base_py.is_symlink()
            or base_backup_path.is_symlink()
            or not base_py.exists()
        ):
            raise ValueError(
                "install state incomplete; exact Base backup exists without manifest; "
                "restore or remove patch before installing"
            )
        current_base = _read_text_preserve_newlines(base_py)
        backup_base = _read_text_preserve_newlines(base_backup_path)
        try:
            backup_clean = remove_base_patch(backup_base) == backup_base
            current_matches = remove_base_patch(current_base) == backup_base
        except ValueError:
            backup_clean = False
            current_matches = False
        if backup_clean and current_matches:
            return
        raise ValueError(
            "install state incomplete; exact Base backup exists without manifest; "
            "restore or remove patch before installing"
        )
    if base_py.exists():
        current_base = _read_text_preserve_newlines(base_py)
        if remove_base_patch(current_base) == current_base:
            return
        raise ValueError(
            "install state incomplete; exact Base already contains patch; "
            "restore or remove patch before installing"
        )


def _validate_complete_cron_install_state(
    cron_py: Path | None,
    cron_backup_path: Path | None,
    manifest: dict[str, object] | None,
    operation: str,
    *,
    read_text: Callable[[Path], str] | None = None,
    file_hash: Callable[[Path], str] | None = None,
) -> None:
    if read_text is None:
        read_text = _read_text_preserve_newlines
    if file_hash is None:
        file_hash = file_sha256
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
    if file_hash(cron_py) != cron_patched_sha256:
        raise ValueError(f"cron scheduler changed since install; refusing to {operation}")

    cron_backup_sha256 = manifest.get("cron_backup_sha256")
    if not isinstance(cron_backup_sha256, str) or not cron_backup_sha256:
        raise ValueError("manifest missing cron backup sha256")
    if file_hash(cron_backup_path) != cron_backup_sha256:
        raise ValueError(f"cron backup changed since install; refusing to {operation}")

    cron_current = read_text(cron_py)
    cron_backup_text = read_text(cron_backup_path)
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
    present = _CRON_MANIFEST_FIELDS.intersection(manifest)
    if present and present != _CRON_MANIFEST_FIELDS:
        raise ValueError(
            "manifest cron ownership fields are incomplete; refusing to mutate"
        )
    return bool(present)


def _manifest_has_base(manifest: dict[str, object]) -> bool:
    present = _BASE_MANIFEST_FIELDS.intersection(manifest)
    if present and present != _BASE_MANIFEST_FIELDS:
        raise ValueError(
            "manifest exact Base ownership fields are incomplete; refusing to mutate"
        )
    return bool(present)


def _validate_complete_base_install_state(
    base_py: Path | None,
    base_backup_path: Path | None,
    manifest: dict[str, object] | None,
    operation: str,
    *,
    read_text: Callable[[Path], str] | None = None,
    file_hash: Callable[[Path], str] | None = None,
) -> None:
    if read_text is None:
        read_text = _read_text_preserve_newlines
    if file_hash is None:
        file_hash = file_sha256
    if manifest is None or not _manifest_has_base(manifest):
        return
    if base_py is None or base_backup_path is None:
        raise ValueError(f"exact Base changed since install; refusing to {operation}")
    expected_base = "gateway/platforms/base.py"
    expected_backup = f"gateway/platforms/base.py{BACKUP_SUFFIX}"
    if manifest.get("base_py") != expected_base or manifest.get(
        "base_backup"
    ) != expected_backup:
        raise ValueError("manifest exact Base ownership paths are invalid")
    if not base_py.exists():
        raise ValueError(f"exact Base changed since install; refusing to {operation}")
    if not base_backup_path.exists():
        raise ValueError(f"exact Base backup changed since install; refusing to {operation}")

    patched_hash = manifest.get("base_patched_sha256")
    if not isinstance(patched_hash, str) or not patched_hash:
        raise ValueError("manifest missing exact Base patched sha256")
    if file_hash(base_py) != patched_hash:
        raise ValueError(f"exact Base changed since install; refusing to {operation}")
    backup_hash = manifest.get("base_backup_sha256")
    if not isinstance(backup_hash, str) or not backup_hash:
        raise ValueError("manifest missing exact Base backup sha256")
    if file_hash(base_backup_path) != backup_hash:
        raise ValueError(
            f"exact Base backup changed since install; refusing to {operation}"
        )

    current = read_text(base_py)
    backup = read_text(base_backup_path)
    if remove_base_patch(backup) != backup:
        raise ValueError(
            f"exact Base backup changed since install; refusing to {operation}"
        )
    try:
        restored = remove_base_patch(current)
    except ValueError as exc:
        raise ValueError(
            f"exact Base changed since install; refusing to {operation}"
        ) from exc
    if restored != backup:
        raise ValueError(f"exact Base changed since install; refusing to {operation}")


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
    validate_install_manifest(manifest)
    return manifest


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _file_state_differs(path: Path, contents: str, mode: int) -> bool:
    try:
        if path.is_symlink():
            return True
        return (
            _read_text_preserve_newlines(path) != contents
            or stat.S_IMODE(path.stat().st_mode) != mode
        )
    except (OSError, UnicodeError):
        return True


_CLI_DIRFD_BINDING_SUPPORTED = (
    os.name != "nt"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "fchmod")
    and all(
        operation in getattr(os, "supports_dir_fd", set())
        for operation in (os.open, os.stat, os.unlink, os.rename)
    )
)


def _cli_dirfd_binding_supported() -> bool:
    return _CLI_DIRFD_BINDING_SUPPORTED


def _atomic_write_text(
    path: Path,
    contents: str,
    *,
    mode: int | None = None,
    _binding: _CliTargetBinding | None = None,
    _expected_before: _RestoreEvidenceSnapshot | None | object = _CLI_SNAPSHOT_UNSET,
    _enforce_path_parent: bool = True,
) -> _RestoreEvidenceSnapshot:
    if not _cli_dirfd_binding_supported():
        if _binding is not None:
            raise ValueError(
                "secure bound write requires directory-relative filesystem "
                "operations on this platform"
            )
        return _atomic_write_text_portable(
            path,
            contents,
            mode=mode,
            expected_before=_expected_before,
        )
    owns_binding = _binding is None
    binding = _binding if _binding is not None else _bind_cli_target(path)
    if binding.path != path:
        if owns_binding:
            binding.close()
        raise ValueError("atomic write binding does not match target")
    expected_before = (
        binding.initial_snapshot
        if _expected_before is _CLI_SNAPSHOT_UNSET
        else _expected_before
    )
    rollback_stage: _CliStagedText | None = None
    write_stage: _CliStagedText | None = None
    try:
        _assert_bound_cli_parent(binding)
        current_snapshot, current_bytes, current_mode = _read_bound_cli_target_state(
            binding.parent_fd, binding.basename
        )
        if current_snapshot != expected_before:
            raise ValueError("refusing to replace a changed target")
        if current_bytes is not None:
            rollback_stage = _stage_bound_cli_bytes(
                binding,
                current_bytes,
                current_mode if current_mode is not None else 0o600,
            )
        selected_mode = mode if mode is not None else current_mode
        write_stage = _stage_bound_cli_bytes(
            binding,
            contents.encode("utf-8"),
            selected_mode if selected_mode is not None else 0o600,
        )
        committed = _replace_bound_cli_stage(
            binding,
            write_stage,
            expected_before=current_snapshot,
        )
        if _enforce_path_parent:
            try:
                _assert_cli_path_parent_bound(binding)
            except (OSError, ValueError) as exc:
                try:
                    if current_snapshot is None:
                        _unlink_bound_cli_target(binding, committed)
                    else:
                        if rollback_stage is None:
                            raise ValueError("atomic write lost rollback stage")
                        restored = _replace_bound_cli_stage(
                            binding,
                            rollback_stage,
                            expected_before=committed,
                        )
                        if restored[2] != current_snapshot[2]:
                            raise ValueError("atomic write rollback verification failed")
                except (OSError, ValueError) as rollback_exc:
                    raise ValueError(
                        "atomic write rollback failed; manual review required"
                    ) from rollback_exc
                raise ValueError("directory changed during write") from exc
        return committed
    finally:
        for staged in (write_stage, rollback_stage):
            if staged is not None:
                _cleanup_bound_cli_stage(binding, staged)
        if owns_binding:
            binding.close()


def _atomic_write_text_portable(
    path: Path,
    contents: str,
    *,
    mode: int | None = None,
    expected_before: _RestoreEvidenceSnapshot | None | object = _CLI_SNAPSHOT_UNSET,
) -> _RestoreEvidenceSnapshot:
    current_snapshot, _current_contents, preserved_mode = _portable_target_state(path)
    selected_expected = (
        current_snapshot
        if expected_before is _CLI_SNAPSHOT_UNSET
        else expected_before
    )
    if current_snapshot != selected_expected:
        raise ValueError("refusing to replace a changed target")

    create = selected_expected is None
    fd = _open_portable_exclusive_fd(path, create=create)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("refusing to replace a non-regular file")
        opened_identity = (opened.st_dev, opened.st_ino)
        current_path = path.lstat()
        if (
            stat.S_ISLNK(current_path.st_mode)
            or not stat.S_ISREG(current_path.st_mode)
            or (current_path.st_dev, current_path.st_ino) != opened_identity
        ):
            raise ValueError("refusing to replace a changed target")

        if create:
            opened_snapshot = None
            original_payload = None
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            original_bytes = bytearray()
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                original_bytes.extend(chunk)
            original_payload = bytes(original_bytes)
            original_text = original_payload.decode("utf-8")
            opened_snapshot = (
                opened.st_dev,
                opened.st_ino,
                _restore_text_sha256(original_text),
            )
        if opened_snapshot != selected_expected:
            raise ValueError("refusing to replace a changed target")

        encoded = contents.encode("utf-8")
        selected_mode = mode if mode is not None else preserved_mode
        try:
            _write_portable_exclusive_bytes(fd, encoded, selected_mode)
            committed = _verify_portable_exclusive_bytes(
                fd, path, opened_identity, encoded
            )
        except (OSError, UnicodeError, ValueError) as exc:
            if original_payload is None:
                raise ValueError(
                    "restore transaction rollback failed; manual review required"
                ) from exc
            try:
                _write_portable_exclusive_bytes(
                    fd, original_payload, preserved_mode
                )
                _verify_portable_exclusive_bytes(
                    fd, path, opened_identity, original_payload
                )
            except (OSError, UnicodeError, ValueError) as rollback_exc:
                raise ValueError(
                    "restore transaction rollback failed; manual review required"
                ) from rollback_exc
            raise
        return (
            committed.st_dev,
            committed.st_ino,
            _restore_text_sha256(contents),
        )
    finally:
        os.close(fd)


def _write_portable_exclusive_bytes(
    fd: int, payload: bytes, mode: int | None
) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    written = 0
    while written < len(payload):
        count = os.write(fd, payload[written:])
        if count <= 0:
            raise OSError("portable write made no progress")
        written += count
    os.ftruncate(fd, len(payload))
    if mode is not None and hasattr(os, "fchmod"):
        os.fchmod(fd, mode)
    os.fsync(fd)


def _verify_portable_exclusive_bytes(
    fd: int,
    path: Path,
    expected_identity: _RestoreIdentity,
    expected_payload: bytes,
) -> os.stat_result:
    committed = os.fstat(fd)
    if (
        not stat.S_ISREG(committed.st_mode)
        or (committed.st_dev, committed.st_ino) != expected_identity
    ):
        raise ValueError("portable write lost target ownership")
    os.lseek(fd, 0, os.SEEK_SET)
    committed_bytes = bytearray()
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        committed_bytes.extend(chunk)
    if bytes(committed_bytes) != expected_payload:
        raise ValueError("portable write verification failed")
    committed_path = path.lstat()
    if (
        stat.S_ISLNK(committed_path.st_mode)
        or not stat.S_ISREG(committed_path.st_mode)
        or (committed_path.st_dev, committed_path.st_ino) != expected_identity
    ):
        raise ValueError("portable write lost target ownership")
    return committed


def _open_portable_exclusive_fd(path: Path, *, create: bool) -> int:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        generic_read = 0x80000000
        generic_write = 0x40000000
        create_new = 1
        open_existing = 3
        file_attribute_normal = 0x00000080
        file_flag_open_reparse_point = 0x00200000
        handle = create_file(
            str(path),
            generic_read | generic_write,
            0,
            None,
            create_new if create else open_existing,
            file_attribute_normal | file_flag_open_reparse_point,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(
                handle,
                os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
        except Exception:
            close_handle(handle)
            raise

    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        try:
            import fcntl
        except ImportError:
            return fd
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise


def _bind_cli_target(path: Path) -> _CliTargetBinding:
    parent_fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        parent_fd = os.open(path.parent, flags)
        opened_parent = os.fstat(parent_fd)
        current_parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(opened_parent.st_mode)
            or not stat.S_ISDIR(current_parent.st_mode)
            or (opened_parent.st_dev, opened_parent.st_ino)
            != (current_parent.st_dev, current_parent.st_ino)
        ):
            raise ValueError("refusing to stage through a changed directory")
        snapshot, payload, file_mode = _read_bound_cli_target_state(
            parent_fd, path.name
        )
        binding = _CliTargetBinding(
            path,
            parent_fd,
            (opened_parent.st_dev, opened_parent.st_ino),
            snapshot,
            payload,
            file_mode,
        )
        parent_fd = -1
        return binding
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _assert_bound_cli_parent(binding: _CliTargetBinding) -> None:
    current = os.fstat(binding.parent_fd)
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != binding.parent_identity
    ):
        raise ValueError("bound target directory changed during write")


def _assert_cli_path_parent_bound(binding: _CliTargetBinding) -> None:
    try:
        current = binding.path.parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError("directory changed during write") from exc
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != binding.parent_identity
    ):
        raise ValueError("directory changed during write")


def _stage_bound_cli_bytes(
    binding: _CliTargetBinding,
    payload: bytes,
    mode: int,
) -> _CliStagedText:
    _assert_bound_cli_parent(binding)
    basename = f".{binding.basename}.{uuid4().hex}.tmp"
    descriptor = -1
    staged: _CliStagedText | None = None
    try:
        descriptor = os.open(
            basename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=binding.parent_fd,
        )
        snapshot = os.fstat(descriptor)
        staged = _CliStagedText(
            basename,
            (snapshot.st_dev, snapshot.st_ino),
            hashlib.sha256(payload).hexdigest(),
            mode,
        )
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        return staged
    except Exception:
        if staged is not None:
            _cleanup_bound_cli_stage(binding, staged)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _replace_bound_cli_stage(
    binding: _CliTargetBinding,
    staged: _CliStagedText,
    *,
    expected_before: _RestoreEvidenceSnapshot | None,
) -> _RestoreEvidenceSnapshot:
    _assert_bound_cli_parent(binding)
    if _read_bound_cli_target(binding.parent_fd, binding.basename) != expected_before:
        raise ValueError("refusing to replace a changed target")
    current_stage = os.stat(
        staged.basename,
        dir_fd=binding.parent_fd,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(current_stage.st_mode)
        or (current_stage.st_dev, current_stage.st_ino) != staged.identity
    ):
        raise ValueError("refusing to replace a changed staged file")
    os.replace(
        staged.basename,
        binding.basename,
        src_dir_fd=binding.parent_fd,
        dst_dir_fd=binding.parent_fd,
    )
    staged.consumed = True
    committed = _read_bound_cli_target(binding.parent_fd, binding.basename)
    expected_committed = (*staged.identity, staged.digest)
    if committed != expected_committed:
        raise ValueError("could not verify committed target")
    return expected_committed


def _unlink_bound_cli_target(
    binding: _CliTargetBinding,
    expected_snapshot: _RestoreEvidenceSnapshot,
) -> None:
    _assert_bound_cli_parent(binding)
    if _read_bound_cli_target(binding.parent_fd, binding.basename) != expected_snapshot:
        raise ValueError("refusing to unlink a changed target")
    os.unlink(binding.basename, dir_fd=binding.parent_fd)
    if _read_bound_cli_target(binding.parent_fd, binding.basename) is not None:
        raise ValueError("could not verify removed target")


def _cleanup_bound_cli_stage(
    binding: _CliTargetBinding,
    staged: _CliStagedText,
) -> None:
    if staged.consumed or binding.parent_fd < 0:
        return
    try:
        current = os.stat(
            staged.basename,
            dir_fd=binding.parent_fd,
            follow_symlinks=False,
        )
        if (
            stat.S_ISREG(current.st_mode)
            and (current.st_dev, current.st_ino) == staged.identity
        ):
            os.unlink(staged.basename, dir_fd=binding.parent_fd)
            staged.consumed = True
    except OSError:
        pass


def _read_bound_cli_target(
    parent_fd: int, basename: str
) -> tuple[int, int, str] | None:
    snapshot, _payload, _mode = _read_bound_cli_target_state(parent_fd, basename)
    return snapshot


def _read_bound_cli_target_state(
    parent_fd: int, basename: str
) -> tuple[_RestoreEvidenceSnapshot | None, bytes | None, int | None]:
    try:
        before = os.stat(
            basename,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None, None, None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("refusing to use a non-regular target")
    identity = (before.st_dev, before.st_ino)
    descriptor = -1
    chunks: list[bytes] = []
    try:
        descriptor = os.open(
            basename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != identity
        ):
            raise ValueError("target changed while being verified")
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after = os.stat(
        basename,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != identity
    ):
        raise ValueError("target changed while being verified")
    payload = b"".join(chunks)
    return (
        (before.st_dev, before.st_ino, hashlib.sha256(payload).hexdigest()),
        payload,
        stat.S_IMODE(before.st_mode),
    )

if __name__ == "__main__":
    raise SystemExit(main())
