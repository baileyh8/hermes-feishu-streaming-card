from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest
import yaml

from hermes_feishu_card.install import plugin


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


def test_enable_plugin_rejects_unrelated_semantic_drift_and_restores_preimage(
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

    assert binding.config_path.read_text(encoding="utf-8") == original


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
