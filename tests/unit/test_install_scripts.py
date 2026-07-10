import os
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


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
  if [ "${HFC_INSTALL_SPEC:-}" != "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git" ]; then
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
    doctor_cmd = f"hermes_feishu_card.cli doctor --config {data_dir / 'config.yaml'} --hermes-dir {hermes_dir} --profile-id default --explain"
    setup_cmd = (
        f"hermes_feishu_card.cli setup --hermes-dir {hermes_dir} "
        f"--config {data_dir / 'config.yaml'} --env-file {env_file} "
        "--profile-id default --event-url http://127.0.0.1:8765/events "
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


def test_install_docker_sh_uses_latest_without_pin(tmp_path):
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
    runtime_python = make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": "latest",
            "HFC_SKIP_START": "1",
            "PYTHON": str(runtime_python),
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
    spec = "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git"
    assert f"-m pip install --upgrade {spec}" in log
    assert f"{spec}@" not in log
    assert "@v" not in log
    assert "@main" not in log


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
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


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
        f"--hermes-dir {hermes_dir} --config {config} --env-file {selected_env} "
        f"--profile-id {profile} --event-url {event_url} --yes --skip-start"
    )
    if no_repair == "1":
        setup += " --no-repair"
    assert f"args={setup}" in log


def test_install_powershell_declares_and_forwards_profile_parameters():
    script = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert """param(
  [string]$Config = $env:HFC_CONFIG,
  [string]$EnvFile = $env:HFC_ENV_FILE,
  [string]$Version = $env:HFC_VERSION,
  [string]$ProfileId = $env:HERMES_FEISHU_CARD_PROFILE_ID,
  [string]$EventUrl = $env:HERMES_FEISHU_CARD_EVENT_URL,
  [switch]$NoRepair
)""" in script
    for argument in (
        '"--config", $Config',
        '"--env-file", $EnvFile',
        '"--profile-id", $ProfileId',
        '"--event-url", $EventUrl',
    ):
        assert argument in script
    assert '$args += "--no-repair"' in script
    assert "UNKNOWN_KEY" not in script
