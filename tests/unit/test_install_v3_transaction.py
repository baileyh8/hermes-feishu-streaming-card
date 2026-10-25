from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card.install import plugin, plugin_config_bytes, v3
from hermes_feishu_card import cli
from hermes_feishu_card.integration import (
    HYBRID_REQUIRED_NATIVE_CAPABILITIES,
    HYBRID_REQUIRED_PATCH_GROUPS,
    NativeHookCapabilities,
    PatchCapabilities,
    select_integration_mode,
)


def _exact_plan(postimage: bytes):
    return SimpleNamespace(
        postimage=postimage,
        verify_source=lambda: None,
        matches_expected=lambda expected: plugin._load_config_mapping(postimage) == expected,
    )


FIXED_SOURCE_ROOT = Path(
    os.environ.get(
        "HFC_FIXED_TAG_SOURCE_ROOT",
        "/private/tmp/hermes-agent-v2026.8.3-v430-audit",
    )
)


@pytest.fixture(autouse=True)
def expected_test_process_bytes(monkeypatch):
    monkeypatch.setattr(
        plugin,
        "_plan_enabled_config_bytes",
        lambda b, p, raw, desired, enabled: _exact_plan(yaml.safe_dump(desired).encode()),
    )


def _fixture(tmp_path: Path):
    root = tmp_path / "home" / "hermes-agent"
    (root / "hermes_cli").mkdir(parents=True)
    for name in ("main.py", "__init__.py"):
        shutil.copy2(FIXED_SOURCE_ROOT / "hermes_cli" / name, root / "hermes_cli" / name)
    for relative in plugin_config_bytes.FAMILIES["fixed"]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXED_SOURCE_ROOT / relative, target)
    for target in v3.HYBRID_PATCH_TARGET_ORDER:
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXED_SOURCE_ROOT / target, path)
    home = root.parent
    (home / "config.yaml").write_text(
        "private: SECRET-CANARY\nplugins: {}\n", encoding="utf-8"
    )
    runtime = root / ".venv" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    runtime.symlink_to("/usr/bin/python3")
    purelib = root / ".venv" / "lib" / "python3.12" / "site-packages"
    binding = plugin.HermesRuntimeBinding(
        checkout_root=root,
        runtime_python=runtime,
        runtime_python_resolved=Path("/usr/bin/python3"),
        python_identity="sha256:" + "d" * 64,
        hermes_home=home,
        config_path=home / "config.yaml",
        purelib=purelib,
        platlib=purelib,
    )
    entrypoint = plugin.PluginEntrypointProbe(
        status="verified",
        reason="verified",
        version="4.3.0",
        module_origin=purelib / "hermes_feishu_card" / "hermes_plugin.py",
    )
    decision = select_integration_mode(
        NativeHookCapabilities.from_names(HYBRID_REQUIRED_NATIVE_CAPABILITIES),
        PatchCapabilities.from_names(HYBRID_REQUIRED_PATCH_GROUPS),
    )
    return root, binding, entrypoint, decision


def _official_enable(binding):
    config = yaml.safe_load(binding.config_path.read_text(encoding="utf-8"))
    plugins = config.setdefault("plugins", {})
    plugins["enabled"] = ["hermes-feishu-card"]
    plugins["disabled"] = []
    plugins["entries"] = {
        "hermes-feishu-card": {"allow_tool_override": False}
    }
    binding.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return 0


def test_execute_fixed_tag_install_commits_seven_targets_config_and_v3_manifest(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    originals = {
        target: (root / target).read_bytes()
        for target in v3.HYBRID_PATCH_TARGET_ORDER
    }
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)

    result = v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version="4.3.0",
    )

    assert result.status == "installed"
    manifest = json.loads((root / ".hermes_feishu_card_manifest").read_text())
    assert manifest["phase"] == "installed"
    assert manifest["integration"]["mode"] == "hybrid"
    assert set(manifest["targets"]) == set(v3.HYBRID_PATCH_TARGET_ORDER)
    for target in v3.HYBRID_PATCH_TARGET_ORDER:
        assert (root / target).read_bytes() != originals[target]
        assert (root / (target + ".hermes_feishu_card.bak")).read_bytes() == originals[target]
    config = yaml.safe_load(binding.config_path.read_text(encoding="utf-8"))
    assert config["private"] == "SECRET-CANARY"
    assert config["plugins"]["enabled"] == ["hermes-feishu-card"]
    inspected = v3.inspect_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        package_version="4.3.0",
    )
    assert inspected.status == "installed"
    assert inspected.gateway_restart_required is False


