from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli as card_cli
from hermes_feishu_card.runtime_control import (
    RuntimeIntegrityFenceBinding,
    RuntimeIntegrityReviewSnapshot,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _run_cli(*args: str, env=None):
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def _legacy_git_install(tmp_path: Path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "HFC Test")
    _git(root, "config", "user.email", "hfc@example.invalid")
    _git(root, "add", "gateway/run.py")
    _git(root, "commit", "-qm", "initial Hermes")
    installed = _run_cli("install", "--hermes-dir", str(root), "--yes")
    assert installed.returncode == 0, installed.stderr
    manifest_path = root / ".hermes_feishu_card_manifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["integrity"]["version"] == 2
    manifest.pop("integrity", None)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    return root, manifest_path


def test_integrity_migrate_safe_preserves_yaml_and_updates_private_env(tmp_path):
    root, manifest_path = _legacy_git_install(tmp_path)
    config = tmp_path / "config.yaml"
    original_config = "# keep this comment\nserver:\n  port: 8765\n"
    config.write_text(original_config, encoding="utf-8")

    result = _run_cli(
        "integrity",
        "migrate-safe",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--yes",
    )

    assert result.returncode == 0, result.stderr
    assert "integrity mode: safe" in result.stdout
    assert "sidecar.restart_required: true" in result.stdout
    assert "gateway.restart_required: false" in result.stdout
    assert config.read_text(encoding="utf-8") == original_config
    assert (
        "HERMES_FEISHU_CARD_INTEGRITY_MODE=safe"
        in (tmp_path / ".env").read_text(encoding="utf-8")
    )
    assert json.loads(manifest_path.read_text())["integrity"]["version"] == 2
    assert str(root) not in result.stdout


def test_integrity_migrate_safe_refuses_user_edits_without_changing_env(tmp_path):
    root, _manifest_path = _legacy_git_install(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("server: {}\n", encoding="utf-8")
    run_py = root / "gateway" / "run.py"
    run_py.write_text(run_py.read_text(encoding="utf-8") + "# user edit\n")

    result = _run_cli(
        "integrity",
        "migrate-safe",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--yes",
    )

    assert result.returncode == 1
    assert "error:" in result.stderr
    assert not (tmp_path / ".env").exists()


def test_integrity_migrate_safe_failure_preserves_existing_secret_env_mode(
    tmp_path,
):
    root, _manifest_path = _legacy_git_install(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("server: {}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    original = b"FEISHU_APP_SECRET=private-test-value\n"
    env_path.write_bytes(original)
    env_path.chmod(0o600)
    run_py = root / "gateway" / "run.py"
    run_py.write_text(run_py.read_text(encoding="utf-8") + "# user edit\n")

    result = _run_cli(
        "integrity",
        "migrate-safe",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--yes",
    )

    assert result.returncode == 1
    assert "error:" in result.stderr
    assert env_path.read_bytes() == original
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_integrity_acknowledge_review_clears_only_verified_manual_fence(tmp_path):
    root, _manifest_path = _legacy_git_install(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  host: 127.0.0.1\n  port: 65531\n", encoding="utf-8"
    )
    state_root = tmp_path / "custom-state"
    migrated = _run_cli(
        "integrity",
        "migrate-safe",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--yes",
    )
    assert migrated.returncode == 0, migrated.stderr
    state_root.mkdir(mode=0o700)
    fence = state_root / "runtime-integrity-fence.json"
    fence.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "restart_required": True,
                "manual_review_required": True,
                "pre_repair_runtime_hash": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    fence.chmod(0o600)
    pidfile = state_root / "sidecar.pid"
    pidfile.write_text(
        json.dumps({"pid": 999999, "token": "stale-token", "manager": "detached"})
        + "\n",
        encoding="utf-8",
    )
    pidfile.chmod(0o600)
    fence_before = fence.read_bytes()

    refused = _run_cli(
        "integrity",
        "acknowledge-review",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--state-dir",
        str(state_root),
        "--yes",
    )

    assert refused.returncode == 1
    assert "stop the sidecar" in refused.stderr
    assert fence.read_bytes() == fence_before
    assert str(state_root) not in refused.stdout + refused.stderr
    pidfile.unlink()

    result = _run_cli(
        "integrity",
        "acknowledge-review",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--state-dir",
        str(state_root),
        "--yes",
    )

    assert result.returncode == 0, result.stderr
    assert "manual review: acknowledged" in result.stdout
    assert "restart sidecar and Hermes Gateway" in result.stdout
    assert str(root) not in result.stdout
    payload = json.loads(fence.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "2"
    assert payload["manual_review_required"] is False
    assert payload["restart_required"] is False
    assert payload["pre_repair_runtime_hash"] == ""
    assert len(payload["target_identity"]) == 64
    assert len(payload["plan_fingerprint"]) == 64
    assert str(root) not in fence.read_text(encoding="utf-8")


def test_integrity_acknowledge_review_refuses_wrong_or_unsafe_legacy_binding(
    tmp_path,
):
    root, _manifest_path = _legacy_git_install(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  host: 127.0.0.1\n  port: 65531\n", encoding="utf-8"
    )
    migrated = _run_cli(
        "integrity",
        "migrate-safe",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--yes",
    )
    assert migrated.returncode == 0, migrated.stderr

    for name, payload in (
        (
            "wrong-bound",
            {
                "schema_version": "2",
                "restart_required": True,
                "manual_review_required": True,
                "pre_repair_runtime_hash": "",
                "target_identity": "d" * 64,
                "plan_fingerprint": "e" * 64,
            },
        ),
        (
            "legacy-nonempty",
            {
                "schema_version": "1",
                "restart_required": True,
                "manual_review_required": True,
                "pre_repair_runtime_hash": "f" * 64,
            },
        ),
    ):
        state_root = tmp_path / name
        state_root.mkdir(mode=0o700)
        fence = state_root / "runtime-integrity-fence.json"
        fence.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        fence.chmod(0o600)
        before = fence.read_bytes()

        result = _run_cli(
            "integrity",
            "acknowledge-review",
            "--config",
            str(config),
            "--hermes-dir",
            str(root),
            "--state-dir",
            str(state_root),
            "--yes",
        )

        assert result.returncode == 1
        assert "could not be acknowledged safely" in result.stderr
        assert fence.read_bytes() == before
        assert str(root) not in result.stdout + result.stderr
        assert str(state_root) not in result.stdout + result.stderr


@pytest.mark.parametrize("layout", ["git-monolithic", "archive-monolithic", "archive-decomposed"])
def test_integrity_acknowledge_review_migrates_verified_same_target_plan_transition(
    tmp_path, layout,
):
    if layout == "archive-decomposed":
        from hermes_feishu_card.install import decomposed
        from hermes_feishu_card.install.detect import detect_hermes
        root = tmp_path / "hermes"
        shutil.copytree(FIXTURE.parent / "hermes_decomposed", root)
        (root / "VERSION").write_text("0.21.0\n")
        decomposed.install(detect_hermes(root))
        manifest_path = root / decomposed.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("integrity")
        manifest_path.write_text(json.dumps(manifest))
    else:
        root, _manifest_path = _legacy_git_install(tmp_path)
        if layout == "archive-monolithic":
            shutil.rmtree(root / ".git")
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  host: 127.0.0.1\n  port: 65531\n", encoding="utf-8"
    )
    migrated = _run_cli(
        "integrity",
        "migrate-safe",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--yes",
    )
    assert migrated.returncode == 0, migrated.stderr
    current_binding = card_cli._verified_integrity_acknowledgement_binding(root)
    state_root = tmp_path / "transition-state"
    state_root.mkdir(mode=0o700)
    fence = state_root / "runtime-integrity-fence.json"
    fence.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "restart_required": True,
                "manual_review_required": True,
                "pre_repair_runtime_hash": "f" * 64,
                "target_identity": current_binding.target_identity,
                "plan_fingerprint": "e" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    fence.chmod(0o600)

    result = _run_cli(
        "integrity",
        "acknowledge-review",
        "--config",
        str(config),
        "--hermes-dir",
        str(root),
        "--state-dir",
        str(state_root),
        "--yes",
    )

    assert result.returncode == 0, result.stderr
    assert "manual review: acknowledged" in result.stdout
    payload = json.loads(fence.read_text(encoding="utf-8"))
    assert payload["target_identity"] == current_binding.target_identity
    assert payload["plan_fingerprint"] == current_binding.plan_fingerprint
    assert payload["manual_review_required"] is False
    assert payload["restart_required"] is True
    assert payload["pre_repair_runtime_hash"] == "f" * 64
    assert str(root) not in result.stdout + result.stderr
    assert str(state_root) not in result.stdout + result.stderr


def test_integrity_acknowledge_review_rechecks_before_bound_cas(
    tmp_path,
    monkeypatch,
):
    binding = RuntimeIntegrityFenceBinding(
        target_identity="a" * 64,
        plan_fingerprint="b" * 64,
    )
    review = RuntimeIntegrityReviewSnapshot(
        state_token="c" * 64,
        state_present=True,
        manual_review_required=True,
        restart_required=True,
        binding=None,
        legacy_unbound_empty_restart=True,
    )
    binding_checks = []
    process_checks = []
    acknowledgements = []

    monkeypatch.setattr(
        card_cli,
        "_verified_integrity_acknowledgement_binding",
        lambda _root: binding_checks.append(True) or binding,
    )
    monkeypatch.setattr(card_cli, "load_config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        card_cli,
        "inspect_runtime_integrity_review",
        lambda _state: review,
    )
    monkeypatch.setattr(
        card_cli,
        "_integrity_acknowledgement_process_stopped",
        lambda *_args: process_checks.append(True) or True,
    )

    def acknowledge(state_dir, **kwargs):
        acknowledgements.append((state_dir, kwargs))
        return True

    monkeypatch.setattr(card_cli, "acknowledge_runtime_integrity_review", acknowledge)
    args = SimpleNamespace(
        hermes_dir=str(tmp_path / "hermes"),
        config=str(tmp_path / "config.yaml"),
        env_file=None,
        state_dir=str(tmp_path / "state"),
        yes=True,
    )

    assert card_cli._run_integrity_acknowledge_review(args) == 0
    assert binding_checks == [True, True]
    assert process_checks == [True, True]
    assert acknowledgements == [
        (
            tmp_path / "state",
            {
                "expected_state_token": review.state_token,
                "expected_binding": binding,
                "allow_legacy_unbound_empty_restart": True,
            },
        )
    ]


def test_integrity_acknowledge_review_requires_explicit_state_dir(tmp_path):
    result = _run_cli(
        "integrity",
        "acknowledge-review",
        "--config",
        str(tmp_path / "config.yaml"),
        "--hermes-dir",
        str(tmp_path / "hermes"),
        "--yes",
    )

    assert result.returncode == 2
    assert "--state-dir" in result.stderr


def test_integrity_acknowledge_review_help_separates_env_and_state_sources():
    result = _run_cli("integrity", "acknowledge-review", "--help")

    assert result.returncode == 0
    normalized = " ".join(result.stdout.split())
    assert "configuration loading only" in normalized
    assert "state directory must be provided with --state-dir" in normalized
