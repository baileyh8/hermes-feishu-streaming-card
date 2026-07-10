from __future__ import annotations

import errno
import json
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install import recovery
from hermes_feishu_card.install.patcher import (
    CRON_PATCH_END,
    apply_cron_patch,
    apply_patch,
)
from hermes_feishu_card.install.recovery import (
    RecoveryFinding,
    RecoveryRefused,
    _classify_evidence,
    _read_evidence,
    execute_recovery,
    plan_recovery,
    sanitize_recovery_plan,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
CRON_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "hermes_cron"
    / "scheduler.py"
)


@pytest.fixture
def installed_state(tmp_path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root)
    (root / "cron").mkdir(exist_ok=True)
    shutil.copy2(CRON_FIXTURE, root / "cron" / "scheduler.py")
    detection = detect_hermes(root)
    original = detection.run_py.read_text(encoding="utf-8")
    patched = apply_patch(original, strategy=detection.hook_strategy)
    backup = detection.run_py.with_name("run.py.hermes_feishu_card.bak")
    backup.write_text(original, encoding="utf-8")
    detection.run_py.write_text(patched, encoding="utf-8")
    assert detection.cron_py is not None
    cron_original = detection.cron_py.read_text(encoding="utf-8")
    cron_patched = apply_cron_patch(cron_original)
    cron_backup = detection.cron_py.with_name(
        "scheduler.py.hermes_feishu_card.bak"
    )
    cron_backup.write_text(cron_original, encoding="utf-8")
    detection.cron_py.write_text(cron_patched, encoding="utf-8")
    manifest_path = root / ".hermes_feishu_card_manifest"
    manifest_path.write_text(
        json.dumps(
            {
                "run_py": "gateway/run.py",
                "patched_sha256": sha256(patched.encode("utf-8")).hexdigest(),
                "backup": "gateway/run.py.hermes_feishu_card.bak",
                "backup_sha256": sha256(original.encode("utf-8")).hexdigest(),
                "cron_py": "cron/scheduler.py",
                "cron_patched_sha256": sha256(
                    cron_patched.encode("utf-8")
                ).hexdigest(),
                "cron_backup": "cron/scheduler.py.hermes_feishu_card.bak",
                "cron_backup_sha256": sha256(
                    cron_original.encode("utf-8")
                ).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return detection, original, patched, manifest_path


def _corrupt_gateway_completion(installed_state):
    detection, _original, patched, manifest_path = installed_state
    corrupt = patched.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    detection.run_py.write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    return detection, corrupt, manifest_path


def _wait_for_path(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def test_execute_recovery_replans_and_refuses_stale_fingerprint(installed_state):
    detection, _corrupt, _manifest_path = _corrupt_gateway_completion(
        installed_state
    )
    plan = plan_recovery(detection)
    detection.run_py.write_text(
        detection.run_py.read_text(encoding="utf-8") + "\nUSER_EDIT = True\n",
        encoding="utf-8",
    )

    with pytest.raises(RecoveryRefused, match="evidence changed"):
        execute_recovery(detection, expected_fingerprint=plan.fingerprint)


def test_execute_recovery_replaces_once_and_keeps_three_quarantines(
    installed_state,
):
    detection, _original, patched, manifest_path = installed_state
    for _ in range(4):
        corrupt = patched.replace(
            "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", ""
        )
        detection.run_py.write_text(corrupt, encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )

        result = execute_recovery(detection)

        assert result.status == "repaired"
        assert result.actions == (
            "run.py: restored verified backup",
            "run.py: reapplied current hook",
        )
        assert result.quarantine_name
        assert detection.run_py.read_text(encoding="utf-8") == patched
    assert len(list(detection.run_py.parent.glob("run.py.hfc-corrupt-*"))) == 3


def test_execute_recovery_same_process_is_exactly_once(installed_state):
    detection, _corrupt, _manifest_path = _corrupt_gateway_completion(
        installed_state
    )
    expected = plan_recovery(detection).fingerprint
    barrier = threading.Barrier(3)
    outcomes = []

    def run():
        barrier.wait()
        try:
            outcomes.append(execute_recovery(detection, expected).status)
        except RecoveryRefused as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["recovery evidence changed; rerun diagnosis", "repaired"]
    assert len(list(detection.run_py.parent.glob("run.py.hfc-corrupt-*"))) == 1


def test_execute_recovery_subprocesses_are_exactly_once(installed_state):
    detection, _corrupt, _manifest_path = _corrupt_gateway_completion(
        installed_state
    )
    expected = plan_recovery(detection).fingerprint
    ready_paths = [detection.root / f"ready-{index}" for index in range(2)]
    start_path = detection.root / "start"
    script = """
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.recovery import RecoveryRefused, execute_recovery
from pathlib import Path
import sys
import time
ready = Path(sys.argv[3])
start = Path(sys.argv[4])
ready.write_text("ready", encoding="utf-8")
while not start.exists():
    time.sleep(0.01)
try:
    print(execute_recovery(detect_hermes(sys.argv[1]), sys.argv[2]).status)
except RecoveryRefused as exc:
    print(str(exc))
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(detection.root),
                expected,
                str(ready_path),
                str(start_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for ready_path in ready_paths
    ]
    for ready_path in ready_paths:
        _wait_for_path(ready_path)
    start_path.write_text("start", encoding="utf-8")
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        outcomes.append(stdout.strip())

    assert sorted(outcomes) == ["recovery evidence changed; rerun diagnosis", "repaired"]
    assert len(list(detection.run_py.parent.glob("run.py.hfc-corrupt-*"))) == 1


def test_root_lock_has_deterministic_cross_process_contention_timeout(tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    held_path = tmp_path / "held"
    release_path = tmp_path / "release"
    holder_script = """
from hermes_feishu_card.install.recovery import _root_lock
from pathlib import Path
import sys
import time
root, held, release = map(Path, sys.argv[1:])
with _root_lock(root):
    held.write_text("held", encoding="utf-8")
    while not release.exists():
        time.sleep(0.01)
"""
    contender_script = """
from hermes_feishu_card.install import recovery
from pathlib import Path
import sys
import time
recovery._LOCK_TIMEOUT_SECONDS = float(sys.argv[2])
started = time.monotonic()
try:
    with recovery._root_lock(Path(sys.argv[1])):
        print("unexpectedly acquired")
except recovery.RecoveryRefused as exc:
    print(f"{exc}|{time.monotonic() - started:.3f}")
"""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            holder_script,
            str(root),
            str(held_path),
            str(release_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_path(held_path)
        contender = subprocess.run(
            [sys.executable, "-c", contender_script, str(root), "0.2"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        assert contender.returncode == 0, contender.stderr
        message, elapsed_text = contender.stdout.strip().split("|")
        assert message == "timed out waiting for recovery lock"
        assert 0.15 <= float(elapsed_text) < 1.0
    finally:
        release_path.write_text("release", encoding="utf-8")
        stdout, stderr = holder.communicate(timeout=3)
        assert holder.returncode == 0, stdout + stderr


def test_root_lock_uses_one_deadline_for_local_and_os_waits(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    root.mkdir()
    clock = [100.0]

    class DelayedProcessLock:
        def __init__(self):
            self.timeouts = []
            self.released = False

        def acquire(self, timeout):
            self.timeouts.append(timeout)
            clock[0] += 0.75
            return True

        def release(self):
            self.released = True

    process_lock = DelayedProcessLock()
    root_key = str(root.resolve())
    monkeypatch.setitem(recovery._PROCESS_LOCKS, root_key, process_lock)
    monkeypatch.setattr(recovery, "_LOCK_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])
    os_deadlines = []

    def exhaust_os_budget(_handle, deadline):
        os_deadlines.append(deadline)
        clock[0] = deadline
        raise RecoveryRefused("timed out waiting for recovery lock")

    monkeypatch.setattr(recovery, "_acquire_os_lock", exhaust_os_budget)

    with pytest.raises(RecoveryRefused, match="timed out waiting"):
        with recovery._root_lock(root):
            pass

    assert process_lock.timeouts == [1.0]
    assert process_lock.released is True
    assert os_deadlines == [101.0]
    assert clock[0] == 101.0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX errno semantics")
def test_root_lock_propagates_permanent_posix_lock_error(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    root.mkdir()
    calls = []

    import fcntl

    def fail_permanently(_descriptor, operation):
        calls.append(operation)
        raise OSError(errno.EBADF, "bad lock descriptor")

    monkeypatch.setattr(recovery, "_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(fcntl, "flock", fail_permanently)

    with pytest.raises(OSError) as exc_info:
        with recovery._root_lock(root):
            pass

    assert exc_info.value.errno == errno.EBADF
    assert len(calls) == 1


def test_acquire_os_lock_retries_windows_contention(monkeypatch, tmp_path):
    calls = []
    sleeps = []

    def locking(_descriptor, mode, length):
        calls.append((mode, length))
        if len(calls) == 1:
            raise OSError(errno.EACCES, "lock busy")

    fake_msvcrt = SimpleNamespace(LK_NBLCK=7, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(recovery.os, "name", "nt")
    monkeypatch.setattr(recovery.time, "sleep", sleeps.append)
    lock_path = tmp_path / "lock"

    with lock_path.open("w+b") as handle:
        recovery._acquire_os_lock(handle, time.monotonic() + 1.0)

    assert calls == [(7, 1), (7, 1)]
    assert sleeps == [0.05]
    assert lock_path.read_bytes() == b"\0"


def test_execute_recovery_rolls_back_when_atomic_replace_fails(
    installed_state, monkeypatch
):
    detection, corrupt, manifest_path = _corrupt_gateway_completion(installed_state)
    original_manifest = manifest_path.read_text(encoding="utf-8")
    real_replace = recovery._atomic_replace

    def fail_manifest_replace(staged, target):
        if target == manifest_path:
            raise OSError("injected manifest replace failure")
        return real_replace(staged, target)

    monkeypatch.setattr(recovery, "_atomic_replace", fail_manifest_replace)

    with pytest.raises(OSError, match="injected manifest replace failure"):
        execute_recovery(detection)

    assert detection.run_py.read_text(encoding="utf-8") == corrupt
    assert manifest_path.read_text(encoding="utf-8") == original_manifest
    assert not list(detection.run_py.parent.glob("run.py.hfc-corrupt-*"))


def test_execute_recovery_rolls_back_when_gateway_replace_fails(
    installed_state, monkeypatch
):
    detection, corrupt, manifest_path = _corrupt_gateway_completion(installed_state)
    original_manifest = manifest_path.read_text(encoding="utf-8")
    real_replace = recovery._atomic_replace

    def fail_gateway_replace(staged, target):
        if target == detection.run_py:
            raise OSError("injected gateway replace failure")
        return real_replace(staged, target)

    monkeypatch.setattr(recovery, "_atomic_replace", fail_gateway_replace)

    with pytest.raises(OSError, match="injected gateway replace failure"):
        execute_recovery(detection)

    assert detection.run_py.read_text(encoding="utf-8") == corrupt
    assert manifest_path.read_text(encoding="utf-8") == original_manifest
    assert not list(detection.run_py.parent.glob("run.py.hfc-corrupt-*"))


def test_execute_recovery_does_not_mutate_when_staging_manifest_fails(
    installed_state, monkeypatch
):
    detection, corrupt, manifest_path = _corrupt_gateway_completion(installed_state)
    original_manifest = manifest_path.read_text(encoding="utf-8")
    real_stage = recovery._stage_text

    def fail_manifest_stage(target, contents):
        if target == manifest_path:
            raise OSError("injected manifest stage failure")
        return real_stage(target, contents)

    monkeypatch.setattr(recovery, "_stage_text", fail_manifest_stage)

    with pytest.raises(OSError, match="injected manifest stage failure"):
        execute_recovery(detection)

    assert detection.run_py.read_text(encoding="utf-8") == corrupt
    assert manifest_path.read_text(encoding="utf-8") == original_manifest
    assert not list(detection.run_py.parent.glob("run.py.hfc-corrupt-*"))


def test_execute_recovery_repairs_cron_in_plan_order(installed_state):
    detection, _original, _patched, manifest_path = installed_state
    assert detection.cron_py is not None
    cron_patched = detection.cron_py.read_text(encoding="utf-8")
    corrupt = cron_patched.replace(f"{CRON_PATCH_END}\n", "")
    detection.cron_py.write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cron_patched_sha256"] = sha256(
        corrupt.encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = execute_recovery(detection)

    assert result.status == "repaired"
    assert result.actions == (
        "cron scheduler: restored verified backup",
        "cron scheduler: reapplied current hook",
    )
    assert detection.cron_py.read_text(encoding="utf-8") == cron_patched


def test_execute_recovery_rebuilds_cron_backup_and_manifest(installed_state):
    detection, _original, _patched, manifest_path = installed_state
    assert detection.cron_py is not None
    cron_backup = detection.cron_py.with_name(
        "scheduler.py.hermes_feishu_card.bak"
    )
    expected_backup = cron_backup.read_text(encoding="utf-8")
    cron_backup.unlink()

    result = execute_recovery(detection)

    assert result.status == "repaired"
    assert result.actions == ("cron backup: recreated", "manifest: rebuilt")
    assert cron_backup.read_text(encoding="utf-8") == expected_backup
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["cron_backup_sha256"] == sha256(
        expected_backup.encode("utf-8")
    ).hexdigest()


def test_execute_recovery_clears_verified_stale_state_in_plan_order(
    installed_state,
):
    detection, original, _patched, manifest_path = installed_state
    assert detection.cron_py is not None
    cron_backup = detection.cron_py.with_name(
        "scheduler.py.hermes_feishu_card.bak"
    )
    cron_original = cron_backup.read_text(encoding="utf-8")
    gateway_backup = detection.run_py.with_name(
        "run.py.hermes_feishu_card.bak"
    )
    detection.run_py.write_text(original, encoding="utf-8")

    result = execute_recovery(detection)

    assert result.status == "cleared"
    assert result.actions == (
        "cron scheduler: restored verified backup",
        "install state: cleared stale unpatched state",
    )
    assert detection.run_py.read_text(encoding="utf-8") == original
    assert detection.cron_py.read_text(encoding="utf-8") == cron_original
    assert not gateway_backup.exists()
    assert not cron_backup.exists()
    assert not manifest_path.exists()


def test_execute_recovery_commits_cron_restore_before_deleting_evidence_and_rolls_back(
    installed_state, monkeypatch
):
    detection, original, _patched, manifest_path = installed_state
    assert detection.cron_py is not None
    gateway_backup = detection.run_py.with_name(
        "run.py.hermes_feishu_card.bak"
    )
    cron_backup = detection.cron_py.with_name(
        "scheduler.py.hermes_feishu_card.bak"
    )
    before = {
        detection.run_py: original,
        detection.cron_py: detection.cron_py.read_text(encoding="utf-8"),
        gateway_backup: gateway_backup.read_text(encoding="utf-8"),
        cron_backup: cron_backup.read_text(encoding="utf-8"),
        manifest_path: manifest_path.read_text(encoding="utf-8"),
    }
    detection.run_py.write_text(original, encoding="utf-8")
    operations = []
    tracked_deletions = {gateway_backup, cron_backup, manifest_path}
    real_atomic_replace = recovery._atomic_replace
    real_unlink = Path.unlink

    def record_replace(staged, target):
        if target == detection.cron_py:
            operations.append(("replace", target.name))
        return real_atomic_replace(staged, target)

    def record_unlink(path, *args, **kwargs):
        if path in tracked_deletions:
            operations.append(("unlink", path.name))
            if path == manifest_path:
                raise OSError("injected evidence deletion failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(recovery, "_atomic_replace", record_replace)
    monkeypatch.setattr(Path, "unlink", record_unlink)

    with pytest.raises(OSError, match="injected evidence deletion failure"):
        execute_recovery(detection)

    assert operations == [
        ("replace", "scheduler.py"),
        ("unlink", "run.py.hermes_feishu_card.bak"),
        ("unlink", "scheduler.py.hermes_feishu_card.bak"),
        ("unlink", ".hermes_feishu_card_manifest"),
    ]
    for path, contents in before.items():
        assert path.read_text(encoding="utf-8") == contents


def test_execute_recovery_refuses_already_repaired_state(installed_state):
    detection, _corrupt, _manifest_path = _corrupt_gateway_completion(
        installed_state
    )
    execute_recovery(detection)
    repaired = detection.run_py.read_text(encoding="utf-8")

    with pytest.raises(RecoveryRefused, match="No recovery is required"):
        execute_recovery(detection)

    assert detection.run_py.read_text(encoding="utf-8") == repaired


def test_plan_recovery_allows_manifest_owned_corrupt_completion_markers(
    installed_state,
):
    detection, _original, patched, manifest_path = installed_state
    corrupt = patched.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    detection.run_py.write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    plan = plan_recovery(detection)

    assert plan.state == "corrupt_owned"
    assert plan.executable is True
    assert plan.actions == ("restore_verified_backup", "reapply_current_hook")


def test_plan_recovery_refuses_corrupt_markers_after_user_edit(installed_state):
    detection, _original, patched, _manifest_path = installed_state
    detection.run_py.write_text(
        patched.replace("import asyncio", "import asyncio\nUSER_EDIT = True").replace(
            "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", ""
        ),
        encoding="utf-8",
    )

    plan = plan_recovery(detection)

    assert plan.executable is False
    assert any(item.code == "current_hash_mismatch" for item in plan.findings)


def test_plan_recovery_reports_healthy_installed_state(installed_state):
    detection, _original, _patched, _manifest_path = installed_state

    plan = plan_recovery(detection)

    assert plan.state == "installed"
    assert plan.executable is False
    assert plan.actions == ()
    assert not any(item.severity == "error" for item in plan.findings)


def test_plan_recovery_reports_healthy_clean_state(tmp_path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root)
    detection = detect_hermes(root)

    plan = plan_recovery(detection)

    assert plan.state == "clean"
    assert plan.executable is False
    assert plan.actions == ()
    assert not any(item.severity == "error" for item in plan.findings)


def test_classify_evidence_treats_any_marker_validation_error_as_corrupt(
    installed_state,
):
    detection, _original, _patched, _manifest_path = installed_state
    evidence = replace(
        _read_evidence(detection),
        marker_error="corrupt completion patch markers",
    )

    classification = _classify_evidence(detection, evidence)

    assert classification.state == "corrupt_owned"
    assert classification.executable is True


def test_plan_recovery_preserves_verified_stale_unpatched_state_without_cron(
    installed_state,
):
    detection, original, _patched, manifest_path = installed_state
    assert detection.cron_py is not None
    detection.cron_py.unlink()
    detection.cron_py.with_name("scheduler.py.hermes_feishu_card.bak").unlink()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in (
        "cron_py",
        "cron_patched_sha256",
        "cron_backup",
        "cron_backup_sha256",
    ):
        manifest.pop(key)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    detection.run_py.write_text(original, encoding="utf-8")

    plan = plan_recovery(detection)

    assert plan.state == "stale_unpatched"
    assert plan.executable is True
    assert plan.actions == ("clear_stale_install_state",)


def test_plan_recovery_restores_installed_cron_before_clearing_gateway_stale_state(
    installed_state,
):
    detection, original, _patched, _manifest_path = installed_state
    detection.run_py.write_text(original, encoding="utf-8")

    plan = plan_recovery(detection)

    assert plan.state == "stale_unpatched"
    assert plan.executable is True
    assert plan.actions == (
        "restore_verified_cron_backup",
        "clear_stale_install_state",
    )


def test_plan_recovery_restores_corrupt_cron_before_clearing_gateway_stale_state(
    installed_state,
):
    detection, original, _patched, manifest_path = installed_state
    detection.run_py.write_text(original, encoding="utf-8")
    assert detection.cron_py is not None
    cron_patched = detection.cron_py.read_text(encoding="utf-8")
    corrupt = cron_patched.replace(f"{CRON_PATCH_END}\n", "")
    detection.cron_py.write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cron_patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    plan = plan_recovery(detection)

    assert plan.state == "corrupt_owned"
    assert plan.executable is True
    assert plan.actions == (
        "restore_verified_cron_backup",
        "clear_stale_install_state",
    )


def test_plan_recovery_refuses_gateway_stale_state_with_cron_hash_mismatch(
    installed_state,
):
    detection, original, _patched, _manifest_path = installed_state
    detection.run_py.write_text(original, encoding="utf-8")
    assert detection.cron_py is not None
    detection.cron_py.write_text(
        detection.cron_py.read_text(encoding="utf-8") + "USER_EDIT = True\n",
        encoding="utf-8",
    )

    plan = plan_recovery(detection)

    assert plan.state == "owned_incomplete"
    assert plan.executable is False
    assert plan.actions == ()
    assert any(item.code == "cron_current_hash_mismatch" for item in plan.findings)


def test_plan_recovery_allows_rebuilding_a_missing_backup(installed_state):
    detection, _original, _patched, _manifest_path = installed_state
    detection.run_py.with_name("run.py.hermes_feishu_card.bak").unlink()

    plan = plan_recovery(detection)

    assert plan.state == "owned_incomplete"
    assert plan.executable is True
    assert "rebuild_backup" in plan.actions
    assert any(item.code == "backup_missing" for item in plan.findings)


def test_plan_recovery_refuses_a_backup_hash_mismatch(installed_state):
    detection, _original, _patched, _manifest_path = installed_state
    backup = detection.run_py.with_name("run.py.hermes_feishu_card.bak")
    backup.write_text(
        backup.read_text(encoding="utf-8") + "USER_EDIT = True\n",
        encoding="utf-8",
    )

    plan = plan_recovery(detection)

    assert plan.state == "owned_incomplete"
    assert plan.executable is False
    assert any(item.code == "backup_hash_mismatch" for item in plan.findings)


def test_plan_recovery_allows_manifest_rebuild_for_removable_owned_patch(
    installed_state,
):
    detection, _original, _patched, manifest_path = installed_state
    manifest_path.unlink()

    plan = plan_recovery(detection)

    assert plan.state == "owned_incomplete"
    assert plan.executable is True
    assert plan.actions == ("rebuild_manifest",)
    assert any(item.code == "manifest_missing" for item in plan.findings)


def test_plan_recovery_refuses_when_verified_backup_has_unsupported_anchors(
    installed_state,
):
    detection, _original, patched, manifest_path = installed_state
    corrupt = patched.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    unsupported = "VALUE = 1\n"
    detection.run_py.write_text(corrupt, encoding="utf-8")
    backup = detection.run_py.with_name("run.py.hermes_feishu_card.bak")
    backup.write_text(unsupported, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["patched_sha256"] = sha256(corrupt.encode("utf-8")).hexdigest()
    manifest["backup_sha256"] = sha256(unsupported.encode("utf-8")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    plan = plan_recovery(detection)

    assert plan.state == "corrupt_owned"
    assert plan.executable is False
    assert any(item.code == "unsupported_anchors" for item in plan.findings)


def test_plan_recovery_allows_manifest_owned_real_cron_marker_damage(
    installed_state,
):
    detection, _original, _patched, manifest_path = installed_state
    assert detection.cron_py is not None
    cron_patched = detection.cron_py.read_text(encoding="utf-8")
    assert CRON_PATCH_END in cron_patched
    corrupt = cron_patched.replace(f"{CRON_PATCH_END}\n", "")
    detection.cron_py.write_text(corrupt, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cron_patched_sha256"] = sha256(
        corrupt.encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    plan = plan_recovery(detection)

    assert plan.state == "corrupt_owned"
    assert plan.executable is True
    assert plan.actions == (
        "restore_verified_cron_backup",
        "reapply_current_cron_hook",
    )


def test_plan_recovery_refuses_real_cron_marker_damage_after_user_edit(
    installed_state,
):
    detection, _original, _patched, _manifest_path = installed_state
    assert detection.cron_py is not None
    cron_patched = detection.cron_py.read_text(encoding="utf-8")
    detection.cron_py.write_text(
        cron_patched.replace("from __future__ import annotations", "USER_EDIT = True")
        .replace(f"{CRON_PATCH_END}\n", ""),
        encoding="utf-8",
    )

    plan = plan_recovery(detection)

    assert plan.state == "corrupt_owned"
    assert plan.executable is False
    assert any(item.code == "cron_current_hash_mismatch" for item in plan.findings)


def test_plan_recovery_refuses_real_cron_hash_mismatch(installed_state):
    detection, _original, _patched, _manifest_path = installed_state
    assert detection.cron_py is not None
    detection.cron_py.write_text(
        detection.cron_py.read_text(encoding="utf-8") + "USER_EDIT = True\n",
        encoding="utf-8",
    )

    plan = plan_recovery(detection)

    assert plan.state == "owned_incomplete"
    assert plan.executable is False
    assert any(item.code == "cron_current_hash_mismatch" for item in plan.findings)


def test_plan_recovery_refuses_real_cron_backup_hash_mismatch(installed_state):
    detection, _original, _patched, _manifest_path = installed_state
    assert detection.cron_py is not None
    cron_backup = detection.cron_py.with_name(
        "scheduler.py.hermes_feishu_card.bak"
    )
    cron_backup.write_text(
        cron_backup.read_text(encoding="utf-8") + "USER_EDIT = True\n",
        encoding="utf-8",
    )

    plan = plan_recovery(detection)

    assert plan.state == "owned_incomplete"
    assert plan.executable is False
    assert any(item.code == "cron_backup_hash_mismatch" for item in plan.findings)


def test_recovery_fingerprint_changes_with_cron_evidence(installed_state):
    detection, _original, _patched, _manifest_path = installed_state
    assert detection.cron_py is not None
    before = plan_recovery(detection)
    detection.cron_py.write_text(
        detection.cron_py.read_text(encoding="utf-8") + "USER_EDIT = True\n",
        encoding="utf-8",
    )

    after = plan_recovery(detection)

    assert after.fingerprint != before.fingerprint


def test_plan_recovery_refuses_cron_symlink_with_explicit_state(tmp_path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root)
    (root / "cron").mkdir(exist_ok=True)
    cron_py = root / "cron" / "scheduler.py"
    cron_py.symlink_to(CRON_FIXTURE)
    detection = detect_hermes(root)

    plan = plan_recovery(detection)

    assert plan.state == "refused"
    assert plan.executable is False
    assert any(item.code == "cron_symlink_refused" for item in plan.findings)


def test_plan_recovery_refuses_cron_read_error_with_explicit_state(tmp_path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root)
    (root / "cron").mkdir(exist_ok=True)
    cron_py = root / "cron" / "scheduler.py"
    cron_py.mkdir()
    detection = detect_hermes(root)

    plan = plan_recovery(detection)

    assert plan.state == "refused"
    assert plan.executable is False
    assert any(item.code == "cron_current_read_error" for item in plan.findings)


def test_plan_recovery_refuses_gateway_symlink(tmp_path):
    root = tmp_path / "hermes"
    (root / "gateway").mkdir(parents=True)
    (root / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")
    (root / "gateway" / "run.py").symlink_to(FIXTURE / "gateway" / "run.py")
    detection = detect_hermes(root)

    plan = plan_recovery(detection)

    assert plan.state == "refused"
    assert plan.executable is False
    assert any(item.code == "symlink_refused" for item in plan.findings)


def test_plan_recovery_refuses_gateway_read_error_with_explicit_state(tmp_path):
    root = tmp_path / "hermes"
    (root / "gateway" / "run.py").mkdir(parents=True)
    (root / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")
    detection = detect_hermes(root)

    plan = plan_recovery(detection)

    assert plan.state == "refused"
    assert plan.executable is False
    assert any(item.code == "current_read_error" for item in plan.findings)


def test_sanitize_recovery_plan_excludes_sensitive_evidence(installed_state):
    detection, original, patched, _manifest_path = installed_state
    detection.run_py.write_text(
        patched.replace("import asyncio", "import asyncio\nUSER_EDIT = True").replace(
            "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", ""
        ),
        encoding="utf-8",
    )
    plan = plan_recovery(detection)

    safe = sanitize_recovery_plan(plan)
    serialized = json.dumps(safe, sort_keys=True)

    assert safe == {
        "state": plan.state,
        "executable": plan.executable,
        "fingerprint": plan.fingerprint[:12],
        "actions": list(plan.actions),
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
            }
            for finding in plan.findings
        ],
    }
    assert str(detection.root) not in serialized
    assert original not in serialized
    assert patched not in serialized
    assert plan.fingerprint not in serialized
    assert all(len(part) != 64 for part in _all_strings(safe))


def test_recovery_findings_are_immutable():
    finding = RecoveryFinding("safe", "info", "No recovery is required.")

    with pytest.raises(FrozenInstanceError):
        finding.code = "changed"


def _all_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, str):
        yield value