def test_inspect_fixed_tag_install_rejects_patched_source_drift(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)
    v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version="4.3.0",
    )
    with (root / "gateway" / "run.py").open("ab") as file:
        file.write(b"\n# drift\n")

    with pytest.raises(v3.FixedTagInstallRefused, match="hash|patch"):
        v3.inspect_fixed_tag_hybrid_install(
            binding=binding,
            entrypoint=entrypoint,
            package_version="4.3.0",
        )


def test_restore_fixed_tag_install_restores_sources_config_and_owned_evidence(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    originals = {
        target: (root / target).read_bytes()
        for target in v3.HYBRID_PATCH_TARGET_ORDER
    }
    original_config = binding.config_path.read_bytes()
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)
    v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version="4.3.0",
    )

    result = v3.restore_fixed_tag_hybrid_install(binding=binding)

    assert result.status == "restored"
    assert all((root / target).read_bytes() == originals[target] for target in originals)
    assert binding.config_path.read_bytes() == original_config
    assert not (root / ".hermes_feishu_card_manifest").exists()
    assert all(
        not (root / (target + ".hermes_feishu_card.bak")).exists()
        for target in originals
    )
    assert not any(
        path.name.startswith("hfc-config-preimage-")
        for path in (binding.hermes_home / ".hermes_feishu_card" / "install").iterdir()
    )


def test_restore_fixed_tag_install_refuses_config_drift_without_source_mutation(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)
    v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version="4.3.0",
    )
    patched = {
        target: (root / target).read_bytes()
        for target in v3.HYBRID_PATCH_TARGET_ORDER
    }
    binding.config_path.write_text("user_change: true\n", encoding="utf-8")

    with pytest.raises(v3.FixedTagInstallRefused, match="config changed"):
        v3.restore_fixed_tag_hybrid_install(binding=binding)

    assert all((root / target).read_bytes() == patched[target] for target in patched)
    assert (root / ".hermes_feishu_card_manifest").exists()


