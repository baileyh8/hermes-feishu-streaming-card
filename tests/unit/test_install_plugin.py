from __future__ import annotations

import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card.install import plugin, plugin_config_bytes


def _exact_plan(postimage: bytes):
    return SimpleNamespace(
        postimage=postimage,
        verify_source=lambda: None,
        matches_expected=lambda expected: plugin._load_config_mapping(postimage) == expected,
    )


def _checkout(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    checkout = home / "hermes-agent"
    (checkout / ".venv" / "bin").mkdir(parents=True)
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    launcher = checkout / ".venv" / "bin" / "python"
    launcher.symlink_to("/usr/bin/python3")
    return checkout, home


def _runtime_payload(checkout: Path) -> dict[str, object]:
    prefix = checkout / ".venv"
    purelib = prefix / "lib" / "python3.12" / "site-packages"
    return {
        "executable": str(checkout / ".venv" / "bin" / "python"),
        "prefix": str(prefix),
        "base_prefix": "/usr",
        "purelib": str(purelib),
        "platlib": str(purelib),
    }


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
    assert binding.runtime_python == checkout / ".venv" / "bin" / "python"
    assert binding.python_identity.startswith("sha256:")


def test_runtime_binding_rejects_symlinked_home_or_config(tmp_path, monkeypatch):
    checkout, home = _checkout(tmp_path)
    monkeypatch.setattr(
        plugin, "_probe_runtime_identity", lambda launcher: _runtime_payload(checkout)
    )
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    (real_home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(real_home, target_is_directory=True)

    with pytest.raises(plugin.RuntimeBindingRefused, match="Hermes home"):
        plugin.resolve_runtime_binding(
            checkout_root=checkout, hermes_home=linked_home, profile_id=None
        )

    (home / "config.yaml").unlink()
    (home / "config.yaml").symlink_to(real_home / "config.yaml")
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


def test_runtime_binding_rejects_wrong_prefix_and_nonexecutable_target(
    tmp_path, monkeypatch
):
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

    launcher = checkout / ".venv" / "bin" / "python"
    launcher.unlink()
    target = tmp_path / "python-target"
    target.write_text("not executable", encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    launcher.symlink_to(target)
    with pytest.raises(plugin.RuntimeBindingRefused, match="executable"):
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
    (home / "config.yaml").write_text(config_text, encoding="utf-8")
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
    monkeypatch.setattr(plugin.os, "name", "nt")
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
        candidate.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
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
        candidate.config_path.write_text(
            "token: CHANGED\nplugins:\n  enabled: [hermes-feishu-card]\n",
            encoding="utf-8",
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
        candidate.config_path.write_text(
            "plugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", official_enable)
    postimage = b"plugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n"
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", lambda *a: _exact_plan(postimage))
    ownership = plugin.enable_plugin(binding, preimage)
    binding.config_path.write_text("plugins: {}\nuser_change: true\n", encoding="utf-8")

    with pytest.raises(plugin.PluginConfigRefused, match="changed"):
        plugin.restore_plugin_config(binding, preimage, ownership)


@pytest.mark.parametrize("legacy", [False, True])
def test_runtime_binding_uses_pm_committed_venv_with_real_child(tmp_path, legacy):
    import subprocess
    import sys

    checkout, home = _checkout(tmp_path)
    if not legacy:
        (checkout / ".venv" / "bin" / "python").unlink()
    managed = tmp_path / "managed environment" / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(managed)], check=True)
    pm = checkout / "pm"
    pm.mkdir()
    (pm / "__init__.py").write_text("")
    (pm / "environments.py").write_text(
        "from pathlib import Path\n"
        f"def committed_venv(root): return Path({str(managed)!r})\n"
    )
    binding = plugin.resolve_runtime_binding(
        checkout_root=checkout, hermes_home=home, profile_id=None,
    )
    assert binding.runtime_python == managed / "bin" / "python"
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
        candidate.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
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
        candidate.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
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
        candidate.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
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
        candidate.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
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
        candidate.config_path.write_text(yaml.safe_dump(expected))
        if outcome == "exception":
            raise OSError("synthetic failure after official config write")
        return 1
    monkeypatch.setattr(plugin, "_run_official_enable", failed_enable)
    with pytest.raises(plugin.PluginConfigRefused, match="official plugin enable failed"):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() == original
