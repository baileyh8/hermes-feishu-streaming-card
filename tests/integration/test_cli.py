import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hermes_feishu_card.cli import main


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
CONFIG_ENV_VARS = {
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
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
    assert report["config"]["path"] == str(config_path)
    assert report["config"]["loaded"] is True
    assert report["config"]["server"] == {"host": "0.0.0.0", "port": 9012}
    assert report["sidecar"]["address"] == "0.0.0.0:9012"
    assert report["hermes"]["checked"] is False
    assert report["hermes"]["status"] == "skipped"
    assert report["install_state"]["status"] == "skipped"
    assert isinstance(report["recommendations"], list)


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
    assert "Install state: clean" in result.stdout
    assert "Next steps" in result.stdout


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