def test_execute_fixed_tag_install_enable_failure_keeps_prepared_evidence(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    originals = {
        target: (root / target).read_bytes()
        for target in v3.HYBRID_PATCH_TARGET_ORDER
    }
    monkeypatch.setattr(plugin, "_run_official_enable", lambda _binding: 1)

    with pytest.raises(v3.FixedTagInstallRefused, match="enable"):
        v3.execute_fixed_tag_hybrid_install(
            binding=binding,
            entrypoint=entrypoint,
            decision=decision,
            source_commit=v3.FIXED_TAG_COMMIT,
            plugin_evidence_sha256="sha256:" + "a" * 64,
            package_version="4.3.0",
        )

    manifest = json.loads((root / ".hermes_feishu_card_manifest").read_text())
    assert manifest["phase"] == "prepared"
    assert all((root / target).read_bytes() == originals[target] for target in originals)
    assert binding.config_path.read_text(encoding="utf-8").startswith(
        "private: SECRET-CANARY"
    )

    recovered = v3.restore_fixed_tag_hybrid_install(binding=binding)
    assert recovered.status == "restored"
    assert recovered.gateway_restart_required is False
    assert not (root / ".hermes_feishu_card_manifest").exists()
    assert all(
        not (root / (target + ".hermes_feishu_card.bak")).exists()
        for target in originals
    )


def test_execute_fixed_tag_install_source_failure_restores_config_and_sources(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    originals = {
        target: (root / target).read_bytes()
        for target in v3.HYBRID_PATCH_TARGET_ORDER
    }
    original_config = binding.config_path.read_bytes()
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)
    real_commit = v3._commit_file_set
    calls = []

    def fail_final(changes, *, expected):
        calls.append(tuple(changes))
        if any(path.name == "run.py" for path in changes):
            raise OSError("injected source commit failure")
        return real_commit(changes, expected=expected)

    monkeypatch.setattr(v3, "_commit_file_set", fail_final)
    with pytest.raises(v3.FixedTagInstallRefused, match="source commit"):
        v3.execute_fixed_tag_hybrid_install(
            binding=binding,
            entrypoint=entrypoint,
            decision=decision,
            source_commit=v3.FIXED_TAG_COMMIT,
            plugin_evidence_sha256="sha256:" + "a" * 64,
            package_version="4.3.0",
        )

    assert all((root / target).read_bytes() == originals[target] for target in originals)
    assert binding.config_path.read_bytes() == original_config
    manifest = json.loads((root / ".hermes_feishu_card_manifest").read_text())
    assert manifest["phase"] == "prepared"


def test_execute_fixed_tag_install_refuses_existing_evidence_before_config_write(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    backup = root / "gateway" / "run.py.hermes_feishu_card.bak"
    backup.write_bytes(b"foreign")
    original_config = binding.config_path.read_bytes()
    monkeypatch.setattr(
        plugin,
        "prepare_plugin_config",
        lambda _binding: pytest.fail("config preimage must not be written"),
    )

    with pytest.raises(v3.FixedTagInstallRefused, match="evidence"):
        v3.execute_fixed_tag_hybrid_install(
            binding=binding,
            entrypoint=entrypoint,
            decision=decision,
            source_commit=v3.FIXED_TAG_COMMIT,
            plugin_evidence_sha256="sha256:" + "a" * 64,
            package_version="4.3.0",
        )

    assert binding.config_path.read_bytes() == original_config


def test_cli_fixed_tag_dispatch_uses_verified_v3_transaction(
    tmp_path, monkeypatch, capsys
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    detection = SimpleNamespace(root=root)
    native_probe = SimpleNamespace(
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(cli, "is_fixed_tag_checkout", lambda _root: True)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **kwargs: binding)
    monkeypatch.setattr(
        cli, "probe_plugin_entrypoint", lambda *args, **kwargs: entrypoint
    )
    monkeypatch.setattr(
        cli,
        "detect_fixed_tag_integration",
        lambda *args, **kwargs: SimpleNamespace(
            decision=decision, native_probe=native_probe
        ),
    )
    monkeypatch.setattr(
        cli,
        "execute_fixed_tag_hybrid_install",
        lambda **kwargs: v3.FixedTagInstallResult(
            status="installed",
            manifest_path=root / ".hermes_feishu_card_manifest",
            gateway_restart_required=True,
        ),
    )
    monkeypatch.setattr(cli, "PACKAGE_VERSION", "4.3.0")

    result = cli._run_fixed_tag_v3_install(
        SimpleNamespace(hermes_home=str(binding.hermes_home)), detection
    )

    assert result == 0
    assert capsys.readouterr().out.splitlines() == [
        "integration.mode: hybrid",
        "install ok",
        "gateway.restart_required: hermes gateway start",
    ]


def test_cli_fixed_tag_v2_migration_restores_legacy_before_runtime_binding(
    tmp_path, monkeypatch
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    (root / ".hermes_feishu_card_manifest").write_text(
        '{"manifest_version":2}\n', encoding="utf-8"
    )
    order = []
    monkeypatch.setattr(cli, "is_fixed_tag_checkout", lambda _root: True)

    def restore(_root):
        order.append("restore")
        (root / ".hermes_feishu_card_manifest").unlink()

    def resolve(**kwargs):
        order.append("bind")
        return binding

    monkeypatch.setattr(cli, "_restore", restore)
    monkeypatch.setattr(cli, "resolve_runtime_binding", resolve)
    monkeypatch.setattr(
        cli, "probe_plugin_entrypoint", lambda *args, **kwargs: entrypoint
    )
    monkeypatch.setattr(
        cli,
        "detect_fixed_tag_integration",
        lambda *args, **kwargs: SimpleNamespace(
            decision=decision,
            native_probe=SimpleNamespace(
                source_commit=v3.FIXED_TAG_COMMIT,
                plugin_evidence_sha256="sha256:" + "a" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "execute_fixed_tag_hybrid_install",
        lambda **kwargs: v3.FixedTagInstallResult(
            status="installed",
            manifest_path=root / ".hermes_feishu_card_manifest",
            gateway_restart_required=False,
        ),
    )
    monkeypatch.setattr(cli, "PACKAGE_VERSION", "4.3.0")

    assert cli._run_fixed_tag_v3_install(
        SimpleNamespace(hermes_home=str(binding.hermes_home)),
        SimpleNamespace(root=root),
    ) == 0
    assert order == ["restore", "bind"]


def test_cli_non_fixed_checkout_preserves_legacy_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "is_fixed_tag_checkout", lambda _root: False)
    assert cli._run_fixed_tag_v3_install(
        SimpleNamespace(hermes_home=None), SimpleNamespace(root=tmp_path)
    ) is None


def test_cli_fixed_tag_installed_v3_is_idempotent_without_native_reprobe(
    tmp_path, monkeypatch, capsys
):
    root, binding, entrypoint, _decision = _fixture(tmp_path)
    (root / ".hermes_feishu_card_manifest").write_text(
        '{"manifest_version":3}\n', encoding="utf-8"
    )
    monkeypatch.setattr(cli, "is_fixed_tag_checkout", lambda _root: True)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **kwargs: binding)
    monkeypatch.setattr(
        cli, "probe_plugin_entrypoint", lambda *args, **kwargs: entrypoint
    )
    monkeypatch.setattr(
        cli,
        "inspect_fixed_tag_hybrid_install",
        lambda **kwargs: v3.FixedTagInstallResult(
            status="installed",
            manifest_path=root / ".hermes_feishu_card_manifest",
            gateway_restart_required=False,
        ),
    )
    monkeypatch.setattr(
        cli,
        "detect_fixed_tag_integration",
        lambda *args, **kwargs: pytest.fail("installed V3 must not re-probe dirty source"),
    )
    monkeypatch.setattr(cli, "PACKAGE_VERSION", "4.3.0")

    assert cli._run_fixed_tag_v3_install(
        SimpleNamespace(hermes_home=str(binding.hermes_home)),
        SimpleNamespace(root=root),
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        "integration.mode: hybrid",
        "install ok",
    ]


@pytest.mark.parametrize("command", ["restore", "uninstall"])
def test_cli_v3_restore_dispatches_before_legacy_binding_free_path(
    tmp_path, monkeypatch, capsys, command
):
    root, binding, _entrypoint, _decision = _fixture(tmp_path)
    (root / ".hermes_feishu_card_manifest").write_text(
        '{"manifest_version":3}\n', encoding="utf-8"
    )
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **kwargs: binding)
    monkeypatch.setattr(
        cli,
        "restore_fixed_tag_hybrid_install",
        lambda **kwargs: v3.FixedTagInstallResult(
            status="restored",
            manifest_path=root / ".hermes_feishu_card_manifest",
            gateway_restart_required=True,
        ),
    )
    monkeypatch.setattr(
        cli,
        "_restore",
        lambda _root: pytest.fail("V3 must not enter Legacy restore"),
    )
    args = SimpleNamespace(hermes_dir=str(root), hermes_home=str(binding.hermes_home))

    result = (
        cli._run_restore(args) if command == "restore" else cli._run_uninstall(args)
    )

    assert result == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{command} ok",
        "gateway.restart_required: hermes gateway start",
    ]


def test_doctor_uses_v3_inspector_without_legacy_recovery_diagnostics(
    tmp_path, monkeypatch, capsys
):
    root, binding, _entrypoint, decision = _fixture(tmp_path)
    entrypoint = plugin.PluginEntrypointProbe(
        status="verified",
        reason="verified",
        version=cli.PACKAGE_VERSION,
        module_origin=binding.purelib / "hermes_feishu_card" / "hermes_plugin.py",
    )
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)
    v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version=cli.PACKAGE_VERSION,
    )
    detection = cli.detect_hermes(root)
    assert detection.supported is True
    binding_calls = []

    def resolve_binding(**kwargs):
        binding_calls.append(kwargs)
        return binding

    monkeypatch.setattr(cli, "resolve_runtime_binding", resolve_binding)
    monkeypatch.setattr(
        cli, "probe_plugin_entrypoint", lambda *args, **kwargs: entrypoint
    )
    monkeypatch.setattr(
        cli,
        "_diagnose_install_state",
        lambda _detection: pytest.fail("V3 doctor must not use Legacy install diagnosis"),
    )
    monkeypatch.setattr(
        cli,
        "plan_recovery",
        lambda _detection: pytest.fail("V3 doctor must not use Legacy recovery"),
    )
    monkeypatch.setattr(
        cli,
        "plan_integrity_repair",
        lambda _detection: pytest.fail("V3 doctor must not use Legacy integrity planning"),
    )
    monkeypatch.setattr(cli, "status_sidecar", lambda _config: {})
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9013\n", encoding="utf-8")

    report = cli._build_doctor_report(
        config_path,
        {
            "server": {"host": "127.0.0.1", "port": 9013},
            "feishu": {"app_id": "app", "app_secret": "secret"},
        },
        SimpleNamespace(
            skip_hermes=False,
            hermes_dir=str(root),
            hermes_home=str(binding.hermes_home),
            profile_id=None,
            _profile_id="default",
            _profile_source="fallback_default",
            _event_url="http://127.0.0.1:9013/events",
        ),
    )

    assert report.install_state["contract"] == "v3"
    assert report.install_state["status"] == "installed"
    assert report.install_state["recovery_state"] == "installed"
    assert binding_calls == [
        {
            "checkout_root": detection.root,
            "hermes_home": str(binding.hermes_home),
            "profile_id": None,
        }
    ]
    finding_codes = {finding.code for finding in report.findings}
    assert "install_state_installed" in finding_codes
    assert not finding_codes & {
        "install_state_incomplete",
        "manifest_current_hash_invalid",
        "manifest_backup_hash_invalid",
        "manifest_path_mismatch",
        "backup_source_mismatch",
        "cron_backup_source_mismatch",
        "base_install_state_incomplete",
    }

    binding_calls.clear()
    exit_code = cli.main(
        [
            "doctor",
            "--config",
            str(config_path),
            "--hermes-dir",
            str(root),
            "--hermes-home",
            str(binding.hermes_home),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["install_state"]["contract"] == "v3"
    assert payload["install_state"]["status"] == "installed"
    assert binding_calls == [
        {
            "checkout_root": detection.root,
            "hermes_home": binding.hermes_home,
            "profile_id": None,
        }
    ]
    cli_finding_codes = {finding["code"] for finding in payload["findings"]}
    assert "install_state_installed" in cli_finding_codes
    assert "install_state_incomplete" not in cli_finding_codes


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("phase", "v3_manifest_recovery_required"),
        ("config", "v3_config_changed"),
        ("target", "v3_target_changed"),
        ("backup", "v3_backup_changed"),
        ("runtime_identity", "v3_runtime_binding_changed"),
    ],
)
def test_doctor_reports_v3_specific_fail_closed_findings(
    tmp_path, monkeypatch, tamper, expected_code
):
    root, binding, _entrypoint, decision = _fixture(tmp_path)
    entrypoint = plugin.PluginEntrypointProbe(
        status="verified",
        reason="verified",
        version=cli.PACKAGE_VERSION,
        module_origin=binding.purelib / "hermes_feishu_card" / "hermes_plugin.py",
    )
    monkeypatch.setattr(plugin, "_run_official_enable", _official_enable)
    v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version=cli.PACKAGE_VERSION,
    )
    resolved_binding = binding
    if tamper == "phase":
        manifest_path = root / ".hermes_feishu_card_manifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["phase"] = "plugin_enabled"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif tamper == "config":
        binding.config_path.write_text(
            binding.config_path.read_text(encoding="utf-8") + "changed: true\n",
            encoding="utf-8",
        )
    elif tamper == "target":
        with (root / v3.HYBRID_PATCH_TARGET_ORDER[0]).open("ab") as handle:
            handle.write(b"\n# changed\n")
    elif tamper == "backup":
        backup = root / (
            v3.HYBRID_PATCH_TARGET_ORDER[0] + ".hermes_feishu_card.bak"
        )
        with backup.open("ab") as handle:
            handle.write(b"\n# changed\n")
    elif tamper == "runtime_identity":
        resolved_binding = replace(binding, python_identity="sha256:" + "e" * 64)

    detection = cli.detect_hermes(root)
    assert detection.supported is True
    monkeypatch.setattr(
        cli, "resolve_runtime_binding", lambda **kwargs: resolved_binding
    )
    monkeypatch.setattr(
        cli, "probe_plugin_entrypoint", lambda *args, **kwargs: entrypoint
    )
    monkeypatch.setattr(
        cli,
        "_diagnose_install_state",
        lambda _detection: pytest.fail("V3 doctor must not use Legacy install diagnosis"),
    )
    monkeypatch.setattr(
        cli,
        "plan_recovery",
        lambda _detection: pytest.fail("V3 doctor must not use Legacy recovery"),
    )
    monkeypatch.setattr(
        cli,
        "plan_integrity_repair",
        lambda _detection: pytest.fail("V3 doctor must not use Legacy integrity planning"),
    )
    monkeypatch.setattr(cli, "status_sidecar", lambda _config: {})
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9013\n", encoding="utf-8")

    report = cli._build_doctor_report(
        config_path,
        {
            "server": {"host": "127.0.0.1", "port": 9013},
            "feishu": {"app_id": "app", "app_secret": "secret"},
        },
        SimpleNamespace(
            skip_hermes=False,
            hermes_dir=str(root),
            hermes_home=str(binding.hermes_home),
            profile_id=None,
            _profile_id="default",
            _profile_source="fallback_default",
            _event_url="http://127.0.0.1:9013/events",
        ),
    )

    assert report.install_state["contract"] == "v3"
    assert report.install_state["status"] == "incomplete"
    assert report.install_state["manual_action_required"] is True
    assert report.install_state["recovery_executable"] is False
    assert report.install_state["recovery_actions"] == []
    finding_codes = {finding.code for finding in report.findings}
    assert expected_code in finding_codes
    assert "v3_install_incomplete" in finding_codes
    assert not finding_codes & {
        "install_state_incomplete",
        "manifest_current_hash_invalid",
        "manifest_backup_hash_invalid",
        "manifest_path_mismatch",
        "backup_source_mismatch",
        "cron_backup_source_mismatch",
        "base_install_state_incomplete",
    }


