import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from hashlib import sha256
from pathlib import Path, PureWindowsPath

import pytest

from hermes_feishu_card import __version__ as PACKAGE_VERSION
from hermes_feishu_card import cli
from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
EXACT_BASE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "hermes_exact_base.py"
)
CRON_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "hermes_cron"
    / "scheduler.py"
)
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


def test_runtime_feishu_sdk_probe_allows_windows_cold_start(monkeypatch):
    observed = {}

    def fake_run(*_args, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{\"version\":\"1.6.8\",\"supports_extra_ua_tags\":true}\n',
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    report = cli._check_runtime_feishu_sdk(Path(sys.executable))

    assert report["status"] == "ok"
    assert observed["timeout"] == 30


def test_candidate_hermes_config_paths_include_hermes_home_parent(tmp_path):
    hermes_root = tmp_path / "hermes" / "hermes-agent"

    candidates = cli._candidate_hermes_config_paths(hermes_root)

    assert hermes_root.parent / "config.yaml" in candidates
    assert hermes_root.parent / "config.yml" in candidates


def test_render_install_manifest_uses_portable_windows_paths(monkeypatch):
    root = PureWindowsPath("C:/Users/test/AppData/Local/hermes/hermes-agent")
    manifest_path = root / ".hermes_feishu_card_manifest"
    monkeypatch.setattr(
        cli,
        "build_integrity_provenance",
        lambda *_args, **_kwargs: {},
    )

    rendered = cli._render_install_manifest(
        manifest_path,
        run_py=root / "gateway" / "run.py",
        run_contents="patched\n",
        backup_path=root / "gateway" / "run.py.hermes_feishu_card.bak",
        run_source="original\n",
        cron_py=root / "cron" / "scheduler.py",
        cron_contents="patched cron\n",
        cron_backup_path=root / "cron" / "scheduler.py.hermes_feishu_card.bak",
        cron_source="original cron\n",
        base_py=root / "gateway" / "platforms" / "base.py",
        base_contents="patched base\n",
        base_backup_path=(
            root / "gateway" / "platforms" / "base.py.hermes_feishu_card.bak"
        ),
        base_source="original base\n",
    )

    manifest = json.loads(rendered)
    for key in (
        "run_py",
        "backup",
        "cron_py",
        "cron_backup",
        "base_py",
        "base_backup",
    ):
        assert "\\" not in manifest[key]


def stub_setup_runtime(monkeypatch, hermes_dir):
    runtime_python = hermes_dir / ".venv" / "bin" / "python"
    identity = "python-sha256:" + "1" * 64
    monkeypatch.setattr(
        cli,
        "_resolve_start_runtime_identity",
        lambda _root: (runtime_python, identity),
    )
    return runtime_python, identity


def initialize_git_fixture(hermes_dir):
    subprocess.run(["git", "-C", str(hermes_dir), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(hermes_dir), "config", "user.name", "HFC Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(hermes_dir),
            "config",
            "user.email",
            "hfc@example.invalid",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(hermes_dir), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(hermes_dir), "commit", "-qm", "initial"],
        check=True,
    )


def run_py(hermes_dir):
    return hermes_dir / "gateway" / "run.py"


def backup_path(hermes_dir):
    return hermes_dir / "gateway" / BACKUP_NAME


def manifest_path(hermes_dir):
    return hermes_dir / MANIFEST_NAME


def base_path(hermes_dir):
    return hermes_dir / "gateway" / "platforms" / "base.py"


def base_backup_path(hermes_dir):
    return base_path(hermes_dir).with_name("base.py.hermes_feishu_card.bak")


def make_exact_019_hermes(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    (hermes_dir / "VERSION").write_text("v0.19.0\n", encoding="utf-8")
    target = base_path(hermes_dir)
    target.parent.mkdir(parents=True)
    shutil.copy2(EXACT_BASE_FIXTURE, target)
    return hermes_dir


def cron_path(hermes_dir):
    return hermes_dir / "cron" / "scheduler.py"


def cron_backup_path(hermes_dir):
    return cron_path(hermes_dir).with_name("scheduler.py.hermes_feishu_card.bak")


def make_cron_hermes(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    target = cron_path(hermes_dir)
    target.parent.mkdir(parents=True)
    shutil.copy2(CRON_FIXTURE, target)
    return hermes_dir


def make_exact_019_cron_hermes(tmp_path):
    hermes_dir = make_exact_019_hermes(tmp_path)
    target = cron_path(hermes_dir)
    target.parent.mkdir(parents=True)
    shutil.copy2(CRON_FIXTURE, target)
    return hermes_dir


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


def test_restore_transaction_rollback_preserves_concurrent_replacement_and_continues(
    tmp_path, monkeypatch
):
    raced = tmp_path / "raced.txt"
    owned = tmp_path / "owned.txt"
    failing = tmp_path / "failing.txt"
    raced.write_text("raced-before\n", encoding="utf-8")
    owned.write_text("owned-before\n", encoding="utf-8")
    failing.write_text("failing-before\n", encoding="utf-8")
    original_atomic_write = cli._atomic_write_text
    writes = 0

    def fail_after_concurrent_replacement(path, contents, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 3:
            raced.unlink()
            raced.write_text("user-concurrent-replacement\n", encoding="utf-8")
            raise OSError("third target unavailable")
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_after_concurrent_replacement)

    with pytest.raises(ValueError, match="manual review required"):
        cli._write_targets_transactionally(
            [
                (raced, "raced-after\n"),
                (owned, "owned-after\n"),
                (failing, "failing-after\n"),
            ]
        )

    assert raced.read_text(encoding="utf-8") == "user-concurrent-replacement\n"
    assert owned.read_text(encoding="utf-8") == "owned-before\n"
    assert failing.read_text(encoding="utf-8") == "failing-before\n"


def test_atomic_write_temp_cleanup_stays_bound_to_staging_parent(
    tmp_path, monkeypatch
):
    parent = tmp_path / "managed"
    parent.mkdir()
    target = parent / "target.txt"
    target.write_text("before\n", encoding="utf-8")
    real_parent = tmp_path / "managed-real"
    original_replace = cli.os.replace
    decoy = None

    def replace_parent_before_temp_replace(source, destination, *args, **kwargs):
        nonlocal decoy
        source_name = Path(source).name
        if (
            decoy is None
            and source_name.startswith(".target.txt.")
            and source_name.endswith(".tmp")
        ):
            parent.rename(real_parent)
            parent.mkdir()
            decoy = parent / source_name
            decoy.write_text("after\n", encoding="utf-8")
            (parent / target.name).write_text("USER-TARGET\n", encoding="utf-8")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(cli.os, "replace", replace_parent_before_temp_replace)

    with pytest.raises(ValueError, match="directory changed during write"):
        cli._atomic_write_text(target, "after\n")

    assert decoy is not None
    assert decoy.read_text(encoding="utf-8") == "after\n"
    assert (parent / target.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert not list(real_parent.glob(".target.txt.*.tmp"))
    assert (real_parent / "target.txt").read_text(encoding="utf-8") == "before\n"


def test_restore_transaction_prebinds_parent_across_atomic_write_entry_gap(
    tmp_path, monkeypatch
):
    parent = tmp_path / "managed"
    parent.mkdir()
    target = parent / "target.txt"
    target.write_text("before\n", encoding="utf-8")
    real_parent = tmp_path / "managed-real"
    original_atomic_write = cli._atomic_write_text
    injected = False

    def swap_parent_before_atomic_open(path, contents, **kwargs):
        nonlocal injected
        if not injected:
            parent.rename(real_parent)
            parent.mkdir()
            (parent / target.name).write_text("USER-TARGET\n", encoding="utf-8")
            injected = True
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", swap_parent_before_atomic_open)

    with pytest.raises(ValueError, match="directory changed during write"):
        cli._write_targets_transactionally([(target, "after\n")])

    assert injected
    assert (parent / target.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert (real_parent / target.name).read_text(encoding="utf-8") == "before\n"


def test_restore_transaction_absent_rollback_unlink_stays_bound_to_parent(
    tmp_path, monkeypatch
):
    parent = tmp_path / "managed"
    parent.mkdir()
    created = parent / "created.txt"
    failing = parent / "failing.txt"
    failing.write_text("before\n", encoding="utf-8")
    real_parent = tmp_path / "managed-real"
    original_atomic_write = cli._atomic_write_text
    original_unlink = cli.os.unlink
    injected = False

    def fail_second_write(path, contents, **kwargs):
        if path == failing:
            raise OSError("second target unavailable")
        return original_atomic_write(path, contents, **kwargs)

    def swap_parent_at_rollback_unlink(path, *args, **kwargs):
        nonlocal injected
        candidate = os.fspath(path)
        if not injected and candidate in {os.fspath(created), created.name}:
            parent.rename(real_parent)
            parent.mkdir()
            (parent / created.name).write_text("USER-TARGET\n", encoding="utf-8")
            injected = True
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_second_write)
    monkeypatch.setattr(cli.os, "unlink", swap_parent_at_rollback_unlink)

    with pytest.raises(OSError, match="second target unavailable"):
        cli._write_targets_transactionally(
            [(created, "created\n"), (failing, "after\n")]
        )

    assert injected
    assert (parent / created.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert not (real_parent / created.name).exists()


def test_restore_transaction_final_sweep_rolls_back_prior_parent_swap(
    tmp_path, monkeypatch
):
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = first_parent / "first.txt"
    second = second_parent / "second.txt"
    first.write_text("first-before\n", encoding="utf-8")
    second.write_text("second-before\n", encoding="utf-8")
    real_parent = tmp_path / "first-real"
    original_atomic_write = cli._atomic_write_text
    injected = False

    def swap_first_parent_after_second_commit(path, contents, **kwargs):
        nonlocal injected
        result = original_atomic_write(path, contents, **kwargs)
        if path == second and not injected:
            first_parent.rename(real_parent)
            first_parent.mkdir()
            (first_parent / first.name).write_text("USER-TARGET\n", encoding="utf-8")
            injected = True
        return result

    monkeypatch.setattr(
        cli, "_atomic_write_text", swap_first_parent_after_second_commit
    )

    with pytest.raises(ValueError, match="directory changed"):
        cli._write_targets_transactionally(
            [(first, "first-after\n"), (second, "second-after\n")]
        )

    assert injected
    assert (first_parent / first.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert (real_parent / first.name).read_text(encoding="utf-8") == "first-before\n"
    assert second.read_text(encoding="utf-8") == "second-before\n"


def test_atomic_write_uses_portable_fallback_without_dirfd_support(
    tmp_path, monkeypatch
):
    target = tmp_path / "config.yaml"
    target.write_text("before\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_cli_dirfd_binding_supported", lambda: False)

    cli._atomic_write_text(target, "after\n")

    assert target.read_text(encoding="utf-8") == "after\n"
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def test_portable_existing_write_does_not_use_unbound_replace(
    tmp_path, monkeypatch
):
    target = tmp_path / "config.yaml"
    target.write_text("before\n", encoding="utf-8")
    replace_called = False

    def unexpected_replace(*_args, **_kwargs):
        nonlocal replace_called
        replace_called = True
        raise AssertionError("portable existing write must stay on the bound handle")

    monkeypatch.setattr(cli.os, "replace", unexpected_replace)

    cli._atomic_write_text_portable(target, "after\n")

    assert not replace_called
    assert target.read_text(encoding="utf-8") == "after\n"


@pytest.mark.parametrize(
    "failure_stage",
    ("short_write", "ftruncate", "fsync", "post_write_verification"),
)
def test_portable_existing_write_restores_original_after_mid_write_failure(
    tmp_path, monkeypatch, failure_stage
):
    target = tmp_path / "config.yaml"
    original = b"ORIGINAL-CONTENT\n"
    target.write_bytes(original)
    expected_snapshot = cli._portable_target_state(target)[0]
    assert expected_snapshot is not None

    if failure_stage == "short_write":
        original_write = cli.os.write
        write_calls = 0

        def fail_after_short_write(fd, payload):
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                return original_write(fd, payload[:4])
            if write_calls == 2:
                raise OSError("simulated write failure")
            return original_write(fd, payload)

        monkeypatch.setattr(cli.os, "write", fail_after_short_write)
    elif failure_stage == "ftruncate":
        original_ftruncate = cli.os.ftruncate
        truncate_calls = 0

        def fail_first_truncate(fd, length):
            nonlocal truncate_calls
            truncate_calls += 1
            if truncate_calls == 1:
                raise OSError("simulated truncate failure")
            return original_ftruncate(fd, length)

        monkeypatch.setattr(cli.os, "ftruncate", fail_first_truncate)
    elif failure_stage == "fsync":
        original_fsync = cli.os.fsync
        fsync_calls = 0

        def fail_first_fsync(fd):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 1:
                raise OSError("simulated fsync failure")
            return original_fsync(fd)

        monkeypatch.setattr(cli.os, "fsync", fail_first_fsync)
    else:
        original_verify = cli._verify_portable_exclusive_bytes
        verify_calls = 0

        def fail_first_verification(fd, path, identity, payload):
            nonlocal verify_calls
            verify_calls += 1
            if verify_calls == 1:
                raise ValueError("simulated post-write verification failure")
            return original_verify(fd, path, identity, payload)

        monkeypatch.setattr(
            cli, "_verify_portable_exclusive_bytes", fail_first_verification
        )

    with pytest.raises((OSError, ValueError)):
        cli._atomic_write_text_portable(
            target,
            "REPLACEMENT-CONTENT-WITH-MORE-BYTES\n",
            expected_before=expected_snapshot,
        )

    assert target.read_bytes() == original
    assert cli._portable_target_state(target)[0] == expected_snapshot


@pytest.mark.skipif(os.name != "nt", reason="requires Windows sharing semantics")
def test_portable_windows_exclusive_handle_blocks_concurrent_writer(
    tmp_path, monkeypatch
):
    target = tmp_path / "config.yaml"
    target.write_text("before\n", encoding="utf-8")
    original_open = cli._open_portable_exclusive_fd
    competing_write_blocked = False

    def open_and_probe(path, *, create):
        nonlocal competing_write_blocked
        fd = original_open(path, create=create)
        try:
            path.write_text("CONCURRENT_EDIT\n", encoding="utf-8")
        except OSError:
            competing_write_blocked = True
        else:
            os.close(fd)
            pytest.fail("Windows exclusive handle allowed a competing writer")
        return fd

    monkeypatch.setattr(cli, "_open_portable_exclusive_fd", open_and_probe)

    cli._atomic_write_text_portable(target, "after\n")

    assert competing_write_blocked
    assert target.read_text(encoding="utf-8") == "after\n"


def test_restore_transaction_fails_closed_without_dirfd_support(
    tmp_path, monkeypatch
):
    target = tmp_path / "run.py"
    target.write_text("before\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_cli_dirfd_binding_supported", lambda: False)

    with pytest.raises(ValueError, match="secure restore transaction requires"):
        cli._write_targets_transactionally([(target, "after\n")])

    assert target.read_text(encoding="utf-8") == "before\n"


def test_integrity_migration_commits_manifest_and_env_in_one_transaction(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    initialize_git_fixture(hermes_dir)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    config_parent = tmp_path / "hfc-config"
    config_parent.mkdir()
    config_path = config_parent / "config.yaml"
    env_path = config_parent / ".env"
    original_transaction = cli._write_targets_transactionally
    calls = []

    def record_transaction(changes, **kwargs):
        calls.append(([path for path, _contents in changes], kwargs))
        return original_transaction(changes, **kwargs)

    monkeypatch.setattr(cli, "_write_targets_transactionally", record_transaction)

    result = cli._run_integrity(
        Namespace(
            integrity_command="migrate-safe",
            hermes_dir=str(hermes_dir),
            config=str(config_path),
            env_file=None,
        )
    )

    assert result == 0
    assert len(calls) == 1
    assert calls[0][0] == [manifest_path(hermes_dir), env_path]
    assert calls[0][1]["expected_identities"]
    assert calls[0][1]["expected_directories"]
    assert "HERMES_FEISHU_CARD_INTEGRITY_MODE=safe" in env_path.read_text(
        encoding="utf-8"
    )
    assert "integrity" in json.loads(
        manifest_path(hermes_dir).read_text(encoding="utf-8")
    )


def test_integrity_migration_refuses_env_parent_swap_before_transaction(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    initialize_git_fixture(hermes_dir)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    config_parent = tmp_path / "hfc-config"
    config_parent.mkdir()
    env_path = config_parent / ".env"
    env_path.write_text("UNKNOWN=keep\n", encoding="utf-8")
    env_before = env_path.read_bytes()
    real_parent = tmp_path / "hfc-config-real"
    manifest_before = manifest_path(hermes_dir).read_bytes()
    original_transaction = cli._write_targets_transactionally

    def swap_before_transaction(changes, **kwargs):
        config_parent.rename(real_parent)
        config_parent.mkdir()
        (config_parent / env_path.name).write_text(
            "USER-TARGET\n", encoding="utf-8"
        )
        return original_transaction(changes, **kwargs)

    monkeypatch.setattr(
        cli, "_write_targets_transactionally", swap_before_transaction
    )

    result = cli._run_integrity(
        Namespace(
            integrity_command="migrate-safe",
            hermes_dir=str(hermes_dir),
            config=str(config_parent / "config.yaml"),
            env_file=None,
        )
    )

    assert result != 0
    assert (config_parent / env_path.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert (real_parent / env_path.name).read_bytes() == env_before
    assert manifest_path(hermes_dir).read_bytes() == manifest_before


def test_integrity_migration_refuses_manifest_parent_swap_before_transaction(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    initialize_git_fixture(hermes_dir)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = manifest_path(hermes_dir)
    manifest_before = manifest.read_bytes()
    real_root = hermes_dir.with_name("hermes-real")
    config_parent = tmp_path / "hfc-config"
    config_parent.mkdir()
    original_transaction = cli._write_targets_transactionally

    def swap_before_transaction(changes, **kwargs):
        hermes_dir.rename(real_root)
        hermes_dir.mkdir()
        (hermes_dir / MANIFEST_NAME).write_text(
            "USER-TARGET\n", encoding="utf-8"
        )
        return original_transaction(changes, **kwargs)

    monkeypatch.setattr(
        cli, "_write_targets_transactionally", swap_before_transaction
    )

    result = cli._run_integrity(
        Namespace(
            integrity_command="migrate-safe",
            hermes_dir=str(hermes_dir),
            config=str(config_parent / "config.yaml"),
            env_file=None,
        )
    )

    assert result != 0
    assert (hermes_dir / MANIFEST_NAME).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert (real_root / MANIFEST_NAME).read_bytes() == manifest_before


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
if [ "$1" = "-I" ]; then
  shift
fi
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
if [ "$1" = "-I" ]; then
  shift
fi
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
if [ "$1" = "-I" ]; then
  shift
fi
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
if [ "$1" = "-I" ]; then
  shift
fi
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
if [ "$1" = "-I" ]; then
  shift
fi
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
    runtime_python, runtime_identity = stub_setup_runtime(monkeypatch, hermes_dir)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    started = {}
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")

    def fake_start_sidecar(path, config, **kwargs):
        started["path"] = Path(path)
        started["config"] = config
        started["kwargs"] = kwargs
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
    assert started["kwargs"] == {
        "hermes_dir": hermes_dir.resolve(),
        "python_executable": runtime_python,
        "expected_package_version": PACKAGE_VERSION,
        "expected_python_identity": runtime_identity,
    }
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
    stub_setup_runtime(monkeypatch, hermes_dir)
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
    runtime_python, runtime_identity = stub_setup_runtime(monkeypatch, hermes_dir)
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
    assert started == {
        "path": config_path,
        "kwargs": {
            "env_file": env_path,
            "hermes_dir": hermes_dir.resolve(),
            "python_executable": runtime_python,
            "expected_package_version": PACKAGE_VERSION,
            "expected_python_identity": runtime_identity,
        },
    }
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
    stub_setup_runtime(monkeypatch, hermes_dir)
    (hermes_dir / "config.yaml").write_text(
        "streaming:\n  enabled: false\n  transport: edit\n", encoding="utf-8"
    )
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args, **_kwargs: "started")
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
    stub_setup_runtime(monkeypatch, hermes_dir)
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
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args, **_kwargs: "started")
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
    stub_setup_runtime(monkeypatch, hermes_dir)
    (hermes_dir / "config.yaml").write_text(
        "streaming:\n  enabled: true\n  transport: edit\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args, **_kwargs: "started")
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


def test_install_and_restore_019_manages_exact_base_as_third_target(tmp_path):
    hermes_dir = make_exact_019_hermes(tmp_path)
    run_original = run_py(hermes_dir).read_bytes()
    base_original = base_path(hermes_dir).read_bytes()

    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0

    assert patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN in base_path(hermes_dir).read_text(
        encoding="utf-8"
    )
    assert patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN in base_path(
        hermes_dir
    ).read_text(encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    assert manifest["base_py"] == "gateway/platforms/base.py"
    assert manifest["base_patched_sha256"] == cli.file_sha256(base_path(hermes_dir))
    assert manifest["base_backup"] == (
        "gateway/platforms/base.py.hermes_feishu_card.bak"
    )
    assert manifest["base_backup_sha256"] == cli.file_sha256(
        base_backup_path(hermes_dir)
    )

    assert cli.main(["restore", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    assert run_py(hermes_dir).read_bytes() == run_original
    assert base_path(hermes_dir).read_bytes() == base_original
    assert not backup_path(hermes_dir).exists()
    assert not base_backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_legacy_manifest_does_not_touch_unowned_base_files(
    tmp_path, command
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    user_base = base_path(hermes_dir)
    user_base.parent.mkdir(parents=True)
    user_base.write_text("# user-owned base\n", encoding="utf-8")
    orphan_backup = base_backup_path(hermes_dir)
    orphan_backup.write_text("# user-owned backup\n", encoding="utf-8")

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert user_base.read_text(encoding="utf-8") == "# user-owned base\n"
    assert orphan_backup.read_text(encoding="utf-8") == "# user-owned backup\n"


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_v1_manifest_refuses_exact_base_patch_pair_without_ownership_fields(
    tmp_path, command
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    for field in cli._BASE_MANIFEST_FIELDS:
        manifest.pop(field)
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    evidence = {
        "run.py": run_py(hermes_dir).read_bytes(),
        "run backup": backup_path(hermes_dir).read_bytes(),
        "manifest": manifest_path(hermes_dir).read_bytes(),
        "exact Base": base_path(hermes_dir).read_bytes(),
        "exact Base backup": base_backup_path(hermes_dir).read_bytes(),
    }

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "exact Base evidence exists but manifest ownership is missing" in result.stderr
    assert run_py(hermes_dir).read_bytes() == evidence["run.py"]
    assert backup_path(hermes_dir).read_bytes() == evidence["run backup"]
    assert manifest_path(hermes_dir).read_bytes() == evidence["manifest"]
    assert base_path(hermes_dir).read_bytes() == evidence["exact Base"]
    assert base_backup_path(hermes_dir).read_bytes() == evidence["exact Base backup"]


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    "missing_side",
    ["backup", "target"],
)
def test_v1_manifest_refuses_single_sided_exact_base_evidence(
    tmp_path, command, missing_side
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    for field in cli._BASE_MANIFEST_FIELDS:
        manifest.pop(field)
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    if missing_side == "backup":
        base_backup_path(hermes_dir).unlink()
    else:
        base_path(hermes_dir).unlink()
    evidence = {
        path: path.read_bytes() if path.exists() else None
        for path in (
            run_py(hermes_dir),
            backup_path(hermes_dir),
            manifest_path(hermes_dir),
            base_path(hermes_dir),
            base_backup_path(hermes_dir),
        )
    }

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "exact Base evidence" in result.stderr
    assert {
        path: path.read_bytes() if path.exists() else None for path in evidence
    } == evidence


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    "missing_side",
    ["backup", "target"],
)
def test_v1_manifest_refuses_single_sided_cron_evidence(
    tmp_path, command, missing_side
):
    hermes_dir = make_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    for field in cli._CRON_MANIFEST_FIELDS:
        manifest.pop(field)
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    if missing_side == "backup":
        cron_backup_path(hermes_dir).unlink()
    else:
        cron_path(hermes_dir).unlink()
    evidence = {
        path: path.read_bytes() if path.exists() else None
        for path in (
            run_py(hermes_dir),
            backup_path(hermes_dir),
            manifest_path(hermes_dir),
            cron_path(hermes_dir),
            cron_backup_path(hermes_dir),
        )
    }

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "cron evidence" in result.stderr
    assert {
        path: path.read_bytes() if path.exists() else None for path in evidence
    } == evidence


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_v1_manifest_does_not_touch_unowned_unpatched_cron_files(
    tmp_path, command
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    user_cron = cron_path(hermes_dir)
    user_cron.parent.mkdir(parents=True)
    user_cron.write_text("# user-owned cron\n", encoding="utf-8")
    user_backup = cron_backup_path(hermes_dir)
    user_backup.write_text("# user-owned cron backup\n", encoding="utf-8")

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert user_cron.read_text(encoding="utf-8") == "# user-owned cron\n"
    assert user_backup.read_text(encoding="utf-8") == "# user-owned cron backup\n"


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    ("factory", "target_path", "managed_backup_path", "ownership_fields"),
    [
        (
            make_exact_019_hermes,
            base_path,
            base_backup_path,
            cli._BASE_MANIFEST_FIELDS,
        ),
        (
            make_cron_hermes,
            cron_path,
            cron_backup_path,
            cli._CRON_MANIFEST_FIELDS,
        ),
    ],
)
@pytest.mark.parametrize(
    "evidence_state",
    [
        "patched_target_ordinary_backup",
        "missing_target_patched_backup",
        "unpatched_target_patched_backup",
    ],
)
def test_v1_manifest_refuses_ambiguous_managed_target_and_backup_evidence(
    tmp_path,
    command,
    factory,
    target_path,
    managed_backup_path,
    ownership_fields,
    evidence_state,
):
    hermes_dir = factory(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    for field in ownership_fields:
        manifest.pop(field)
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    target = target_path(hermes_dir)
    managed_backup = managed_backup_path(hermes_dir)
    patched = target.read_bytes()
    unpatched = managed_backup.read_bytes()
    if evidence_state == "patched_target_ordinary_backup":
        managed_backup.write_text("# ordinary user backup\n", encoding="utf-8")
    elif evidence_state == "missing_target_patched_backup":
        target.unlink()
        managed_backup.write_bytes(patched)
    else:
        target.write_bytes(unpatched)
        managed_backup.write_bytes(patched)
    evidence = {
        path: path.read_bytes() if path.exists() else None
        for path in (
            run_py(hermes_dir),
            backup_path(hermes_dir),
            manifest_path(hermes_dir),
            target,
            managed_backup,
        )
    }

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert {path: path.read_bytes() if path.exists() else None for path in evidence} == evidence


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    ("user_target", "user_backup"),
    [
        (base_path, base_backup_path),
        (cron_path, cron_backup_path),
    ],
)
def test_v1_manifest_allows_missing_target_with_unhookable_user_backup(
    tmp_path, command, user_target, user_backup
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    target = user_target(hermes_dir)
    target.unlink(missing_ok=True)
    backup = user_backup(hermes_dir)
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text("# ordinary non-hookable user backup\n", encoding="utf-8")

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert backup.read_text(encoding="utf-8") == (
        "# ordinary non-hookable user backup\n"
    )


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_commands_refuse_symlinked_manifest_before_reading(tmp_path, command):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = manifest_path(hermes_dir)
    manifest.unlink()
    manifest.symlink_to(hermes_dir / "missing-manifest")

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "manifest" in result.stderr
    assert "symlink" in result.stderr
    assert manifest.is_symlink()
    assert backup_path(hermes_dir).exists()
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in run_py(hermes_dir).read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_commands_refuse_symlinked_exact_base_backup(tmp_path, command):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    base_backup = base_backup_path(hermes_dir)
    original_backup = base_backup.read_bytes()
    symlink_target = hermes_dir / "base-backup-target.py"
    symlink_target.write_bytes(original_backup)
    base_backup.unlink()
    base_backup.symlink_to(symlink_target)
    manifest_before = manifest_path(hermes_dir).read_bytes()
    run_before = run_py(hermes_dir).read_bytes()
    base_before = base_path(hermes_dir).read_bytes()

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "exact Base backup" in result.stderr
    assert "symlink" in result.stderr
    assert base_backup.is_symlink()
    assert symlink_target.read_bytes() == original_backup
    assert manifest_path(hermes_dir).read_bytes() == manifest_before
    assert run_py(hermes_dir).read_bytes() == run_before
    assert base_path(hermes_dir).read_bytes() == base_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    "symlinked_path",
    [
        run_py,
        backup_path,
        cron_path,
        cron_backup_path,
    ],
)
def test_restore_commands_refuse_all_run_and_cron_symlink_evidence(
    tmp_path, command, symlinked_path
):
    hermes_dir = make_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    path = symlinked_path(hermes_dir)
    original = path.read_bytes()
    symlink_target = path.with_name(f"{path.name}.target")
    symlink_target.write_bytes(original)
    path.unlink()
    path.symlink_to(symlink_target)
    manifest_before = manifest_path(hermes_dir).read_bytes()

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert path.is_symlink()
    assert symlink_target.read_bytes() == original
    assert manifest_path(hermes_dir).read_bytes() == manifest_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_commands_refuse_symlinked_hermes_root_before_reading(
    tmp_path, command
):
    real_hermes_dir = copy_hermes(tmp_path / "real")
    assert cli.main(["install", "--hermes-dir", str(real_hermes_dir), "--yes"]) == 0
    hermes_link = tmp_path / "hermes-link"
    hermes_link.symlink_to(real_hermes_dir, target_is_directory=True)
    manifest_before = manifest_path(real_hermes_dir).read_bytes()
    run_before = run_py(real_hermes_dir).read_bytes()
    backup_before = backup_path(real_hermes_dir).read_bytes()

    result = run_cli(command, "--hermes-dir", str(hermes_link), "--yes")

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert hermes_link.is_symlink()
    assert manifest_path(real_hermes_dir).read_bytes() == manifest_before
    assert run_py(real_hermes_dir).read_bytes() == run_before
    assert backup_path(real_hermes_dir).read_bytes() == backup_before


@pytest.mark.parametrize(
    ("factory", "parent_parts", "relative_evidence"),
    [
        (copy_hermes, ("gateway",), ("run.py", BACKUP_NAME)),
        (make_cron_hermes, ("cron",), ("scheduler.py", "scheduler.py.hermes_feishu_card.bak")),
        (make_exact_019_hermes, ("gateway", "platforms"), ("base.py", "base.py.hermes_feishu_card.bak")),
    ],
)
@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_commands_refuse_symlinked_managed_parent_directories(
    tmp_path, command, factory, parent_parts, relative_evidence
):
    hermes_dir = factory(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    parent = hermes_dir.joinpath(*parent_parts)
    real_parent = parent.with_name(f"{parent.name}-real")
    parent.rename(real_parent)
    parent.symlink_to(real_parent, target_is_directory=True)
    evidence_before = {
        name: (real_parent / name).read_bytes() for name in relative_evidence
    }
    manifest_before = manifest_path(hermes_dir).read_bytes()

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert parent.is_symlink()
    assert manifest_path(hermes_dir).read_bytes() == manifest_before
    assert {
        name: (real_parent / name).read_bytes() for name in relative_evidence
    } == evidence_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    "parent_parts",
    [
        (),
        ("gateway",),
        ("cron",),
        ("gateway", "platforms"),
    ],
    ids=["hermes-root", "gateway", "cron", "gateway-platforms"],
)
def test_restore_commands_refuse_managed_parent_swap_after_outer_validation(
    tmp_path, monkeypatch, command, parent_parts
):
    hermes_dir = make_exact_019_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    managed_targets = [
        base_path(hermes_dir),
        run_py(hermes_dir),
        cron_path(hermes_dir),
    ]
    evidence_paths = [
        base_backup_path(hermes_dir),
        backup_path(hermes_dir),
        cron_backup_path(hermes_dir),
        manifest_path(hermes_dir),
    ]
    targets_before = {path: path.read_bytes() for path in managed_targets}
    evidence_before = {path: path.read_bytes() for path in evidence_paths}
    raced_parent = hermes_dir.joinpath(*parent_parts)
    real_parent = raced_parent.with_name(f"{raced_parent.name}-real")
    original_assert = cli._assert_restore_evidence_set_unchanged
    injected = False

    def swap_parent_after_outer_validation(restore_identities):
        nonlocal injected
        original_assert(restore_identities)
        if not injected:
            raced_parent.rename(real_parent)
            raced_parent.symlink_to(real_parent, target_is_directory=True)
            injected = True

    monkeypatch.setattr(
        cli,
        "_assert_restore_evidence_set_unchanged",
        swap_parent_after_outer_validation,
    )

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert raced_parent.is_symlink()
    assert {path: path.read_bytes() for path in managed_targets} == targets_before
    assert {path: path.read_bytes() for path in evidence_paths} == evidence_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_transaction_rolls_back_prior_targets_when_cron_parent_swaps(
    tmp_path, monkeypatch, command
):
    hermes_dir = make_exact_019_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    managed_targets = [
        base_path(hermes_dir),
        run_py(hermes_dir),
        cron_path(hermes_dir),
    ]
    evidence_paths = [
        base_backup_path(hermes_dir),
        backup_path(hermes_dir),
        cron_backup_path(hermes_dir),
        manifest_path(hermes_dir),
    ]
    targets_before = {path: path.read_bytes() for path in managed_targets}
    evidence_before = {path: path.read_bytes() for path in evidence_paths}
    cron_parent = hermes_dir / "cron"
    real_cron_parent = hermes_dir / "cron-real"
    original_atomic_write = cli._atomic_write_text
    injected = False

    def swap_cron_after_run_write(path, contents, **kwargs):
        nonlocal injected
        result = original_atomic_write(path, contents, **kwargs)
        if Path(path) == run_py(hermes_dir) and not injected:
            cron_parent.rename(real_cron_parent)
            cron_parent.symlink_to(real_cron_parent, target_is_directory=True)
            injected = True
        return result

    monkeypatch.setattr(cli, "_atomic_write_text", swap_cron_after_run_write)

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert cron_parent.is_symlink()
    assert {path: path.read_bytes() for path in managed_targets} == targets_before
    assert {path: path.read_bytes() for path in evidence_paths} == evidence_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    "parent_parts",
    [
        (),
        ("gateway",),
        ("cron",),
        ("gateway", "platforms"),
    ],
    ids=["hermes-root", "gateway", "cron", "gateway-platforms"],
)
def test_restore_cleanup_refuses_managed_parent_swap_before_unlink(
    tmp_path, monkeypatch, command, parent_parts
):
    hermes_dir = make_exact_019_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    evidence_paths = [
        base_backup_path(hermes_dir),
        backup_path(hermes_dir),
        cron_backup_path(hermes_dir),
        manifest_path(hermes_dir),
    ]
    evidence_before = {path: path.read_bytes() for path in evidence_paths}
    raced_parent = hermes_dir.joinpath(*parent_parts)
    real_parent = raced_parent.with_name(f"{raced_parent.name}-real")
    original_writer = cli._write_targets_transactionally
    injected = False

    def swap_parent_after_target_transaction(changes, **kwargs):
        nonlocal injected
        result = original_writer(changes, **kwargs)
        raced_parent.rename(real_parent)
        raced_parent.symlink_to(real_parent, target_is_directory=True)
        injected = True
        return result

    monkeypatch.setattr(
        cli, "_write_targets_transactionally", swap_parent_after_target_transaction
    )

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert raced_parent.is_symlink()
    assert {path: path.read_bytes() for path in evidence_paths} == evidence_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_cleanup_unlink_stays_bound_when_parent_swaps_at_syscall(
    tmp_path, monkeypatch, command
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    evidence = backup_path(hermes_dir)
    parent = evidence.parent
    real_parent = parent.with_name("gateway-real")
    original_unlink = cli.os.unlink
    injected = False

    def swap_parent_at_evidence_unlink(path, *args, **kwargs):
        nonlocal injected
        candidate = os.fspath(path)
        if not injected and candidate in {os.fspath(evidence), evidence.name}:
            parent.rename(real_parent)
            parent.mkdir()
            (parent / evidence.name).write_text("USER-TARGET\n", encoding="utf-8")
            injected = True
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli.os, "unlink", swap_parent_at_evidence_unlink)

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result == 0
    assert injected
    assert (parent / evidence.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert not (real_parent / evidence.name).exists()


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_commands_refuse_symlinked_exact_base_target(tmp_path, command):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    target = base_path(hermes_dir)
    original = target.read_bytes()
    symlink_target = target.with_name("base-target.py")
    symlink_target.write_bytes(original)
    target.unlink()
    target.symlink_to(symlink_target)
    manifest_before = manifest_path(hermes_dir).read_bytes()
    backup_before = base_backup_path(hermes_dir).read_bytes()

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "exact Base" in result.stderr
    assert "symlink" in result.stderr
    assert target.is_symlink()
    assert symlink_target.read_bytes() == original
    assert manifest_path(hermes_dir).read_bytes() == manifest_before
    assert base_backup_path(hermes_dir).read_bytes() == backup_before


def test_restore_refuses_leaf_swap_after_manifest_validation(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    backup = backup_path(hermes_dir)
    backup_before = backup.read_bytes()
    symlink_target = backup.with_name("run-backup-target.py")
    symlink_target.write_bytes(backup_before)
    original_read = cli._read_restore_text

    def replace_backup_after_manifest_read(path, expected_identity):
        contents = original_read(path, expected_identity)
        if path == manifest_path(hermes_dir):
            backup.unlink()
            backup.symlink_to(symlink_target)
        return contents

    monkeypatch.setattr(cli, "_read_restore_text", replace_backup_after_manifest_read)

    result = cli._run_restore(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert backup.is_symlink()
    assert symlink_target.read_bytes() == backup_before
    assert manifest_path(hermes_dir).exists()


def test_restore_refuses_backup_swap_before_cleanup(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    backup = backup_path(hermes_dir)
    backup_before = backup.read_bytes()
    symlink_target = backup.with_name("run-backup-target.py")
    symlink_target.write_bytes(backup_before)

    def replace_backup_before_cleanup(_changes, **_kwargs):
        backup.unlink()
        backup.symlink_to(symlink_target)

    monkeypatch.setattr(
        cli, "_write_targets_transactionally", replace_backup_before_cleanup
    )

    result = cli._run_restore(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert backup.is_symlink()
    assert symlink_target.read_bytes() == backup_before
    assert manifest_path(hermes_dir).exists()


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize(
    ("factory", "raced_path", "trigger_path"),
    [
        (make_exact_019_hermes, base_path, None),
        (make_exact_019_hermes, run_py, base_path),
        (make_exact_019_cron_hermes, cron_path, run_py),
    ],
)
def test_restore_transaction_refuses_regular_inode_target_race_and_rolls_back(
    tmp_path, monkeypatch, command, factory, raced_path, trigger_path
):
    hermes_dir = factory(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    target = raced_path(hermes_dir)
    trigger = trigger_path(hermes_dir) if trigger_path is not None else None
    managed_targets = [base_path(hermes_dir), run_py(hermes_dir)]
    if cron_path(hermes_dir).exists():
        managed_targets.append(cron_path(hermes_dir))
    target_before = {path: path.read_bytes() for path in managed_targets}
    evidence_paths = [backup_path(hermes_dir), base_backup_path(hermes_dir)]
    if cron_backup_path(hermes_dir).exists():
        evidence_paths.append(cron_backup_path(hermes_dir))
    evidence_paths.append(manifest_path(hermes_dir))
    evidence_before = {path: path.read_bytes() for path in evidence_paths}
    raced_contents = b"# concurrent regular-inode replacement\n"
    injected = False

    def replace_raced_target():
        nonlocal injected
        target.unlink()
        target.write_bytes(raced_contents)
        injected = True

    if trigger is None:
        original_assert = cli._assert_restore_evidence_set_unchanged

        def replace_after_outer_identity_check(restore_identities):
            original_assert(restore_identities)
            if not injected:
                replace_raced_target()

        monkeypatch.setattr(
            cli,
            "_assert_restore_evidence_set_unchanged",
            replace_after_outer_identity_check,
        )
    else:
        original_atomic_write = cli._atomic_write_text

        def replace_after_prior_target_write(path, contents, **kwargs):
            result = original_atomic_write(path, contents, **kwargs)
            if Path(path) == trigger and not injected:
                replace_raced_target()
            return result

        monkeypatch.setattr(cli, "_atomic_write_text", replace_after_prior_target_write)

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert target.read_bytes() == raced_contents
    assert {
        path: path.read_bytes() for path in managed_targets if path != target
    } == {
        path: contents for path, contents in target_before.items() if path != target
    }
    assert {path: path.read_bytes() for path in evidence_paths} == evidence_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_restore_transaction_refuses_same_inode_base_content_race(
    tmp_path, monkeypatch, command
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    target = base_path(hermes_dir)
    inode_before = target.stat().st_ino
    raced_contents = "# same-inode concurrent Base rewrite\n"
    evidence_paths = [
        backup_path(hermes_dir),
        base_backup_path(hermes_dir),
        manifest_path(hermes_dir),
    ]
    evidence_before = {path: path.read_bytes() for path in evidence_paths}
    original_assert = cli._assert_restore_evidence_set_unchanged
    injected = False

    def rewrite_after_outer_identity_check(restore_identities):
        nonlocal injected
        original_assert(restore_identities)
        if not injected:
            target.write_text(raced_contents, encoding="utf-8")
            injected = True

    monkeypatch.setattr(
        cli,
        "_assert_restore_evidence_set_unchanged",
        rewrite_after_outer_identity_check,
    )

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert target.stat().st_ino == inode_before
    assert target.read_text(encoding="utf-8") == raced_contents
    assert {path: path.read_bytes() for path in evidence_paths} == evidence_before


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize("raced_evidence", [backup_path, manifest_path])
def test_restore_cleanup_refuses_same_inode_evidence_content_race(
    tmp_path, monkeypatch, command, raced_evidence
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    evidence = raced_evidence(hermes_dir)
    inode_before = evidence.stat().st_ino
    raced_contents = "# same-inode concurrent evidence rewrite\n"
    run_backup = backup_path(hermes_dir)
    install_manifest = manifest_path(hermes_dir)
    original_writer = cli._write_targets_transactionally
    injected = False

    def rewrite_after_target_transaction(changes, **kwargs):
        nonlocal injected
        result = original_writer(changes, **kwargs)
        evidence.write_text(raced_contents, encoding="utf-8")
        injected = True
        return result

    monkeypatch.setattr(
        cli, "_write_targets_transactionally", rewrite_after_target_transaction
    )

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert evidence.stat().st_ino == inode_before
    assert evidence.read_text(encoding="utf-8") == raced_contents
    assert run_backup.exists()
    assert install_manifest.exists()


@pytest.mark.parametrize("command", ["restore", "uninstall"])
@pytest.mark.parametrize("backup_present", [True, False])
def test_manifestless_restore_refuses_run_target_drift_at_write_boundary(
    tmp_path, monkeypatch, command, backup_present
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest_path(hermes_dir).unlink()
    run_backup = backup_path(hermes_dir)
    backup_before = run_backup.read_bytes()
    if not backup_present:
        run_backup.unlink()
    target = run_py(hermes_dir)
    raced_contents = b"# concurrent manifestless run rewrite\n"
    original_assert = cli._assert_restore_evidence_set_unchanged
    injected = False

    def drift_after_outer_identity_check(restore_identities):
        nonlocal injected
        original_assert(restore_identities)
        if not injected:
            if backup_present:
                target.unlink()
                target.write_bytes(raced_contents)
            else:
                target.write_bytes(raced_contents)
            injected = True

    monkeypatch.setattr(
        cli,
        "_assert_restore_evidence_set_unchanged",
        drift_after_outer_identity_check,
    )

    runner = cli._run_restore if command == "restore" else cli._run_uninstall
    result = runner(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert target.read_bytes() == raced_contents
    assert not manifest_path(hermes_dir).exists()
    if backup_present:
        assert run_backup.read_bytes() == backup_before
    else:
        assert not run_backup.exists()


def test_019_doctor_state_requires_base_ownership_but_install_can_migrate(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    legacy_manifest = json.loads(
        manifest_path(hermes_dir).read_text(encoding="utf-8")
    )
    assert "base_py" not in legacy_manifest
    (hermes_dir / "VERSION").write_text("v0.19.0\n", encoding="utf-8")
    target = base_path(hermes_dir)
    target.parent.mkdir(parents=True)
    shutil.copy2(EXACT_BASE_FIXTURE, target)
    detection = cli.detect_hermes(hermes_dir)

    diagnosed = cli._diagnose_install_state(detection)

    assert diagnosed["status"] == "incomplete"
    assert "exact Base ownership" in diagnosed["message"]

    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    migrated = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    assert set(cli._BASE_MANIFEST_FIELDS) <= set(migrated)


def test_install_019_rolls_back_base_when_later_gateway_write_fails(
    tmp_path, monkeypatch
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    run_original = run_py(hermes_dir).read_bytes()
    base_original = base_path(hermes_dir).read_bytes()
    original_atomic_write = cli._atomic_write_text
    writes = []

    def fail_gateway_after_base(path, contents, **kwargs):
        writes.append(Path(path))
        if Path(path) == run_py(hermes_dir):
            raise OSError("gateway unavailable")
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_gateway_after_base)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert base_path(hermes_dir) in writes
    assert writes.index(base_path(hermes_dir)) < writes.index(run_py(hermes_dir))
    assert run_py(hermes_dir).read_bytes() == run_original
    assert base_path(hermes_dir).read_bytes() == base_original
    assert not backup_path(hermes_dir).exists()
    assert not base_backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_019_preserves_evidence_when_base_rollback_also_fails(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    base_original = base_path(hermes_dir).read_text(encoding="utf-8")
    original_atomic_write = cli._atomic_write_text
    base_writes = 0

    def fail_gateway_and_base_rollback(path, contents, **kwargs):
        nonlocal base_writes
        path = Path(path)
        if path == base_path(hermes_dir):
            base_writes += 1
            if base_writes > 1:
                raise OSError("base rollback unavailable")
        if path == run_py(hermes_dir):
            raise OSError("gateway unavailable")
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_gateway_and_base_rollback)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    captured = capsys.readouterr()
    assert result != 0
    assert "install rollback failed; manual review required" in captured.err
    assert base_writes == 2
    assert base_path(hermes_dir).read_text(encoding="utf-8") != base_original
    assert patcher.remove_base_patch(
        base_path(hermes_dir).read_text(encoding="utf-8")
    ) == base_original
    assert backup_path(hermes_dir).exists()
    assert base_backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


def test_reinstall_019_refuses_partial_base_manifest_contract(tmp_path):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest.pop("base_backup_sha256")
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"])

    assert result != 0


def test_restore_019_rolls_back_base_when_gateway_restore_fails(
    tmp_path, monkeypatch
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    patched_run = run_py(hermes_dir).read_bytes()
    patched_base = base_path(hermes_dir).read_bytes()
    manifest_before = manifest_path(hermes_dir).read_bytes()
    original_atomic_write = cli._atomic_write_text

    def fail_gateway_restore(path, contents, **kwargs):
        if Path(path) == run_py(hermes_dir):
            raise OSError("gateway restore unavailable")
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_gateway_restore)

    result = cli._run_restore(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert run_py(hermes_dir).read_bytes() == patched_run
    assert base_path(hermes_dir).read_bytes() == patched_base
    assert manifest_path(hermes_dir).read_bytes() == manifest_before
    assert backup_path(hermes_dir).exists()
    assert base_backup_path(hermes_dir).exists()


def test_restore_019_without_manifest_refuses_base_evidence_without_mutation(
    tmp_path,
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest_path(hermes_dir).unlink()
    patched_run = run_py(hermes_dir).read_bytes()
    patched_base = base_path(hermes_dir).read_bytes()
    run_backup = backup_path(hermes_dir).read_bytes()
    base_backup = base_backup_path(hermes_dir).read_bytes()

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "exact Base evidence exists without manifest" in result.stderr
    assert run_py(hermes_dir).read_bytes() == patched_run
    assert base_path(hermes_dir).read_bytes() == patched_base
    assert backup_path(hermes_dir).read_bytes() == run_backup
    assert base_backup_path(hermes_dir).read_bytes() == base_backup
    assert not manifest_path(hermes_dir).exists()


def test_restore_019_without_run_backup_refuses_owned_base_without_mutation(
    tmp_path,
):
    hermes_dir = make_exact_019_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    backup_path(hermes_dir).unlink()
    patched_run = run_py(hermes_dir).read_bytes()
    patched_base = base_path(hermes_dir).read_bytes()
    base_backup = base_backup_path(hermes_dir).read_bytes()
    manifest = manifest_path(hermes_dir).read_bytes()

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "owned exact Base state but run.py backup is missing" in result.stderr
    assert run_py(hermes_dir).read_bytes() == patched_run
    assert base_path(hermes_dir).read_bytes() == patched_base
    assert not backup_path(hermes_dir).exists()
    assert base_backup_path(hermes_dir).read_bytes() == base_backup
    assert manifest_path(hermes_dir).read_bytes() == manifest


def test_restore_without_manifest_refuses_cron_evidence_without_mutation(tmp_path):
    hermes_dir = make_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest_path(hermes_dir).unlink()
    patched_run = run_py(hermes_dir).read_bytes()
    patched_cron = cron_path(hermes_dir).read_bytes()
    run_backup = backup_path(hermes_dir).read_bytes()
    cron_backup = cron_backup_path(hermes_dir).read_bytes()

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "cron evidence exists without manifest" in result.stderr
    assert run_py(hermes_dir).read_bytes() == patched_run
    assert cron_path(hermes_dir).read_bytes() == patched_cron
    assert backup_path(hermes_dir).read_bytes() == run_backup
    assert cron_backup_path(hermes_dir).read_bytes() == cron_backup
    assert not manifest_path(hermes_dir).exists()


def test_restore_without_run_backup_refuses_owned_cron_without_mutation(tmp_path):
    hermes_dir = make_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    backup_path(hermes_dir).unlink()
    patched_run = run_py(hermes_dir).read_bytes()
    patched_cron = cron_path(hermes_dir).read_bytes()
    cron_backup = cron_backup_path(hermes_dir).read_bytes()
    manifest = manifest_path(hermes_dir).read_bytes()

    result = run_cli("restore", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "owned cron state but run.py backup is missing" in result.stderr
    assert run_py(hermes_dir).read_bytes() == patched_run
    assert cron_path(hermes_dir).read_bytes() == patched_cron
    assert not backup_path(hermes_dir).exists()
    assert cron_backup_path(hermes_dir).read_bytes() == cron_backup
    assert manifest_path(hermes_dir).read_bytes() == manifest


@pytest.mark.parametrize("command", ["install", "repair", "restore", "uninstall"])
@pytest.mark.parametrize("future_version", [999, "999"])
def test_mutations_refuse_future_or_invalid_manifest_without_touching_unknown_targets(
    tmp_path, command, future_version
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    future_target = hermes_dir / "gateway" / "future-owned-target.py"
    future_target.write_text("FUTURE_OWNERSHIP = True\n", encoding="utf-8")
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = future_version
    manifest["future_target"] = "gateway/future-owned-target.py"
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = {
        path.relative_to(hermes_dir).as_posix(): path.read_bytes()
        for path in hermes_dir.rglob("*")
        if path.is_file()
    }

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    after = {
        path.relative_to(hermes_dir).as_posix(): path.read_bytes()
        for path in hermes_dir.rglob("*")
        if path.is_file()
    }
    assert result.returncode != 0
    assert "newer installer required" in result.stderr
    assert after == before


@pytest.mark.parametrize("command", ["install", "repair", "restore", "uninstall"])
def test_mutations_refuse_backup_only_cron_manifest_without_mutation(
    tmp_path, command
):
    hermes_dir = make_cron_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    cron_backup = manifest["cron_backup"]
    for field in (
        "cron_py",
        "cron_patched_sha256",
        "cron_backup",
        "cron_backup_sha256",
    ):
        manifest.pop(field)
    manifest["cron_backup"] = cron_backup
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = {
        path.relative_to(hermes_dir).as_posix(): path.read_bytes()
        for path in hermes_dir.rglob("*")
        if path.is_file()
    }

    result = run_cli(command, "--hermes-dir", str(hermes_dir), "--yes")

    after = {
        path.relative_to(hermes_dir).as_posix(): path.read_bytes()
        for path in hermes_dir.rglob("*")
        if path.is_file()
    }
    assert result.returncode != 0
    assert "cron ownership fields are incomplete" in result.stderr
    assert after == before


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
    original_atomic_write = cli._atomic_write_text

    def fail_manifest(path, contents, **kwargs):
        if Path(path) == manifest_path(hermes_dir):
            raise OSError("manifest unavailable")
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_manifest)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    current = run_py(hermes_dir).read_text(encoding="utf-8")
    assert current == original
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" not in current
    assert not manifest_path(hermes_dir).exists()
    assert not backup_path(hermes_dir).exists()


def test_install_posix_commits_all_mutations_in_one_transaction(
    tmp_path, monkeypatch
):
    if not cli._cli_dirfd_binding_supported():
        pytest.skip("requires POSIX dirfd transaction support")
    hermes_dir = copy_hermes(tmp_path)
    original_writer = cli._write_targets_transactionally
    calls = []

    def record_transaction(changes, **kwargs):
        calls.append(([path for path, _contents in changes], kwargs))
        return original_writer(changes, **kwargs)

    monkeypatch.setattr(cli, "_write_targets_transactionally", record_transaction)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result == 0
    assert len(calls) == 1
    assert calls[0][0] == [
        backup_path(hermes_dir),
        run_py(hermes_dir),
        manifest_path(hermes_dir),
    ]
    assert calls[0][1]["expected_identities"]
    assert calls[0][1]["expected_directories"]


def test_install_transaction_refuses_parent_swap_at_entry(tmp_path, monkeypatch):
    if not cli._cli_dirfd_binding_supported():
        pytest.skip("requires POSIX dirfd transaction support")
    hermes_dir = copy_hermes(tmp_path)
    parent = run_py(hermes_dir).parent
    real_parent = parent.with_name("gateway-real")
    original_writer = cli._write_targets_transactionally
    injected = False

    def swap_parent_before_transaction(changes, **kwargs):
        nonlocal injected
        parent.rename(real_parent)
        parent.mkdir()
        (parent / "run.py").write_text("USER-TARGET\n", encoding="utf-8")
        injected = True
        return original_writer(changes, **kwargs)

    monkeypatch.setattr(
        cli, "_write_targets_transactionally", swap_parent_before_transaction
    )

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert (parent / "run.py").read_text(encoding="utf-8") == "USER-TARGET\n"
    assert not (parent / BACKUP_NAME).exists()
    assert not manifest_path(hermes_dir).exists()
    assert (real_parent / "run.py").exists()


def test_install_snapshot_precedes_original_read_parent_swap(tmp_path, monkeypatch):
    if not cli._cli_dirfd_binding_supported():
        pytest.skip("requires POSIX dirfd transaction support")
    hermes_dir = copy_hermes(tmp_path)
    target = run_py(hermes_dir)
    parent = target.parent
    real_parent = parent.with_name("gateway-real")
    original_read = cli._read_restore_text
    injected = False

    def swap_parent_after_original_read(path, expected_snapshot):
        nonlocal injected
        contents = original_read(path, expected_snapshot)
        if path == target and not injected:
            parent.rename(real_parent)
            parent.mkdir()
            (parent / target.name).write_text(contents, encoding="utf-8")
            injected = True
        return contents

    monkeypatch.setattr(cli, "_read_restore_text", swap_parent_after_original_read)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert (parent / target.name).read_text(encoding="utf-8") == (
        real_parent / target.name
    ).read_text(encoding="utf-8")
    assert not (parent / BACKUP_NAME).exists()
    assert not manifest_path(hermes_dir).exists()


def test_install_rollback_unlink_stays_bound_when_parent_swaps_at_syscall(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    evidence = backup_path(hermes_dir)
    parent = evidence.parent
    real_parent = parent.with_name("gateway-real")
    original_atomic_write = cli._atomic_write_text
    original_unlink = cli.os.unlink
    injected = False

    def fail_run_write(path, contents, **kwargs):
        if path == run_py(hermes_dir):
            raise OSError("run.py write failed")
        return original_atomic_write(path, contents, **kwargs)

    def swap_parent_at_evidence_unlink(path, *args, **kwargs):
        nonlocal injected
        candidate = os.fspath(path)
        if not injected and candidate in {os.fspath(evidence), evidence.name}:
            parent.rename(real_parent)
            parent.mkdir()
            (parent / evidence.name).write_text("USER-TARGET\n", encoding="utf-8")
            injected = True
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text", fail_run_write)
    monkeypatch.setattr(cli.os, "unlink", swap_parent_at_evidence_unlink)

    result = cli._run_install(Namespace(hermes_dir=str(hermes_dir), yes=True))

    assert result != 0
    assert injected
    assert (parent / evidence.name).read_text(encoding="utf-8") == "USER-TARGET\n"
    assert not (real_parent / evidence.name).exists()


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


def test_reinstall_migrates_manifestless_legacy_owned_patch(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    assert legacy_patched != current_patched
    assert patcher.remove_patch_lenient(legacy_patched) == backup_path(
        hermes_dir
    ).read_text(encoding="utf-8")
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "manifest: rebuilt" in result.stdout
    assert run_py(hermes_dir).read_text(encoding="utf-8") == current_patched
    assert manifest_path(hermes_dir).exists()


def test_reinstall_migrates_manifestless_legacy_patch_without_dirfd_support(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()
    capsys.readouterr()
    monkeypatch.setattr(cli, "_cli_dirfd_binding_supported", lambda: False)

    result = cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"])

    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert "manifest: rebuilt" in captured.out
    assert run_py(hermes_dir).read_text(encoding="utf-8") == current_patched
    assert manifest_path(hermes_dir).exists()


def test_reinstall_refuses_manifestless_legacy_patch_with_outside_edit(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    edited = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    edited += "\nUSER_EDIT = True\n"
    run_py(hermes_dir).write_text(edited, encoding="utf-8")
    manifest_path(hermes_dir).unlink()
    original_backup = backup_path(hermes_dir).read_text(encoding="utf-8")

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert run_py(hermes_dir).read_text(encoding="utf-8") == edited
    assert backup_path(hermes_dir).read_text(encoding="utf-8") == original_backup
    assert not manifest_path(hermes_dir).exists()


def test_reinstall_no_repair_refuses_manifestless_legacy_owned_patch(tmp_path):
    hermes_dir = copy_hermes(tmp_path)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()

    result = run_cli(
        "install",
        "--no-repair",
        "--hermes-dir",
        str(hermes_dir),
        "--yes",
    )

    assert result.returncode != 0
    assert run_py(hermes_dir).read_text(encoding="utf-8") == legacy_patched
    assert not manifest_path(hermes_dir).exists()


def test_reinstall_refuses_manifestless_legacy_patch_with_missing_cron_target(
    tmp_path,
):
    hermes_dir = copy_hermes(tmp_path)
    cron_target = hermes_dir / "cron" / "scheduler.py"
    cron_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CRON_FIXTURE, cron_target)

    install_result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install_result.returncode == 0, install_result.stderr
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()
    cron_backup = cli._backup_path(cron_target)
    assert cron_backup.exists()
    cron_target.unlink()

    result = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert run_py(hermes_dir).read_text(encoding="utf-8") == legacy_patched
    assert cron_backup.exists()
    assert not cron_target.exists()
    assert not manifest_path(hermes_dir).exists()


def test_reinstall_refuses_manifestless_legacy_patch_with_missing_base_target(
    tmp_path,
):
    hermes_dir = make_exact_019_hermes(tmp_path)

    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()
    exact_base = base_path(hermes_dir)
    exact_base_backup = base_backup_path(hermes_dir)
    assert exact_base_backup.exists()
    exact_base.unlink()

    result = cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"])

    assert result != 0
    assert run_py(hermes_dir).read_text(encoding="utf-8") == legacy_patched
    assert exact_base_backup.exists()
    assert not exact_base.exists()
    assert not manifest_path(hermes_dir).exists()


def test_manifestless_portable_install_refuses_concurrent_gateway_edit(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()
    capsys.readouterr()
    monkeypatch.setattr(cli, "_cli_dirfd_binding_supported", lambda: False)
    original_atomic_write = cli._atomic_write_text_portable
    injected = False

    def edit_before_write(path, contents, **kwargs):
        nonlocal injected
        if path == run_py(hermes_dir) and not injected:
            injected = True
            path.write_text(legacy_patched + "\nUSER_EDIT = True\n", encoding="utf-8")
        return original_atomic_write(path, contents, **kwargs)

    monkeypatch.setattr(cli, "_atomic_write_text_portable", edit_before_write)

    result = cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"])

    captured = capsys.readouterr()
    assert result != 0, captured.out
    assert "USER_EDIT = True" in run_py(hermes_dir).read_text(encoding="utf-8")
    assert not manifest_path(hermes_dir).exists()


def test_manifestless_portable_rollback_preserves_concurrent_gateway_edit(
    tmp_path, monkeypatch, capsys
):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    current_patched = run_py(hermes_dir).read_text(encoding="utf-8")
    legacy_patched = current_patched.replace(
        "_hfc_stable_tool_callbacks = False\n",
        "_hfc_stable_tool_callbacks = False  # v4.0.x generated body\n",
        1,
    )
    run_py(hermes_dir).write_text(legacy_patched, encoding="utf-8")
    manifest_path(hermes_dir).unlink()
    capsys.readouterr()
    monkeypatch.setattr(cli, "_cli_dirfd_binding_supported", lambda: False)
    original_atomic_write = cli._atomic_write_text_portable
    gateway_written = False

    def fail_after_gateway_write(path, contents, **kwargs):
        nonlocal gateway_written
        if path == manifest_path(hermes_dir) and gateway_written:
            run_py(hermes_dir).write_text(
                current_patched + "\nCONCURRENT_EDIT = True\n",
                encoding="utf-8",
            )
            raise OSError("simulated manifest write failure")
        result = original_atomic_write(path, contents, **kwargs)
        if path == run_py(hermes_dir):
            gateway_written = True
        return result

    monkeypatch.setattr(cli, "_atomic_write_text_portable", fail_after_gateway_write)

    result = cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"])

    captured = capsys.readouterr()
    assert result != 0, captured.out
    assert "CONCURRENT_EDIT = True" in run_py(hermes_dir).read_text(encoding="utf-8")
    assert backup_path(hermes_dir).exists()
    assert not manifest_path(hermes_dir).exists()


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


@pytest.mark.parametrize(
    ("factory", "managed_backup", "ownership_fields"),
    [
        (make_exact_019_hermes, base_backup_path, cli._BASE_MANIFEST_FIELDS),
        (make_cron_hermes, cron_backup_path, cli._CRON_MANIFEST_FIELDS),
    ],
)
def test_repair_refuses_non_executable_empty_plan_for_owned_incomplete_state(
    tmp_path, factory, managed_backup, ownership_fields
):
    hermes_dir = factory(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    manifest = json.loads(manifest_path(hermes_dir).read_text(encoding="utf-8"))
    manifest["manifest_version"] = 1
    for field in ownership_fields:
        manifest.pop(field)
    manifest_path(hermes_dir).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    managed_backup(hermes_dir).unlink()
    run_py(hermes_dir).write_bytes(backup_path(hermes_dir).read_bytes())
    evidence = {
        path: path.read_bytes() if path.exists() else None
        for path in (
            run_py(hermes_dir),
            backup_path(hermes_dir),
            manifest_path(hermes_dir),
            base_path(hermes_dir),
            base_backup_path(hermes_dir),
            cron_path(hermes_dir),
            cron_backup_path(hermes_dir),
        )
    }

    result = run_cli("repair", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "repair ok" not in result.stdout
    assert "repair: no changes" not in result.stdout
    assert {
        path: path.read_bytes() if path.exists() else None for path in evidence
    } == evidence


def test_repair_healthy_installed_state_is_noop(tmp_path):
    hermes_dir = copy_hermes(tmp_path)
    assert cli.main(["install", "--hermes-dir", str(hermes_dir), "--yes"]) == 0
    evidence = {
        path: path.read_bytes()
        for path in (
            run_py(hermes_dir),
            backup_path(hermes_dir),
            manifest_path(hermes_dir),
        )
    }

    result = run_cli("repair", "--hermes-dir", str(hermes_dir), "--yes")

    assert result.returncode == 0, result.stderr
    assert "repair: no changes" in result.stdout
    assert "repair ok" in result.stdout
    assert {path: path.read_bytes() for path in evidence} == evidence


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
    stub_setup_runtime(monkeypatch, hermes_dir)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_repair")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-repair-secret")
    monkeypatch.setattr(cli, "start_sidecar", lambda *_args, **_kwargs: "started")
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
    stub_setup_runtime(monkeypatch, hermes_dir)
    config_path = tmp_path / "generated" / "feishu-card.yaml"
    monkeypatch.setenv("FEISHU_APP_ID", "cli_setup_auto_repair")
    monkeypatch.setenv("FEISHU_APP_SECRET", "setup-auto-repair-secret")
    started = []
    monkeypatch.setattr(
        cli,
        "start_sidecar",
        lambda *_args, **_kwargs: started.append(True) or "started",
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

    original_atomic_write = cli._atomic_write_text

    def fail_atomic_write(path, contents, **kwargs):
        if Path(path) == manifest_path(hermes_dir):
            raise OSError("atomic manifest write failed")
        return original_atomic_write(path, contents, **kwargs)

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
