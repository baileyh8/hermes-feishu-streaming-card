from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def make_release_test_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-I" ] && [ "$2" = "-c" ]; then
  exec "$REAL_PYTHON" "$@"
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def make_release_test_curl(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_CURL_LOG"
output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ "${FAKE_CURL_EXIT:-0}" != "0" ]; then
  exit "$FAKE_CURL_EXIT"
fi
cp "$FAKE_CURL_BODY" "$output"
printf '%s' "${FAKE_CURL_STATUS:-200}"
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run_release_installer(
    tmp_path: Path,
    script_name: str,
    *,
    version: str,
    response: str = '{"tag_name":"v4.2.4"}',
    http_status: str = "200",
    curl_exit: str = "0",
    local_source: Path | None = None,
):
    hermes_dir = tmp_path / "hermes"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    runtime_python = make_release_test_python(
        hermes_dir / "venv" / "bin" / "python"
    )
    fake_bin = tmp_path / "fake-bin"
    make_release_test_curl(fake_bin / "curl")
    response_file = tmp_path / "release.json"
    response_file.write_text(response, encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env_file = data_dir / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_release\nFEISHU_APP_SECRET=release_secret\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "FAKE_CURL_BODY": str(response_file),
            "FAKE_CURL_EXIT": curl_exit,
            "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
            "FAKE_CURL_STATUS": http_status,
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "REAL_PYTHON": sys.executable,
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": version,
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_PYTHON": str(runtime_python),
            "PYTHON": str(runtime_python),
        }
    )
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    if local_source is None:
        env.pop("HFC_INSTALL_SOURCE", None)
    else:
        env["HFC_INSTALL_SOURCE"] = str(local_source)
        env["HFC_TEST_NOOP_DELIVERY"] = "1"
    result = subprocess.run(
        ["bash", script_name],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, tmp_path / "python.log", tmp_path / "curl.log", data_dir / "state"


def test_install_sh_prefers_hermes_venv_python(tmp_path):
    hermes_dir = tmp_path / "hermes-agent"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_venv\nFEISHU_APP_SECRET=venv_secret\n",
        encoding="utf-8",
    )
    runtime_python = hermes_dir / "venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
exit 0
""",
        encoding="utf-8",
    )
    runtime_python.chmod(runtime_python.stat().st_mode | stat.S_IXUSR)
    system_marker = tmp_path / "system-python-used"
    system_python = tmp_path / "system-python"
    system_python.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
touch {system_marker!s}
exit 0
""",
        encoding="utf-8",
    )
    system_python.chmod(system_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "PYTHON": str(system_python),
        }
    )
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    python_log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert "hermes_feishu_card.cli setup" in python_log
    assert "-m pip install --upgrade" in python_log
    assert "--user" not in python_log
    assert not system_marker.exists()


def test_install_sh_persists_process_credentials_to_private_env_file(tmp_path):
    env_file = tmp_path / "data" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "export FEISHU_APP_ID=old_app\n"
        "export FEISHU_APP_SECRET=old_secret\n"
        "KEEP_VALUE=untouched\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FEISHU_APP_ID": "cli_process_value",
            "FEISHU_APP_SECRET": "process secret value",
            "HERMES_DIR": str(tmp_path / "hermes-agent"),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "HFC_PYTHON": str(fake_python),
            "HFC_PIP_USER": "0",
        }
    )

    result = subprocess.run(
        ["bash", "install.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "FEISHU_APP_ID='cli_process_value'",
        "FEISHU_APP_SECRET='process secret value'",
        "KEEP_VALUE=untouched",
    ]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "process secret value" not in result.stdout + result.stderr


def test_install_sh_latest_resolves_release_tag_and_pins_spec(tmp_path):
    result, python_log, curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install.sh",
        version="latest",
        response='{"tag_name":"v4.2.4"}',
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = python_log.read_text(encoding="utf-8")
    spec = "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@v4.2.4"
    assert f"-m pip install --upgrade {spec}" in log
    assert curl_log.read_text(encoding="utf-8").count("releases/latest") == 1


@pytest.mark.parametrize(
    ("http_status", "curl_exit", "error_text"),
    [
        ("200", "7", "latest release lookup failed"),
        ("429", "0", "latest release lookup returned HTTP 429"),
    ],
)
def test_install_sh_latest_fails_closed_on_api_error(
    tmp_path,
    http_status,
    curl_exit,
    error_text,
):
    result, python_log, _curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install.sh",
        version="latest",
        http_status=http_status,
        curl_exit=curl_exit,
    )

    assert result.returncode != 0
    assert error_text in result.stderr
    log = python_log.read_text(encoding="utf-8") if python_log.exists() else ""
    assert "-m pip" not in log
    assert "hermes_feishu_card.cli" not in log


def test_install_sh_latest_fails_closed_on_rate_limit(tmp_path):
    result, python_log, _curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install.sh",
        version="latest",
        http_status="403",
    )

    assert result.returncode != 0
    assert "latest release lookup returned HTTP 403" in result.stderr
    log = python_log.read_text(encoding="utf-8") if python_log.exists() else ""
    assert "-m pip" not in log
    assert "hermes_feishu_card.cli" not in log


def test_install_sh_latest_rejects_malformed_release_json(tmp_path):
    result, python_log, _curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install.sh",
        version="latest",
        response='{"tag_name":"v4.2.4-rc1"}',
    )

    assert result.returncode != 0
    assert "latest release tag was invalid" in result.stderr
    log = python_log.read_text(encoding="utf-8")
    assert "-m pip" not in log
    assert "hermes_feishu_card.cli" not in log