def _execute_diagnostic_fixture(binding, entrypoint, decision):
    return v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version="4.3.0",
    )


def test_plugin_enable_exit_zero_unrelated_change_reports_guard_and_preserves_edit(
    tmp_path, monkeypatch,
):
    import subprocess
    import sys

    root, binding, entrypoint, decision = _fixture(tmp_path)
    originals = {target: (root / target).read_bytes() for target in v3.HYBRID_PATCH_TARGET_ORDER}
    original_config = binding.config_path.read_bytes()
    original_run = subprocess.run
    child_results = []

    def controlled_enable_child(argv, **kwargs):
        assert argv[:3] == [str(binding.runtime_python), "-I", "-c"]
        assert argv[4] == str(root)
        assert argv[5:] == ["plugins", "enable", plugin.PLUGIN_KEY, "--no-allow-tool-override"]
        assert kwargs["cwd"] == str(root)
        assert kwargs["env"] == {
            "HOME": str(binding.hermes_home),
            "HERMES_HOME": str(binding.hermes_home),
            "HERMES_INSTALL_ROOT": str(root),
        }
        # Execute a real bounded child at the process port. This synthetic child
        # represents an official command that succeeds but changes other config.
        script = ("from pathlib import Path; "
                  f"Path({str(binding.config_path)!r}).write_text("
                  "'private: CHANGED\\nplugins:\\n  enabled: [hermes-feishu-card]\\n', encoding='utf-8')")
        result = original_run([sys.executable, "-I", "-c", script], **kwargs)
        child_results.append(result)
        return result

    monkeypatch.setattr(plugin.subprocess, "run", controlled_enable_child)
    try:
        with pytest.raises(v3.FixedTagInstallRefused) as caught:
            _execute_diagnostic_fixture(binding, entrypoint, decision)
        assert len(child_results) == 1 and child_results[0].returncode == 0
        assert b"private: CHANGED" in binding.config_path.read_bytes()
        assert all((root / target).read_bytes() == raw for target, raw in originals.items())
        manifest = json.loads((root / ".hermes_feishu_card_manifest").read_text())
        assert manifest["phase"] == "prepared"
        assert type(caught.value.__cause__) is plugin.PluginConfigRefused
        assert str(caught.value.__cause__) == "official plugin enable changed unrelated config"
        assert str(caught.value) == "official plugin enable changed unrelated config"
    finally:
        with pytest.raises(v3.FixedTagInstallRefused, match="config changed"):
            v3.restore_fixed_tag_hybrid_install(binding=binding)
    assert b"private: CHANGED" in binding.config_path.read_bytes()
    assert (root / ".hermes_feishu_card_manifest").exists()
    assert original_config in [p.read_bytes() for p in (binding.hermes_home / ".hermes_feishu_card" / "install").glob("*.yaml")]


