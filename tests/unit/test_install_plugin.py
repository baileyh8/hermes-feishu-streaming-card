from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card.install import plugin, plugin_config_bytes, v3


_POSIX_MODES = os.name != "nt"


def _symlink_or_skip_windows_privilege(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires privilege (WinError 1314)")
        raise


def _write_utf8(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


_REVIEW_FIXED_SOURCE_ROOT = Path(
    os.environ.get(
        "HFC_FIXED_TAG_SOURCE_ROOT",
        "/private/tmp/hermes-agent-v2026.8.3-v430-audit",
    )
)


def _review_v3_helpers():
    path = Path(__file__).with_name("test_install_v3_transaction.py")
    spec = importlib.util.spec_from_file_location("review_v3_helpers", path)
    assert spec is not None and spec.loader is not None
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    return helpers


def _exact_plan(postimage: bytes):
    return SimpleNamespace(
        postimage=postimage,
        verify_source=lambda: None,
        matches_expected=lambda expected: plugin._load_config_mapping(postimage) == expected,
    )


def _checkout(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    checkout = home / "hermes-agent"
    home.mkdir(exist_ok=True)
    checkout.mkdir(parents=True)
    _write_utf8(home / "config.yaml", "plugins: {}\n")
    prefix = checkout / ".venv"
    if os.name == "nt":
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(prefix)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        launcher = _venv_python(prefix)
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(sys.executable)
        _venv_purelib(prefix).mkdir(parents=True, exist_ok=True)
    return checkout, home


def _venv_python(prefix: Path) -> Path:
    return prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_purelib(prefix: Path) -> Path:
    if os.name == "nt":
        return prefix / "Lib" / "site-packages"
    return prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def _runtime_payload(checkout: Path) -> dict[str, object]:
    prefix = checkout / ".venv"
    purelib = _venv_purelib(prefix)
    return {
        "executable": str(_venv_python(prefix)),
        "prefix": str(prefix),
        "base_prefix": sys.base_prefix,
        "purelib": str(purelib),
        "platlib": str(purelib),
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink launcher case")
def test_runtime_binding_accepts_verified_posix_venv_symlink(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )

    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout,
        hermes_home=home,
        profile_id=None,
    )

    assert binding.checkout_root == checkout
    assert binding.hermes_home == home
    assert binding.config_path == home / "config.yaml"
    assert binding.runtime_python == _venv_python(checkout / ".venv")
    assert binding.python_identity.startswith("sha256:")


@pytest.mark.skipif(os.name != "nt", reason="native Windows venv launcher case")
def test_runtime_binding_accepts_native_venv_launcher(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None
    )
    assert binding.runtime_python == _venv_python(checkout / ".venv")
    assert binding.runtime_python.is_file()


def test_runtime_binding_rejects_symlinked_home_or_config(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    _write_utf8(real_home / "config.yaml", "plugins: {}\n")
    linked_home = tmp_path / "linked-home"
    _symlink_or_skip_windows_privilege(linked_home, real_home, directory=True)

    with pytest.raises(plugin.RuntimeBindingRefused, match="Hermes home"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout, hermes_home=linked_home, profile_id=None
        )

    (home / "config.yaml").unlink()
    _symlink_or_skip_windows_privilege(
        home / "config.yaml", real_home / "config.yaml"
    )
    with pytest.raises(plugin.RuntimeBindingRefused, match="config"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout, hermes_home=home, profile_id=None
        )


def test_runtime_binding_profile_id_never_selects_home(tmp_path, monkeypatch):
    checkout, _home = _checkout(tmp_path)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    with pytest.raises(plugin.RuntimeBindingRefused, match="profile-id"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout,
            hermes_home=None,
            profile_id="bot-a",
        )


def test_runtime_binding_rejects_wrong_prefix(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin,
        "_probe_runtime_identity",
        lambda launcher: {
            **_runtime_payload(checkout),
            "prefix": str(tmp_path / "other-venv"),
        },
    )
    with pytest.raises(plugin.RuntimeBindingRefused, match="prefix"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout, hermes_home=home, profile_id=None
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-bit case")
def test_runtime_binding_rejects_nonexecutable_posix_target(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    launcher = _venv_python(checkout / ".venv")
    launcher.unlink()
    target = tmp_path / "python-target"
    _write_utf8(target, "not executable")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    launcher.symlink_to(target)
    with pytest.raises(plugin.RuntimeBindingRefused, match="executable"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout, hermes_home=home, profile_id=None
        )


@pytest.mark.skipif(os.name != "nt", reason="native Windows nonregular launcher case")
def test_runtime_binding_rejects_nonregular_native_launcher(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    launcher = _venv_python(checkout / ".venv")
    launcher.unlink()
    launcher.mkdir()
    with pytest.raises(plugin.RuntimeBindingRefused, match="nonregular"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout, hermes_home=home, profile_id=None
        )


def test_entrypoint_probe_accepts_one_exact_installed_distribution(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    payload = _runtime_payload(checkout)
    purelib = Path(str(payload["purelib"]))
    module_origin = purelib / "hermes_feishu_card" / "hermes_plugin.py"
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: payload
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None
    )
    monkeypatch.setattr(
        plugin,
        "_run_entrypoint_probe",
        lambda _binding: {
            "candidates": [
                {
                    "name": "hermes-feishu-card",
                    "value": "hermes_feishu_card.hermes_plugin",
                    "distribution": "hermes-feishu-streaming-card",
                    "version": "4.2.12",
                }
            ],
            "module_origin": str(module_origin),
        },
    )

    result = plugin.probe_plugin_entrypoint(binding, expected_version="4.2.12")

    assert result.status == "verified"
    assert result.reason == "verified"
    assert result.module_origin == module_origin


def test_entrypoint_probe_default_requires_current_version(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    runtime_payload = _runtime_payload(checkout)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: runtime_payload
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None
    )
    monkeypatch.setattr(
        plugin,
        "_run_entrypoint_probe",
        lambda _binding: {
            "candidates": [
                {
                    "name": "hermes-feishu-card",
                    "value": "hermes_feishu_card.hermes_plugin",
                    "distribution": "hermes-feishu-streaming-card",
                    "version": "0.0.0",
                }
            ],
            "module_origin": str(
                Path(str(runtime_payload["purelib"]))
                / "hermes_feishu_card"
                / "hermes_plugin.py"
            ),
        },
    )

    result = plugin.probe_plugin_entrypoint(binding)

    assert result.status == "failed"
    assert "version" in result.reason


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        (lambda payload, checkout: payload.update(candidates=[]), "exactly one"),
        (
            lambda payload, checkout: payload["candidates"].append(
                dict(payload["candidates"][0])
            ),
            "exactly one",
        ),
        (
            lambda payload, checkout: payload.update(
                module_origin=str(checkout / "hermes_feishu_card" / "hermes_plugin.py")
            ),
            "site-packages",
        ),
        (
            lambda payload, checkout: payload["candidates"][0].update(
                version="future"
            ),
            "version",
        ),
        (
            lambda payload, checkout: payload["candidates"][0].update(
                value="other.module"
            ),
            "identity",
        ),
    ),
)
def test_entrypoint_probe_rejects_missing_duplicate_editable_or_wrong_version(
    tmp_path, monkeypatch, mutation, reason
):
    checkout, home = _checkout(tmp_path)
    runtime_payload = _runtime_payload(checkout)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: runtime_payload
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None
    )
    payload = {
        "candidates": [
            {
                "name": "hermes-feishu-card",
                "value": "hermes_feishu_card.hermes_plugin",
                "distribution": "hermes-feishu-streaming-card",
                "version": "4.2.12",
            }
        ],
        "module_origin": str(
            Path(str(runtime_payload["purelib"]))
            / "hermes_feishu_card"
            / "hermes_plugin.py"
        ),
    }
    mutation(payload, checkout)
    monkeypatch.setattr(plugin, "_run_entrypoint_probe", lambda _binding: payload)

    result = plugin.probe_plugin_entrypoint(binding, expected_version="4.2.12")

    assert result.status == "failed"
    assert reason in result.reason
    assert "home" not in json.dumps(result.sanitized(), sort_keys=True)


def _binding(tmp_path: Path, monkeypatch, config_text: str) -> plugin.HermesRuntimeBinding:
    checkout, home = _checkout(tmp_path)
    # Pure transaction tests inject a known byte-writing process contract. Real
    # writer planning is covered separately by pinned upstream integration.
    monkeypatch.setattr(
        plugin,
        "_plan_enabled_config_bytes",
        lambda b, p, raw, desired, enabled: _exact_plan(yaml.safe_dump(desired).encode()),
    )
    _write_utf8(home / "config.yaml", config_text)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    return plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None
    )


def test_prepare_plugin_config_creates_private_exact_preimage(tmp_path, monkeypatch):
    config_text = "token: PRIVATE-CANARY\nplugins: {}\n"
    binding = _binding(tmp_path, monkeypatch, config_text)

    preimage = plugin.prepare_plugin_config(binding)

    if _POSIX_MODES:
        assert stat.S_IMODE(preimage.state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(preimage.backup_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(preimage.journal_path.stat().st_mode) == 0o600
    assert preimage.backup_path.read_text(encoding="utf-8") == config_text
    assert preimage.config_backup_id.startswith("hfc-config-preimage-")
    assert str(binding.hermes_home) not in json.dumps(preimage.sanitized())
    assert "PRIVATE-CANARY" not in json.dumps(preimage.sanitized())


def test_official_enable_refuses_unverified_checkout_before_child(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    monkeypatch.setattr(
        plugin.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unverified checkout must not execute"),
    )

    with pytest.raises(plugin.PluginConfigRefused, match="enable root is invalid"):
        plugin._run_official_enable(binding)


def test_official_enable_uses_private_windows_home_environment(tmp_path, monkeypatch):
    checkout, home = tmp_path / "checkout with spaces", tmp_path / "home with spaces"
    checkout.mkdir()
    home.mkdir()
    binding = SimpleNamespace(
        checkout_root=checkout,
        runtime_python=Path(sys.executable),
        hermes_home=home,
    )
    monkeypatch.setattr(plugin, "_verify_official_enable_source", lambda *a, **k: None)
    # The environment builder owns the Windows branch; do not mutate global os.name.
    windows_os = SimpleNamespace(**{**vars(plugin_config_bytes.os), "name": "nt"})
    monkeypatch.setattr(plugin_config_bytes, "os", windows_os)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("HFC_PRIVATE_SECRET", "do-not-inherit")
    captured = {}

    def run(argv, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(plugin.subprocess, "run", run)
    assert plugin._run_official_enable(binding) == 0
    assert captured["env"]["HOME"] == captured["env"]["HERMES_HOME"] == str(home)
    assert captured["env"]["USERPROFILE"] == str(home)
    assert captured["env"]["HERMES_INSTALL_ROOT"] == str(checkout)
    assert "HFC_PRIVATE_SECRET" not in captured["env"]


def test_official_enable_refusal_keeps_only_safe_diagnostic(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise plugin_config_bytes.ConfigBytesRefused(
            "PRIVATE-CANARY",
            code="launch-source-drift",
            stage="launch",
            source="hermes_cli/main.py",
        )

    monkeypatch.setattr(plugin_config_bytes, "verify_launch_sources", refuse)
    with pytest.raises(plugin.PluginConfigRefused) as caught:
        plugin._verify_official_enable_source(tmp_path)
    message = str(caught.value)
    assert "launch:launch-source-drift:hermes_cli/main.py" in message
    assert "PRIVATE-CANARY" not in message


def test_config_plan_refusal_keeps_only_safe_diagnostic(tmp_path, monkeypatch):
    plan_enabled_config_bytes = plugin._plan_enabled_config_bytes
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    preimage = plugin.prepare_plugin_config(binding)
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", plan_enabled_config_bytes)

    def refuse(**kwargs):
        raise plugin_config_bytes.ConfigBytesRefused(
            "PRIVATE-CANARY",
            code="unbound-local-source",
            stage="exec",
            source="hermes_cli/extra.py",
        )

    monkeypatch.setattr(plugin_config_bytes, "plan_enabled_bytes", refuse)
    with pytest.raises(plugin.PluginConfigRefused) as caught:
        plugin._plan_enabled_config_bytes(binding, preimage, b"plugins: {}\n", {}, False)
    message = str(caught.value)
    assert "exec:unbound-local-source:hermes_cli/extra.py" in message
    assert "PRIVATE-CANARY" not in message


def test_enable_plugin_allows_only_exact_plugin_config_change(tmp_path, monkeypatch):
    binding = _binding(
        tmp_path,
        monkeypatch,
        "token: PRIVATE-CANARY\nplugins:\n  disabled: [hermes-feishu-card, keep-disabled]\n  enabled: [keep-enabled]\n",
    )
    preimage = plugin.prepare_plugin_config(binding)

    def official_enable(candidate):
        config = yaml.safe_load(candidate.config_path.read_text(encoding="utf-8"))
        plugins = config.setdefault("plugins", {})
        plugins["enabled"] = sorted(set(plugins.get("enabled", [])) | {"hermes-feishu-card"})
        plugins["disabled"] = sorted(
            set(plugins.get("disabled", [])) - {"hermes-feishu-card"}
        )
        plugins.setdefault("entries", {}).setdefault("hermes-feishu-card", {})[
            "allow_tool_override"
        ] = False
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", official_enable)
    ownership = plugin.enable_plugin(binding, preimage)

    config = yaml.safe_load(binding.config_path.read_text(encoding="utf-8"))
    assert config["token"] == "PRIVATE-CANARY"
    assert config["plugins"]["enabled"] == ["hermes-feishu-card", "keep-enabled"]
    assert config["plugins"]["disabled"] == ["keep-disabled"]
    assert config["plugins"]["entries"]["hermes-feishu-card"] == {
        "allow_tool_override": False
    }
    assert ownership.added_by_hfc is True
    assert ownership.enabled_before is False
    assert ownership.post_sha256 != ownership.pre_sha256


def test_enable_plugin_rejects_unrelated_semantic_drift_and_preserves_edit(
    tmp_path, monkeypatch
):
    original = "token: PRIVATE-CANARY\nplugins: {}\n"
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)

    def drifting_enable(candidate):
        _write_utf8(
            candidate.config_path,
            "token: CHANGED\nplugins:\n  enabled: [hermes-feishu-card]\n",
        )
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", drifting_enable)
    with pytest.raises(plugin.PluginConfigRefused, match="unrelated"):
        plugin.enable_plugin(binding, preimage)

    assert "CHANGED" in binding.config_path.read_text(encoding="utf-8")
    assert preimage.backup_path.read_bytes() == original.encode()


def test_restore_plugin_config_refuses_postimage_drift(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    preimage = plugin.prepare_plugin_config(binding)

    def official_enable(candidate):
        _write_utf8(
            candidate.config_path,
            "plugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n",
        )
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", official_enable)
    postimage = b"plugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n"
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", lambda *a: _exact_plan(postimage))
    ownership = plugin.enable_plugin(binding, preimage)
    _write_utf8(binding.config_path, "plugins: {}\nuser_change: true\n")

    with pytest.raises(plugin.PluginConfigRefused, match="changed"):
        plugin.restore_plugin_config(binding, preimage, ownership)


@pytest.mark.parametrize("legacy", [False, True])
def test_runtime_binding_uses_pm_committed_venv_with_real_child(tmp_path, legacy):
    checkout, home = _checkout(tmp_path)
    if not legacy:
        _venv_python(checkout / ".venv").unlink()
    managed = tmp_path / "managed environment" / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(managed)], check=True)
    pm = checkout / "pm"
    pm.mkdir()
    _write_utf8(pm / "__init__.py", "")
    _write_utf8(
        pm / "environments.py",
        "from pathlib import Path\n"
        f"def committed_venv(root): return Path({str(managed)!r})\n"
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None,
    )
    assert binding.runtime_python == _venv_python(managed)
    assert binding.purelib.is_relative_to(managed)
    assert binding.hermes_home == home


@pytest.mark.parametrize("normalize_lists", [False, True])
@pytest.mark.parametrize("plugin_config", [
    {"enabled": ["hermes-feishu-card"]},
    {"enabled": ["z-plugin", "hermes-feishu-card", "a-plugin"]},
    {"enabled": ["hermes-feishu-card"], "disabled": ["z-disabled", "a-disabled"]},
    {"enabled": ["hermes-feishu-card"], "entries": {
        "hermes-feishu-card": {"allow_tool_override": True, "keep": "entry-value"},
        "other-plugin": {"allow_tool_override": True}}},
])
def test_enable_plugin_accepts_exact_already_enabled_official_branch(
    tmp_path, monkeypatch, plugin_config, normalize_lists
):
    # Official cmd_enable leaves lists untouched in its already_enabled branch.
    # A normalized official variant remains supported, but no arbitrary subset.
    original = "# exact preimage\n" + yaml.safe_dump({
        "model": "synthetic-model", "plugins": plugin_config,
    }, sort_keys=False)
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)
    calls = []
    if normalize_lists:
        def normalized_plan(candidate, _preimage, before, desired, enabled):
            expected, _ = plugin._expected_enabled_config(yaml.safe_load(before))
            return _exact_plan(yaml.safe_dump(expected).encode())
        monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", normalized_plan)

    def official_enable(candidate):
        calls.append(candidate)
        config = yaml.safe_load(candidate.config_path.read_bytes())
        section = config["plugins"]
        if normalize_lists:
            section["enabled"] = sorted(set(section["enabled"]))
            section["disabled"] = sorted(set(section.get("disabled", [])))
        section.setdefault("entries", {}).setdefault(plugin.PLUGIN_KEY, {})[
            "allow_tool_override"
        ] = False
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", official_enable)
    ownership = plugin.enable_plugin(binding, preimage)
    actual = yaml.safe_load(binding.config_path.read_bytes())
    expected = json.loads(json.dumps({"model": "synthetic-model", "plugins": plugin_config}))
    if normalize_lists:
        expected["plugins"]["enabled"] = sorted(set(plugin_config["enabled"]))
        expected["plugins"]["disabled"] = sorted(set(plugin_config.get("disabled", [])))
    expected["plugins"].setdefault("entries", {}).setdefault(plugin.PLUGIN_KEY, {})[
        "allow_tool_override"
    ] = False
    assert actual == expected
    assert calls == [binding]
    assert ownership.enabled_before is True
    assert actual["plugins"]["entries"][plugin.PLUGIN_KEY]["allow_tool_override"] is False
    plugin.restore_plugin_config(binding, preimage, ownership)
    assert binding.config_path.read_bytes() == original.encode()


@pytest.mark.parametrize("drift", ["model", "other-entry", "target-extra", "enabled-drop", "disabled-drop", "grant-true"])
def test_enable_plugin_already_enabled_rejects_non_derived_delta_and_preserves_edit(
    tmp_path, monkeypatch, drift
):
    original = "# preserve comment and order\n" + yaml.safe_dump({
        "model": "synthetic-model", "plugins": {
            "enabled": ["z-plugin", plugin.PLUGIN_KEY, "a-plugin"],
            "disabled": ["keep-disabled"],
            "entries": {"other-plugin": {"keep": True}},
        },
    }, sort_keys=False)
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)

    def drifting_enable(candidate):
        config = yaml.safe_load(candidate.config_path.read_bytes())
        section = config["plugins"]
        section["entries"][plugin.PLUGIN_KEY] = {"allow_tool_override": False}
        if drift == "model":
            config["model"] = "changed-model"
        elif drift == "other-entry":
            section["entries"]["other-plugin"]["keep"] = False
        elif drift == "target-extra":
            section["entries"][plugin.PLUGIN_KEY]["unexpected"] = True
        elif drift == "enabled-drop":
            section["enabled"].remove("a-plugin")
        elif drift == "disabled-drop":
            section["disabled"] = []
        else:
            section["entries"][plugin.PLUGIN_KEY]["allow_tool_override"] = True
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", drifting_enable)
    with pytest.raises(plugin.PluginConfigRefused, match="unrelated"):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() != original.encode()
    assert preimage.backup_path.read_bytes() == original.encode()


@pytest.mark.parametrize("enabled,disabled", [([], []), ([plugin.PLUGIN_KEY], [plugin.PLUGIN_KEY])])
def test_enable_plugin_not_enabled_rejects_entry_only_shortcut(tmp_path, monkeypatch, enabled, disabled):
    original = yaml.safe_dump({"plugins": {"enabled": enabled, "disabled": disabled}})
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)

    def entry_only(candidate):
        config = yaml.safe_load(candidate.config_path.read_bytes())
        config["plugins"]["entries"] = {plugin.PLUGIN_KEY: {"allow_tool_override": False}}
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", entry_only)
    with pytest.raises(plugin.PluginConfigRefused, match="unrelated"):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() != original.encode()
    assert preimage.backup_path.read_bytes() == original.encode()


@pytest.mark.parametrize("normalize_only", ["enabled", "disabled"])
def test_enable_plugin_already_enabled_rejects_mixed_list_normalization(
    tmp_path, monkeypatch, normalize_only
):
    original = yaml.safe_dump({"plugins": {
        "enabled": ["z-plugin", plugin.PLUGIN_KEY, "a-plugin"],
        "disabled": ["z-disabled", "a-disabled"],
    }}, sort_keys=False)
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)

    def partial_enable(candidate):
        config = yaml.safe_load(candidate.config_path.read_bytes())
        section = config["plugins"]
        section[normalize_only] = sorted(set(section[normalize_only]))
        section["entries"] = {plugin.PLUGIN_KEY: {"allow_tool_override": False}}
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", partial_enable)
    with pytest.raises(plugin.PluginConfigRefused, match="unrelated"):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() != original.encode()
    assert preimage.backup_path.read_bytes() == original.encode()

@pytest.mark.parametrize("outcome", ["exception", "nonzero", "invalid-yaml"])
def test_enable_failure_preserves_unowned_concurrent_config_and_preimage(tmp_path, monkeypatch, outcome):
    binding = _binding(tmp_path, monkeypatch, "# original\nplugins: {}\n")
    preimage = plugin.prepare_plugin_config(binding)
    changed = b"user_change: preserve-me\nplugins: {}\n" if outcome != "invalid-yaml" else b"plugins: [broken\n"
    def changed_enable(candidate):
        candidate.config_path.write_bytes(changed)
        if outcome == "exception":
            raise OSError("synthetic CLI failure after concurrent edit")
        return 1 if outcome == "nonzero" else 0
    monkeypatch.setattr(plugin, "_run_official_enable", changed_enable)
    with pytest.raises(plugin.PluginConfigRefused):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() == changed
    assert preimage.backup_path.read_bytes() == b"# original\nplugins: {}\n"
    assert preimage.journal_path.is_file()


@pytest.mark.parametrize("outcome", ["exception", "nonzero"])
def test_enable_failure_rolls_back_only_verified_official_postimage(tmp_path, monkeypatch, outcome):
    original = b"# preserve original bytes\r\nplugins: {}\r\n"
    binding = _binding(tmp_path, monkeypatch, original.decode())
    binding.config_path.write_bytes(original)
    preimage = plugin.prepare_plugin_config(binding)
    def failed_enable(candidate):
        expected, _ = plugin._expected_enabled_config(yaml.safe_load(original))
        _write_utf8(candidate.config_path, yaml.safe_dump(expected))
        if outcome == "exception":
            raise OSError("synthetic failure after official config write")
        return 1
    monkeypatch.setattr(plugin, "_run_official_enable", failed_enable)
    with pytest.raises(plugin.PluginConfigRefused, match="official plugin enable failed"):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() == original


def test_official_enable_failure_includes_sanitized_stdout_tail(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    monkeypatch.setattr(plugin, "_verify_official_enable_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        plugin.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="No plugin named 'hermes-feishu-card'.\n",
            stderr="",
        ),
    )

    assert plugin._run_official_enable(binding) == 1
    assert plugin._official_enable_diagnostic == "out=No plugin named 'hermes-feishu-card'."
    assert plugin._official_enable_failure_message() == (
        "official plugin enable failed (No plugin named 'hermes-feishu-card'.)"
    )


def test_official_enable_failure_message_stays_fixed_without_diagnostic(monkeypatch):
    monkeypatch.setattr(plugin, "_official_enable_diagnostic", None)
    monkeypatch.setattr(plugin, "_official_enable_diagnostic_sources", (False, False))

    assert plugin._official_enable_failure_message() == "official plugin enable failed"


def test_official_enable_output_note_folds_and_truncates():
    text = "first line\ntoken: xxx\n" + "A" * 300 + "\x00 tail marker"

    note = plugin._official_child_output_note(text)

    assert note.startswith("...")
    assert len(note) <= 243
    assert "\n" not in note
    assert "\x00" not in note
    assert "  " not in note
    assert "token: xxx" not in note
    assert note.count("token") <= 1
    assert "tail marker" in note
    assert "[REDACTED]" in plugin._official_child_output_note("token: xxx\n")
    assert "token: xxx" not in plugin._official_child_output_note("token: xxx\n")


def test_official_enable_visibility_probe_skips_non_pm_checkouts(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    monkeypatch.setattr(
        plugin.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("non-PM checkout must not start a probe"),
    )

    assert plugin._official_enable_visibility_note(binding) is None


def test_official_probe_interpreter_resolution_failure_keeps_venv(tmp_path, monkeypatch):
    """Resolution is an optimization, never a bypass: any failure keeps the
    existing probe/refusal behavior on the bound runtime python."""
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")

    def refuse(*args, **kwargs):
        raise OSError("resolution unavailable")

    monkeypatch.setattr(plugin.subprocess, "run", refuse)

    assert plugin._official_probe_interpreter(binding) is None


def test_official_probe_interpreter_rejects_relative_and_missing(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="relative/python\n", stderr="")

    monkeypatch.setattr(plugin.subprocess, "run", fake_run)
    assert plugin._official_probe_interpreter(binding) is None

    def missing_run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=str(tmp_path / "no-python") + "\n", stderr="")

    monkeypatch.setattr(plugin.subprocess, "run", missing_run)
    assert plugin._official_probe_interpreter(binding) is None

    def multi_run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="/usr/bin/python3\n/usr/bin/python3\n", stderr="")

    monkeypatch.setattr(plugin.subprocess, "run", multi_run)
    assert plugin._official_probe_interpreter(binding) is None

    def failed_run(argv, **kwargs):
        return SimpleNamespace(returncode=4, stdout="", stderr="none\n")

    monkeypatch.setattr(plugin.subprocess, "run", failed_run)
    assert plugin._official_probe_interpreter(binding) is None


def _profile_home(tmp_path: Path, name: str = "jishu") -> tuple[Path, Path]:
    home = tmp_path / "hermes-home"
    profile = home / "profiles" / name
    profile.mkdir(parents=True)
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    (profile / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    return home, profile




@pytest.mark.parametrize(
    "sticky,expected_args",
    [
        ("jishu", ["-p", "default"]),
        ("default", ["-p", "default"]),
        ("", ["-p", "default"]),
        (None, ["-p", "default"]),
        ("../escape", ["-p", "default"]),
        ("[invalid", ["-p", "default"]),
    ],
)
def test_official_enable_child_pins_root_home_against_sticky(
    tmp_path, monkeypatch, sticky, expected_args
):
    """A root home stays bound even with a sticky profile active (#507)."""
    home, _profile = _profile_home(tmp_path)
    if sticky is not None:
        (home / "active_profile").write_text(sticky + "\n", encoding="utf-8")
    binding = SimpleNamespace(
        checkout_root=tmp_path / "checkout",
        runtime_python=Path(sys.executable),
        hermes_home=home,
    )
    monkeypatch.setattr(plugin, "_verify_official_enable_source", lambda *a, **k: None)
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(plugin.subprocess, "run", run)

    assert plugin._run_official_enable(binding) == 0
    assert captured["argv"][-6:] == [
        *expected_args, "plugins", "enable", plugin.PLUGIN_KEY,
        "--no-allow-tool-override",
    ]


def test_official_enable_child_skips_flag_for_explicit_profiles_home(
    tmp_path, monkeypatch
):
    """An explicit profiles/<name> home is trusted by the official CLI (#507)."""
    _home, profile = _profile_home(tmp_path)
    (profile.parent.parent / "active_profile").write_text(
        "jishu\n", encoding="utf-8"
    )
    binding = SimpleNamespace(
        checkout_root=tmp_path / "checkout",
        runtime_python=Path(sys.executable),
        hermes_home=profile,
    )
    monkeypatch.setattr(plugin, "_verify_official_enable_source", lambda *a, **k: None)
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(plugin.subprocess, "run", run)

    assert plugin._run_official_enable(binding) == 0
    assert captured["argv"][-4:] == [
        "plugins", "enable", plugin.PLUGIN_KEY,
        "--no-allow-tool-override",
    ]


def test_enable_plugin_restores_root_config_after_enable_and_sticky_change(
    tmp_path, monkeypatch
):
    """Enable then restore the bound root config across a sticky change (#507)."""
    checkout, home = _checkout(tmp_path)
    profile = home / "profiles" / "jishu"
    profile.mkdir(parents=True)
    _write_utf8(profile / "config.yaml", "plugins: {}\n")
    (home / "active_profile").write_text("jishu\n", encoding="utf-8")
    original = "# preserve\nplugins: {}\n"
    _write_utf8(home / "config.yaml", original)
    monkeypatch.setattr(
        plugin,
        "_plan_enabled_config_bytes",
        lambda b, p, raw, desired, enabled: _exact_plan(
            yaml.safe_dump(desired).encode()
        ),
    )
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None
    )
    preimage = plugin.prepare_plugin_config(binding)

    def official_enable(candidate):
        # The official child writes the bound root config, not the profile.
        assert candidate.config_path == home / "config.yaml"
        config = yaml.safe_load(candidate.config_path.read_bytes())
        plugins = config.setdefault("plugins", {})
        plugins["enabled"] = sorted(
            set(plugins.get("enabled") or []) | {plugin.PLUGIN_KEY}
        )
        plugins["disabled"] = sorted(
            set(plugins.get("disabled") or []) - {plugin.PLUGIN_KEY}
        )
        plugins.setdefault("entries", {}).setdefault(plugin.PLUGIN_KEY, {})[
            "allow_tool_override"
        ] = False
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", official_enable)
    ownership = plugin.enable_plugin(binding, preimage)
    enabled_bytes = (home / "config.yaml").read_bytes()
    assert plugin.PLUGIN_KEY in yaml.safe_load(enabled_bytes)["plugins"]["enabled"]

    # The sticky profile changes before restore; the root evidence stays put.
    (home / "active_profile").write_text("other\n", encoding="utf-8")
    plugin.restore_plugin_config(binding, preimage, ownership)
    assert (home / "config.yaml").read_bytes() == original.encode()
    assert (profile / "config.yaml").read_text(encoding="utf-8") == "plugins: {}\n"


def test_official_enable_visibility_probe_refuses_unresolvable_plugin(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    pm_dir = binding.checkout_root / "pm"
    pm_dir.mkdir()
    _write_utf8(pm_dir / "environments.py", "# PM-managed checkout\n")
    payload = json.dumps({"entry_points": [], "resolved": None, "error": None})
    stdout = "Hermes plugins list\nStartup banner\n" + payload + "\n"
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(plugin.subprocess, "run", run)

    note = plugin._official_enable_visibility_note(binding)

    assert note is not None
    assert "plugin not resolvable in the selected runtime" in note
    assert "out=" in note
    assert captured["argv"][:6] == [
        str(binding.runtime_python),
        "-I",
        "-X",
        "utf8",
        "-c",
        plugin._OFFICIAL_ENABLE_PROBE_SCRIPT,
    ]
    assert captured["argv"][-2:] == ["plugins", "list"]


def test_enable_plugin_visibility_probe_blocks_before_official_enable(tmp_path, monkeypatch):
    original = "token: PRIVATE-CANARY\nplugins: {}\n"
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)
    before_bytes = binding.config_path.read_bytes()
    monkeypatch.setattr(
        plugin,
        "_official_enable_visibility_note",
        lambda candidate: "plugin not resolvable in the selected runtime",
    )
    monkeypatch.setattr(
        plugin,
        "_run_official_enable",
        lambda candidate: pytest.fail("official enable must not run"),
    )

    with pytest.raises(plugin.PluginConfigRefused, match="visibility probe"):
        plugin.enable_plugin(binding, preimage)

    assert binding.config_path.read_bytes() == before_bytes
    assert preimage.backup_path.read_bytes() == before_bytes
    assert preimage.journal_path.is_file()


def test_enable_plugin_visibility_probe_passes_then_enables(tmp_path, monkeypatch):
    original = (
        "token: PRIVATE-CANARY\n"
        "plugins:\n"
        "  disabled: [hermes-feishu-card, keep-disabled]\n"
        "  enabled: [keep-enabled]\n"
    )
    binding = _binding(tmp_path, monkeypatch, original)
    preimage = plugin.prepare_plugin_config(binding)
    probe_calls = []
    enable_calls = []

    def probe(candidate):
        probe_calls.append(candidate)
        return None

    def official_enable(candidate):
        enable_calls.append(candidate)
        config = yaml.safe_load(candidate.config_path.read_text(encoding="utf-8"))
        plugins = config.setdefault("plugins", {})
        plugins["enabled"] = sorted(set(plugins.get("enabled", [])) | {plugin.PLUGIN_KEY})
        plugins["disabled"] = sorted(set(plugins.get("disabled", [])) - {plugin.PLUGIN_KEY})
        plugins.setdefault("entries", {}).setdefault(plugin.PLUGIN_KEY, {})[
            "allow_tool_override"
        ] = False
        _write_utf8(candidate.config_path, yaml.safe_dump(config))
        return 0

    monkeypatch.setattr(plugin, "_official_enable_visibility_note", probe)
    monkeypatch.setattr(plugin, "_run_official_enable", official_enable)

    ownership = plugin.enable_plugin(binding, preimage)

    assert len(probe_calls) == 1
    assert len(enable_calls) == 1
    assert ownership.added_by_hfc is True
    config = yaml.safe_load(binding.config_path.read_text(encoding="utf-8"))
    assert config["token"] == "PRIVATE-CANARY"
    assert config["plugins"]["enabled"] == [plugin.PLUGIN_KEY, "keep-enabled"]
    assert config["plugins"]["disabled"] == ["keep-disabled"]


@pytest.mark.parametrize(
    "raw, canary",
    [
        ("api_key=SYNTHETIC_API_CANARY", "SYNTHETIC_API_CANARY"),
        ("API_KEY: SYNTHETIC_API_CANARY", "SYNTHETIC_API_CANARY"),
        ("Authorization: Bearer SYNTHETIC_AUTH_CANARY", "SYNTHETIC_AUTH_CANARY"),
        ("Authorization: Negotiate SYNTHETIC_NEGOTIATE_CANARY", "SYNTHETIC_NEGOTIATE_CANARY"),
        ("Authorization=Basic SYNTHETIC_BASIC_CANARY", "SYNTHETIC_BASIC_CANARY"),
        ('password: "first SYNTHETIC_QUOTED_CANARY"', "SYNTHETIC_QUOTED_CANARY"),
        ("token: 'first SYNTHETIC_SINGLE_CANARY'", "SYNTHETIC_SINGLE_CANARY"),
        ("https://user:SYNTHETIC_URL_CANARY@api.example.test/path", "SYNTHETIC_URL_CANARY"),
        ("Bearer SYNTHETIC_STANDALONE_CANARY", "SYNTHETIC_STANDALONE_CANARY"),
        ('{"api_key": "SYNTHETIC_JSON_CANARY"}', "SYNTHETIC_JSON_CANARY"),
    ],
)
def test_official_child_output_note_redacts_credential_shapes(raw, canary):
    note = plugin._official_child_output_note(raw)

    assert canary not in note
    assert "[REDACTED]" in note


@pytest.mark.skipif(
    not (_REVIEW_FIXED_SOURCE_ROOT / "hermes_cli" / "main.py").is_file(),
    reason="fixed Hermes fixture unavailable",
)
@pytest.mark.parametrize(
    "raw, canary",
    [
        ("api_key=SYNTHETIC_API_CANARY", "SYNTHETIC_API_CANARY"),
        ("Authorization: Bearer SYNTHETIC_AUTH_CANARY", "SYNTHETIC_AUTH_CANARY"),
        ('password: "first SYNTHETIC_QUOTED_CANARY"', "SYNTHETIC_QUOTED_CANARY"),
        ('--password "first SYNTHETIC_FLAG_CANARY"', "SYNTHETIC_FLAG_CANARY"),
        ('--api-key "first SYNTHETIC_API_FLAG_CANARY"', "SYNTHETIC_API_FLAG_CANARY"),
        (
            "failure: password=" + json.dumps('first " SYNTHETIC_ESCAPED_CANARY'),
            "SYNTHETIC_ESCAPED_CANARY",
        ),
        (
            "failure: api_key=" + json.dumps('first " SYNTHETIC_API_ESCAPED_CANARY'),
            "SYNTHETIC_API_ESCAPED_CANARY",
        ),
    ],
)
def test_official_enable_failure_redacts_review_canaries_in_v3_refusal(
    tmp_path, monkeypatch, raw, canary
):
    monkeypatch.setenv("HFC_FIXED_TAG_SOURCE_ROOT", str(_REVIEW_FIXED_SOURCE_ROOT))
    helpers = _review_v3_helpers()
    root, binding, entrypoint, decision = helpers._fixture(tmp_path)
    before = binding.config_path.read_bytes()
    originals = {target: (root / target).read_bytes() for target in v3.HYBRID_PATCH_TARGET_ORDER}
    monkeypatch.setattr(
        plugin,
        "_plan_enabled_config_bytes",
        lambda b, p, original, desired, enabled: helpers._exact_plan(
            yaml.safe_dump(desired).encode()
        ),
    )
    monkeypatch.setattr(
        plugin.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=raw, stderr=""),
    )
    try:
        with pytest.raises(v3.FixedTagInstallRefused) as caught:
            helpers._execute_diagnostic_fixture(binding, entrypoint, decision)
        assert canary not in str(caught.value)
        assert "[REDACTED]" in str(caught.value)
        assert v3._bounded_install_refusal(str(caught.value))
        assert binding.config_path.read_bytes() == before
        assert all((root / target).read_bytes() == value for target, value in originals.items())
    finally:
        v3.restore_fixed_tag_hybrid_install(binding=binding)