def test_install_sh_explicit_ref_bypasses_release_api(tmp_path):
    result, python_log, curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install.sh",
        version="main",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = python_log.read_text(encoding="utf-8")
    spec = "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@main"
    assert f"-m pip install --upgrade {spec}" in log
    assert not curl_log.exists()


def test_install_sh_reads_dotenv_without_sourcing_unknown_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID='cli_dotenv'",
                "FEISHU_APP_SECRET='dotenv secret'",
                "AGENT_BROWSER_EXECUTABLE_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        ),
        encoding="utf-8",
    )
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "ensurepip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  if [ "${HFC_INSTALL_SPEC:-}" != "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@main" ]; then
    echo "HFC_INSTALL_SPEC was not exported" >&2
    exit 4
  fi
  if [ "${FEISHU_APP_ID:-}" != "cli_dotenv" ]; then
    echo "FEISHU_APP_ID was not loaded" >&2
    exit 2
  fi
  if [ "${FEISHU_APP_SECRET:-}" != "dotenv secret" ]; then
    echo "FEISHU_APP_SECRET was not loaded" >&2
    exit 3
  fi
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(tmp_path / "hermes-agent"),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_python),
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Chrome.app/Contents/MacOS/Google" not in result.stderr
    assert "hermes_feishu_card.cli setup" in (tmp_path / "python.log").read_text(
        encoding="utf-8"
    )


def test_install_sh_retries_externally_managed_python(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_dotenv\nFEISHU_APP_SECRET=dotenv_secret\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  case "$*" in
    *--break-system-packages*) exit 0 ;;
    *) echo "error: externally-managed-environment" >&2; exit 1 ;;
  esac
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(tmp_path / "hermes-agent"),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_python),
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "retrying with --break-system-packages" in result.stdout
    assert "error: externally-managed-environment" not in (
        result.stderr + result.stdout
    )
    assert "pip warning handled safely; package install completed" in result.stdout
    assert "--break-system-packages" in (tmp_path / "python.log").read_text(
        encoding="utf-8"
    )


def test_install_sh_stops_when_pip_install_fails(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_dotenv\nFEISHU_APP_SECRET=dotenv_secret\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  echo "fatal package install failure" >&2
  exit 9
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  touch "$SETUP_MARKER"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "SETUP_MARKER": str(tmp_path / "setup-ran"),
            "HERMES_DIR": str(tmp_path / "hermes-agent"),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_python),
        }
    )
    env.pop("HFC_PYTHON", None)
    env.pop("HFC_PIP_USER", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 9
    assert "fatal package install failure" in result.stderr
    assert not (tmp_path / "setup-ran").exists()


def test_install_sh_suppresses_pip_root_user_warning(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_dotenv\nFEISHU_APP_SECRET=dotenv_secret\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  if [ "${PIP_ROOT_USER_ACTION:-}" != "ignore" ]; then
    echo "WARNING: Running pip as the 'root' user can result in broken permissions" >&2
  fi
  echo "install ok"
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(tmp_path / "hermes-agent"),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_python),
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)
    env.pop("PIP_ROOT_USER_ACTION", None)

    result = subprocess.run(
        ["bash", "install.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "install ok" in result.stdout
    assert "Running pip as the 'root' user" not in result.stderr + result.stdout


def make_fake_docker_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "ensurepip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  if [ "${FEISHU_APP_ID:-}" != "cli_docker" ]; then
    echo "FEISHU_APP_ID was not loaded" >&2
    exit 5
  fi
  if [ "${FEISHU_APP_SECRET:-}" != "docker_secret" ]; then
    echo "FEISHU_APP_SECRET was not loaded" >&2
    exit 6
  fi
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_install_docker_sh_persists_process_credentials_to_private_env_file(
    tmp_path,
):
    hermes_dir = tmp_path / "hermes"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_PYTHON_LOG"
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    env_file = tmp_path / "data" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "export FEISHU_APP_ID=old_app\n"
        "export FEISHU_APP_SECRET=old_secret\n"
        "KEEP_VALUE=untouched\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "FEISHU_APP_ID": "cli_docker_process",
            "FEISHU_APP_SECRET": "docker process secret",
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(tmp_path / "data" / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "HFC_PYTHON": str(fake_python),
            "HFC_INSTALL_SOURCE": str(ROOT),
        }
    )

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "FEISHU_APP_ID='cli_docker_process'",
        "FEISHU_APP_SECRET='docker process secret'",
        "KEEP_VALUE=untouched",
    ]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "docker process secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("raw_secret", "expected_secret"),
    [
        (r"docker\\secret", r"docker\\secret"),
        (r"abc\ndef", r"abc\ndef"),
    ],
)
def test_install_docker_sh_keeps_env_secret_literal_backslashes(
    tmp_path, raw_secret, expected_secret
):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\n", encoding="utf-8")
    env_file = data_dir / ".env"
    data_dir.mkdir(parents=True)
    env_file.write_text(
        f"FEISHU_APP_ID=cli_docker\nFEISHU_APP_SECRET={raw_secret}\n",
        encoding="utf-8",
    )
    fake_python = make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "ensurepip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  if [ "${FEISHU_APP_ID:-}" != "cli_docker" ]; then
    echo "FEISHU_APP_ID was not loaded" >&2
    exit 5
  fi
  if [ "${FEISHU_APP_SECRET:-}" != "${EXPECTED_SECRET}" ]; then
    echo "FEISHU_APP_SECRET was not preserved literally" >&2
    echo "actual=${FEISHU_APP_SECRET:-}" >&2
    echo "expected=${EXPECTED_SECRET}" >&2
    exit 6
  fi
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "EXPECTED_SECRET": expected_secret,
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_python),
        }
    )
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert "-m pip install --upgrade git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@main" in log


