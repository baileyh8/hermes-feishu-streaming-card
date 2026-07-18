from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .install.detect import HermesDetection
from .install.recovery import RecoveryPlan, sanitize_recovery_plan
from .profile_sources import PROFILE_SOURCES


_IDENTIFIER_KEYS = {
    "agent_id",
    "bot_id",
    "chat_id",
    "conversation_id",
    "message_id",
    "open_id",
    "operator_open_id",
    "profile_id",
    "tenant_key",
}
_SENSITIVE_KEYS = {
    "api_key",
    "app_secret",
    "authorization",
    "credential",
    "credentials",
    "current_text",
    "backup_text",
    "password",
    "secret",
    "source_text",
    "tenant_token",
    "token",
}
_PATH_KEYS = {
    "backup_path",
    "config_path",
    "cron_backup_path",
    "cron_py",
    "manifest_path",
    "path",
    "python",
    "root",
    "run_py",
    "suggested_root",
}
_RAW_HASH_KEYS = {
    "backup_sha256",
    "current_sha256",
    "cron_backup_sha256",
    "cron_current_sha256",
    "raw_hash",
    "sha256",
}
_SAFE_VERSION_RE = re.compile(r"^(?:v?\d+(?:\.\d+)+|unknown)$")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_CARD_PROFILE_SOURCES = PROFILE_SOURCES
_CARD_ROUTE_REASONS = {
    "bindings.chats",
    "bindings.fallback_bot",
    "bots.default",
    "default",
}
_CARD_REDACTED_ENDPOINT_PATH = "/[redacted-path]"
_CARD_CAPABILITIES = {
    "answer_delta_callback",
    "attachment_delivery",
    "completion_return",
    "cron_delivery",
    "message_handler",
    "reply_context",
    "run_agent",
    "status_callback",
    "thinking_delta_callback",
    "tool_callback",
}
_CARD_METRICS = {
    "cron_cards_sent",
    "cron_fallbacks",
    "events_applied",
    "events_ignored",
    "events_received",
    "events_rejected",
    "event_auth_rejections",
    "feishu_send_attempts",
    "feishu_send_failures",
    "feishu_noop_attempts",
    "feishu_send_successes",
    "feishu_update_attempts",
    "feishu_update_failures",
    "feishu_update_latency_ms",
    "feishu_update_retries",
    "feishu_update_successes",
    "flush_controllers_collected",
    "profile_mismatches",
    "recovery_attempts",
    "recovery_plans_available",
    "recovery_refusals",
    "recovery_successes",
    "sessions_collected",
    "terminal_drain_latency_ms",
    "terminal_drain_timeouts",
    "terminal_drains",
    "update_coalesced",
    "update_queue_peak",
    "update_scheduled",
    "zombie_sessions_collected",
}
_CARD_RECOVERY_ACTIONS = {
    "clear_stale_install_state",
    "reapply_current_cron_hook",
    "reapply_current_hook",
    "rebuild_backup",
    "rebuild_cron_backup",
    "rebuild_manifest",
    "restore_verified_backup",
    "restore_verified_cron_backup",
}
_CARD_FINDING_CODES = {
    "backup_hash_mismatch",
    "backup_invalid",
    "backup_missing",
    "backup_read_error",
    "backup_source_mismatch",
    "bot_unknown",
    "config_load_failed",
    "cron_backup_hash_mismatch",
    "cron_backup_invalid",
    "cron_backup_read_error",
    "cron_backup_source_mismatch",
    "cron_current_hash_mismatch",
    "cron_current_patch_mismatch",
    "cron_current_read_error",
    "cron_manifest_backup_hash_invalid",
    "cron_manifest_current_hash_invalid",
    "cron_manifest_missing",
    "cron_manifest_path_mismatch",
    "cron_marker_error",
    "cron_reapplication_invalid",
    "cron_source_missing",
    "cron_symlink_refused",
    "cron_unsupported_anchors",
    "current_hash_mismatch",
    "current_patch_mismatch",
    "current_read_error",
    "event_endpoint_mismatch",
    "hermes_check_skipped",
    "hermes_compatibility_partial",
    "hermes_not_checked",
    "hermes_unsupported",
    "install_state_changed",
    "install_state_clean",
    "install_state_incomplete",
    "install_state_installed",
    "manifest_backup_hash_invalid",
    "manifest_current_hash_invalid",
    "manifest_invalid",
    "manifest_missing",
    "manifest_path_mismatch",
    "marker_error",
    "profile_credentials_missing",
    "profile_identity_missing",
    "profile_unknown",
    "reapplication_invalid",
    "route_fallback",
    "runtime_import_failed",
    "streaming_disabled",
    "streaming_not_detected",
    "symlink_refused",
    "unsupported_anchors",
}


