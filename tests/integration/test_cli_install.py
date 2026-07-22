import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from hashlib import sha256
from pathlib import Path

import pytest

from hermes_feishu_card import __version__ as PACKAGE_VERSION
from hermes_feishu_card import cli
from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
BACKUP_NAME = "run.py.hermes_feishu_card.bak"
MANIFEST_NAME = ".hermes_feishu_card_manifest"


def run_cli(*args):
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def copy_hermes(tmp_path):
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    return hermes_dir


def run_py(hermes_dir):
    return hermes_dir / "gateway" / "run.py"


def backup_path(hermes_dir):
    return hermes_dir / "gateway" / BACKUP_NAME


def manifest_path(hermes_dir):
    return hermes_dir / MANIFEST_NAME


def phase_one_placeholder(content):
    current = patcher.apply_patch(content)
    return current.replace(
        (
            "        from hermes_feishu_card.hook_runtime "
            "import emit_from_hermes_locals as _hfc_emit\n"
            "        _hfc_emit(locals())\n"
        ),
        "        pass\n",
    )


def write_manifest(hermes_dir):
    manifest = {
        "run_py": "gateway/run.py",
        "patched_sha256": cli.file_sha256(run_py(hermes_dir)),
        "backup": f"gateway/{BACKUP_NAME}",
        "backup_sha256": cli.file_sha256(backup_path(hermes_dir)),
    }
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_phase_one_install_state(hermes_dir):
    original = run_py(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).write_text(original, encoding="utf-8")
    run_py(hermes_dir).write_text(phase_one_placeholder(original), encoding="utf-8")
    write_manifest(hermes_dir)
    return original