def test_plugin_preimage_drift_reports_guard_without_running_enable(tmp_path, monkeypatch):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    originals = {target: (root / target).read_bytes() for target in v3.HYBRID_PATCH_TARGET_ORDER}
    prepare = plugin.prepare_plugin_config
    changed = b"private: USER-EDIT\nplugins: {}\n"

    def prepare_then_edit(candidate):
        preimage = prepare(candidate)
        candidate.config_path.write_bytes(changed)
        return preimage

    monkeypatch.setattr(plugin, "prepare_plugin_config", prepare_then_edit)
    monkeypatch.setattr(plugin, "_run_official_enable", lambda _: pytest.fail("preimage guard must precede command"))
    with pytest.raises(v3.FixedTagInstallRefused) as caught:
        _execute_diagnostic_fixture(binding, entrypoint, decision)
    assert binding.config_path.read_bytes() == changed  # Never erase a concurrent user edit.
    assert all((root / target).read_bytes() == raw for target, raw in originals.items())
    assert type(caught.value.__cause__) is plugin.PluginConfigRefused
    assert str(caught.value.__cause__) == "Hermes config changed after preimage"
    assert str(caught.value) == "Hermes config changed after preimage"


@pytest.mark.parametrize("error_type", [plugin.PluginConfigRefused, RuntimeError])
@pytest.mark.parametrize("message", ["SYNTHETIC-SECRET: token=DO-NOT-EXPORT", "Hermes config changed after preimage SYNTHETIC-SECRET"])
def test_plugin_enable_unknown_exception_is_hidden_and_original_cause_retained(
    tmp_path, monkeypatch, error_type, message,
):
    root, binding, entrypoint, decision = _fixture(tmp_path)
    original_config = binding.config_path.read_bytes()
    originals = {target: (root / target).read_bytes() for target in v3.HYBRID_PATCH_TARGET_ORDER}
    original_error = error_type(message)

    def fail_unknown(*_args):
        raise original_error

    monkeypatch.setattr(plugin, "enable_plugin", fail_unknown)
    try:
        with pytest.raises(v3.FixedTagInstallRefused) as caught:
            _execute_diagnostic_fixture(binding, entrypoint, decision)
        assert str(caught.value) == "official plugin enable failed"
        assert "SYNTHETIC-SECRET" not in str(caught.value)
        assert caught.value.__cause__ is original_error
        assert binding.config_path.read_bytes() == original_config
        assert all((root / target).read_bytes() == raw for target, raw in originals.items())
    finally:
        v3.restore_fixed_tag_hybrid_install(binding=binding)
