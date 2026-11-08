"""#512 root-bound runtime identity adapter: shared by hook and admission."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card import profile_root


@pytest.fixture
def native_root(tmp_path, monkeypatch):
    root = tmp_path / "opt" / "data"
    root.mkdir(parents=True)
    module = SimpleNamespace(get_default_hermes_root=lambda: root)
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    return root


def test_native_profile_root_resolves_verified_absolute_root(native_root):
    assert profile_root.native_profile_root() == native_root.resolve()


def test_native_profile_root_missing_helper_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace())
    assert profile_root.native_profile_root() is None


def test_native_profile_root_inspection_failure_fails_closed(monkeypatch, tmp_path):
    def broken():
        raise OSError("uninspectable")

    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=broken
    ))
    with pytest.raises(profile_root.ProfileRootUnavailable):
        profile_root.native_profile_root()


def test_native_profile_root_relative_refuses(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = SimpleNamespace(get_default_hermes_root=lambda: Path("relative/root"))
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    with pytest.raises(profile_root.ProfileRootUnavailable):
        profile_root.native_profile_root()


def test_resolve_task_identity_docker_default_and_named(native_root, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(native_root))
    assert profile_root.resolve_task_profile_identity(
        task_home=str(native_root), multiplex=True
    ) == ("default", "hermes_home")
    named = native_root / "profiles" / "content"
    assert profile_root.resolve_task_profile_identity(
        task_home=str(named), multiplex=True
    ) == ("content", "hermes_home")


def test_resolve_task_identity_refuses_outside_root(native_root, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(native_root))
    other = native_root.parent / "elsewhere" / "profiles" / "x"
    # Root present, task home unrecognized: refuse as unproven default,
    # never a legacy fallthrough onto the outside-root .hermes-like shape.
    assert profile_root.resolve_task_profile_identity(
        task_home=str(other), multiplex=True
    ) == ("default", "fallback_default")


def test_resolve_task_identity_no_env_keeps_legacy(native_root, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    # Without an explicit HERMES_HOME the platform default root is not a
    # deployment decision: the non-multiplex caller keeps the legacy
    # fallback contract. Multiplex still resolves root-bound.
    assert profile_root.resolve_task_profile_identity(
        task_home=str(native_root), multiplex=False
    ) is None
    assert profile_root.resolve_task_profile_identity(
        task_home=str(native_root), multiplex=True
    ) == ("default", "hermes_home")


@pytest.mark.parametrize("getter", [None, 42, "/opt/data"])
def test_existing_noncallable_helper_refuses(monkeypatch, getter):
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=getter,
    ))
    with pytest.raises(profile_root.ProfileRootUnavailable):
        profile_root.native_profile_root()


@pytest.mark.parametrize("raw", [None, 42, object(), ""])
def test_invalid_helper_result_refuses(monkeypatch, raw):
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: raw,
    ))
    with pytest.raises(profile_root.ProfileRootUnavailable):
        profile_root.native_profile_root()


@pytest.mark.parametrize("missing_name", ["hermes_constants", "nested_dependency"])
def test_missing_module_is_distinct_from_broken_import(monkeypatch, missing_name):
    def missing(name):
        raise ModuleNotFoundError("missing", name=missing_name)
    monkeypatch.setattr(profile_root.importlib, "import_module", missing)
    if missing_name == "hermes_constants":
        assert profile_root.native_profile_root() is None
        assert profile_root.resolve_task_profile_identity(task_home="", multiplex=False) is None
    else:
        with pytest.raises(profile_root.ProfileRootUnavailable):
            profile_root.native_profile_root()


def test_missing_helpers_preserve_legacy_named_environment(monkeypatch):
    from hermes_feishu_card import hook_runtime as h
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace())
    monkeypatch.setattr(h, "_GATEWAY_RUNNER_REF", None)
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(Path.home() / ".hermes" / "profiles" / "work"))
    assert h._profile_identity({}, None, None) == ("work", "hermes_home")


def test_nonmultiplex_explicit_custom_home_uses_native_root(native_root, monkeypatch):
    from hermes_feishu_card import hook_runtime as h
    named = native_root / "profiles" / "work"
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: native_root, get_hermes_home=lambda: named,
    ))
    monkeypatch.setattr(h, "_GATEWAY_RUNNER_REF", None)
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(named))
    assert h._profile_identity({}, None, None) == ("work", "hermes_home")


@pytest.mark.parametrize("task_home", ["", ".", "relative"])
def test_invalid_task_home_never_becomes_default_via_cwd(native_root, monkeypatch, task_home):
    from hermes_feishu_card import hook_runtime as h
    monkeypatch.chdir(native_root)
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: native_root, get_hermes_home=lambda: task_home,
    ))
    monkeypatch.setattr(h, "_GATEWAY_RUNNER_REF", None)
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True))
    assert h._profile_identity({"self": runner}, None, None) == ("default", "fallback_default")
    assert h._admission_default_profile_unverified({"self": runner}) is True


def test_outside_legacy_shape_cannot_fall_through(native_root, monkeypatch):
    from hermes_feishu_card import hook_runtime as h
    outside = native_root.parent / "elsewhere" / ".hermes" / "profiles" / "work"
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: native_root, get_hermes_home=lambda: outside,
    ))
    monkeypatch.setattr(h, "_GATEWAY_RUNNER_REF", None)
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True))
    assert h._profile_identity({"self": runner}, None, None) == ("default", "fallback_default")
    assert h._admission_default_profile_unverified({"self": runner}) is True