@dataclass(frozen=True)
class DiagnosticFinding:
    code: str
    severity: str
    message: str
    impact: str = ""
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticReport:
    status: str
    created_at: float
    config: dict[str, object]
    hermes: dict[str, object]
    streaming: dict[str, object]
    install_state: dict[str, object]
    routing: dict[str, object]
    runtime: dict[str, object]
    findings: tuple[DiagnosticFinding, ...]
    internal_recovery_fingerprint: str = ""

    @property
    def fingerprint(self) -> str:
        return diagnostic_fingerprint(self)

    @property
    def recovery_fingerprint(self) -> str:
        """Return the process-local recovery evidence fingerprint."""
        return self.internal_recovery_fingerprint or str(
            self.install_state.get("recovery_fingerprint") or ""
        )

    def to_dict(self, card_safe: bool = False) -> dict[str, object]:
        data = _report_dict(self)
        data["fingerprint"] = self.fingerprint
        return _card_safe_report(data) if card_safe else data


def build_diagnostic_report(
    config_path: Path,
    config: dict[str, object],
    detection: HermesDetection,
    recovery_plan: RecoveryPlan,
    *,
    health: dict[str, object] | None = None,
    profile_id: str = "",
    profile_source: str = "",
    event_url: str = "",
) -> DiagnosticReport:
    health_data = health if isinstance(health, dict) else {}
    server = _mapping(config.get("server"))
    host = str(server.get("host") or "127.0.0.1")
    port = _integer(server.get("port"), 8765)
    profile_count = len(_profiles(config))
    config_report: dict[str, object] = {
        "path": str(config_path),
        "loaded": True,
        "server": {"host": host, "port": port},
        "feishu_credentials": (
            "configured" if _profile_credentials_present(config, profile_id) else "missing"
        ),
        "profiles_enabled": profile_count > 0,
        "profile_count": profile_count,
    }
    hermes_report: dict[str, object] = {
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
        "suggested_root": str(detection.suggested_root or ""),
        "suggestion_reason": detection.suggestion_reason,
    }
    streaming = _section(
        health_data.get("streaming"),
        {"status": "not_checked", "message": "Hermes streaming config was not checked."},
    )
    install_state = _build_install_state(recovery_plan, health_data.get("install_state"))
    runtime_import = _section(
        health_data.get("runtime_import"),
        {
            "checked": False,
            "status": "not_checked",
            "message": "Hermes runtime import was not checked.",
        },
    )
    route = _last_route(health_data)
    routing = build_route_chain(
        config,
        profile_id=profile_id,
        profile_source=profile_source,
        event_url=event_url,
        route=route,
    )
    runtime = _build_runtime(health_data, runtime_import)
    findings = _build_findings(
        config,
        detection,
        install_state,
        streaming,
        runtime_import,
        routing,
        recovery_plan,
    )
    return DiagnosticReport(
        status=_status_for_findings(findings),
        created_at=time.time(),
        config=config_report,
        hermes=hermes_report,
        streaming=streaming,
        install_state=install_state,
        routing=routing,
        runtime=runtime,
        findings=findings,
        internal_recovery_fingerprint=recovery_plan.fingerprint,
    )


def build_route_chain(
    config: dict[str, object],
    *,
    profile_id: str,
    profile_source: str,
    event_url: str,
    route: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "profile_source": profile_source,
        "event_endpoint": _safe_endpoint(event_url),
        "profile_exists": _profile_exists(config, profile_id),
        "credentials_present": _profile_credentials_present(config, profile_id),
        "bot_id": str((route or {}).get("bot_id") or ""),
        "route_reason": str((route or {}).get("reason") or ""),
    }


def build_route_diagnostics(
    config: dict[str, object],
    *,
    profile_id: str,
    profile_source: str,
    event_url: str,
    route: dict[str, object] | None,
) -> tuple[dict[str, object], tuple[DiagnosticFinding, ...]]:
    """Build route state and findings without requiring Hermes detection."""
    routing = build_route_chain(
        config,
        profile_id=profile_id,
        profile_source=profile_source,
        event_url=event_url,
        route=route,
    )
    return routing, tuple(_route_findings(config, routing))


