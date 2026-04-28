import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

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
    assert backup_path(hermes_dir).exists()
    assert manifest_path(hermes_dir).exists()


def test_install_upgrades_phase_one_placeholder_install(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    write_phase_one_install_state(hermes_dir)

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    patched = run_py(hermes_dir).read_text(encoding="utf-8")
    assert "emit_from_hermes_locals" in patched
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