def make_fake_system_python(path: Path, marker: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{marker}"
exit 99
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_install_docker_sh_declares_container_defaults():
    script_path = ROOT / "install-docker.sh"
    script = script_path.read_text(encoding="utf-8")

    assert 'HERMES_DIR="${HERMES_DIR:-/opt/hermes}"' in script
    assert 'CONFIG_PATH="${CONFIG_PATH:-/opt/data/config.yaml}"' in script
    assert 'ENV_FILE="${ENV_FILE:-/opt/data/.env}"' in script
    assert 'NO_PROMPT="${HFC_NO_PROMPT:-1}"' in script
    assert 'SKIP_START="${HFC_SKIP_START:-0}"' in script
    assert 'SERVICE_MANAGER="${HERMES_FEISHU_CARD_SERVICE_MANAGER:-detached}"' in script
    assert 'STATE_DIR="${HERMES_FEISHU_CARD_STATE_DIR:-}"' in script
    assert 'STATE_DIR="${STATE_DIR:-$(dirname "$CONFIG_PATH")/state}"' in script


def test_install_docker_sh_explicit_local_source_allows_credential_free_smoke(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    data_dir.mkdir(parents=True)
    (data_dir / "config.yaml").write_text(
        "server:\n  host: 127.0.0.1\n  port: 8765\n",
        encoding="utf-8",
    )
    runtime_python = make_argument_capture_python(
        hermes_dir / "venv" / "bin" / "python"
    )

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "HFC_INSTALL_SOURCE": str(ROOT),
            "HFC_TEST_NOOP_DELIVERY": "1",
            "HFC_SKIP_START": "1",
            "HFC_PYTHON": str(runtime_python),
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert f"-m pip install --upgrade {ROOT}" in log
    assert "git+https://" not in log
    assert "hermes_feishu_card.cli doctor" in log
    assert (
        "hermes_feishu_card.cli install "
        f"--hermes-dir {hermes_dir} --hermes-home {hermes_dir.parent} --yes"
        in log
    )
    assert "hermes_feishu_card.cli setup" not in log


def test_install_docker_sh_noop_smoke_refuses_remote_install_source(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    data_dir.mkdir(parents=True)
    runtime_python = make_argument_capture_python(
        hermes_dir / "venv" / "bin" / "python"
    )
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "HFC_TEST_NOOP_DELIVERY": "1",
            "HFC_SKIP_START": "1",
            "HFC_PYTHON": str(runtime_python),
        }
    )
    env.pop("HFC_INSTALL_SOURCE", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HFC_TEST_NOOP_DELIVERY requires HFC_INSTALL_SOURCE" in result.stderr


def test_install_docker_sh_local_source_alone_does_not_bypass_credentials(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    data_dir.mkdir(parents=True)
    runtime_python = make_argument_capture_python(
        hermes_dir / "venv" / "bin" / "python"
    )
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "HFC_INSTALL_SOURCE": str(ROOT),
            "HFC_SKIP_START": "1",
            "HFC_PYTHON": str(runtime_python),
        }
    )
    env.pop("HFC_TEST_NOOP_DELIVERY", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FEISHU_APP_ID/FEISHU_APP_SECRET are missing" in result.stderr


def test_install_docker_sh_noop_smoke_rejects_relative_source(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    data_dir.mkdir(parents=True)
    runtime_python = make_argument_capture_python(
        hermes_dir / "venv" / "bin" / "python"
    )
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "HFC_INSTALL_SOURCE": ".",
            "HFC_TEST_NOOP_DELIVERY": "1",
            "HFC_SKIP_START": "1",
            "HFC_PYTHON": str(runtime_python),
        }
    )

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HFC_INSTALL_SOURCE must be an absolute local directory" in result.stderr


def test_install_docker_sh_noop_smoke_rejects_symlink_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source, target_is_directory=True)
    env = os.environ.copy()
    env.update(
        {
            "HFC_INSTALL_SOURCE": str(source_link),
            "HFC_TEST_NOOP_DELIVERY": "1",
            "HFC_SKIP_START": "1",
        }
    )

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HFC_INSTALL_SOURCE must not be a symlink" in result.stderr


def test_install_docker_sh_noop_smoke_requires_skip_start():
    env = os.environ.copy()
    env.update(
        {
            "HFC_INSTALL_SOURCE": str(ROOT),
            "HFC_TEST_NOOP_DELIVERY": "1",
            "HFC_SKIP_START": "0",
        }
    )

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HFC_TEST_NOOP_DELIVERY requires HFC_SKIP_START=1" in result.stderr


def test_install_docker_sh_makes_shared_state_private(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    state_dir = tmp_path / "opt" / "hfc-state"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text(
        "# gateway\n", encoding="utf-8"
    )
    data_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True, mode=0o755)
    state_dir.chmod(0o755)
    env_file = data_dir / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_docker\nFEISHU_APP_SECRET=docker_secret\n",
        encoding="utf-8",
    )
    make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HERMES_FEISHU_CARD_STATE_DIR": str(state_dir),
            "HFC_SKIP_START": "1",
            "HFC_VERSION": "main",
        }
    )
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700


