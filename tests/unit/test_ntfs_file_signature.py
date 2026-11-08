"""#509 NTFS lstat/fstat ctime divergence: injected positive acceptance tests.

Windows NTFS reports a constant nonzero ``st_ctime_ns`` delta between path
queries (``lstat``) and handle queries (``fstat``), so any read window that
mixes the two would refuse every read on a real Windows file if ``ctime``
participated in the signature. The production signatures are the 4-tuple
``(dev, ino, size, mtime_ns)`` with content integrity on sha256 digests.

This module injects that divergence into the actual reader implementations
(never a mirror of them) and asserts the reads are accepted; genuine drift
inside the read window is still refused. The PM child probes inline their
reader source into a fresh ``python -I`` process, so there is no in-process
injection seam; the ``pm_dependency`` host adapter shares the same 4-tuple
shape and needs no separate case.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import pytest

from hermes_feishu_card.install import config_backend_0226


class _CtimeShim:
    """stat_result stand-in with an NTFS-shaped split ctime.

    Path queries (lstat) observe ``ctime + DELTA``; handle queries (fstat)
    observe ``ctime`` -- the constant divergence the reporter measured at
    ~+1.54 ms on NTFS. Every other field is the real stat result.
    """

    DELTA_NS = 1_540_000

    def __init__(self, base: os.stat_result, ctime_ns: int) -> None:
        self._base = base
        self._ctime_ns = ctime_ns

    def __getattr__(self, name: str):
        return getattr(self._base, name)

    @property
    def st_ctime_ns(self) -> int:
        return self._ctime_ns


@pytest.fixture
def ntfs_ctime_split(monkeypatch):
    """Route path queries through +DELTA ctime; handle queries stay real."""
    real_lstat, real_fstat, real_stat = os.lstat, os.fstat, os.stat

    def lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        return _CtimeShim(result, result.st_ctime_ns + _CtimeShim.DELTA_NS)

    def fstat(fd):
        result = real_fstat(fd)
        return _CtimeShim(result, result.st_ctime_ns)

    def os_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        return _CtimeShim(result, result.st_ctime_ns + _CtimeShim.DELTA_NS)

    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(os, "stat", os_stat)


def _write_private(target: Path, payload: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    os.chmod(target, 0o600)
    return target


def test_layer_reader_accepts_ntfs_ctime_split(tmp_path, ntfs_ctime_split):
    """config_backend_0226._inspect_layer_file mixes lstat and fstat inside
    one read window; the constant NTFS divergence must not refuse it."""
    payload = b"HERMES_MANAGED_DIR=/policy\\n"
    layer = _write_private(tmp_path / ".env", payload)
    mode, digest, signature = config_backend_0226._inspect_layer_file(layer)
    assert mode == "present"
    assert digest == hashlib.sha256(payload).hexdigest()
    real = os.lstat(layer)
    assert signature == (real.st_dev, real.st_ino, real.st_size, real.st_mtime_ns)


def test_layer_reader_refuses_in_place_content_drift(tmp_path, ntfs_ctime_split, monkeypatch):
    """A same-size in-place rewrite inside the layer read window changes
    mtime, so the 4-tuple signature refuses even with the NTFS split active."""
    layer = _write_private(tmp_path / ".env", b"HERMES_MANAGED_DIR=/policy\\n")
    original = layer.stat()
    real_read = os.read

    def rewrite_mid_read(fd, *args):
        payload = real_read(fd, *args)
        layer.write_bytes(b"HERMES_MANAGED_DIR=/other\\n")
        os.utime(layer, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
        return payload

    monkeypatch.setattr(os, "read", rewrite_mid_read)
    with pytest.raises(config_backend_0226.BackendRefused, match="identity shifted"):
        config_backend_0226._inspect_layer_file(layer)


def test_home_revocation_reader_accepts_ntfs_ctime_split(tmp_path, ntfs_ctime_split):
    """legacy_home_revocation._read_regular mixes lstat and fstat; the
    revocation ticket read must survive the NTFS divergence."""
    from hermes_feishu_card.install import legacy_home_revocation

    ticket = _write_private(tmp_path / "ticket.json", b"{")
    raw = legacy_home_revocation._read_regular(ticket, limit=64 * 1024)
    assert raw == b"{"


@pytest.mark.parametrize("rewrite", [b"HERMES_MANAGED_DIR=/other", b"HERMES_MANAGED_DIR=/other-x"])
def test_layer_snapshot_changes_after_bytes_restore_with_mtime_drift(tmp_path, rewrite):
    """A single reader window cannot see a rewrite that finished before it
    started; detection happens when BackendProof.verify compares the layer
    snapshot taken before the rewrite with the one after (#509). Bytes
    restored without the original mtime change the signature and refuse."""
    layer = _write_private(tmp_path / ".env", b"HERMES_MANAGED_DIR=/policy\\n")
    env = {"HERMES_HOME": str(tmp_path)}
    from hermes_feishu_card.install.config_backend_0226 import _layer_snapshot
    before = _layer_snapshot(tmp_path, tmp_path, env)["home_env"]
    original = layer.stat()
    layer.write_bytes(rewrite)
    layer.write_bytes(b"HERMES_MANAGED_DIR=/policy\\n")
    os.utime(layer, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
    after = _layer_snapshot(tmp_path, tmp_path, env)["home_env"]
    # tuple shape: (resolved_path, mode, digest, signature)
    assert before[1] == "present" and after[1] == "present"
    assert before[3] is not None and after[3] is not None
    assert before[3] != after[3], "restored bytes with changed mtime must shift the signature"


