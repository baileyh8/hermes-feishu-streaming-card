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
    assert "--break-system-packages" in (tmp_path / "python.log").read_text(
        encoding="utf-8"
    )


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
    assert 'CONFIG_PATH="${HFC_CONFIG:-/opt/data/config.yaml}"' in script
    assert 'ENV_FILE="${HFC_ENV_FILE:-/opt/data/.env}"' in script
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
    doctor_cmd = f"hermes_feishu_card.cli doctor --config {data_dir / 'config.yaml'} --hermes-dir {hermes_dir} --explain"
    setup_cmd = f"hermes_feishu_card.cli setup --hermes-dir {hermes_dir} --config {data_dir / 'config.yaml'} --yes --skip-start"
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