def test_docker_compose_uses_ordinary_setup_gateway_and_sidecar_processes():
    compose_path = ROOT / "docker-compose.example.yml"
    source = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(source)
    services = compose["services"]

    assert set(services) == {"setup", "gateway", "sidecar"}
    setup = services["setup"]
    gateway = services["gateway"]
    sidecar = services["sidecar"]

    assert setup["command"] == ["bash", "/tmp/install-docker.sh"]
    assert setup["environment"]["HFC_SKIP_START"] == "1"
    assert sidecar["depends_on"]["setup"]["condition"] == "service_completed_successfully"
    assert gateway["depends_on"]["sidecar"]["condition"] == "service_healthy"
    assert sidecar["command"][:4] == [
        "/opt/hermes/venv/bin/python",
        "-m",
        "hermes_feishu_card.runner",
        "--config",
    ]
    assert "healthcheck" in sidecar

    for service in services.values():
        assert service.get("privileged") is not True
        assert service.get("network_mode") != "host"
        assert service.get("pid") != "host"
        assert "hfc-state:/opt/hfc-state" in service["volumes"]

    for service in (setup, sidecar):
        environment = service["environment"]
        assert environment["HERMES_FEISHU_CARD_STATE_DIR"] == "/opt/hfc-state"
        assert environment["HERMES_FEISHU_CARD_SERVICE_MANAGER"] == "detached"
        assert environment["HERMES_FEISHU_CARD_HOST"] == "0.0.0.0"
        assert environment["HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK"] == "true"

    assert gateway["environment"]["HERMES_FEISHU_CARD_STATE_DIR"] == "/opt/hfc-state"
    assert (
        gateway["environment"]["HERMES_FEISHU_CARD_EVENT_URL"]
        == "http://sidecar:8765/events"
    )
    assert "hfc-state" in compose["volumes"]

    lowered = source.lower()
    for forbidden in (
        "privileged:",
        "network_mode: host",
        "pid: host",
        "systemd-run",
        "systemctl",
        "/etc/systemd",
        "/run/systemd",
        "/var/run/docker.sock",
    ):
        assert forbidden not in lowered


def test_install_docker_sh_uses_container_defaults_and_hermes_venv(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
    env_file = data_dir / ".env"
    data_dir.mkdir(parents=True)
    env_file.write_text(
        "FEISHU_APP_ID=cli_docker\nFEISHU_APP_SECRET=docker_secret\n",
        encoding="utf-8",
    )
    runtime_python = make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")
    system_python_marker = tmp_path / "system-python.log"
    fake_system_python = make_fake_system_python(
        tmp_path / "system-python", system_python_marker
    )

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": "v3.7.0",
            "HFC_SKIP_START": "1",
            "PYTHON": str(fake_system_python),
        }
    )
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert str(runtime_python) in result.stdout
    assert "-m pip install --upgrade git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@v3.7.0" in log
    doctor_cmd = (
        "hermes_feishu_card.cli doctor "
        f"--config {data_dir / 'config.yaml'} --hermes-dir {hermes_dir} "
        f"--hermes-home {hermes_dir.parent} --explain"
    )
    setup_cmd = (
        f"hermes_feishu_card.cli setup --hermes-dir {hermes_dir} "
        f"--hermes-home {hermes_dir.parent} "
        f"--config {data_dir / 'config.yaml'} --env-file {env_file} "
        "--event-url http://127.0.0.1:8765/events "
        "--yes --skip-start"
    )
    assert doctor_cmd in log
    assert setup_cmd in log
    assert log.index(doctor_cmd) < log.index(setup_cmd)
    assert not system_python_marker.exists()


def test_install_docker_sh_prefers_hermes_venv_python3(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\n", encoding="utf-8")
    env_file = data_dir / ".env"
    data_dir.mkdir(parents=True)
    env_file.write_text(
        "FEISHU_APP_ID=cli_docker\nFEISHU_APP_SECRET=docker_secret\n",
        encoding="utf-8",
    )
    runtime_python = make_fake_docker_python(hermes_dir / "venv" / "bin" / "python3")
    system_python_marker = tmp_path / "system-python.log"
    fake_system_bin = tmp_path / "system-bin"
    fake_system_python = make_fake_system_python(
        fake_system_bin / "python", system_python_marker
    )
    make_fake_system_python(fake_system_bin / "python3", system_python_marker)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": "main",
            "HFC_SKIP_START": "1",
            "PYTHON": str(fake_system_python),
        }
    )
    env["PATH"] = f"{fake_system_bin}:{env['PATH']}"
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert str(runtime_python) in result.stdout
    assert "using Hermes Python" in result.stdout
    assert not system_python_marker.exists()


def test_install_docker_sh_latest_resolves_to_pinned_release_tag(tmp_path):
    result, python_log, curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install-docker.sh",
        version="latest",
        response='{"tag_name":"v4.2.4"}',
    )
    assert result.returncode == 0, result.stderr + result.stdout
    log = python_log.read_text(encoding="utf-8")
    spec = "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@v4.2.4"
    assert f"-m pip install --upgrade {spec}" in log
    assert curl_log.read_text(encoding="utf-8").count("releases/latest") == 1


def test_install_docker_sh_latest_fails_closed_without_running_pip_or_setup(
    tmp_path,
):
    result, python_log, _curl_log, state_dir = run_release_installer(
        tmp_path,
        "install-docker.sh",
        version="latest",
        http_status="429",
    )

    assert result.returncode != 0
    assert "no package was installed" in result.stderr
    log = python_log.read_text(encoding="utf-8") if python_log.exists() else ""
    assert "-m pip" not in log
    assert "hermes_feishu_card.cli" not in log
    assert not state_dir.exists()


