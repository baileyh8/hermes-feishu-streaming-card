import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from hermes_feishu_card import cli


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


def test_install_patches_run_py_and_writes_backup_and_manifest(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "install ok" in result.stdout.lower()
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


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


def test_reinstall_without_manifest_refuses_unedited_patched_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    manifest_path(hermes_dir).unlink()
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")
    patched = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode != 0
    assert "install state incomplete" in reinstall.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert not manifest_path(hermes_dir).exists()


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


def test_reinstall_without_state_refuses_owned_patch_in_run_py(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    backup_path(hermes_dir).unlink()
    manifest_path(hermes_dir).unlink()
    patched = run_py(hermes_dir).read_text(encoding="utf-8")

    reinstall = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert reinstall.returncode != 0
    assert "install state incomplete" in reinstall.stderr
    assert run_py(hermes_dir).read_text(encoding="utf-8") == patched
    assert not backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


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