def test_install_patches_run_py_and_writes_backup_and_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "install ok" in result.stdout.lower()
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert "[hermes-feishu-card] hook failed" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_install_bootstraps_package_into_hermes_runtime_venv(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    venv_bin = hermes_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    runtime_python = venv_bin / "python"
    marker = tmp_path / "runtime-import-ok"
    runtime_log = tmp_path / "runtime-python.log"
    runtime_python.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {str(runtime_log)!r}
if [ "$1" = "-c" ]; then
  if [ -f {str(marker)!r} ]; then
    printf '%s\\n' '{{"version":"{PACKAGE_VERSION}","location":"/runtime/hermes_feishu_card/__init__.py"}}'
    exit 0
  fi
  echo "No module named hermes_feishu_card" >&2
  exit 1
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  touch {str(marker)!r}
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    monkeypatch.setenv("HFC_INSTALL_SPEC", "git+https://example.test/pkg.git@v3.6.2")

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    log = runtime_log.read_text(encoding="utf-8")
    assert "-m pip install --upgrade git+https://example.test/pkg.git@v3.6.2" in log
    assert "runtime package: installed into" in result.stdout
    assert "install ok" in result.stdout.lower()


def test_install_upgrades_importable_outdated_runtime_package(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    venv_bin = hermes_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    runtime_python = venv_bin / "python"
    upgraded = tmp_path / "runtime-upgraded"
    runtime_log = tmp_path / "runtime-python.log"
    runtime_python.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {str(runtime_log)!r}
if [ "$1" = "-c" ]; then
  if [ -f {str(upgraded)!r} ]; then
    printf '%s\\n' '{{"version":"{PACKAGE_VERSION}","location":"/runtime/hermes_feishu_card/__init__.py"}}'
  else
    printf '%s\\n' '{{"version":"3.6.3","location":"/runtime/hermes_feishu_card/__init__.py"}}'
  fi
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  touch {str(upgraded)!r}
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    monkeypatch.setenv(
        "HFC_INSTALL_SPEC", f"git+https://example.test/pkg.git@v{PACKAGE_VERSION}"
    )

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert upgraded.exists()
    assert (
        f"-m pip install --upgrade git+https://example.test/pkg.git@v{PACKAGE_VERSION}"
        in runtime_log.read_text(encoding="utf-8")
    )
    assert f"runtime package: upgraded 3.6.3 -> {PACKAGE_VERSION}" in result.stdout


def test_install_skips_matching_runtime_package(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    venv_bin = hermes_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    runtime_python = venv_bin / "python"
    runtime_log = tmp_path / "runtime-python.log"
    runtime_python.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {str(runtime_log)!r}
if [ "$1" = "-c" ]; then
  printf '%s\\n' '{{"version":"{PACKAGE_VERSION}","location":"/runtime/hermes_feishu_card/__init__.py"}}'
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  echo "unexpected install" >&2
  exit 90
fi
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    monkeypatch.delenv("HFC_INSTALL_SPEC", raising=False)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "-m pip install" not in runtime_log.read_text(encoding="utf-8")
    assert f"runtime package: {PACKAGE_VERSION} import ok" in result.stdout


def test_install_upgrades_incompatible_hermes_feishu_sdk(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    adapter = hermes_dir / "plugins" / "platforms" / "feishu" / "adapter.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(
        "FeishuWSClient(app_id='test', extra_ua_tags=['channel'])\n",
        encoding="utf-8",
    )
    venv_bin = hermes_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    runtime_python = venv_bin / "python"
    upgraded = tmp_path / "feishu-sdk-upgraded"
    runtime_log = tmp_path / "runtime-python.log"
    runtime_python.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {str(runtime_log)!r}
if [ "$1" = "-c" ]; then
  if [[ "$2" == *"lark_oapi.ws"* ]]; then
    if [ -f {str(upgraded)!r} ]; then
      printf '%s\\n' '{{"version":"1.6.8","supports_extra_ua_tags":true}}'
    else
      printf '%s\\n' '{{"version":"1.5.3","supports_extra_ua_tags":false}}'
    fi
  else
    printf '%s\\n' '{{"version":"{PACKAGE_VERSION}","location":"/runtime/hermes_feishu_card/__init__.py"}}'
  fi
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  if [[ "$*" == *"lark-oapi==1.6.8"* ]]; then
    touch {str(upgraded)!r}
  fi
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    monkeypatch.delenv("HFC_INSTALL_SPEC", raising=False)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert upgraded.exists()
    log = runtime_log.read_text(encoding="utf-8")
    assert "-m pip install --upgrade lark-oapi==1.6.8" in log
    assert "feishu sdk: upgraded 1.5.3 -> 1.6.8" in result.stdout
    assert "install ok" in result.stdout.lower()


def test_install_does_not_accept_project_cwd_runtime_import_false_positive(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    venv_bin = hermes_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    runtime_python = venv_bin / "python"
    marker = tmp_path / "runtime-import-ok"
    runtime_log = tmp_path / "runtime-python.log"
    runtime_python.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'cwd=%s args=%s\\n' "$PWD" "$*" >> {str(runtime_log)!r}
if [ "$1" = "-c" ]; then
  if [ -f {str(marker)!r} ]; then
    printf '%s\\n' '{{"version":"{PACKAGE_VERSION}","location":"/runtime/hermes_feishu_card/__init__.py"}}'
    exit 0
  fi
  if [ "$PWD" != {str(hermes_dir)!r} ]; then
    echo "project-cwd-hook-runtime"
    exit 0
  fi
  echo "No module named hermes_feishu_card" >&2
  exit 1
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  touch {str(marker)!r}
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    monkeypatch.setenv("HFC_INSTALL_SPEC", "git+https://example.test/pkg.git@v3.8.0")

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert marker.exists()
    assert "runtime package: installed into" in result.stdout
    assert f"cwd={hermes_dir}" in runtime_log.read_text(encoding="utf-8")
    assert "install ok" in result.stdout.lower()


def test_setup_creates_config_installs_hook_and_starts_sidecar(tmp_path, monkeypatch, capsys):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    started = {}
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")

    def fake_start_sidecar(path, config):
        started["path"] = Path(path)
        started["config"] = config
        return "started"

    def fake_status_sidecar(config):
        return {
            "running": True,
            "pid": 12345,
            "health": {"active_sessions": 0, "metrics": {}},
            "pid_running": True,
        }

    monkeypatch.setattr(cli, "start_sidecar", fake_start_sidecar)
    monkeypatch.setattr(cli, "status_sidecar", fake_status_sidecar)

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert config_path.exists()
    assert "setup ok" in captured.out
    assert "config: created" in captured.out
    assert "install ok" in captured.out
    assert "start ok" in captured.out
    assert "status: running" in captured.out
    assert started["path"] == config_path
    assert started["config"]["feishu"]["app_id"] == "cli_setup_test"
    assert started["config"]["feishu"]["app_secret"] == "setup-secret"
    assert started["config"]["server"]["port"] == 8765
    assert f"HERMES_DIR={hermes_dir}" in (config_path.parent / ".env").read_text(
        encoding="utf-8"
    )
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )


def test_setup_updates_selected_profile_env_and_reports_route_chain(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "selected.env"
    config_path.write_text(
        """server:
  host: 127.0.0.1
  port: 8765
profiles:
  default:
    feishu:
      app_id: default-app
      app_secret: default-secret
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
    env_path.write_text(
        "# preserve me\n"
        "UNKNOWN_KEY=keep\n"
        "HERMES_FEISHU_CARD_PROFILE_ID=from-file\n"
        "HERMES_FEISHU_CARD_EVENT_URL=http://127.0.0.1:9999/events\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "from-process")
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8888/events"
    )
    monkeypatch.setattr(cli, "_run_install", lambda args: 0)

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--env-file",
            str(env_path),
            "--profile-id",
            "child",
            "--event-url",
            "http://127.0.0.1:8765/events",
            "--yes",
            "--skip-start",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert env_path.read_text(encoding="utf-8") == (
        "# preserve me\n"
        "UNKNOWN_KEY=keep\n"
        "HERMES_FEISHU_CARD_PROFILE_ID=child\n"
        "HERMES_FEISHU_CARD_EVENT_URL=http://127.0.0.1:8765/events\n"
        f"HERMES_DIR={hermes_dir}\n"
    )
    assert "Route Chain" in captured.out
    assert "profile_id: child" in captured.out
    assert "event_endpoint: http://127.0.0.1:8765/events" in captured.out
    assert "config_profile: child" in captured.out
    assert "bot_id: child-bot" in captured.out
    assert "route_reason: bots.default" in captured.out
    assert "profile_credentials_missing" not in captured.out
    assert "child-secret" not in captured.out


def test_setup_starts_sidecar_with_selected_env_file(tmp_path, monkeypatch, capsys):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "selected.env"
    config_path.write_text("", encoding="utf-8")
    env_path.write_text(
        "FEISHU_APP_ID=setup-app\nFEISHU_APP_SECRET=setup-secret\n",
        encoding="utf-8",
    )
    started = {}
    monkeypatch.setattr(cli, "_run_install", lambda args: 0)
    monkeypatch.setattr(
        cli,
        "start_sidecar",
        lambda path, config, **kwargs: started.update(path=Path(path), kwargs=kwargs) or "started",
    )
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda config: {"running": True, "pid": 123, "health": {"metrics": {}}},
    )

    exit_code = cli.main(
        [
            "setup", "--hermes-dir", str(hermes_dir), "--config", str(config_path),
            "--env-file", str(env_path), "--yes",
        ]
    )

    assert exit_code == 0, capsys.readouterr().err
    assert started == {"path": config_path, "kwargs": {"env_file": env_path}}
    assert f"HERMES_DIR={hermes_dir}" in env_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "event_url",
    [
        "ftp://127.0.0.1:8765/events",
        "http://user:secret@127.0.0.1:8765/events",
        "http://127.0.0.1:8765/events?token=secret",
        "http://127.0.0.1:8765/events#fragment",
        "http://example.com:8765/events",
        "http://192.168.1.20:8765/events",
        "http://127.0.0.1:8765/health",
    ],
)
def test_setup_rejects_invalid_event_url_without_writing_env(
    event_url, tmp_path, capsys
):
    env_path = tmp_path / ".env"

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(tmp_path / "hermes"),
            "--config",
            str(tmp_path / "config.yaml"),
            "--env-file",
            str(env_path),
            "--profile-id",
            "default",
            "--event-url",
            event_url,
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "invalid event URL" in captured.err
    assert not env_path.exists()


@pytest.mark.parametrize(
    ("event_url", "normalized"),
    [
        ("http://localhost:8765/events", "http://localhost:8765/events"),
        ("http://127.0.0.2:8765/events", "http://127.0.0.2:8765/events"),
        ("http://[::1]:8765/events", "http://[::1]:8765/events"),
        (
            "https://host.docker.internal/events",
            "https://host.docker.internal/events",
        ),
        ("http://hfc-sidecar:8765/api/events", "http://hfc-sidecar:8765/api/events"),
    ],
)
def test_event_url_accepts_supported_sidecar_hosts(event_url, normalized):
    assert cli._validate_event_url(event_url) == normalized


def test_setup_warns_when_hermes_streaming_appears_disabled(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    (hermes_dir / "config.yaml").write_text(
        "streaming:\n  enabled: false\n  transport: edit\n", encoding="utf-8"
    )
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args: "started")
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda _config: {
            "running": True,
            "pid": 12345,
            "health": {"active_sessions": 0, "metrics": {}},
            "pid_running": True,
        },
    )

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "warning: Hermes Gateway streaming appears disabled for Feishu" in captured.out
    assert "streaming.enabled: true" in captured.out
    assert "streaming.transport: edit" in captured.out
    assert "thinking.delta" in captured.out
    assert "answer.delta" in captured.out
    assert "setup ok" in captured.out


def test_setup_warns_when_feishu_streaming_override_is_disabled(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    (hermes_dir / "config.yaml").write_text(
        (
            "streaming:\n"
            "  enabled: true\n"
            "  transport: edit\n"
            "display:\n"
            "  platforms:\n"
            "    feishu:\n"
            "      streaming: false\n"
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args: "started")
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda _config: {
            "running": True,
            "pid": 12345,
            "health": {"active_sessions": 0, "metrics": {}},
            "pid_running": True,
        },
    )

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "display.platforms.feishu.streaming: true" in captured.out
    assert "setup ok" in captured.out


def test_setup_accepts_minimal_streaming_config_without_reasoning_display_warning(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    (hermes_dir / "config.yaml").write_text(
        "streaming:\n  enabled: true\n  transport: edit\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args: "started")
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda _config: {
            "running": True,
            "pid": 12345,
            "health": {"active_sessions": 0, "metrics": {}},
            "pid_running": True,
        },
    )

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "Hermes Gateway streaming appears disabled for Feishu" not in captured.out
    assert "Hermes reasoning display appears disabled for Feishu" not in captured.out
    assert "show_reasoning" not in captured.out


def test_doctor_reads_user_level_hermes_config(tmp_path, monkeypatch, capsys):
    hermes_dir = copy_hermes(tmp_path)
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / "config.yaml").write_text(
        "streaming:\n  enabled: true\n  transport: edit\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))

    exit_code = cli.main(
        [
            "doctor",
            "--config",
            "config.yaml.example",
            "--hermes-dir",
            str(hermes_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "hermes: supported" in captured.out
    assert "Hermes Gateway streaming config was not detected" not in captured.out
    assert "show_reasoning" not in captured.out


def test_setup_requires_feishu_credentials_before_installing_hook(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    started = False
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    def fake_start_sidecar(*_args):
        nonlocal started
        started = True
        return "started"

    monkeypatch.setattr(cli, "start_sidecar", fake_start_sidecar)

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "feishu credentials are required" in captured.err.lower()
    assert "FEISHU_APP_ID" in captured.err
    assert config_path.exists()
    assert not started
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" not in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert not manifest_path(hermes_dir).exists()


def test_setup_fail_closed_for_unsupported_hermes(tmp_path, monkeypatch, capsys):
    hermes_dir = tmp_path / "not-hermes"
    hermes_dir.mkdir()
    config_path = tmp_path / "feishu-card.yaml"
    started = False
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")

    def fake_start_sidecar(*_args):
        nonlocal started
        started = True
        return "started"

    monkeypatch.setattr(cli, "start_sidecar", fake_start_sidecar)

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "hermes: unsupported" in captured.err
    assert "gateway/run.py missing" in captured.err
    assert not started
    assert config_path.exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_upgrades_phase_one_placeholder_install(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    write_phase_one_install_state(hermes_dir)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    assert "emit_from_hermes_locals" in patched
    assert "except Exception as _hfc_exc:" in patched
    assert "[hermes-feishu-card] hook failed" in patched
    assert "        pass\n    except Exception:" not in patched
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_install_upgrades_owned_callback_blocks_from_previous_version(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr

    old_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    old_patched = old_patched.replace(
        "if _hfc_emit_threadsafe({",
        "_hfc_emit_threadsafe({",
    )
    old_patched = old_patched.replace(
        '}, event_name="answer.delta"):\n                    return\n',
        '}, event_name="answer.delta")\n',
    )
    old_patched = old_patched.replace(
        '}, event_name="tool.updated"):\n                    return\n',
        '}, event_name="tool.updated")\n',
    )
    old_patched = old_patched.replace(
        '}, event_name="thinking.delta"):\n                    return\n',
        '}, event_name="thinking.delta")\n',
    )
    run_py(hermes_dir).write_text(old_patched, encoding="utf-8")
    write_manifest(hermes_dir)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    upgraded = run_py(hermes_dir).read_text(encoding="utf-8")
    assert '}, event_name="answer.delta"):\n                    return\n' in upgraded
    assert '}, event_name="thinking.delta"):\n                    return\n' in upgraded
    assert patcher.remove_patch(upgraded) == backup_path(hermes_dir).read_text(
        encoding="utf-8"
    )


def test_install_and_restore_013_plus_fixture(tmp_path):
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "fixtures" / "hermes_0_13_plus",
        hermes_dir,
    )
    original = (hermes_dir / "gateway" / "run.py").read_text(encoding="utf-8")

    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    patched = (hermes_dir / "gateway" / "run.py").read_text(encoding="utf-8")
    assert "HERMES_FEISHU_CARD_STRATEGY gateway_run_013_plus" in patched
    assert patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN in patched
    assert patched.index(patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN) < patched.index(
        "watchers = process_registry.pending_watchers"
    )

    assert cli.main(["restore", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    restored = (hermes_dir / "gateway" / "run.py").read_text(encoding="utf-8")
    assert restored == original


def test_install_and_restore_latest_layout_patches_scheduler_cron(tmp_path):
    hermes_dir = tmp_path / "hermes"
    gateway_dir = hermes_dir / "gateway"
    cron_dir = hermes_dir / "cron"
    gateway_dir.mkdir(parents=True)
    cron_dir.mkdir(exist_ok=True)
    (hermes_dir / "VERSION").write_text("v0.13.0\n", encoding="utf-8")
    run_original = '''
class GatewayRunner:
    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):
        response = "ok"
        agent_result = {"model": "m"}
        _response_time = 1.0
        await self.hooks.emit("agent:end", {"response": response})
        return response

    async def _run_agent(self, source, event_message_id=None):
        _loop_for_step = None
        def _run_still_current():
            return True
        def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):
            return None
        def _stream_delta_cb(text: str) -> None:
            return None
        def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:
            return None
        return {}

def _reply_anchor_for_event(event):
    return getattr(event, "reply_to_message_id", None)

def _deliver_media_from_response(response):
    extract_media(response)
'''
    cron_original = '''
def _deliver_result(job: dict, content: str, adapters=None, loop=None):
    return None
'''
    (gateway_dir / "run.py").write_text(run_original, encoding="utf-8")
    (cron_dir / "scheduler.py").write_text(cron_original, encoding="utf-8")

    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    patched_run = (gateway_dir / "run.py").read_text(encoding="utf-8")
    patched_cron = (cron_dir / "scheduler.py").read_text(encoding="utf-8")
    manifest = json.loads((hermes_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in patched_run
    assert "HERMES_FEISHU_CARD_CRON_PATCH_BEGIN" in patched_cron
    assert manifest["cron_py"] == "cron/scheduler.py"
    assert (cron_dir / "scheduler.py.hermes_feishu_card.bak").exists()

    assert cli.main(["restore", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    assert (gateway_dir / "run.py").read_text(encoding="utf-8") == run_original
    assert (cron_dir / "scheduler.py").read_text(encoding="utf-8") == cron_original
    assert not (cron_dir / "scheduler.py.hermes_feishu_card.bak").exists()


def test_repeat_install_ignores_unchanged_optional_cron_evidence(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    cron_dir = hermes_dir / "cron"
    cron_dir.mkdir(exist_ok=True)
    (cron_dir / "scheduler.py").write_text("def unrelated():\n    return None\n", encoding="utf-8")

    first = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    second = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr


def test_restore_accepts_phase_one_placeholder_install(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = write_phase_one_install_state(hermes_dir)

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_restore_preserves_crlf_run_py_bytes(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original_lf = run_py(hermes_dir).read_text(encoding="utf-8")
    original_crlf_bytes = original_lf.replace("\n", "\r\n").encode("utf-8")
    run_py(hermes_dir).write_bytes(original_crlf_bytes)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    assert b"\r\n" in run_py(hermes_dir).read_bytes()
    assert backup_path(hermes_dir).read_bytes() == original_crlf_bytes

    restore_result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert restore_result.returncode == 0, restore_result.stderr
    assert run_py(hermes_dir).read_bytes() == original_crlf_bytes


def test_restore_restores_backup_to_original_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "restore ok" in result.stdout.lower()
    restored = run_py(hermes_dir).read_text(encoding="utf-8")
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" not in restored
    assert restored == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_uninstall_restores_installed_fixture(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr

    result = run_cli("uninstall", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "uninstall ok" in result.stdout.lower()
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_unsupported_hermes_dir_returns_nonzero(tmp_path):
    hermes_dir = tmp_path / "unsupported"
    hermes_dir.mkdir()

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "hermes: unsupported" in result.stderr
    assert f"hermes_root: {hermes_dir}" in result.stderr
    assert "run_py_exists: no" in result.stderr
    assert "version_source: unknown" in result.stderr
    assert "version: unknown" in result.stderr
    assert "minimum_supported_version: v2026.4.23" in result.stderr
    assert "reason: gateway/run.py missing" in result.stderr
    assert "gateway/run.py missing" in result.stderr
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_failure_restores_run_py_and_removes_manifest_and_backup(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    def fail_manifest(*_args):
        raise OSError("manifest unavailable")

    monkeypatch.setattr(cli, "_write_manifest", fail_manifest)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    assert current == original
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" not in current
    assert not manifest_path(hermes_dir).exists()
    assert not backup_path(hermes_dir).exists()


def test_restore_refuses_to_overwrite_user_edited_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
        encoding="utf-8",
    )
    edited = run_py(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "run.py changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_reinstall_refuses_to_bless_user_edited_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
        encoding="utf-8",
    )
    edited = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    restore = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode != 0
    assert "run.py changed since install" in reinstall.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert restore.returncode != 0
    assert "run.py changed since install" in restore.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited


def test_reinstall_after_hermes_upgrade_refuses_changed_stale_state(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    upgraded = (
        "class GatewayRunner:\n"
        "    async def _handle_message_with_agent(self, event, source):\n"
        "        response = await self._run_agent(event, source)\n"
        "        _response_time = 0.2\n"
        "        agent_result = {'input_tokens': 1, 'output_tokens': 1}\n"
        "        await self.hooks.emit('agent:end', {'response': response})\n"
        "        return response\n"
        "    async def _run_agent(self, event, source):\n"
        "        return 'upgraded answer'\n"
    )
    (hermes_dir / "VERSION").write_text("v2026.7.7.2\n", encoding="utf-8")
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    run_py(hermes_dir).write_text(upgraded, encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode != 0
    assert "run.py changed since install" in reinstall.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == upgraded
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_repair_refuses_changed_stale_state_after_hermes_upgrade(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    upgraded = patcher.remove_patch(
        run_py(hermes_dir).read_text(encoding="utf-8")
    ) + "\n# upstream Hermes changed this file during upgrade\n"
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    run_py(hermes_dir).write_text(upgraded, encoding="utf-8")

    result = run_cli("repair", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "run.py changed since install" in result.stderr
    assert "--accept-hermes-upgrade" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == upgraded
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_repair_accepts_explicit_changed_state_after_hermes_upgrade(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    upgraded = patcher.remove_patch(
        run_py(hermes_dir).read_text(encoding="utf-8")
    ) + "\n# upstream Hermes changed this file during upgrade\n"
    run_py(hermes_dir).write_text(upgraded, encoding="utf-8")

    result = run_cli(
        "repair",
        "--hermes-dir",
        str(hermes_dir),
        "--accept-hermes-upgrade",
        "--yes",
    )

    assert result.returncode == 0, result.stderr
    assert "install state: cleared stale unpatched state" in result.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == upgraded
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_accepts_explicit_changed_state_after_hermes_upgrade(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    upgraded = patcher.remove_patch(
        run_py(hermes_dir).read_text(encoding="utf-8")
    ) + "\n# upstream Hermes changed this file during upgrade\n"
    run_py(hermes_dir).write_text(upgraded, encoding="utf-8")

    result = run_cli(
        "install",
        "--hermes-dir",
        str(hermes_dir),
        "--accept-hermes-upgrade",
        "--yes",
    )

    assert result.returncode == 0, result.stderr
    assert "install state: cleared stale unpatched state" in result.stdout
    assert "install ok" in result.stdout.lower()
    assert "gateway.restart_required: hermes gateway start" in result.stdout
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == upgraded
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    assert patcher.remove_patch(current) == upgraded
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    assert manifest["backup_sha256"] == sha256(upgraded.encode("utf-8")).hexdigest()


def _write_lifecycle_config(tmp_path, hermes_dir):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 19015\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"HERMES_DIR={hermes_dir}\n",
        encoding="utf-8",
    )
    return config_path


def _simulate_hermes_upgrade(hermes_dir):
    upgraded = patcher.remove_patch(
        run_py(hermes_dir).read_text(encoding="utf-8")
    ) + "\n# upstream Hermes changed this file during upgrade\n"
    run_py(hermes_dir).write_text(upgraded, encoding="utf-8")
    return upgraded


def test_status_detects_missing_hook_after_hermes_upgrade(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    config_path = _write_lifecycle_config(tmp_path, hermes_dir)
    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    upgraded = _simulate_hermes_upgrade(hermes_dir)

    result = run_cli("status", "--config", str(config_path))

    assert result.returncode != 0
    assert "hook.status: upgrade_repair_required" in result.stdout
    assert "--accept-hermes-upgrade --yes" in result.stdout
    assert "hermes gateway start" in result.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == upgraded

    repair = run_cli(
        "install",
        "--hermes-dir",
        str(hermes_dir),
        "--accept-hermes-upgrade",
        "--yes",
    )
    assert repair.returncode == 0, repair.stderr
    assert "gateway.restart_required: hermes gateway start" in repair.stdout

    repaired_status = run_cli("status", "--config", str(config_path))
    assert repaired_status.returncode == 0, repaired_status.stderr
    assert "hook.status: installed" in repaired_status.stdout


def test_start_refuses_missing_hook_after_hermes_upgrade(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    config_path = _write_lifecycle_config(tmp_path, hermes_dir)
    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    upgraded = _simulate_hermes_upgrade(hermes_dir)

    result = run_cli("start", "--config", str(config_path))

    assert result.returncode != 0
    assert "hook.status: upgrade_repair_required" in result.stderr
    assert "--accept-hermes-upgrade --yes" in result.stderr
    assert "hermes gateway start" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == upgraded


def test_status_reports_installed_hook(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    config_path = _write_lifecycle_config(tmp_path, hermes_dir)
    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr

    result = run_cli("status", "--config", str(config_path))

    assert result.returncode == 0, result.stderr
    assert "hook.status: installed" in result.stdout


def test_status_does_not_offer_upgrade_acceptance_for_user_edited_patch(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    config_path = _write_lifecycle_config(tmp_path, hermes_dir)
    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    edited = run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n"
    run_py(hermes_dir).write_text(edited, encoding="utf-8")

    result = run_cli("status", "--config", str(config_path))

    assert result.returncode != 0
    assert "hook.status: manual_review_required" in result.stdout
    assert "--accept-hermes-upgrade" not in result.stdout
    assert "doctor" in result.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited


def test_doctor_json_reports_changed_installed_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9015\n", encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
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
    assert report["install_state"]["checked"] is True
    assert report["install_state"]["status"] == "changed"
    assert report["install_state"]["manual_action_required"] is True
    assert "run.py changed since install" in report["install_state"]["message"]
    assert any(
        item["code"] == "install_state_changed"
        for item in report["recommendations"]
    )


def test_doctor_json_reports_repairable_missing_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9016\n", encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()

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
    assert report["install_state"]["status"] == "incomplete"
    assert report["install_state"]["automatic_repair_available"] is True
    assert "manifest missing" in report["install_state"]["message"]


def test_restore_refuses_changed_backup_with_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    changed_backup = backup_path(hermes_dir).read_text(encoding="utf-8").replace(
        "agent:end", "agent:changed", 1
    )
    assert changed_backup != backup_path(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).write_text(changed_backup, encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "backup changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == changed_backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_reinstall_refuses_changed_backup_with_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    changed_backup = backup_path(hermes_dir).read_text(encoding="utf-8").replace(
        "agent:end", "agent:changed", 1
    )
    assert changed_backup != backup_path(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).write_text(changed_backup, encoding="utf-8")

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "backup changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == changed_backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_restore_refuses_patched_backup_with_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).write_text(patched, encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "backup changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == patched
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_restore_refuses_symlinked_run_py_with_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    symlink_target = hermes_dir / "gateway" / "run-target.py"
    symlink_target.write_text(patched, encoding="utf-8")
    run_py(hermes_dir).unlink()
    run_py(hermes_dir).symlink_to(symlink_target)

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert run_py(hermes_dir).is_symlink()
    assert symlink_target.read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_reinstall_refuses_patched_backup_with_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).write_text(patched, encoding="utf-8")

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "backup changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == patched
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_restore_without_backup_refuses_symlinked_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).unlink()
    symlink_target = hermes_dir / "gateway" / "run-target.py"
    symlink_target.write_text(patched, encoding="utf-8")
    run_py(hermes_dir).unlink()
    run_py(hermes_dir).symlink_to(symlink_target)

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert run_py(hermes_dir).is_symlink()
    assert symlink_target.read_text(encoding="utf-8") == patched
    assert not backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest


def test_restore_refuses_manifest_missing_backup_sha256(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest.pop("backup_sha256", None)
    manifest_text = json.dumps(manifest, sort_keys=True) + "\n"
    manifest_path(hermes_dir).write_text(manifest_text, encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "manifest missing backup sha256" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == manifest_text


def test_reinstall_refuses_manifest_missing_backup_sha256(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest.pop("backup_sha256", None)
    manifest_text = json.dumps(manifest, sort_keys=True) + "\n"
    manifest_path(hermes_dir).write_text(manifest_text, encoding="utf-8")

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "manifest missing backup sha256" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == backup
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == manifest_text


def test_reinstall_without_manifest_refuses_user_edited_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
        encoding="utf-8",
    )
    edited = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode != 0
    assert "install state incomplete" in reinstall.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert not manifest_path(hermes_dir).exists()


def test_reinstall_without_manifest_auto_repairs_unedited_patched_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    patched = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode == 0, reinstall.stderr
    assert "manifest: rebuilt" in reinstall.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert manifest_path(hermes_dir).exists()


def test_reinstall_without_backup_refuses_user_edited_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    backup_path(hermes_dir).unlink()
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
        encoding="utf-8",
    )
    edited = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode != 0
    assert "run.py changed since install" in reinstall.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest
    assert not backup_path(hermes_dir).exists()


def test_repair_rebuilds_missing_manifest_for_owned_patch(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    manifest_path(hermes_dir).unlink()

    result = run_cli("repair", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "repair ok" in result.stdout.lower()
    assert "manifest: rebuilt" in result.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == backup
    assert manifest_path(hermes_dir).exists()
    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert reinstall.returncode == 0, reinstall.stderr


def test_repair_recreates_missing_backup_from_owned_patch(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    expected_backup = patcher.remove_patch(patched)
    backup_path(hermes_dir).unlink()

    result = run_cli("repair", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "repair ok" in result.stdout.lower()
    assert "backup: recreated" in result.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == expected_backup
    assert manifest_path(hermes_dir).exists()
    restore = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")
    assert restore.returncode == 0, restore.stderr


def test_repair_refuses_user_edited_installed_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    edited = run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n"
    run_py(hermes_dir).write_text(edited, encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("repair", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "run.py changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup


def test_setup_repair_rebuilds_missing_manifest_before_install(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_repair")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-repair-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args: "started")
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda _config: {
            "running": True,
            "pid": 12345,
            "health": {"active_sessions": 0, "metrics": {}},
            "pid_running": True,
        },
    )

    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest_path(hermes_dir).unlink()

    exit_code = cli.main(
        [
            "setup",
            "--repair",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "repair ok" in captured.out
    assert "setup ok" in captured.out
    assert manifest_path(hermes_dir).exists()


def test_setup_auto_repairs_issue_82_corrupt_completion_marker(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_auto_repair")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-auto-repair-secret")
    started = []
    monkeypatch.setattr(
        cli,
        "start_sidecar",
        lambda *_args: started.append(True) or "started",
    )
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda _config: {
            "running": True,
            "pid": 12345,
            "health": {"active_sessions": 0, "metrics": {}},
            "pid_running": True,
        },
    )
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    corrupt = "".join(
        line
        for line in current.splitlines(keepends=True)
        if "HERMES_FEISHU_CARD_COMPLETE_PATCH_END" not in line
    )
    run_py(hermes_dir).write_text(corrupt, encoding="utf-8")

    exit_code = cli.main(
        [
            "setup",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "run.py: restored verified backup" in captured.out
    assert "run.py: reapplied current hook" in captured.out
    assert "setup ok" in captured.out
    assert started == [True]
    assert "HERMES_FEISHU_CARD_COMPLETE_PATCH_END" in run_py(
        hermes_dir
    ).read_text(encoding="utf-8")

    doctor_code = cli.main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--hermes-dir",
            str(hermes_dir),
            "--json",
        ]
    )
    doctor = json.loads(capsys.readouterr().out)
    assert doctor_code == 0
    assert doctor["install_state"]["status"] == "installed"


def test_install_no_repair_refuses_repairable_corrupt_state(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    assert run_cli("install", "--hermes-dir", str(hermes_dir), "--yes").returncode == 0
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    corrupt = current.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    run_py(hermes_dir).write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = run_cli(
        "install",
        "--no-repair",
        "--hermes-dir",
        str(hermes_dir),
        "--yes",
    )

    assert result.returncode != 0
    assert run_py(hermes_dir).read_text(encoding="utf-8") == corrupt
    assert not list(run_py(hermes_dir).parent.glob("run.py.hfc-corrupt-*"))


def test_setup_no_repair_leaves_repairable_state_untouched(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_no_repair")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-no-repair-secret")
    started = []
    monkeypatch.setattr(
        cli,
        "start_sidecar",
        lambda *_args: started.append(True) or "started",
    )
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    corrupt = current.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    run_py(hermes_dir).write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    exit_code = cli.main(
        [
            "setup",
            "--repair",
            "--no-repair",
            "--hermes-dir",
            str(hermes_dir),
            "--config",
            str(config_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert started == []
    assert run_py(hermes_dir).read_text(encoding="utf-8") == corrupt
    assert "run.py: restored verified backup" not in captured.out
    assert not list(run_py(hermes_dir).parent.glob("run.py.hfc-corrupt-*"))


def test_doctor_does_not_execute_repairable_plan(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    assert run_cli("install", "--hermes-dir", str(hermes_dir), "--yes").returncode == 0
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    corrupt = current.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    run_py(hermes_dir).write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = run_cli(
        "doctor",
        "--config",
        "config.yaml.example",
        "--hermes-dir",
        str(hermes_dir),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == corrupt
    assert not list(run_py(hermes_dir).parent.glob("run.py.hfc-corrupt-*"))


def test_reinstall_without_state_auto_repairs_owned_patch_in_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    backup_path(hermes_dir).unlink()
    manifest_path(hermes_dir).unlink()
    patched = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode == 0, reinstall.stderr
    assert "backup: recreated" in reinstall.stdout
    assert "manifest: rebuilt" in reinstall.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_existing_manifest_survives_manifest_rewrite_failure(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    old_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")

    def fail_atomic_write(*_args):
        raise OSError("atomic manifest write failed")

    monkeypatch.setattr(cli, "_atomic_write_text", fail_atomic_write, raising=False)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == old_manifest


def test_repeated_install_is_idempotent(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    first = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    second = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    assert patched.count("HERMES_FEISHU_CARD_PATCH_BEGIN") == 1
    backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    assert backup == original


def test_restore_after_successful_restore_is_idempotent(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    first_restore = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")
    second_restore = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert install_result.returncode == 0, install_result.stderr
    assert first_restore.returncode == 0, first_restore.stderr
    assert second_restore.returncode == 0, second_restore.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_after_successful_restore_reinstalls_cleanly(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    first_install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    restore = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")
    second_install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert first_install.returncode == 0, first_install.stderr
    assert restore.returncode == 0, restore.stderr
    assert second_install.returncode == 0, second_install.stderr
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_restore_without_backup_removes_patch_and_stale_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    backup_path(hermes_dir).unlink()

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_restore_cleans_stale_manifest_after_run_py_was_restored(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    run_py(hermes_dir).write_text(original, encoding="utf-8")
    backup_path(hermes_dir).unlink()

    restore_result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")
    install_again = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert restore_result.returncode == 0, restore_result.stderr
    assert install_again.returncode == 0, install_again.stderr
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_restore_without_backup_refuses_user_edited_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    backup_path(hermes_dir).unlink()
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
        encoding="utf-8",
    )
    edited = run_py(hermes_dir).read_text(encoding="utf-8")
    original_manifest = manifest_path(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "run.py changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert manifest_path(hermes_dir).read_text(encoding="utf-8") == original_manifest
    assert not backup_path(hermes_dir).exists()


def test_restore_without_manifest_removes_patch_and_stale_backup(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")
    second_result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_restore_without_manifest_accepts_legacy_completion_patch(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    return response\n"
    )
    run_py(hermes_dir).write_text(original, encoding="utf-8")
    backup_path(hermes_dir).write_text(original, encoding="utf-8")
    patched = patcher.apply_patch(original)
    current_complete = "".join(patcher._render_complete_hook_block("    ", "\n"))
    legacy_complete = "".join(
        patcher._render_legacy_complete_hook_block("    ", "\n")
    )
    run_py(hermes_dir).write_text(
        patched.replace(current_complete, legacy_complete),
        encoding="utf-8",
    )

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_restore_cleans_stale_backup_after_run_py_was_restored(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    run_py(hermes_dir).write_text(original, encoding="utf-8")
    manifest_path(hermes_dir).unlink()

    restore_result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")
    install_again = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert restore_result.returncode == 0, restore_result.stderr
    assert install_again.returncode == 0, install_again.stderr
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_restore_cleans_stale_backup_and_manifest_after_run_py_was_restored(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    run_py(hermes_dir).write_text(original, encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_restore_without_manifest_refuses_user_edited_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()
    run_py(hermes_dir).write_text(
        run_py(hermes_dir).read_text(encoding="utf-8") + "\n# user edit\n",
        encoding="utf-8",
    )
    edited = run_py(hermes_dir).read_text(encoding="utf-8")
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "run.py changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert not manifest_path(hermes_dir).exists()


def test_restore_without_manifest_refuses_patched_backup(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    backup_path(hermes_dir).write_text(patched, encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "backup changed since install" in result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == patched
    assert not manifest_path(hermes_dir).exists()


def test_restore_clean_run_py_removes_orphan_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")
    manifest_path(hermes_dir).write_text('{"orphan": true}\n', encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_restore_uninstalled_fixture_is_idempotent(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "restore ok" in result.stdout.lower()
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