def test_install_docker_sh_explicit_release_stays_pinned_without_api(tmp_path):
    result, python_log, curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install-docker.sh",
        version="v4.2.4",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    spec = "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@v4.2.4"
    assert f"-m pip install --upgrade {spec}" in python_log.read_text(
        encoding="utf-8"
    )
    assert not curl_log.exists()


def test_install_docker_sh_explicit_main_uses_named_moving_ref(tmp_path):
    result, python_log, curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install-docker.sh",
        version="main",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    spec = "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@main"
    assert f"-m pip install --upgrade {spec}" in python_log.read_text(
        encoding="utf-8"
    )
    assert not curl_log.exists()


def test_install_docker_sh_local_source_bypasses_release_resolution(tmp_path):
    result, python_log, curl_log, _state_dir = run_release_installer(
        tmp_path,
        "install-docker.sh",
        version="latest",
        local_source=ROOT,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = python_log.read_text(encoding="utf-8")
    assert f"-m pip install --upgrade {ROOT}" in log
    assert "git+https://" not in log
    assert not curl_log.exists()


def test_install_docker_sh_retries_externally_managed_python(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\n", encoding="utf-8")
    env_file = data_dir / ".env"
    data_dir.mkdir(parents=True)
    env_file.write_text(
        "FEISHU_APP_ID=cli_docker\nFEISHU_APP_SECRET=docker_secret\n",
        encoding="utf-8",
    )
    fake_python = hermes_dir / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True, exist_ok=True)
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  if [ "${PIP_ROOT_USER_ACTION:-}" != "ignore" ]; then
    echo "WARNING: Running pip as the 'root' user can result in broken permissions" >&2
  fi
  case "$*" in
    *--break-system-packages*) echo "install ok"; exit 0 ;;
    *) echo "error: externally-managed-environment" >&2; exit 1 ;;
  esac
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": "main",
            "HFC_SKIP_START": "1",
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)
    env.pop("PIP_ROOT_USER_ACTION", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    combined = result.stderr + result.stdout
    assert "retrying with --break-system-packages" in result.stdout
    assert "error: externally-managed-environment" not in combined
    assert "Running pip as the 'root' user" not in combined
    assert "pip warning handled safely; package install completed" in result.stdout
    assert "--break-system-packages" in (tmp_path / "python.log").read_text(
        encoding="utf-8"
    )


def run_install_docker_with_failing_pip(tmp_path, *, failure_mode):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\n", encoding="utf-8")
    data_dir.mkdir(parents=True)
    env_file = data_dir / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_docker\nFEISHU_APP_SECRET=docker_secret\n",
        encoding="utf-8",
    )
    fake_python = hermes_dir / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True, exist_ok=True)
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then
  case "$FAKE_PIP_FAILURE_MODE:$*" in
    ordinary:*) echo "fatal package install failure" >&2; exit 9 ;;
    externally:*--break-system-packages*)
      echo "externally managed retry still failed" >&2
      exit 13
      ;;
    externally:*) echo "error: externally-managed-environment" >&2; exit 1 ;;
  esac
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ] && [ "$3" = "setup" ]; then
  touch "$SETUP_MARKER"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

    setup_marker = tmp_path / "setup-ran"
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PIP_FAILURE_MODE": failure_mode,
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "SETUP_MARKER": str(setup_marker),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": "main",
            "HFC_SKIP_START": "1",
        }
    )
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, setup_marker, tmp_path / "python.log"


def test_install_docker_sh_stops_when_pip_install_fails(tmp_path):
    result, setup_marker, python_log = run_install_docker_with_failing_pip(
        tmp_path, failure_mode="ordinary"
    )

    assert result.returncode == 9
    assert "fatal package install failure" in result.stderr
    assert not setup_marker.exists()
    assert "hermes_feishu_card.cli" not in python_log.read_text(encoding="utf-8")


def test_install_docker_sh_stops_when_externally_managed_retry_fails(tmp_path):
    result, setup_marker, python_log = run_install_docker_with_failing_pip(
        tmp_path, failure_mode="externally"
    )

    assert result.returncode == 13
    assert "retrying with --break-system-packages" in result.stdout
    assert "externally managed retry still failed" in result.stderr
    assert not setup_marker.exists()
    log = python_log.read_text(encoding="utf-8")
    assert "--break-system-packages" in log
    assert "hermes_feishu_card.cli" not in log


def test_install_docker_sh_fails_without_hermes_venv_python(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
    marker = tmp_path / "system-python.log"
    system_bin = tmp_path / "system-bin"
    fake_system_python = make_fake_system_python(
        system_bin / "python", marker
    )
    make_fake_system_python(system_bin / "python3", marker)
    data_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "FEISHU_APP_ID": "cli_docker",
            "FEISHU_APP_SECRET": "docker_secret",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_system_python),
        }
    )
    env.pop("HFC_PYTHON", None)
    env["PATH"] = f"{system_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "Hermes venv Python was not found" in result.stderr
    assert "HFC_PYTHON" in result.stderr