def test_official_enable_non_utf8_child_output_is_decoded(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    monkeypatch.setattr(plugin, "_verify_official_enable_source", lambda *args, **kwargs: None)
    original_run = subprocess.run

    def raw_child(argv, **kwargs):
        return original_run(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.stdout.buffer.write(bytes([255])); sys.exit(1)",
            ],
            **kwargs,
        )

    monkeypatch.setattr(plugin.subprocess, "run", raw_child)

    assert plugin._run_official_enable(binding) == 1
    assert plugin._official_enable_diagnostic is not None
    assert chr(0xFFFD) in plugin._official_enable_diagnostic
    assert "official plugin enable failed" in plugin._official_enable_failure_message()


def test_official_enable_visibility_probe_non_utf8_output_is_classified(tmp_path, monkeypatch):
    binding = _binding(tmp_path, monkeypatch, "plugins: {}\n")
    pm_dir = binding.checkout_root / "pm"
    pm_dir.mkdir()
    _write_utf8(pm_dir / "environments.py", "# PM-managed checkout\n")
    original_run = subprocess.run

    def raw_child(argv, **kwargs):
        return original_run(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.stdout.buffer.write(bytes([255]))",
            ],
            **kwargs,
        )

    monkeypatch.setattr(plugin.subprocess, "run", raw_child)

    note = plugin._official_enable_visibility_note(binding)

    assert note is not None
    assert "probe output unreadable" in note
    assert chr(0xFFFD) in note
