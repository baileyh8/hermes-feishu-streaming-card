import os
import stat
import subprocess
from pathlib import Path


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
        "FEISHU_APP_ID=cli_docker\\nFEISHU_APP_SECRET=docker_secret\\n",
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
            "HFC_VERSION": "main",
            "HFC_SKIP_START": "1",
        }
    )
    env.pop("PYTHON", None)
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
    assert "-m pip install --upgrade git+https://github.com/baileyh8/hermes-feishu-streaming-card.git" in log
    assert f"hermes_feishu_card.cli doctor --config {data_dir / 'config.yaml'} --hermes-dir {hermes_dir} --explain" in log
    assert f"hermes_feishu_card.cli setup --hermes-dir {hermes_dir} --config {data_dir / 'config.yaml'} --yes --skip-start" in log


def test_install_docker_sh_fails_without_hermes_venv_python(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
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
        }
    )
    env.pop("HFC_PYTHON", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
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
