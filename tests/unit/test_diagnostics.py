from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from hermes_feishu_card.diagnostics import (
    DiagnosticFinding,
    DiagnosticReport,
    build_diagnostic_report,
    build_route_chain,
)
from hermes_feishu_card.install.detect import HermesDetection
from hermes_feishu_card.install.recovery import RecoveryPlan


def _detection(tmp_path: Path) -> HermesDetection:
    root = tmp_path / "private" / "hermes"
    return HermesDetection(
        root=root,
        version="0.17.0",
        version_source="VERSION",
        minimum_version="v2026.4.23",
        run_py=root / "gateway" / "run.py",
        run_py_exists=True,
        supported=True,
        reason="supported",
        hook_strategy="gateway_run_013_plus",
        compatibility="full",
        capabilities={"message_handler": True},
    )


def _recovery_plan(tmp_path: Path, *, state: str = "installed") -> RecoveryPlan:
    return RecoveryPlan(
        root=tmp_path / "private" / "hermes",
        state=state,
        executable=state not in {"installed", "refused"},
        fingerprint="a" * 64,
        actions=("repair_gateway",) if state != "installed" else (),
        findings=(),
    )


def _report(tmp_path: Path, **overrides: object) -> DiagnosticReport:
    values: dict[str, object] = {
        "status": "warning",
        "created_at": 100.0,
        "config": {"path": str(tmp_path / "private" / "config.yaml")},
        "hermes": {"root": str(tmp_path / "private" / "hermes")},
        "streaming": {"status": "enabled"},
        "install_state": {"status": "installed", "recovery_state": "installed"},
        "routing": {
            "profile_id": "work",
            "chat_id": "oc_secret_chat",
            "operator_open_id": "ou_secret_operator",
        },
        "runtime": {},
        "findings": (),
    }
    values.update(overrides)
    return DiagnosticReport(**values)


def test_card_safe_report_redacts_paths_and_route_ids(tmp_path):
    report = _report(tmp_path)

    payload = report.to_dict(card_safe=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "oc_secret_chat" not in serialized
    assert "ou_secret_operator" not in serialized
    assert str(tmp_path) not in serialized
    assert payload["routing"]["chat_id_hash"]


def test_card_safe_report_keeps_event_auth_rejection_counter(tmp_path):
    report = _report(
        tmp_path,
        runtime={"metrics": {"event_auth_rejections": 3}},
    )

    payload = report.to_dict(card_safe=True)

    assert payload["runtime"]["metrics"]["event_auth_rejections"] == 3


def test_report_keeps_full_recovery_fingerprint_internal_and_redacts_output(tmp_path):
    plan = _recovery_plan(tmp_path, state="owned_incomplete")
    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
        _detection(tmp_path),
        plan,
    )

    assert report.recovery_fingerprint == plan.fingerprint
    assert report.install_state["recovery_fingerprint"] == plan.fingerprint[:12]
    assert plan.fingerprint not in json.dumps(report.to_dict(), sort_keys=True)
    assert plan.fingerprint not in json.dumps(report.to_dict(card_safe=True), sort_keys=True)


def test_partial_compatibility_reports_unavailable_compaction_visibility(tmp_path):
    detection = replace(
        _detection(tmp_path),
        compatibility="partial",
        capabilities={"message_handler": True, "status_callback": False},
    )

    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
        detection,
        _recovery_plan(tmp_path),
    )

    finding = next(
        item for item in report.findings if item.code == "hermes_compatibility_partial"
    )
    assert detection.supported is True
    assert "Context-compaction visibility is unavailable" in finding.impact
    assert "status_callback" in finding.actions[0]


