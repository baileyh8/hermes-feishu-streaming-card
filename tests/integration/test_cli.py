import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import hermes_feishu_card.cli as cli_module
from hermes_feishu_card.cli import main
from hermes_feishu_card.diagnostics import DiagnosticReport


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
CONFIG_ENV_VARS = {
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "HERMES_FEISHU_CARD_PROFILE_ID",
    "HERMES_FEISHU_CARD_EVENT_URL",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
}


@pytest.fixture(autouse=True)
def clear_config_env(monkeypatch):
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_doctor_loads_config_and_prints_sidecar_address(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9002\n", encoding="utf-8")

    exit_code = main(["doctor", "--config", str(config_path), "--skip-hermes"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "doctor" in captured.out.lower()
    assert "127.0.0.1:9002" in captured.out


def test_status_reports_process_state(capsys):
    exit_code = main(["status"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "status" in captured.out.lower()
    assert "not implemented" not in captured.out.lower()
    assert "running" in captured.out.lower() or "stopped" in captured.out.lower()


def test_start_passes_explicit_env_file_to_sidecar(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "CUSTOM.env"
    config_path.write_text("server: {}\n", encoding="utf-8")
    env_path.write_text("HERMES_DIR=custom-hermes\n", encoding="utf-8")
    started = {}
    monkeypatch.setattr(
        cli_module,
        "start_sidecar",
        lambda path, config, **kwargs: started.update(
            path=Path(path), config=config, kwargs=kwargs
        )
        or "started",
    )

    assert main(["start", "--config", str(config_path), "--env-file", str(env_path)]) == 0
    assert capsys.readouterr().err == ""
    assert started["path"] == config_path
    assert started["kwargs"] == {"env_file": str(env_path)}


def test_start_keeps_default_sidecar_arguments(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server: {}\n", encoding="utf-8")
    started = {}
    monkeypatch.setattr(
        cli_module,
        "start_sidecar",
        lambda path, config, **kwargs: started.update(kwargs=kwargs) or "started",
    )

    assert main(["start", "--config", str(config_path)]) == 0
    assert capsys.readouterr().err == ""
    assert started == {"kwargs": {}}


def test_status_reports_cron_metrics_when_sidecar_is_running(monkeypatch, capsys):
    def fake_status_sidecar(config):
        return {
            "running": True,
            "pid": 12345,
            "health": {
                "active_sessions": 0,
                "metrics": {
                    "cron_cards_sent": 2,
                    "cron_fallbacks": 1,
                },
            },
        }

    monkeypatch.setattr("hermes_feishu_card.cli.status_sidecar", fake_status_sidecar)

    exit_code = main(["status"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cron_cards_sent: 2" in captured.out
    assert "cron_fallbacks: 1" in captured.out


def test_status_reports_event_auth_rejections(monkeypatch, capsys):
    monkeypatch.setattr(
        "hermes_feishu_card.cli.status_sidecar",
        lambda config: {
            "running": True,
            "pid": 12345,
            "health": {
                "active_sessions": 0,
                "metrics": {"event_auth_rejections": 2},
            },
        },
    )

    exit_code = main(["status"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "event_auth_rejections: 2" in captured.out


def test_status_reports_routing_and_profile_diagnostics(monkeypatch, capsys):
    def fake_status_sidecar(config):
        return {
            "running": True,
            "pid": 12345,
            "health": {
                "active_sessions": 0,
                "metrics": {},
                "routing": {
                    "bot_count": 3,
                    "chat_binding_count": 2,
                    "last_route": {
                        "profile_id": "work",
                        "bot_id": "sales",
                        "reason": "bindings.chats",
                    },
                    "last_route_error": "",
                },
                "profile_diagnostics": {
                    "work": {
                        "events": 4,
                        "last_profile_source": "env",
                    }
                },
            },
        }

    monkeypatch.setattr("hermes_feishu_card.cli.status_sidecar", fake_status_sidecar)

    exit_code = main(["status"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "routing.bot_count: 3" in captured.out
    assert "routing.chat_binding_count: 2" in captured.out
    assert "routing.last_route: profile=work bot=sales reason=bindings.chats" in captured.out
    assert "profile.work.events: 4" in captured.out
    assert "profile.work.last_profile_source: env" in captured.out


def test_doctor_bad_config_returns_nonzero(tmp_path, capsys):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("- bad\n", encoding="utf-8")

    exit_code = main(["doctor", "--config", str(config_path), "--skip-hermes"])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "error" in captured.err.lower()


def run_cli(*args):
    env = {key: value for key, value in os.environ.items() if key not in CONFIG_ENV_VARS}
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_module_doctor_loads_config_and_prints_sidecar_address(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  host: 0.0.0.0\n  port: 9004\n", encoding="utf-8")

    result = run_cli("doctor", "--config", str(config_path), "--skip-hermes")

    assert result.returncode == 0
    assert "doctor" in result.stdout.lower()
    assert "0.0.0.0:9004" in result.stdout


def test_module_doctor_ignores_parent_config_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", "9005")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9006\n", encoding="utf-8")

    result = run_cli("doctor", "--config", str(config_path), "--skip-hermes")

    assert result.returncode == 0
    assert "127.0.0.1:9006" in result.stdout
    assert "9005" not in result.stdout


def test_module_doctor_reports_supported_hermes_detection(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9007\n", encoding="utf-8")

    result = run_cli("doctor", "--config", str(config_path), "--hermes-dir", str(FIXTURE))

    assert result.returncode == 0, result.stderr
    assert "hermes: supported" in result.stdout
    assert f"hermes_root: {FIXTURE}" in result.stdout
    assert f"run_py: {FIXTURE / 'gateway' / 'run.py'}" in result.stdout
    assert "run_py_exists: yes" in result.stdout
    assert "version_source: VERSION" in result.stdout
    assert "version: v2026.4.23" in result.stdout
    assert "minimum_supported_version: v2026.4.23" in result.stdout
    assert "hook_strategy: legacy_gateway_run" in result.stdout
    assert "compatibility: partial" in result.stdout
    assert "anchors:" in result.stdout
    assert "  message_handler: found" in result.stdout
    assert "reason: supported" in result.stdout


def test_module_doctor_json_reports_skipped_hermes(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  host: 0.0.0.0\n  port: 9012\n", encoding="utf-8")

    result = run_cli(
        "doctor",
        "--config",
        str(config_path),
        "--skip-hermes",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "1"
    assert report["status"] == "ok"
    assert report["config"]["path"] == "[redacted]"
    assert str(config_path) not in result.stdout
    assert report["config"]["loaded"] is True
    assert report["config"]["server"] == {"host": "0.0.0.0", "port": 9012}
    assert report["sidecar"]["address"] == "0.0.0.0:9012"
    assert report["hermes"]["checked"] is False
    assert report["hermes"]["status"] == "skipped"
    assert report["install_state"]["status"] == "skipped"
    assert isinstance(report["recommendations"], list)


def test_module_doctor_json_redacts_paths_inside_error_and_recommendation_text(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "private" / "bad.yaml"
    config_path.parent.mkdir()
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda _path: (_ for _ in ()).throw(ValueError(f"invalid config {config_path}")),
    )

    exit_code = main(["doctor", "--config", str(config_path), "--skip-hermes", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert str(config_path) not in captured.out
    report = json.loads(captured.out)
    assert report["config"]["path"] == "[redacted]"
    assert report["config"]["error"].startswith("[redacted-path-text:")
    assert report["recommendations"][0]["message"].startswith("[redacted-path-text:")


@pytest.mark.parametrize(
    "sensitive_path",
    [
        "/Users/Alice/My Project/config.yaml",
        r"C:\Users\Alice\My Project\config.yaml",
        r"\\server\share\My Folder\config.yaml",
    ],
)
def test_doctor_json_redacts_common_absolute_paths_in_text_fields(sensitive_path):
    payload = {
        "config": {"error": f"could not load {sensitive_path}"},
        "hermes": {"reason": f"unsupported root {sensitive_path}"},
        "recommendations": [
            {"next_step": f"inspect {sensitive_path} then retry"},
        ],
    }

    output = cli_module._doctor_json_output_payload(payload)
    output_text = json.dumps(output)

    assert sensitive_path not in output_text
    assert output["config"]["error"].startswith("[redacted-path-text:")
    assert output["hermes"]["reason"].startswith("[redacted-path-text:")
    assert output["recommendations"][0]["next_step"].startswith("[redacted-path-text:")


@pytest.mark.parametrize(
    "sensitive_path",
    [
        "/Users/Alice/My Project/config.yaml",
        r"C:\Users\Alice\My Project\config.yaml",
        r"\\server\share\My Folder\config.yaml",
    ],
)
def test_doctor_json_config_load_failure_redacts_common_absolute_paths(
    sensitive_path, tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda _path: (_ for _ in ()).throw(ValueError(f"invalid config {sensitive_path}")),
    )

    assert main(["doctor", "--config", str(config_path), "--skip-hermes", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    output_text = json.dumps(report)

    assert sensitive_path not in output_text
    assert report["config"]["error"].startswith("[redacted-path-text:")
    assert report["recommendations"][0]["message"].startswith("[redacted-path-text:")


@pytest.mark.parametrize(
    "path_text",
    [
        "/Users/Alice/My Project",
        "/var/log/hermes.log",
        "/opt/hermes/config.bak",
        r"C:\Users\Alice\My Project",
        r"C:\Program Files\Hermes",
        r"C:\Logs\hermes.log",
        r"C:\Backups\config.bak",
        r"\\server\share\My Folder",
    ],
)
def test_doctor_json_redacts_entire_text_field_for_absolute_path_prefix(path_text):
    value = f"operation failed near {path_text} and should be retried"

    result = cli_module._redact_doctor_json_paths({"message": value})["message"]

    assert result.startswith("[redacted-path-text:")
    assert result.endswith("]")
    assert path_text not in result
    assert "operation failed" not in result
    assert "retried" not in result


def test_doctor_json_redacts_entire_text_field_when_it_contains_multiple_paths():
    first_path = "/Users/Alice/My Project/config.yaml"
    second_path = r"C:\Backups\config.bak"
    value = f"copy {first_path} to {second_path} before retrying the operation"

    result = cli_module._redact_doctor_json_paths({"message": value})["message"]

    assert result.startswith("[redacted-path-text:")
    assert first_path not in result
    assert second_path not in result
    assert "before retrying" not in result


@pytest.mark.parametrize(
    "value",
    [
        "ordinary diagnostic text",
        "see https://example.com/health for details",
        "/health",
    ],
)
def test_doctor_json_keeps_non_local_path_text(value):
    assert cli_module._redact_doctor_json_paths({"message": value})["message"] == value


def test_doctor_json_path_text_summaries_are_stable_and_distinct():
    first = "failure at /Users/Alice/private/config.yaml"
    second = "failure at /Users/Bob/private/config.yaml"

    first_summary = cli_module._redact_doctor_json_paths({"message": first})["message"]

    assert first_summary == (
        f"[redacted-path-text:{hashlib.sha256(first.encode('utf-8')).hexdigest()[:12]}]"
    )
    assert first_summary == cli_module._redact_doctor_json_paths({"message": first})["message"]
    assert first_summary != cli_module._redact_doctor_json_paths({"message": second})["message"]


def test_module_doctor_json_reports_supported_hermes_and_clean_install_state(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9013\n", encoding="utf-8")
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    (hermes_dir / "config.yaml").write_text(
        "streaming:\n  enabled: true\n  transport: edit\n",
        encoding="utf-8",
    )

    result = run_cli(
        "doctor",
        "--config",
        str(config_path),
        "--hermes-dir",
        str(hermes_dir),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "warning"
    assert report["hermes"]["checked"] is True
    assert report["hermes"]["status"] == "supported"
    assert report["hermes"]["version"] == "v2026.4.23"
    assert report["hermes"]["hook_strategy"] == "legacy_gateway_run"
    assert report["hermes"]["compatibility"] == "partial"
    assert report["install_state"]["checked"] is True
    assert report["install_state"]["status"] == "clean"
    assert report["streaming"]["status"] == "enabled"
    assert any(item["code"] == "hermes_compatibility_partial" for item in report["recommendations"])
    assert str(config_path) not in result.stdout
    assert str(hermes_dir) not in result.stdout


def test_module_doctor_json_is_read_only_and_has_stable_fingerprint(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9013\n", encoding="utf-8")
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    before = {
        path.relative_to(hermes_dir): path.read_bytes()
        for path in hermes_dir.rglob("*")
        if path.is_file()
    }

    first = run_cli(
        "doctor", "--config", str(config_path), "--hermes-dir", str(hermes_dir), "--json"
    )
    second = run_cli(
        "doctor", "--config", str(config_path), "--hermes-dir", str(hermes_dir), "--json"
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["fingerprint"] == json.loads(second.stdout)["fingerprint"]
    after = {
        path.relative_to(hermes_dir): path.read_bytes()
        for path in hermes_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_module_doctor_json_reports_runtime_import_failure(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9018\n", encoding="utf-8")
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    venv_bin = hermes_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    runtime_python = venv_bin / "python"
    runtime_python.write_text(
        """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  echo "No module named hermes_feishu_card" >&2
  exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)

    result = run_cli(
        "doctor",
        "--config",
        str(config_path),
        "--hermes-dir",
        str(hermes_dir),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "warning"
    assert report["runtime_import"]["checked"] is True
    assert report["runtime_import"]["status"] == "failed"
    assert report["runtime_import"]["python"] == "[redacted]"
    assert str(runtime_python) not in result.stdout
    assert "hook_runtime" in report["runtime_import"]["message"]
    assert any(item["code"] == "runtime_import_failed" for item in report["recommendations"])


def test_module_doctor_explain_reports_summary_and_next_steps(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9014\n", encoding="utf-8")

    result = run_cli(
        "doctor",
        "--config",
        str(config_path),
        "--hermes-dir",
        str(FIXTURE),
        "--explain",
    )

    assert result.returncode == 0, result.stderr
    assert "Doctor Summary" in result.stdout
    assert "Sidecar: 127.0.0.1:9014" in result.stdout
    assert "Hermes: supported" in result.stdout
    assert "Runtime import:" in result.stdout
    assert "Install state: clean" in result.stdout
    assert "Next steps" in result.stdout


@pytest.mark.parametrize(
    ("profile_id", "expected_bot_id"),
    [("default", "main-bot"), ("child", "child-bot")],
)
def test_doctor_explain_reports_profile_route_without_credentials(
    profile_id, expected_bot_id, tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """server:
  host: 127.0.0.1
  port: 8765
profiles:
  default:
    feishu:
      app_id: cli-default-app
      app_secret: cli-default-secret
    bots:
      default: main-bot
      items:
        main-bot:
          app_id: cli-main-bot-app
          app_secret: cli-main-bot-secret
  child:
    feishu:
      app_id: cli-child-app
      app_secret: cli-child-secret
    bots:
      default: child-bot
      items:
        child-bot:
          app_id: cli-child-bot-app
          app_secret: cli-child-bot-secret
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8765/events"
    )

    exit_code = main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--hermes-dir",
            str(FIXTURE),
            "--profile-id",
            profile_id,
            "--explain",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "Route Chain" in captured.out
    assert "identity_source: argument" in captured.out
    assert f"profile_id: {profile_id}" in captured.out
    assert "event_endpoint: http://127.0.0.1:8765/events" in captured.out
    assert f"config_profile: {profile_id}" in captured.out
    assert f"bot_id: {expected_bot_id}" in captured.out
    assert "route_reason: bots.default" in captured.out
    assert "profile_credentials_missing" not in captured.out
    assert "cli-child-app" not in captured.out
    assert "cli-child-secret" not in captured.out
    assert "cli-default-secret" not in captured.out


@pytest.mark.parametrize(
    ("hermes_args", "expected_hermes"),
    [(["--skip-hermes"], "Hermes: skipped"), ([], "Hermes: not_checked")],
)
def test_doctor_explain_reports_profile_route_before_hermes_detection(
    hermes_args, expected_hermes, tmp_path, capsys
):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        """server:
  host: 127.0.0.1
  port: 8765
profiles:
  child:
    feishu:
      app_id: child-app
      app_secret: child-secret
    bots:
      default: child-bot
      items:
        child-bot:
          app_id: child-bot-app
          app_secret: child-bot-secret
""",
        encoding="utf-8",
    )
    original_env = (
        "# doctor must stay read-only\n"
        "HERMES_FEISHU_CARD_EVENT_URL=http://127.0.0.1:8765/events\n"
    )
    env_path.write_text(original_env, encoding="utf-8")

    exit_code = main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--profile-id",
            "child",
            "--explain",
            *hermes_args,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert expected_hermes in captured.out
    assert "Route Chain" in captured.out
    assert "profile_id: child" in captured.out
    assert "event_endpoint: http://127.0.0.1:8765/events" in captured.out
    assert "config_profile: child" in captured.out
    assert "bot_id: child-bot" in captured.out
    assert "route_reason: bots.default" in captured.out
    assert "profile_credentials_missing" not in captured.out
    assert env_path.read_text(encoding="utf-8") == original_env


@pytest.mark.parametrize("secret_segment", ["SECRET_TOKEN", "oc_private", "ou_private"])
def test_doctor_explain_redacts_unreviewed_nested_event_path(
    secret_segment, tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """server:
  host: 127.0.0.1
  port: 8765
profiles:
  child:
    feishu:
      app_id: child-app
      app_secret: child-secret
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL",
        f"http://127.0.0.1:8765/private/{secret_segment}/events",
    )

    exit_code = main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--hermes-dir",
            str(FIXTURE),
            "--profile-id",
            "child",
            "--explain",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "event_endpoint: http://127.0.0.1:8765/[redacted-path]" in captured.out
    assert secret_segment not in captured.out
    assert "event_endpoint_mismatch" in captured.out


@pytest.mark.parametrize("hermes_mode", ["normal", "skip", "no_hermes"])
@pytest.mark.parametrize(
    ("event_path", "expected_output_path", "sensitive_value"),
    [
        ("/events", "/events", None),
        ("/private/SECRET_TOKEN/events", "/[redacted-path]", "SECRET_TOKEN"),
        ("/private/oc_SECRET_CHAT/events", "/[redacted-path]", "oc_SECRET_CHAT"),
        ("/private/ou_SECRET_USER/events", "/[redacted-path]", "ou_SECRET_USER"),
    ],
)
def test_doctor_json_sanitizes_only_endpoint_output_copy(
    hermes_mode,
    event_path,
    expected_output_path,
    sensitive_value,
    tmp_path,
    monkeypatch,
    capsys,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """server:
  host: 127.0.0.1
  port: 8765
profiles:
  child:
    feishu:
      app_id: child-app
      app_secret: child-secret
    bots:
      default: child-bot
      items:
        child-bot:
          app_id: child-bot-app
          app_secret: child-bot-secret
""",
        encoding="utf-8",
    )
    event_endpoint = f"http://127.0.0.1:8765{event_path}"
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", event_endpoint)
    captured_report = {}
    original_build = cli_module._build_doctor_report

    def capture_report(config_path, config, args):
        report = original_build(config_path, config, args)
        captured_report["report"] = report
        if isinstance(report, DiagnosticReport):
            captured_report["fingerprint"] = report.fingerprint
        return report

    monkeypatch.setattr(cli_module, "_build_doctor_report", capture_report)
    hermes_args = {
        "normal": ["--hermes-dir", str(FIXTURE)],
        "skip": ["--skip-hermes"],
        "no_hermes": [],
    }[hermes_mode]

    exit_code = main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--profile-id",
            "child",
            "--json",
            *hermes_args,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["routing"]["event_endpoint"] == (
        f"http://127.0.0.1:8765{expected_output_path}"
    )
    if sensitive_value is not None:
        assert sensitive_value not in captured.out

    report = captured_report["report"]
    raw_payload = report.to_dict() if isinstance(report, DiagnosticReport) else report
    raw_routing = raw_payload["routing"]
    assert raw_routing["event_endpoint"] == event_endpoint
    assert set(payload) == set(raw_payload)
    for section, value in raw_payload.items():
        if isinstance(value, dict):
            assert set(payload[section]) == set(value)

    if isinstance(report, DiagnosticReport):
        assert payload["fingerprint"] == captured_report["fingerprint"]
        assert report.fingerprint == captured_report["fingerprint"]
        finding_codes = {finding.code for finding in report.findings}
    else:
        finding_codes = {item["code"] for item in report["recommendations"]}
    if event_path == "/events":
        assert "event_endpoint_mismatch" not in finding_codes
    else:
        assert "event_endpoint_mismatch" in finding_codes


@pytest.mark.parametrize(
    "profile_id",
    ["../child", "child profile", "x" * 65],
)
def test_doctor_rejects_invalid_profile_id(profile_id, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 8765\n", encoding="utf-8")

    exit_code = main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--skip-hermes",
            "--profile-id",
            profile_id,
            "--explain",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "invalid profile id" in captured.out + captured.err


def test_module_doctor_explain_supports_hermes_015_without_v_prefix(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9015\n", encoding="utf-8")
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    (hermes_dir / "VERSION").write_text("0.15.1\n", encoding="utf-8")

    result = run_cli(
        "doctor",
        "--config",
        str(config_path),
        "--hermes-dir",
        str(hermes_dir),
        "--explain",
    )

    assert result.returncode == 0, result.stderr
    assert "Hermes: supported" in result.stdout
    assert "0.15.1" in result.stdout
    assert "gateway_run_013_plus" in result.stdout
    assert "unsupported" not in result.stdout


def test_module_doctor_reports_unsupported_hermes_detection(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9008\n", encoding="utf-8")
    hermes_dir = tmp_path / "not-hermes"
    hermes_dir.mkdir()

    result = run_cli("doctor", "--config", str(config_path), "--hermes-dir", str(hermes_dir))

    assert result.returncode != 0
    assert "hermes: unsupported" in result.stdout
    assert f"hermes_root: {hermes_dir}" in result.stdout
    assert "run_py_exists: no" in result.stdout
    assert "version_source: unknown" in result.stdout
    assert "version: unknown" in result.stdout
    assert "minimum_supported_version: v2026.4.23" in result.stdout
    assert "reason: gateway/run.py missing" in result.stdout


def test_supported_anchor_fallback_labels_source_stripped_version(tmp_path):
    hermes_dir = tmp_path / "source-stripped-hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    (hermes_dir / "VERSION").unlink(missing_ok=True)
    detection = cli_module.detect_hermes(hermes_dir)

    formatted = cli_module._format_hermes_detection(detection)

    assert detection.supported is True
    assert "version_source: gateway anchors" in formatted
    assert "version: unknown (source-stripped metadata)" in formatted


def test_module_doctor_suggests_hermes_cli_project_when_hermes_dir_is_wrong(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9008\n", encoding="utf-8")
    wrong_dir = tmp_path / "wrong-hermes"
    wrong_dir.mkdir()
    actual_dir = tmp_path / "actual-hermes"
    shutil.copytree(FIXTURE, actual_dir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hermes_bin = bin_dir / "hermes"
    hermes_bin.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-V\" ]; then\n"
        f"  printf 'Hermes Agent v0.17.0 (2026.6.19)\\nProject: {actual_dir}\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    hermes_bin.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = run_cli("doctor", "--config", str(config_path), "--hermes-dir", str(wrong_dir), "--explain")

    assert result.returncode != 0
    assert f"Hermes CLI reports project: {actual_dir}" in result.stdout
    assert f"Use --hermes-dir {actual_dir}" in result.stdout


def test_module_status_reports_success():
    result = run_cli("status")

    assert result.returncode == 0
    assert "status" in result.stdout.lower()
    assert "not implemented" not in result.stdout.lower()
    assert "running" in result.stdout.lower() or "stopped" in result.stdout.lower()


def test_module_doctor_requires_config_argument():
    result = run_cli("doctor", "--skip-hermes")

    assert result.returncode != 0
    assert "usage" in result.stderr.lower() or "error" in result.stderr.lower()


def test_module_requires_command_argument():
    result = run_cli()

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode != 0
    assert "usage" in combined_output or "error" in combined_output


def test_module_doctor_malformed_known_section_returns_nonzero_without_traceback(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("server: 1\n", encoding="utf-8")

    result = run_cli("doctor", "--config", str(config_path), "--skip-hermes")

    assert result.returncode != 0
    assert "error" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_module_doctor_invalid_port_returns_nonzero_without_traceback(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("server:\n  port: 65536\n", encoding="utf-8")

    result = run_cli("doctor", "--config", str(config_path), "--skip-hermes")

    assert result.returncode != 0
    assert "error" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_bots_list_prints_bot_metadata_without_secret(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
server:
  port: 9010
bots:
  default: default
  items:
    default:
      name: Default Bot
      app_id: cli-default-app
      app_secret: default-secret
    sales:
      name: Sales Bot
      app_id: cli-sales-app
      app_secret: sales-secret
""",
        encoding="utf-8",
    )

    exit_code = main(["bots", "list", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "default" in captured.out
    assert "Default Bot" in captured.out
    assert "cli-default-app" in captured.out
    assert "sales" in captured.out
    assert "Sales Bot" in captured.out
    assert "cli-sales-app" in captured.out
    assert "default-secret" not in captured.out
    assert "sales-secret" not in captured.out


def test_bots_add_creates_placeholder_in_config_path(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9011\n", encoding="utf-8")

    exit_code = main(["bots", "add", "sales", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "sales" in captured.out
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["bots"]["items"]["sales"] == {
        "name": "sales",
        "app_id": "",
        "app_secret": "",
        "base_url": "https://open.feishu.cn/open-apis",
        "timeout_seconds": 30,
    }


def test_bots_add_existing_bot_returns_nonzero_without_secret(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
bots:
  items:
    sales:
      name: Sales Bot
      app_id: cli-sales-app
      app_secret: sales-secret
""",
        encoding="utf-8",
    )

    exit_code = main(["bots", "add", "sales", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "exists" in captured.err.lower()
    assert "sales-secret" not in captured.err


def test_bots_add_default_rejects_implicit_legacy_default_without_secret(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
feishu:
  app_id: legacy-app
  app_secret: legacy-secret
""",
        encoding="utf-8",
    )

    exit_code = main(["bots", "add", "default", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "exists" in captured.err.lower()
    assert "legacy-secret" not in captured.err
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "bots" not in config or "default" not in config.get("bots", {}).get("items", {})


def test_bots_bind_chat_writes_yaml_binding(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
bots:
  items:
    sales:
      name: Sales Bot
      app_id: cli-sales-app
      app_secret: sales-secret
bindings:
  chats: {}
""",
        encoding="utf-8",
    )

    exit_code = main(
        ["bots", "bind-chat", "oc-chat-1", "sales", "--config", str(config_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "bound" in captured.out.lower()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["bindings"]["chats"]["oc-chat-1"] == "sales"


def test_bots_unbind_chat_removes_binding_and_succeeds_when_missing(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
bindings:
  chats:
    oc-chat-1: sales
""",
        encoding="utf-8",
    )

    first_exit_code = main(
        ["bots", "unbind-chat", "oc-chat-1", "--config", str(config_path)]
    )
    second_exit_code = main(
        ["bots", "unbind-chat", "oc-chat-1", "--config", str(config_path)]
    )

    captured = capsys.readouterr()
    assert first_exit_code == 0
    assert second_exit_code == 0
    assert "unbound" in captured.out.lower()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["bindings"]["chats"] == {}


def test_bots_bind_chat_unknown_bot_returns_nonzero_without_secret(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
bots:
  items:
    sales:
      name: Sales Bot
      app_id: cli-sales-app
      app_secret: sales-secret
""",
        encoding="utf-8",
    )

    exit_code = main(
        ["bots", "bind-chat", "oc-chat-1", "support", "--config", str(config_path)]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "unknown bot" in captured.err.lower()
    assert "traceback" not in captured.err.lower()
    assert "sales-secret" not in captured.err


def test_bots_test_selects_named_bot_and_chat_id(tmp_path, capsys, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
bots:
  default: default
  items:
    default:
      name: Default Bot
      app_id: cli-default-app
      app_secret: default-secret
    sales:
      name: Sales Bot
      app_id: cli-sales-app
      app_secret: sales-secret
""",
        encoding="utf-8",
    )
    calls = []

    async def fake_smoke(config, bot_id, chat_id):
        calls.append((config, bot_id, chat_id))
        return "om-message-1"

    monkeypatch.setattr("hermes_feishu_card.cli._smoke_feishu_card_with_bot", fake_smoke)

    exit_code = main(
        [
            "bots",
            "test",
            "sales",
            "--chat-id",
            "oc-chat-1",
            "--config",
            str(config_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls[0][1:] == ("sales", "oc-chat-1")
    assert "om-message-1" in captured.out
    assert "sales-secret" not in captured.out


def test_smoke_feishu_card_selects_profile_config(tmp_path, capsys, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
profiles:
  work:
    feishu:
      app_id: cli-work-app
      app_secret: work-secret
""",
        encoding="utf-8",
    )
    calls = []

    async def fake_smoke(config, chat_id):
        calls.append((config, chat_id))
        return "om-profile-smoke"

    monkeypatch.setattr("hermes_feishu_card.cli._smoke_feishu_card", fake_smoke)

    exit_code = main(
        [
            "smoke-feishu-card",
            "--profile-id",
            "work",
            "--config",
            str(config_path),
            "--chat-id",
            "oc-chat-1",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls[0][0]["feishu"]["app_id"] == "cli-work-app"
    assert calls[0][1] == "oc-chat-1"
    assert "om-profile-smoke" in captured.out
    assert "work-secret" not in captured.out


def test_bots_test_selects_profile_config(tmp_path, capsys, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
profiles:
  work:
    bots:
      default: sales
      items:
        sales:
          name: Sales Bot
          app_id: cli-work-sales
          app_secret: work-sales-secret
""",
        encoding="utf-8",
    )
    calls = []

    async def fake_smoke(config, bot_id, chat_id):
        calls.append((config, bot_id, chat_id))
        return "om-profile-bot-smoke"

    monkeypatch.setattr("hermes_feishu_card.cli._smoke_feishu_card_with_bot", fake_smoke)

    exit_code = main(
        [
            "bots",
            "test",
            "sales",
            "--profile-id",
            "work",
            "--chat-id",
            "oc-chat-1",
            "--config",
            str(config_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls[0][0]["bots"]["items"]["sales"]["app_id"] == "cli-work-sales"
    assert calls[0][1:] == ("sales", "oc-chat-1")
    assert "om-profile-bot-smoke" in captured.out
    assert "work-sales-secret" not in captured.out
