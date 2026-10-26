"""Native Windows byte and portable-transaction boundaries for Issue #463."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from hermes_feishu_card.install import file_transaction as portable
from hermes_feishu_card.install import pm_wheel_store as store


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows")


def _payload() -> bytes:
    return (
        b"prefix\r\n\x1a"
        + random.Random(463).randbytes(2 * 1024 * 1024)
        + b"\x00\r\nsuffix\x1a"
    )


def test_real_and_random_wheels_keep_exact_bytes_on_windows(tmp_path):
    home = tmp_path / "home with spaces" / "用户-463"
    payload = _payload()
    name = "hermes_feishu_streaming_card-5.0.8-py3-none-any.whl"
    reference = store.publish(home, name, payload)
    wheel = Path(reference["path"])
    assert reference["sha256"] == sha256(payload).hexdigest()
    assert wheel.read_bytes() == payload
    assert store.validate(home, reference) == wheel

    configured = os.environ.get("HFC_TEST_WHEEL")
    if configured:
        built = Path(configured)
    else:
        wheel_dir = tmp_path / "ordinary wheel with spaces"
        wheel_dir.mkdir()
        checkout = Path(__file__).parents[2]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--disable-pip-version-check",
                "--no-build-isolation",
                "--no-deps",
                "--wheel-dir",
                str(wheel_dir),
                str(checkout),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        built, = wheel_dir.glob("*.whl")
    built_bytes = built.read_bytes()
    real = store.publish(home, built.name, built_bytes)
    assert real["sha256"] == sha256(built_bytes).hexdigest()
    assert Path(real["path"]).read_bytes() == built_bytes


def test_portable_write_and_rollback_preserve_crlf_ctrl_z_and_io_failure(tmp_path, monkeypatch):
    target = tmp_path / "path with spaces" / "用户" / "config.yaml"
    target.parent.mkdir(parents=True)
    original = b"old\r\n\x1a-bytes\r\n"
    target.write_bytes(original)
    portable._atomic_write_text_portable(target, "new\r\n中文\r\n")
    assert target.read_bytes() == "new\r\n中文\r\n".encode()

    before = portable._portable_target_state(target)[0]
    original_write = portable.os.write
    failed = []

    def fail_once(fd, value):
        if not failed:
            failed.append(True)
            raise OSError("injected native Windows write failure")
        return original_write(fd, value)

    monkeypatch.setattr(portable.os, "write", fail_once)
    with pytest.raises(OSError, match="injected native Windows write failure"):
        portable._atomic_write_text_portable(
            target, "replacement\r\n", expected_before=before
        )
    assert target.read_bytes() == "new\r\n中文\r\n".encode()


def test_portable_evidence_success_deletes_verified_family_on_windows(tmp_path):
    root = tmp_path / "hermes with spaces"
    root.mkdir()
    first = root / "run.py.hermes_feishu_card.bak"
    second = root / "manifest with 用户.json"
    first.write_text("backup\r\n", encoding="utf-8")
    second.write_text('{"ok": true}\r\n', encoding="utf-8")
    identities, directories = portable._snapshot_restore_evidence(
        root, {"backup": first, "manifest": second}
    )
    portable._remove_targets_portably(
        [first, second],
        expected_identities=identities,
        expected_directories=directories,
    )
    assert not first.exists() and not second.exists()

    changed = root / "changed.bak"
    changed.write_text("expected\r\n", encoding="utf-8")
    identities, directories = portable._snapshot_restore_evidence(
        root, {"changed": changed}
    )
    changed.write_text("concurrent edit\r\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        portable._remove_targets_portably(
            [changed],
            expected_identities=identities,
            expected_directories=directories,
        )
    assert changed.read_bytes() == b"concurrent edit\r\n"


def _portable_evidence_family(tmp_path):
    root = tmp_path / "family with spaces"
    root.mkdir()
    values = {
        root / "run.py.hermes_feishu_card.bak": "backup\r\n",
        root / "cron.py.hermes_feishu_card.bak": "cron\r\n",
        root / ".hermes_feishu_card_manifest": '{"manifest": true}\r\n',
    }
    for path, value in values.items():
        path.write_text(value, encoding="utf-8")
    identities, directories = portable._snapshot_restore_evidence(
        root, {path.name: path for path in values}
    )
    return list(values), values, identities, directories


def test_portable_evidence_last_mark_failure_preserves_every_file(tmp_path, monkeypatch):
    paths, values, identities, directories = _portable_evidence_family(tmp_path)
    original = portable._set_portable_delete
    marks = 0

    def fail_last_mark(fd, enabled):
        nonlocal marks
        if enabled:
            marks += 1
            if marks == len(paths):
                raise OSError("injected last mark failure")
        return original(fd, enabled)

    monkeypatch.setattr(portable, "_set_portable_delete", fail_last_mark)
    with pytest.raises(OSError, match="last mark failure"):
        portable._remove_targets_portably(
            paths,
            expected_identities=identities,
            expected_directories=directories,
            recovery_contents=values,
        )
    assert {path: path.read_bytes() for path in paths} == {
        path: value.encode("utf-8") for path, value in values.items()
    }


def test_portable_evidence_last_close_failure_restores_partial_deletions(tmp_path, monkeypatch):
    paths, values, identities, directories = _portable_evidence_family(tmp_path)
    original_close = portable.os.close
    closes = 0
    failed = False

    def fail_last_close(fd):
        nonlocal closes, failed
        closes += 1
        if closes == len(paths) and not failed:
            failed = True
            raise OSError("injected last close failure")
        return original_close(fd)

    monkeypatch.setattr(portable.os, "close", fail_last_close)
    with pytest.raises(ValueError, match="original evidence restored"):
        portable._remove_targets_portably(
            paths,
            expected_identities=identities,
            expected_directories=directories,
            recovery_contents=values,
        )
    assert {path: path.read_bytes() for path in paths} == {
        path: value.encode("utf-8") for path, value in values.items()
    }


def test_portable_transaction_refuses_a_junction(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "junction with spaces"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with pytest.raises(ValueError, match="directory|symlink"):
            portable._snapshot_restore_evidence(
                tmp_path, {"junction": junction}
            )
    finally:
        os.rmdir(junction)


def test_platform_version_stub_preserves_the_metadata_value():
    import platform

    original = platform._syscmd_ver
    try:
        platform._uname_cache = None
        expected = platform.platform()

        def quiet(system="", release="", version="",
                  supported_platforms=("win32", "win16", "dos")):
            return system, release, version

        platform._syscmd_ver = quiet
        platform._uname_cache = None
        assert platform.platform() == expected
    finally:
        platform._syscmd_ver = original
        platform._uname_cache = None