def test_card_safe_report_redacts_secrets_source_text_and_raw_hashes(tmp_path):
    raw_hash = "b" * 64
    report = _report(
        tmp_path,
        config={
            "path": str(tmp_path / "private" / "config.yaml"),
            "app_secret": "secret-value",
        },
        install_state={"source_text": "private source", "current_sha256": raw_hash},
        runtime={
            "message_id": "om_private_message",
            "credential": "private-token",
            "api_key": "private-api-key",
            "password": "private-password",
            "source_hash": "c" * 64,
        },
    )

    payload = report.to_dict(card_safe=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    for sensitive in (
        "secret-value",
        "private source",
        raw_hash,
        "om_private_message",
        "private-token",
        "private-api-key",
        "private-password",
        "c" * 64,
    ):
        assert sensitive not in serialized


def test_card_safe_report_allowlists_nested_data_and_scrubs_finding_text(tmp_path):
    raw_hash = "d" * 64
    report = _report(
        tmp_path,
        config={
            "path": str(tmp_path / "private" / "config.yaml"),
            "source": "raw source content",
            "oauth_credentials": {
                "client_material": "private-oauth-material",
            },
        },
        routing={
            "profile_id": "work",
            "profile_source": "env",
            "event_endpoint": "http://127.0.0.1:8765/events",
            "profile_exists": True,
            "credentials_present": True,
            "bot_id": "sales",
            "route_reason": "bindings.chats",
            "source": "nested route source",
        },
        runtime={
            "details": [
                {"source": "list source text"},
                {"oauth_credentials": ["list-oauth-material", "ou_list_operator"]},
            ]
        },
        findings=(
            DiagnosticFinding(
                "custom_sensitive_finding",
                "warning",
                (
                    "Source raw finding text used oauth_credentials=private-finding-oauth "
                    f"for oc_private_chat at {tmp_path / 'private' / 'source.py'} "
                    f"with hash {raw_hash}."
                ),
                "Credential private-impact-secret cannot be shown.",
                ("Use token private-action-token for ou_private_operator.",),
            ),
        ),
    )

    payload = report.to_dict(card_safe=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    for sensitive in (
        "raw source content",
        "private-oauth-material",
        "nested route source",
        "list source text",
        "list-oauth-material",
        "ou_list_operator",
        "private-finding-oauth",
        "oc_private_chat",
        "private-impact-secret",
        "private-action-token",
        "ou_private_operator",
        raw_hash,
        str(tmp_path),
    ):
        assert sensitive not in serialized
    assert payload["routing"]["profile_source"] == "env"
    assert payload["routing"]["event_endpoint"] == "http://127.0.0.1:8765/events"


@pytest.mark.parametrize("profile_source", ["sanitized_locals", "sanitized_hermes_home"])
def test_card_safe_report_keeps_sanitized_profile_sources(tmp_path, profile_source):
    report = _report(
        tmp_path,
        routing={"profile_id": "default", "profile_source": profile_source},
    )

    assert report.to_dict(card_safe=True)["routing"]["profile_source"] == profile_source


def test_cli_report_keeps_existing_top_level_doctor_contract(tmp_path):
    payload = _report(tmp_path, status="ok").to_dict()

    assert set(("status", "config", "hermes", "streaming", "install_state")) <= payload.keys()
    assert payload["schema_version"] == "1"
    assert isinstance(payload["recommendations"], list)


def test_diagnostic_fingerprint_ignores_created_at_and_sensitive_identifiers(tmp_path):
    report = _report(tmp_path)
    changed_volatile = replace(
        report,
        created_at=999.0,
        routing={
            **report.routing,
            "chat_id": "oc_another_chat",
            "operator_open_id": "ou_another_operator",
        },
    )

    assert report.fingerprint == changed_volatile.fingerprint


def test_diagnostic_fingerprint_ignores_runtime_counters(tmp_path):
    report = _report(
        tmp_path,
        runtime={"active_sessions": 1, "metrics": {"sessions_collected": 3}},
    )
    changed_counters = replace(
        report,
        runtime={"active_sessions": 9, "metrics": {"sessions_collected": 100}},
    )

    assert report.fingerprint == changed_counters.fingerprint


def test_diagnostic_fingerprint_changes_with_recovery_and_profile_state(tmp_path):
    report = _report(
        tmp_path,
        install_state={
            "status": "installed",
            "recovery_state": "installed",
            "recovery_fingerprint": "aaaaaaaaaaaa",
        },
    )
    changed_recovery = replace(
        report,
        install_state={"status": "incomplete", "recovery_state": "owned_incomplete"},
    )
    changed_profile = replace(
        report,
        routing={**report.routing, "profile_id": "sales", "profile_exists": False},
    )
    changed_recovery_evidence = replace(
        report,
        install_state={**report.install_state, "recovery_fingerprint": "bbbbbbbbbbbb"},
    )

    assert report.fingerprint != changed_recovery.fingerprint
    assert report.fingerprint != changed_profile.fingerprint
    assert report.fingerprint != changed_recovery_evidence.fingerprint


def test_diagnostic_fingerprint_changes_with_valid_profile_and_bot_identity(tmp_path):
    report = _report(
        tmp_path,
        routing={
            "profile_id": "work",
            "profile_source": "env",
            "profile_exists": True,
            "credentials_present": True,
            "bot_id": "sales",
            "route_reason": "bindings.chats",
        },
    )
    changed_profile = replace(
        report,
        routing={**report.routing, "profile_id": "personal"},
    )
    changed_bot = replace(
        report,
        routing={**report.routing, "bot_id": "support"},
    )

    assert report.fingerprint != changed_profile.fingerprint
    assert report.fingerprint != changed_bot.fingerprint


def test_diagnostic_fingerprint_changes_with_normalized_endpoint_state(tmp_path):
    report = _report(
        tmp_path,
        routing={
            **_report(tmp_path).routing,
            "event_endpoint": "http://127.0.0.1:8765/events",
        },
    )
    changed_endpoint = replace(
        report,
        routing={
            **report.routing,
            "event_endpoint": "http://sidecar.example:8765/events",
        },
    )

    assert report.fingerprint != changed_endpoint.fingerprint


def test_finding_defaults_are_immutable_tuples():
    first = DiagnosticFinding("first", "info", "First")
    second = DiagnosticFinding("second", "warning", "Second")

    assert first.impact == ""
    assert first.actions == ()
    assert second.actions == ()


def test_build_route_chain_exposes_ordered_profile_route_state():
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "profiles": {
            "work": {
                "feishu": {"app_id": "app", "app_secret": "secret"},
                "bots": {"items": {"sales": {"app_id": "app", "app_secret": "secret"}}},
            }
        },
    }

    chain = build_route_chain(
        config,
        profile_id="work",
        profile_source="env",
        event_url="http://127.0.0.1:8765/events?ignored=yes",
        route={"bot_id": "sales", "reason": "bindings.chats"},
    )

    assert list(chain) == [
        "profile_id",
        "profile_source",
        "event_endpoint",
        "profile_exists",
        "credentials_present",
        "bot_id",
        "route_reason",
    ]
    assert chain == {
        "profile_id": "work",
        "profile_source": "env",
        "event_endpoint": "http://127.0.0.1:8765/events",
        "profile_exists": True,
        "credentials_present": True,
        "bot_id": "sales",
        "route_reason": "bindings.chats",
    }


@pytest.mark.parametrize(
    ("profile_id", "profile_source", "event_url", "route", "expected_code"),
    [
        ("", "", "http://127.0.0.1:8765/events", None, "profile_identity_missing"),
        ("ghost", "env", "http://127.0.0.1:8765/events", None, "profile_unknown"),
        ("work", "env", "http://127.0.0.1:9999/events", None, "event_endpoint_mismatch"),
        ("empty", "env", "http://127.0.0.1:8765/events", None, "profile_credentials_missing"),
        (
            "work",
            "env",
            "http://127.0.0.1:8765/events",
            {"bot_id": "ghost", "reason": "bindings.chats"},
            "bot_unknown",
        ),
        (
            "work",
            "fallback_default",
            "http://127.0.0.1:8765/events",
            {"bot_id": "sales", "reason": "bindings.fallback_bot"},
            "route_fallback",
        ),
    ],
)
def test_build_report_emits_profile_route_findings(
    tmp_path,
    profile_id,
    profile_source,
    event_url,
    route,
    expected_code,
):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "", "app_secret": ""},
        "profiles": {
            "work": {
                "feishu": {"app_id": "app", "app_secret": "secret"},
                "bots": {
                    "default": "sales",
                    "items": {
                        "sales": {"app_id": "app", "app_secret": "secret"},
                    },
                },
            },
            "empty": {
                "feishu": {"app_id": "", "app_secret": ""},
                "bots": {"items": {}},
            },
        },
    }
    health = {"routing": {"last_route": route or {}}}

    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        config,
        _detection(tmp_path),
        _recovery_plan(tmp_path),
        health=health,
        profile_id=profile_id,
        profile_source=profile_source,
        event_url=event_url,
    )

    assert expected_code in {finding.code for finding in report.findings}