def safe_event_endpoint_for_output(event_url: str) -> str:
    """Render only the reviewed /events path in user-visible diagnostics."""
    return _card_safe_endpoint(event_url)


def diagnostic_fingerprint(report: DiagnosticReport) -> str:
    canonical = _fingerprint_value(_card_safe_report(_report_dict(report)))
    if not isinstance(canonical, dict):
        canonical = {}
    canonical["internal_state"] = {
        "profile_identity": _state_digest(
            "profile_identity", str(report.routing.get("profile_id") or "")
        ),
        "bot_identity": _state_digest(
            "bot_identity", str(report.routing.get("bot_id") or "")
        ),
        "event_endpoint": _state_digest(
            "event_endpoint", _canonical_endpoint(str(report.routing.get("event_endpoint") or ""))
        ),
        "recovery_evidence": _state_digest(
            "recovery_evidence",
            report.recovery_fingerprint,
        ),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _report_dict(report: DiagnosticReport) -> dict[str, object]:
    server = _mapping(report.config.get("server"))
    address = None
    if server:
        address = f"{server.get('host')}:{server.get('port')}"
    runtime_import = _section(report.runtime.get("runtime_import"), {})
    recommendations = [_recommendation(finding) for finding in report.findings]
    return {
        "schema_version": "1",
        "status": report.status,
        "created_at": report.created_at,
        "config": dict(report.config),
        "sidecar": {"address": address},
        "hermes": dict(report.hermes),
        "streaming": dict(report.streaming),
        "install_state": dict(report.install_state),
        "runtime_import": runtime_import,
        "routing": dict(report.routing),
        "runtime": dict(report.runtime),
        "findings": [_finding_dict(finding) for finding in report.findings],
        "recommendations": recommendations,
    }


def _card_safe_report(data: dict[str, object]) -> dict[str, object]:
    findings = _card_safe_findings(data.get("findings"))
    result: dict[str, object] = {
        "schema_version": "1",
        "status": _safe_enum(data.get("status"), {"error", "ok", "warning"}, "warning"),
        "config": _card_safe_config(data.get("config")),
        "sidecar": {},
        "hermes": _card_safe_hermes(data.get("hermes")),
        "streaming": _card_safe_status_section(
            data.get("streaming"),
            {"disabled", "enabled", "not_checked", "not_detected", "skipped"},
        ),
        "install_state": _card_safe_install_state(data.get("install_state")),
        "runtime_import": _card_safe_runtime_import(data.get("runtime_import")),
        "routing": _card_safe_routing(data.get("routing")),
        "runtime": _card_safe_runtime(data.get("runtime")),
        "findings": findings,
        "recommendations": [
            {
                "severity": finding["severity"],
                "code": finding["code"],
                "message": finding["message"],
                "next_step": "",
            }
            for finding in findings
        ],
    }
    created_at = data.get("created_at")
    if isinstance(created_at, (int, float)) and not isinstance(created_at, bool):
        result["created_at"] = created_at
    config_server = _mapping(_mapping(result["config"]).get("server"))
    host = str(config_server.get("host") or "")
    port = config_server.get("port")
    if host and isinstance(port, int):
        rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        result["sidecar"] = {"address": f"{rendered_host}:{port}"}
    fingerprint = data.get("fingerprint")
    if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        result["fingerprint"] = fingerprint
    return result


def format_diagnostic_text(report: DiagnosticReport, explain: bool) -> str:
    data = report.to_dict()
    if not explain:
        sidecar = _mapping(data.get("sidecar"))
        lines = [f"doctor: {report.status}", f"sidecar: {sidecar.get('address')}"]
        hermes = report.hermes
        lines.append(f"hermes: {hermes.get('status', 'not_checked')}")
        return "\n".join(lines)

    config = report.config
    lines = ["Doctor Summary"]
    if config.get("loaded"):
        lines.append(f"- Config: OK ({config.get('path')})")
        lines.append(f"- Sidecar: {_mapping(data.get('sidecar')).get('address')}")
    else:
        lines.append(f"- Config: ERROR ({config.get('path')})")
        lines.append(f"- Error: {config.get('error', 'unknown')}")

    hermes = report.hermes
    hermes_status = hermes.get("status", "not_checked")
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

    runtime_import = _section(report.runtime.get("runtime_import"), {})
    if runtime_import.get("status"):
        lines.append(
            f"- Runtime import: {runtime_import['status']} - "
            f"{runtime_import.get('message', '')}"
        )
    if report.streaming.get("status"):
        lines.append(
            f"- Streaming: {report.streaming['status']} - "
            f"{report.streaming.get('message', '')}"
        )
    if report.install_state.get("status"):
        lines.append(
            f"- Install state: {report.install_state['status']} - "
            f"{report.install_state.get('message', '')}"
        )

    lines.extend(("", "Next steps"))
    if not report.findings:
        lines.append("- No action required.")
        return "\n".join(lines)
    for finding in report.findings:
        lines.append(f"- [{finding.severity}] {finding.message}")
        if finding.actions:
            lines.append(f"  Next: {finding.actions[0]}")
    return "\n".join(lines)


def _build_findings(
    config: dict[str, object],
    detection: HermesDetection,
    install_state: dict[str, object],
    streaming: dict[str, object],
    runtime_import: dict[str, object],
    routing: dict[str, object],
    recovery_plan: RecoveryPlan,
) -> tuple[DiagnosticFinding, ...]:
    findings: list[DiagnosticFinding] = []
    if not detection.supported:
        findings.append(
            DiagnosticFinding(
                "hermes_unsupported",
                "error",
                f"Hermes is unsupported: {detection.reason}",
                "The hook cannot be installed safely.",
                ("Use a supported Hermes install before running install or setup.",),
            )
        )
    elif detection.compatibility != "full":
        status_callback_missing = detection.capabilities.get("status_callback") is False
        findings.append(
            DiagnosticFinding(
                "hermes_compatibility_partial",
                "warning",
                "Hermes is supported, but optional compatibility anchors are missing.",
                (
                    "Context-compaction visibility is unavailable; other supported "
                    "features remain installable."
                    if status_callback_missing
                    else "Some streaming, cron, reply, or attachment features may be unavailable."
                ),
                (
                    (
                        "Review anchors.status_callback before relying on "
                        "context-compaction visibility."
                        if status_callback_missing
                        else "Review the anchors section if streaming, cron, reply, or attachment features do not behave as expected."
                    ),
                ),
            )
        )
    if runtime_import.get("status") == "failed":
        findings.append(
            DiagnosticFinding(
                "runtime_import_failed",
                "warning",
                str(runtime_import.get("message") or "Hermes runtime import failed."),
                "Hermes cannot load the sidecar hook runtime.",
                (
                    "Run setup/install again so hermes-feishu-streaming-card is installed into the Hermes Gateway venv Python.",
                ),
            )
        )
    if streaming.get("status") == "disabled":
        findings.append(
            DiagnosticFinding(
                "streaming_disabled",
                "warning",
                str(streaming.get("message") or "Hermes streaming is disabled."),
                "Cards may not receive answer.delta updates.",
                (
                    "Set streaming.enabled: true with streaming.transport: edit, or set display.platforms.feishu.streaming: true.",
                ),
            )
        )
    elif streaming.get("status") == "not_detected":
        findings.append(
            DiagnosticFinding(
                "streaming_not_detected",
                "warning",
                str(streaming.get("message") or "Hermes streaming config was not detected."),
                "Cards may miss answer.delta updates.",
                ("If cards miss answer.delta updates, add Hermes streaming config and rerun doctor.",),
            )
        )
    _append_install_finding(findings, install_state)
    findings.extend(_route_findings(config, routing))

    existing_codes = {finding.code for finding in findings}
    for recovery_finding in sanitize_recovery_plan(recovery_plan)["findings"]:
        if not isinstance(recovery_finding, dict):
            continue
        code = str(recovery_finding.get("code") or "")
        if not code or code in existing_codes:
            continue
        findings.append(
            DiagnosticFinding(
                code,
                "warning",
                str(recovery_finding.get("message") or "Recovery evidence needs attention."),
                "The current install state may need recovery.",
                tuple(recovery_plan.actions),
            )
        )
    return tuple(findings)


def _route_findings(
    config: dict[str, object], routing: dict[str, object]
) -> list[DiagnosticFinding]:
    findings: list[DiagnosticFinding] = []
    profiles = _profiles(config)
    profile_id = str(routing.get("profile_id") or "")
    if profiles and not profile_id:
        findings.append(
            DiagnosticFinding(
                "profile_identity_missing",
                "warning",
                "No profile identity was supplied for a multi-profile config.",
                "The event may route through the wrong profile.",
                ("Set an explicit HERMES_FEISHU_CARD_PROFILE_ID.",),
            )
        )
    elif profile_id and not routing.get("profile_exists"):
        findings.append(
            DiagnosticFinding(
                "profile_unknown",
                "warning",
                "The requested profile is not present in the loaded config.",
                "The requested profile cannot deliver cards.",
                ("Select a configured profile id and rerun doctor.",),
            )
        )
    elif routing.get("profile_exists") and not routing.get("credentials_present"):
        findings.append(
            DiagnosticFinding(
                "profile_credentials_missing",
                "warning",
                "The requested profile has no complete Feishu credentials.",
                "The profile cannot authenticate with Feishu.",
                ("Configure both app_id and app_secret for the requested profile.",),
            )
        )

    endpoint = str(routing.get("event_endpoint") or "")
    if endpoint and not _endpoint_matches_config(endpoint, config):
        findings.append(
            DiagnosticFinding(
                "event_endpoint_mismatch",
                "warning",
                "The event endpoint does not match the loaded sidecar address.",
                "Hermes events may be sent to a different sidecar.",
                ("Use the loaded sidecar host and port in the event URL.",),
            )
        )

    bot_id = str(routing.get("bot_id") or "")
    if bot_id and not _bot_exists(config, profile_id, bot_id):
        findings.append(
            DiagnosticFinding(
                "bot_unknown",
                "warning",
                "The resolved bot is not present in the selected profile.",
                "The route cannot select valid Feishu credentials.",
                ("Bind the route to a configured bot id.",),
            )
        )
    profile_source = str(routing.get("profile_source") or "")
    route_reason = str(routing.get("route_reason") or "")
    if profile_source.startswith("fallback") or route_reason in {
        "bindings.fallback_bot",
        "bots.default",
        "default",
    }:
        findings.append(
            DiagnosticFinding(
                "route_fallback",
                "info",
                "The event resolved through a fallback route.",
                "Delivery may use a default profile or bot instead of an explicit binding.",
                ("Add an explicit profile or chat binding when deterministic routing is required.",),
            )
        )
    return findings


def _append_install_finding(
    findings: list[DiagnosticFinding], install_state: dict[str, object]
) -> None:
    status = str(install_state.get("status") or "")
    if status == "clean":
        findings.append(
            DiagnosticFinding(
                "install_state_clean",
                "info",
                "No existing Hermes Feishu hook install state was found.",
                actions=("Run install --hermes-dir PATH --yes when ready to patch Hermes.",),
            )
        )
    elif status == "installed":
        findings.append(
            DiagnosticFinding(
                "install_state_installed",
                "info",
                "Existing hook install state is complete and consistent.",
                actions=("No install-state action is required.",),
            )
        )
    elif status in {"changed", "incomplete", "error"}:
        code = "install_state_changed" if status == "changed" else "install_state_incomplete"
        automatic = bool(install_state.get("automatic_repair_available"))
        action = (
            "Run repair --hermes-dir PATH --yes to rebuild known-safe backup/manifest state, then rerun doctor."
            if automatic
            else "Back up the Hermes directory, inspect gateway/run.py and the manifest, then restore or reinstall only after confirming the local edits are intentional."
        )
        findings.append(
            DiagnosticFinding(
                code,
                "warning",
                str(install_state.get("message") or "Install state needs attention."),
                "The hook state is not safe to change without review.",
                (action,),
            )
        )


def _build_install_state(
    recovery_plan: RecoveryPlan, legacy_state: object
) -> dict[str, object]:
    safe = sanitize_recovery_plan(recovery_plan)
    if isinstance(legacy_state, dict):
        result = dict(legacy_state)
    else:
        status = {
            "clean": "clean",
            "installed": "installed",
            "stale_unpatched": "incomplete",
            "owned_incomplete": "incomplete",
            "corrupt_owned": "incomplete",
            "refused": "changed",
        }.get(recovery_plan.state, "error")
        result = {
            "checked": True,
            "status": status,
            "message": f"Recovery state: {recovery_plan.state}.",
            "manual_action_required": status not in {"clean", "installed"},
            "automatic_repair_available": recovery_plan.executable,
        }
    result.update(
        {
            "recovery_state": recovery_plan.state,
            "recovery_executable": recovery_plan.executable,
            "recovery_fingerprint": safe["fingerprint"],
            "recovery_actions": safe["actions"],
            "recovery_findings": safe["findings"],
        }
    )
    return result


def _build_runtime(
    health: dict[str, object], runtime_import: dict[str, object]
) -> dict[str, object]:
    runtime: dict[str, object] = {"runtime_import": runtime_import}
    if health:
        runtime["sidecar_status"] = str(health.get("status") or "")
        runtime["active_sessions"] = _integer(health.get("active_sessions"), 0)
        metrics = _mapping(health.get("metrics"))
        if metrics:
            runtime["metrics"] = dict(metrics)
    return runtime


def _last_route(health: dict[str, object]) -> dict[str, object] | None:
    routing = _mapping(health.get("routing"))
    route = routing.get("last_route")
    if isinstance(route, dict):
        return route
    return None


def _profile_exists(config: dict[str, object], profile_id: str) -> bool:
    profiles = _profiles(config)
    if profiles:
        return bool(profile_id and profile_id in profiles)
    return profile_id in {"", "default"}


def _profile_credentials_present(config: dict[str, object], profile_id: str) -> bool:
    profiles = _profiles(config)
    if profiles:
        profile = _mapping(profiles.get(profile_id))
        if not profile:
            return False
        return _config_credentials_present(profile)
    return _config_credentials_present(config)


def _config_credentials_present(config: dict[str, object]) -> bool:
    feishu = _mapping(config.get("feishu"))
    if feishu.get("app_id") and feishu.get("app_secret"):
        return True
    bots = _mapping(config.get("bots"))
    items = _mapping(bots.get("items"))
    return any(
        bool(_mapping(value).get("app_id") and _mapping(value).get("app_secret"))
        for value in items.values()
    )


def _bot_exists(config: dict[str, object], profile_id: str, bot_id: str) -> bool:
    profiles = _profiles(config)
    selected = _mapping(profiles.get(profile_id)) if profiles else config
    bots = _mapping(selected.get("bots"))
    items = _mapping(bots.get("items"))
    if bot_id in items:
        return True
    return bot_id == "default" and _config_credentials_present(selected)


def _endpoint_matches_config(endpoint: str, config: dict[str, object]) -> bool:
    server = _mapping(config.get("server"))
    actual = _endpoint_parts(endpoint)
    if actual is None:
        return False
    scheme, host, port, path = actual
    expected_host = _normalize_host(str(server.get("host") or "127.0.0.1"))
    expected_port = _integer(server.get("port"), 8765)
    return (
        scheme == "http"
        and _hosts_equivalent(host, expected_host)
        and port == expected_port
        and path == "/events"
    )


def _safe_endpoint(event_url: str) -> str:
    text = str(event_url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = _normalize_host(parsed.hostname)
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
        path = _normalize_endpoint_path(parsed.path)
        return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
    except ValueError:
        return ""


def _card_safe_endpoint(event_url: str) -> str:
    endpoint = _safe_endpoint(event_url)
    if not endpoint:
        return ""
    parsed = urlsplit(endpoint)
    path = parsed.path if parsed.path == "/events" else _CARD_REDACTED_ENDPOINT_PATH
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _endpoint_parts(endpoint: str) -> tuple[str, str, int, str] | None:
    try:
        parsed = urlsplit(endpoint)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        return (
            scheme,
            _normalize_host(parsed.hostname),
            port,
            _normalize_endpoint_path(parsed.path),
        )
    except ValueError:
        return None


def _canonical_endpoint(endpoint: str) -> str:
    parts = _endpoint_parts(_safe_endpoint(endpoint))
    if parts is None:
        return ""
    scheme, host, port, path = parts
    if host in {"127.0.0.1", "::1", "localhost"}:
        host = "loopback"
    return f"{scheme}|{host}|{port}|{path}"


def _normalize_endpoint_path(path: str) -> str:
    normalized = path or "/events"
    if normalized != "/":
        normalized = normalized.rstrip("/") or "/"
    return normalized


def _normalize_host(host: str) -> str:
    return str(host or "").strip().lower().rstrip(".")


def _hosts_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    loopback = {"127.0.0.1", "::1", "localhost"}
    wildcard = {"0.0.0.0", "::"}
    if left in loopback and right in loopback:
        return True
    return (left in loopback and right in wildcard) or (
        left in wildcard and right in loopback
    )


def _fingerprint_value(value: object, key: str = "") -> object:
    lowered = key.lower()
    if _is_volatile_key(lowered):
        return None
    if isinstance(value, dict):
        return {
            str(child_key): sanitized
            for child_key, child_value in sorted(value.items(), key=lambda item: str(item[0]))
            if (sanitized := _fingerprint_value(child_value, str(child_key))) is not None
        }
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(item) for item in value]
    return value


def _is_volatile_key(key: str) -> bool:
    return (
        key == "created_at"
        or key in _IDENTIFIER_KEYS
        or _is_sensitive_key(key)
        or key in _PATH_KEYS
        or key in _RAW_HASH_KEYS
        or key in {"active_sessions", "event_endpoint", "metrics", "process_pid", "sessions"}
        or key.endswith("_id")
        or key.endswith("_id_hash")
        or key.endswith("_path")
        or key.endswith("_sha256")
        or (key.endswith("_hash") and key != "recovery_fingerprint")
        or key in {"fingerprint", "message", "reason"}
    )


def _card_safe_config(value: object) -> dict[str, object]:
    data = _mapping(value)
    result: dict[str, object] = {}
    _copy_path_hash(result, data, "path")
    _copy_bool(result, data, "loaded")
    server = _mapping(data.get("server"))
    host = _safe_host(server.get("host"))
    port = _safe_port(server.get("port"))
    if host and port is not None:
        result["server"] = {"host": host, "port": port}
    credentials = _safe_enum(
        data.get("feishu_credentials"), {"configured", "missing"}, ""
    )
    if credentials:
        result["feishu_credentials"] = credentials
    _copy_bool(result, data, "profiles_enabled")
    _copy_nonnegative_int(result, data, "profile_count")
    return result


def _card_safe_hermes(value: object) -> dict[str, object]:
    data = _mapping(value)
    result: dict[str, object] = {}
    _copy_bool(result, data, "checked")
    status = _safe_enum(
        data.get("status"), {"not_checked", "skipped", "supported", "unsupported"}, ""
    )
    if status:
        result["status"] = status
    for key in ("root", "run_py", "cron_py", "suggested_root"):
        _copy_path_hash(result, data, key)
    _copy_bool(result, data, "run_py_exists")
    _copy_bool(result, data, "cron_py_exists")
    for key in ("version", "minimum_supported_version"):
        text = str(data.get(key) or "")
        if _SAFE_VERSION_RE.fullmatch(text):
            result[key] = text
    hook_strategy = _safe_enum(
        data.get("hook_strategy"),
        {"gateway_run_013_plus", "legacy_gateway_run"},
        "",
    )
    if hook_strategy:
        result["hook_strategy"] = hook_strategy
    cron_strategy = _safe_enum(data.get("cron_hook_strategy"), {"cron_scheduler"}, "")
    if cron_strategy:
        result["cron_hook_strategy"] = cron_strategy
    compatibility = _safe_enum(
        data.get("compatibility"), {"full", "partial", "unsupported"}, ""
    )
    if compatibility:
        result["compatibility"] = compatibility
    anchors = _mapping(data.get("anchors"))
    result["anchors"] = {
        key: bool(anchors[key])
        for key in sorted(_CARD_CAPABILITIES)
        if isinstance(anchors.get(key), bool)
    }
    suggestion_reason = _safe_enum(
        data.get("suggestion_reason"), {"hermes_cli_project"}, ""
    )
    if suggestion_reason:
        result["suggestion_reason"] = suggestion_reason
    return result


def _card_safe_status_section(value: object, statuses: set[str]) -> dict[str, object]:
    data = _mapping(value)
    result: dict[str, object] = {}
    status = _safe_enum(data.get("status"), statuses, "")
    if status:
        result["status"] = status
    _copy_bool(result, data, "checked")
    return result


def _card_safe_install_state(value: object) -> dict[str, object]:
    data = _mapping(value)
    result = _card_safe_status_section(
        data, {"changed", "clean", "error", "incomplete", "installed", "skipped"}
    )
    for key in (
        "automatic_repair_available",
        "manual_action_required",
        "recovery_executable",
    ):
        _copy_bool(result, data, key)
    recovery_state = _safe_enum(
        data.get("recovery_state"),
        {"clean", "corrupt_owned", "installed", "owned_incomplete", "refused", "stale_unpatched"},
        "",
    )
    if recovery_state:
        result["recovery_state"] = recovery_state
    actions = data.get("recovery_actions")
    if isinstance(actions, (list, tuple)):
        result["recovery_actions"] = [
            action
            for action in actions
            if isinstance(action, str) and action in _CARD_RECOVERY_ACTIONS
        ]
    findings = data.get("recovery_findings")
    if isinstance(findings, (list, tuple)):
        result["recovery_findings"] = _card_safe_findings(findings)
    return result


def _card_safe_runtime_import(value: object) -> dict[str, object]:
    data = _mapping(value)
    result = _card_safe_status_section(
        data, {"failed", "not_checked", "ok", "skipped"}
    )
    _copy_path_hash(result, data, "python")
    return result


def _card_safe_routing(value: object) -> dict[str, object]:
    data = _mapping(value)
    result: dict[str, object] = {}
    for key in sorted(_IDENTIFIER_KEYS):
        raw = data.get(key)
        if isinstance(raw, str) and raw:
            result[f"{key}_hash"] = _short_hash(raw)
    profile_source = _safe_enum(data.get("profile_source"), _CARD_PROFILE_SOURCES, "")
    if profile_source:
        result["profile_source"] = profile_source
    endpoint = _card_safe_endpoint(str(data.get("event_endpoint") or ""))
    if endpoint:
        result["event_endpoint"] = endpoint
    _copy_bool(result, data, "profile_exists")
    _copy_bool(result, data, "credentials_present")
    route_reason = _safe_enum(data.get("route_reason"), _CARD_ROUTE_REASONS, "")
    if route_reason:
        result["route_reason"] = route_reason
    return result


def _card_safe_runtime(value: object) -> dict[str, object]:
    data = _mapping(value)
    result: dict[str, object] = {}
    if isinstance(data.get("runtime_import"), dict):
        result["runtime_import"] = _card_safe_runtime_import(data["runtime_import"])
    status = _safe_enum(data.get("sidecar_status"), {"degraded", "healthy", "ok"}, "")
    if status:
        result["sidecar_status"] = status
    _copy_nonnegative_int(result, data, "active_sessions")
    metrics = _mapping(data.get("metrics"))
    safe_metrics: dict[str, object] = {}
    for key in sorted(_CARD_METRICS):
        _copy_nonnegative_int(safe_metrics, metrics, key)
    if safe_metrics:
        result["metrics"] = safe_metrics
    return result


def _card_safe_findings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    findings: list[dict[str, object]] = []
    for item in value:
        data = _mapping(item)
        code = str(data.get("code") or "")
        if code not in _CARD_FINDING_CODES:
            code = "diagnostic_finding"
        severity = _safe_enum(data.get("severity"), {"error", "info", "warning"}, "warning")
        findings.append(
            {
                "code": code,
                "severity": severity,
                "message": "Diagnostic finding requires attention.",
                "impact": "",
                "actions": [],
            }
        )
    return findings


def _copy_path_hash(
    result: dict[str, object], data: dict[str, object], key: str
) -> None:
    value = data.get(key)
    if isinstance(value, str) and value:
        result[f"{key}_hash"] = _short_hash(value)


def _copy_bool(result: dict[str, object], data: dict[str, object], key: str) -> None:
    value = data.get(key)
    if isinstance(value, bool):
        result[key] = value


def _copy_nonnegative_int(
    result: dict[str, object], data: dict[str, object], key: str
) -> None:
    value = data.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        result[key] = value


def _safe_enum(value: object, allowed: set[str], default: str) -> str:
    text = str(value or "")
    return text if text in allowed else default


def _safe_host(value: object) -> str:
    host = _normalize_host(str(value or ""))
    if not host or len(host) > 253 or not _SAFE_HOST_RE.fullmatch(host):
        return ""
    return host


def _safe_port(value: object) -> int | None:
    port = _integer(value, 0)
    return port if 1 <= port <= 65535 else None


def _is_sensitive_key(key: str) -> bool:
    return key in _SENSITIVE_KEYS or key.endswith(
        ("_api_key", "_credential", "_password", "_secret", "_token")
    )


def _short_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:12]


def _state_digest(kind: str, value: str) -> str:
    material = f"hfc-diagnostic-state-v1\0{kind}\0{value}".encode("utf-8")
    return sha256(material).hexdigest()


def _finding_dict(finding: DiagnosticFinding) -> dict[str, object]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "message": finding.message,
        "impact": finding.impact,
        "actions": list(finding.actions),
    }


def _recommendation(finding: DiagnosticFinding) -> dict[str, object]:
    return {
        "severity": finding.severity,
        "code": finding.code,
        "message": finding.message,
        "next_step": finding.actions[0] if finding.actions else "",
    }


def _status_for_findings(findings: tuple[DiagnosticFinding, ...]) -> str:
    severities = {finding.severity for finding in findings}
    if "error" in severities:
        return "error"
    if "warning" in severities:
        return "warning"
    return "ok"


def _profiles(config: dict[str, object]) -> dict[str, object]:
    return _mapping(config.get("profiles"))


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _section(value: object, default: dict[str, object]) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else dict(default)


def _integer(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
