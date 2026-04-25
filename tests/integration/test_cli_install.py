import os
import shutil
import subprocess
import sys
from pathlib import Path


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


def test_install_patches_run_py_and_writes_backup_and_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "install ok" in result.stdout.lower()
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert (hermes_dir / "gateway" / BACKUP_NAME).exists()
    assert (hermes_dir / MANIFEST_NAME).exists()


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


def test_uninstall_restores_installed_fixture(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr

    result = run_cli("uninstall", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "uninstall ok" in result.stdout.lower()
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original


def test_install_unsupported_hermes_dir_returns_nonzero(tmp_path):
    hermes_dir = tmp_path / "unsupported"
    hermes_dir.mkdir()

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "gateway/run.py missing" in result.stderr


def test_repeated_install_is_idempotent(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    first = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    second = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    assert patched.count("HERMES_FEISHU_CARD_PATCH_BEGIN") == 1
    backup = (hermes_dir / "gateway" / BACKUP_NAME).read_text(encoding="utf-8")
    assert backup == original


def test_restore_uninstalled_fixture_is_idempotent(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    original = run_py(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "restore ok" in result.stdout.lower()
    assert run_py(hermes_dir).read_text(encoding="utf-8") == original