@pytest.mark.parametrize(
    "event_url",
    [
        "http://sidecar.example:8765/events",
        "https://127.0.0.1:8765/events",
        "http://127.0.0.1:8765/wrong-path",
    ],
)
def test_build_report_compares_complete_normalized_event_endpoint(tmp_path, event_url):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }

    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        config,
        _detection(tmp_path),
        _recovery_plan(tmp_path),
        event_url=event_url,
    )

    assert "event_endpoint_mismatch" in {finding.code for finding in report.findings}


def test_card_safe_endpoint_is_useful_and_strips_url_credentials(tmp_path):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }
    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        config,
        _detection(tmp_path),
        _recovery_plan(tmp_path),
        event_url=(
            "http://private-user:private-password@localhost:8765/events"
            "?tenant_token=private-token#private-fragment"
        ),
    )

    payload = report.to_dict(card_safe=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["routing"]["event_endpoint"] == "http://localhost:8765/events"
    assert "event_endpoint_mismatch" not in {finding.code for finding in report.findings}
    for sensitive in (
        "private-user",
        "private-password",
        "private-token",
        "private-fragment",
    ):
        assert sensitive not in serialized


@pytest.mark.parametrize(
    "private_path",
    [
        "/events/oc_private_chat/private-token",
        "/callbacks/ou_private_operator/tenant_access_token",
    ],
)
def test_card_safe_endpoint_redacts_unreviewed_url_paths(tmp_path, private_path):
    report = _report(
        tmp_path,
        routing={"event_endpoint": f"http://localhost:8765{private_path}"},
    )

    payload = report.to_dict(card_safe=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["routing"]["event_endpoint"] == (
        "http://localhost:8765/[redacted-path]"
    )
    assert "oc_private_chat" not in serialized
    assert "ou_private_operator" not in serialized
    assert "private-token" not in serialized
    assert "tenant_access_token" not in serialized


def test_card_safe_endpoint_preserves_reviewed_events_path(tmp_path):
    report = _report(
        tmp_path,
        routing={"event_endpoint": "http://localhost:8765/events"},
    )

    payload = report.to_dict(card_safe=True)

    assert payload["routing"]["event_endpoint"] == "http://localhost:8765/events"


def test_unreviewed_endpoint_path_keeps_internal_mismatch_and_fingerprint_state(tmp_path):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }
    private_endpoint = "http://localhost:8765/events/oc_private_chat/private-token"
    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        config,
        _detection(tmp_path),
        _recovery_plan(tmp_path),
        event_url=private_endpoint,
    )
    expected_endpoint_report = _report(
        tmp_path,
        routing={"event_endpoint": "http://localhost:8765/events"},
    )
    private_endpoint_report = replace(
        expected_endpoint_report,
        routing={"event_endpoint": private_endpoint},
    )

    assert report.routing["event_endpoint"] == private_endpoint
    assert "event_endpoint_mismatch" in {finding.code for finding in report.findings}
    assert expected_endpoint_report.fingerprint != private_endpoint_report.fingerprint


def test_build_report_finds_missing_credentials_for_legacy_single_profile(tmp_path):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "", "app_secret": ""},
    }

    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        config,
        _detection(tmp_path),
        _recovery_plan(tmp_path),
        profile_id="",
    )

    assert "profile_credentials_missing" in {finding.code for finding in report.findings}
