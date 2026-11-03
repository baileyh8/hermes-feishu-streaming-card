"""Unit tests for v0.22.6 config backend verification probe."""
import os
from pathlib import Path
import pytest

from hermes_feishu_card.install.config_backend_0226 import (
    BackendProof,
    BackendRefused,
    verify_file_backend,
    _inspect_layer_file,
)


def _configured_roots():
    root_env = os.environ.get("HFC_CONFIG_WRITER_0226_ROOT")
    python_env = os.environ.get("HFC_LEGACY_PLUGIN_PYTHON")
    required = os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1"

    if not root_env or not python_env:
        if required:
            pytest.fail("HFC_REQUIRE_CONFIG_WRITER_CONTRACT=1 requires HFC_CONFIG_WRITER_0226_ROOT and HFC_LEGACY_PLUGIN_PYTHON")
        pytest.skip("set HFC_CONFIG_WRITER_0226_ROOT and HFC_LEGACY_PLUGIN_PYTHON to run 0226 backend probe tests")

    root = Path(root_env)
    python = Path(python_env)
    assert root.is_dir(), f"configured root {root} is not a directory"
    assert python.is_file(), f"configured python {python} is not a file"
    return root, python


def test_inspect_layer_file_absent(tmp_path):
    status, digest, identity = _inspect_layer_file(tmp_path / "absent.env")
    assert status == "absent"
    assert digest is None
    assert identity is None


def test_inspect_layer_file_present(tmp_path):
    env_file = tmp_path / "valid.env"
    env_file.write_text("HERMES_CONFIG_BACKEND=file\n", encoding="utf-8")
    status, digest, identity = _inspect_layer_file(env_file)
    assert status == "present"
    assert digest is not None
    assert identity is not None


def test_inspect_layer_file_symlink_refused(tmp_path):
    target = tmp_path / "target.env"
    target.write_text("HERMES_CONFIG_BACKEND=file\n", encoding="utf-8")
    sym = tmp_path / "link.env"
    try:
        sym.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise

    with pytest.raises(BackendRefused) as excinfo:
        _inspect_layer_file(sym)
    assert excinfo.value.code == "backend-file-symlink"


def test_inspect_layer_file_unreadable_refused(tmp_path, monkeypatch):
    env_file = tmp_path / "unreadable.env"
    env_file.write_text("HERMES_CONFIG_BACKEND=file\n", encoding="utf-8")
    from hermes_feishu_card.install import config_backend_0226 as backend
    original = backend.os.open
    def denied(path, *args, **kwargs):
        if Path(path) == env_file:
            raise PermissionError("synthetic inaccessible env")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(backend.os, "open", denied)
    with pytest.raises(BackendRefused) as excinfo:
        _inspect_layer_file(env_file)
    assert excinfo.value.code == "backend-file-unreadable"


def test_default_file_backend_real_source(tmp_path):
    root, python = _configured_roots()
    home = tmp_path / "home"
    home.mkdir()

    proof = verify_file_backend(root, python, home)
    assert isinstance(proof, BackendProof)
    assert proof.scope_layers["pm_scope"]["home_env"][2] is None
    assert proof.scope_layers["pm_scope"]["project_env"][2] is None

    proof.verify()


def test_explicit_file_backend_real_source(tmp_path):
    root, python = _configured_roots()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("HERMES_CONFIG_BACKEND=file\n", encoding="utf-8")

    proof = verify_file_backend(root, python, home)
    assert isinstance(proof, BackendProof)
    assert proof.scope_layers["pm_scope"]["home_env"][2] is not None

    proof.verify()


def test_remote_backend_real_source_refused(tmp_path):
    root, python = _configured_roots()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("HERMES_CONFIG_BACKEND=remote\n", encoding="utf-8")

    with pytest.raises(BackendRefused) as excinfo:
        verify_file_backend(root, python, home)
    assert excinfo.value.code == "backend-non-file"


def test_ambient_remote_env_real_source_refused(tmp_path, monkeypatch):
    root, python = _configured_roots()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_CONFIG_BACKEND", "remote")

    with pytest.raises(BackendRefused) as excinfo:
        verify_file_backend(root, python, home)
    assert excinfo.value.code == "backend-non-file"


def test_proof_drift_detected_real_source(tmp_path):
    root, python = _configured_roots()
    home = tmp_path / "home"
    home.mkdir()

    proof = verify_file_backend(root, python, home)
    proof.verify()

    (home / ".env").write_text("DRIFT=1\n", encoding="utf-8")

    with pytest.raises(BackendRefused) as excinfo:
        proof.verify()
    assert excinfo.value.code == "backend-drift"



def test_real_probe_rejects_same_inode_rewrite_and_restore(tmp_path, monkeypatch):
    from hermes_feishu_card.install import config_backend_0226 as backend
    root, python = _configured_roots()
    home = tmp_path / "home"
    home.mkdir()
    layer = home / ".env"
    original_bytes = b"HERMES_CONFIG_BACKEND=remote\n"
    layer.write_bytes(original_bytes)
    original_probe = backend._run_child_probe
    calls = []
    def raced(*args):
        layer.write_bytes(b"HERMES_CONFIG_BACKEND=file\n")
        try:
            result = original_probe(*args)
            calls.append(result)
            return result
        finally:
            layer.write_bytes(original_bytes)
    monkeypatch.setattr(backend, "_run_child_probe", raced)
    with pytest.raises(BackendRefused) as error:
        verify_file_backend(root, python, home)
    assert error.value.code == "backend-drift"
    assert len(calls) == 1
    assert layer.read_bytes() == original_bytes
