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