def test_install_docker_sh_fails_without_noninteractive_credentials(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
    make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")
    data_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "HFC_VERSION": "main",
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FEISHU_APP_ID/FEISHU_APP_SECRET are missing" in result.stderr
    assert "/opt/data/.env" in result.stderr or str(data_dir / ".env") in result.stderr


def make_argument_capture_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'normalized=%s|%s|%s|%s|%s|%s\n' \
  "${HFC_CONFIG:-}" "${HFC_ENV_FILE:-}" "${HFC_VERSION:-}" \
  "${HERMES_FEISHU_CARD_PROFILE_ID:-}" \
  "${HERMES_FEISHU_CARD_EVENT_URL:-}" "${HFC_NO_REPAIR:-}" \
  >> "$FAKE_PYTHON_LOG"
printf 'args=%s\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "${1:-}" = "-c" ] && [ -n "${REAL_PYTHON:-}" ]; then
  exec "$REAL_PYTHON" "$@"
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.mark.parametrize("script_name", ["install.sh", "install-docker.sh"])
@pytest.mark.parametrize("inherited_profile", ["", "default", "ai-secretary"])
def test_named_only_installers_use_config_credentials_without_pinning_profile(script_name, inherited_profile, tmp_path):
    hermes = tmp_path / "hermes"
    (hermes / "gateway").mkdir(parents=True)
    (hermes / "gateway/run.py").write_text("# fixture\n")
    runtime = make_argument_capture_python(hermes / "venv/bin/python")
    config = tmp_path / "config.yaml"
    config.write_text("profiles:\n  ai-secretary:\n    feishu:\n      app_id: fixture-app\n      app_secret: fixture-secret\n")
    selected_env = tmp_path / ".env"
    selected_env.write_text("")
    env = dict(os.environ)
    for name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "HERMES_FEISHU_CARD_PROFILE_ID"):
        env.pop(name, None)
    env.update({"HERMES_DIR": str(hermes), "HFC_CONFIG": str(config), "HFC_ENV_FILE": str(selected_env),
                "HFC_VERSION": "v4.4.1", "HFC_SKIP_START": "1", "HFC_NO_PROMPT": "1", "PYTHON": str(runtime),
                "HFC_PYTHON": str(runtime), "REAL_PYTHON": sys.executable, "FAKE_PYTHON_LOG": str(tmp_path / "python.log")})
    if inherited_profile:
        env["HERMES_FEISHU_CARD_PROFILE_ID"] = inherited_profile
    result = subprocess.run(["bash", script_name], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    log = (tmp_path / "python.log").read_text()
    setup = next(line for line in log.splitlines() if line.startswith("args=-m hermes_feishu_card.cli setup "))
    assert "--profile-id" not in setup
    assert f"|v4.4.1|{inherited_profile}|" in log
    assert "FEISHU_APP_SECRET=" not in selected_env.read_text()
    assert "fixture-secret" not in result.stdout + result.stderr


def test_powershell_preserves_implicit_profile_and_checks_config_credentials():
    script = (ROOT / "install.ps1").read_text()
    assert '$ProfileIdExplicit = $PSBoundParameters.ContainsKey("ProfileId")' in script
    assert 'if ($ProfileIdExplicit)' in script
    assert '$ProfileId = if ($ProfileId) { $ProfileId } else { "default" }' not in script
    assert '_has_feishu_credentials(c,p)' in script
    assert 'if ($LASTEXITCODE -eq 0) { return }' in script


@pytest.mark.parametrize("script_name", ["install.sh", "install-docker.sh"])
@pytest.mark.parametrize("source", ["args", "process", "env_file"])
def test_installers_resolve_profile_arguments_with_shared_precedence(
    script_name, source, tmp_path
):
    hermes_dir = tmp_path / "hermes"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\n", encoding="utf-8")
    runtime_python = make_argument_capture_python(
        hermes_dir / "venv" / "bin" / "python"
    )
    selected_env = tmp_path / "selected.env"
    injection_marker = tmp_path / "unknown-key-was-sourced"
    selected_env.write_text(
        "FEISHU_APP_ID=file-app\n"
        "FEISHU_APP_SECRET=file-secret\n"
        f"HFC_CONFIG={tmp_path / 'file-config.yaml'}\n"
        "HFC_VERSION=v-file\n"
        "HERMES_FEISHU_CARD_PROFILE_ID=file-profile\n"
        "HERMES_FEISHU_CARD_EVENT_URL=http://file-sidecar:8765/events\n"
        "HFC_NO_REPAIR=1\n"
        f"UNKNOWN_KEY=$(touch {injection_marker})\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_ENV_FILE": str(selected_env),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "PYTHON": str(runtime_python),
        }
    )
    for key in (
        "HFC_CONFIG",
        "HFC_VERSION",
        "HERMES_FEISHU_CARD_PROFILE_ID",
        "HERMES_FEISHU_CARD_EVENT_URL",
        "HFC_NO_REPAIR",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
    ):
        env.pop(key, None)

    command = ["bash", script_name]
    if source in {"args", "process"}:
        env.update(
            {
                "HFC_CONFIG": str(tmp_path / "process-config.yaml"),
                "HFC_VERSION": "v-process",
                "HERMES_FEISHU_CARD_PROFILE_ID": "process-profile",
                "HERMES_FEISHU_CARD_EVENT_URL": "http://process-sidecar:8765/events",
                "HFC_NO_REPAIR": "0",
            }
        )
    if source == "args":
        command.extend(
            [
                "--config",
                str(tmp_path / "argument-config.yaml"),
                "--env-file",
                str(selected_env),
                "--version",
                "v-argument",
                "--profile-id",
                "argument-profile",
                "--event-url",
                "http://argument-sidecar:8765/events",
                "--no-repair",
            ]
        )

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert not injection_marker.exists()
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    if source == "args":
        expected = (
            str(tmp_path / "argument-config.yaml"),
            "v-argument",
            "argument-profile",
            "http://argument-sidecar:8765/events",
            "1",
        )
    elif source == "process":
        expected = (
            str(tmp_path / "process-config.yaml"),
            "v-process",
            "process-profile",
            "http://process-sidecar:8765/events",
            "0",
        )
    else:
        expected = (
            str(tmp_path / "file-config.yaml"),
            "v-file",
            "file-profile",
            "http://file-sidecar:8765/events",
            "1",
        )
    config, version, profile, event_url, no_repair = expected
    assert (
        f"normalized={config}|{selected_env}|{version}|{profile}|{event_url}|{no_repair}"
        in log
    )
    assert f"-m pip install" in log
    assert f"@{version}" in log
    setup = (
        "-m hermes_feishu_card.cli setup "
        f"--hermes-dir {hermes_dir} --hermes-home {hermes_dir.parent} "
        f"--config {config} --env-file {selected_env} "
    )
    if source == "args":
        setup += f"--profile-id {profile} "
    setup += f"--event-url {event_url} --yes --skip-start"
    if no_repair == "1":
        setup += " --no-repair"
    elif script_name == "install.sh":
        setup += " --accept-hermes-upgrade"
    assert f"args={setup}" in log
    setup_line = next(
        line
        for line in log.splitlines()
        if line.startswith("args=-m hermes_feishu_card.cli setup ")
    )
    if no_repair == "1":
        assert "--accept-hermes-upgrade" not in setup_line


def test_install_powershell_declares_and_forwards_profile_parameters():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert """param(
  [string]$Config = $env:HFC_CONFIG,
  [string]$EnvFile = $env:HFC_ENV_FILE,
  [string]$Version = $env:HFC_VERSION,
  [string]$ProfileId = $env:HERMES_FEISHU_CARD_PROFILE_ID,
  [string]$EventUrl = $env:HERMES_FEISHU_CARD_EVENT_URL,
  [string]$HermesHome = $env:HERMES_HOME,
  [switch]$NoRepair
)""" in script
    for argument in (
        '"--config", $Config',
        '"--env-file", $EnvFile',
        '"--profile-id", $ProfileId',
        '"--event-url", $EventUrl',
        '"--hermes-home", $HermesHome',
    ):
        assert argument in script
    assert '$args += "--no-repair"' in script
    assert "UNKNOWN_KEY" not in script


def test_install_powershell_latest_resolves_release_tag_and_pins_spec():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert 'return "git+https://github.com/$Repo.git@$ResolvedVersion"' in script
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in script
    assert "-InstallSpec $ResolvedInstallSpec" in script


def test_install_powershell_propagates_native_setup_and_pip_failures():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")
    package_function = script.split("function Install-HfcPackage", 1)[1].split(
        "function Invoke-HfcSetup", 1
    )[0]
    setup_function = script.split("function Invoke-HfcSetup", 1)[1].split(
        "$envValues = Read-HfcEnvFile", 1
    )[0]

    assert "& $PythonBin -m pip @pipArgs" in package_function
    assert "if ($LASTEXITCODE -ne 0)" in package_function
    assert 'Fail "package installation failed' in package_function
    assert "& $PythonBin @args" in setup_function
    assert "if ($LASTEXITCODE -ne 0)" in setup_function
    assert 'Fail "setup failed' in setup_function


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_install_powershell_persists_process_credentials_to_env_file(tmp_path):
    fake_python = tmp_path / "fake-python.ps1"
    fake_python.write_text("exit 0\n", encoding="utf-8")
    env_file = tmp_path / "data" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        " export FEISHU_APP_ID = 'old_app'\n"
        "export FEISHU_APP_SECRET=old_secret\n"
        "KEEP_VALUE=untouched\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "FEISHU_APP_ID": "cli_powershell_process",
            "FEISHU_APP_SECRET": "powershell process secret",
            "HERMES_DIR": str(tmp_path / "hermes"),
            "HFC_CONFIG": str(tmp_path / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_PIP_USER": "0",
            "HFC_VERSION": "main",
            "PYTHON": str(fake_python),
        }
    )

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(ROOT / "install.ps1")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert env_file.read_text(encoding="utf-8-sig").splitlines() == [
        "FEISHU_APP_ID='cli_powershell_process'",
        "FEISHU_APP_SECRET='powershell process secret'",
        "KEEP_VALUE=untouched",
    ]
    assert "powershell process secret" not in result.stdout + result.stderr


def test_install_powershell_latest_fails_closed_on_api_error():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")
    catch_block = script.split("} catch {", 1)[1].split("}", 1)[0]

    assert "latest release lookup failed" in catch_block
    assert 'return "main"' not in catch_block
    assert "no package was installed" in catch_block


def test_install_powershell_latest_rejects_malformed_release_json():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "$tag -notmatch '^v[0-9]+\\.[0-9]+\\.[0-9]+$'" in script
    assert "latest release response was invalid" in script


def test_install_powershell_explicit_ref_bypasses_release_api():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")
    resolver = script.split("function Resolve-HfcVersion", 1)[1].split(
        "$HfcAllowedEnvKeys", 1
    )[0]

    assert resolver.index('if ($Version -ne "latest")') < resolver.index(
        "Invoke-RestMethod"
    )
    assert "return $Version" in resolver
    assert ".Contains('..')" in resolver


def test_install_powershell_env_parser_has_safe_dotenv_contract():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "ConvertFrom-HfcEnvLine" in script
    assignment_pattern = (
        "^(?:export\\s+)?([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*(.*)$"
    )
    assert assignment_pattern in script
    assert "ConvertFrom-HfcEnvValue" in script
    assert "^'([^']*)'\\s*(?:#.*)?$" in script
    assert '^"([^"]*)"\\s*(?:#.*)?$' in script
    assert "\\s+#.*$" in script
    assert "Invoke-Expression" not in script
    assert "iex $line" not in script.lower()
    assert script.index("$key -notin $HfcAllowedEnvKeys") < script.index(
        "ConvertFrom-HfcEnvValue $match.Groups[2].Value"
    )

    file_resolution = script.index("$envValues = Read-HfcEnvFile")
    defaults = script.index(
        '$Config = if ($Config) { $Config } else { Join-Path $HOME ".hermes/config.yaml" }'
    )
    assert file_resolution < script.index(
        'if (!$Config -and $envValues.ContainsKey("HFC_CONFIG"))'
    ) < defaults
    assert file_resolution < script.index(
        'if (!$Version -and $envValues.ContainsKey("HFC_VERSION"))'
    ) < defaults
    assert file_resolution < script.index(
        'if (!$ProfileId -and $envValues.ContainsKey("HERMES_FEISHU_CARD_PROFILE_ID"))'
    ) < defaults
    assert file_resolution < script.index(
        'if (!$EventUrl -and $envValues.ContainsKey("HERMES_FEISHU_CARD_EVENT_URL"))'
    ) < defaults


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
@pytest.mark.parametrize("source", ["argument", "process", "env_file"])
def test_install_powershell_executes_safe_env_parsing_and_precedence(source, tmp_path):
    fake_python = tmp_path / "fake-python.ps1"
    fake_python.write_text(
        """$line = [string]::Join(' ', $args)
$normalized = @(
  $env:HFC_CONFIG,
  $env:HFC_ENV_FILE,
  $env:HFC_VERSION,
  $env:HERMES_FEISHU_CARD_PROFILE_ID,
  $env:HERMES_FEISHU_CARD_EVENT_URL,
  $env:HFC_NO_REPAIR,
  $env:FEISHU_APP_ID,
  $env:FEISHU_APP_SECRET
) -join '|'
Add-Content -LiteralPath $env:FAKE_PYTHON_LOG -Value "normalized=$normalized"
Add-Content -LiteralPath $env:FAKE_PYTHON_LOG -Value "args=$line"
exit 0
""",
        encoding="utf-8",
    )
    env_file = tmp_path / "selected.env"
    injection_marker = tmp_path / "unknown-line-executed"
    env_file.write_text(
        "  export FEISHU_APP_ID = \"file-app\"   # supported comment\n"
        "export FEISHU_APP_SECRET=\"file\\secret\" # supported comment\n"
        f" export HFC_CONFIG = '{tmp_path / 'file config.yaml'}' # comment\n"
        "HFC_VERSION = 'v-file' # comment\n"
        "HERMES_FEISHU_CARD_PROFILE_ID = file-profile # comment\n"
        "HERMES_FEISHU_CARD_EVENT_URL = \"http://file-sidecar:8765/events\" # comment\n"
        "HFC_NO_REPAIR = 1 # comment\n"
        f"UNKNOWN_KEY=$(New-Item -ItemType File '{injection_marker}')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for key in (
        "HFC_CONFIG",
        "HFC_VERSION",
        "HERMES_FEISHU_CARD_PROFILE_ID",
        "HERMES_FEISHU_CARD_EVENT_URL",
        "HFC_NO_REPAIR",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
    ):
        env.pop(key, None)
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_NO_PROMPT": "1",
            "HFC_SKIP_START": "1",
            "HFC_PIP_USER": "0",
            "HERMES_DIR": str(tmp_path / "hermes"),
            "PYTHON": str(fake_python),
        }
    )
    command = ["pwsh", "-NoProfile", "-File", str(ROOT / "install.ps1")]
    if source in {"argument", "process"}:
        env.update(
            {
                "HFC_CONFIG": str(tmp_path / "process-config.yaml"),
                "HFC_VERSION": "v-process",
                "HERMES_FEISHU_CARD_PROFILE_ID": "process-profile",
                "HERMES_FEISHU_CARD_EVENT_URL": "http://process-sidecar:8765/events",
                "HFC_NO_REPAIR": "0",
            }
        )
    if source == "argument":
        command.extend(
            [
                "-Config",
                str(tmp_path / "argument-config.yaml"),
                "-Version",
                "v-argument",
                "-ProfileId",
                "argument-profile",
                "-EventUrl",
                "http://argument-sidecar:8765/events",
                "-NoRepair",
            ]
        )

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert not injection_marker.exists()
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    if source == "argument":
        expected = (
            str(tmp_path / "argument-config.yaml"),
            "v-argument",
            "argument-profile",
            "http://argument-sidecar:8765/events",
            "1",
        )
    elif source == "process":
        expected = (
            str(tmp_path / "process-config.yaml"),
            "v-process",
            "process-profile",
            "http://process-sidecar:8765/events",
            "0",
        )
    else:
        expected = (
            str(tmp_path / "file config.yaml"),
            "v-file",
            "file-profile",
            "http://file-sidecar:8765/events",
            "1",
        )
    config, version, profile, event_url, no_repair = expected
    normalized = (
        f"normalized={config}|{env_file}|{version}|{profile}|{event_url}|"
        f"{no_repair}|file-app|file\\secret"
    )
    assert normalized in log
