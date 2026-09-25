"""An upstream update can reapply known hooks while leaving old ownership files."""
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card.install import decomposed, patcher, recovery
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card import cli


FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_decomposed"


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", *args],
        text=True, stderr=subprocess.DEVNULL,
    ).strip()


def source_bytes(root):
    return {name: (root / name).read_bytes() for name in decomposed.SOURCE_TARGETS
            if (root / name).is_file()}


@pytest.fixture
def reapplied(tmp_path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "VERSION").write_text("0.20.0\n")
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "old upstream")
    old = source_bytes(root)
    decomposed.install(detect_hermes(root))
    # Updater installs new Git sources, then reapplies the previous hook patch.
    # Its manifest and backups still describe the old upstream tree.
    new = {name: raw + b"\n# new upstream revision\n" for name, raw in old.items()}
    for name, raw in new.items():
        (root / name).write_bytes(raw)
    git(root, "add", *new)
    git(root, "commit", "--quiet", "-m", "new upstream")
    for name, raw in decomposed.render(new).items():
        (root / name).write_bytes(raw)
    return root, new


def test_reapplied_hooks_require_acceptance_and_restore_new_sources(reapplied):
    root, new = reapplied
    # Unmanaged local customization is never a repair target.
    custom = root / "agent_custom.py"
    custom.write_text("LOCAL_CUSTOMIZATION = True\n")
    before = source_bytes(root)
    detection = detect_hermes(root)
    assert detection.supported, detection.reason
    plan = recovery.plan_recovery(detection)
    assert plan.state == "stale_reapplied" and not plan.executable
    with pytest.raises(ValueError, match="accept-hermes-upgrade"):
        decomposed.install(detection)
    with pytest.raises(ValueError, match="no-repair"):
        decomposed.install(detection, no_repair=True, accept_hermes_upgrade=True)
    assert source_bytes(root) == before
    accepted = recovery.plan_recovery(detection, accept_hermes_upgrade=True)
    assert accepted.executable
    recovery.execute_recovery(detection, expected_fingerprint=accepted.fingerprint,
                              accept_hermes_upgrade=True)
    assert recovery.plan_recovery(detect_hermes(root)).state == "installed"
    assert not decomposed.install(detect_hermes(root))
    decomposed.restore(detect_hermes(root))
    assert source_bytes(root) == new
    assert custom.read_text() == "LOCAL_CUSTOMIZATION = True\n"


@pytest.mark.parametrize("damage", ["uncommitted_source", "hook_body", "backup", "no_git"])
def test_reapplied_recovery_does_not_adopt_user_edits(reapplied, damage):
    root, _ = reapplied
    target = root / "gateway/run_turn_runner.py"
    if damage == "uncommitted_source":
        target.write_bytes(target.read_bytes() + b"\n# uncommitted custom source\n")
    elif damage == "hook_body":
        text = target.read_text()
        assert 'event_name="answer.delta"' in text
        target.write_text(text.replace('event_name="answer.delta"', 'event_name="wrong.event"', 1))
    elif damage == "backup":
        backup = root / ("gateway/run_turn_runner.py" + decomposed.BACKUP_SUFFIX)
        backup.write_bytes(backup.read_bytes() + b"\n# edited backup\n")
    else:
        shutil.rmtree(root / ".git")
    before = source_bytes(root)
    manifest = (root / decomposed.MANIFEST_NAME).read_bytes()
    detection = detect_hermes(root)
    assert recovery.plan_recovery(detection, accept_hermes_upgrade=True).state == "refused"
    with pytest.raises((ValueError, recovery.RecoveryRefused)):
        decomposed.install(detection, accept_hermes_upgrade=True)
    assert source_bytes(root) == before
    assert (root / decomposed.MANIFEST_NAME).read_bytes() == manifest


def test_reapplied_plan_binds_resolved_git_head(reapplied):
    root, _ = reapplied
    detection = detect_hermes(root)
    accepted = recovery.plan_recovery(detection, accept_hermes_upgrade=True)
    assert accepted.executable
    before = source_bytes(root)
    git(root, "commit", "--quiet", "--allow-empty", "-m", "concurrent HEAD change")
    with pytest.raises(recovery.RecoveryRefused, match="evidence changed"):
        recovery.execute_recovery(detection, expected_fingerprint=accepted.fingerprint,
                                  accept_hermes_upgrade=True)
    assert source_bytes(root) == before


def test_reapplied_recovery_preserves_unmarked_source_and_staged_index(reapplied):
    root, new = reapplied
    # A managed file without hooks can also contain deliberate user changes.
    scheduler = "cron/scheduler.py"
    custom = new[scheduler] + b"\nLOCAL_PREFILL = False\n"
    (root / scheduler).write_bytes(custom)
    git(root, "add", *source_bytes(root))
    index = (root / ".git/index").read_bytes()
    decomposed.install(detect_hermes(root), accept_hermes_upgrade=True)
    assert (root / ".git/index").read_bytes() == index
    assert (root / (scheduler + decomposed.BACKUP_SUFFIX)).read_bytes() == custom
    decomposed.restore(detect_hermes(root))
    assert source_bytes(root) == {**new, scheduler: custom}
    assert (root / ".git/index").read_bytes() == index


def test_reapplied_recovery_rolls_back_sources_and_ownership(reapplied, monkeypatch):
    root, _ = reapplied
    before = decomposed._snapshot(root)
    replace = recovery._atomic_replace

    def fail_manifest(staged, target):
        if target.name == decomposed.MANIFEST_NAME:
            raise OSError("injected manifest failure")
        return replace(staged, target)

    monkeypatch.setattr(recovery, "_atomic_replace", fail_manifest)
    with pytest.raises(OSError, match="injected manifest failure"):
        decomposed.install(detect_hermes(root), accept_hermes_upgrade=True)
    assert decomposed._snapshot(root) == before


def test_reapplied_cli_diagnoses_and_requires_explicit_upgrade(reapplied, monkeypatch, capsys):
    root, _ = reapplied
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda detection: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda detection: None)
    detection = detect_hermes(root)
    report = cli._diagnose_install_state(detection)
    assert report["manual_action_required"]
    assert report["status"] == "stale_reapplied"
    before = decomposed._snapshot(root)
    assert cli.main(["install", "--hermes-dir", str(root), "--yes"]) != 0
    assert decomposed._snapshot(root) == before
    assert "accept-hermes-upgrade" in capsys.readouterr().err
    assert cli.main(["install", "--hermes-dir", str(root), "--yes",
                     "--accept-hermes-upgrade"]) == 0
    assert cli._diagnose_install_state(detect_hermes(root))["status"] == "installed"


def test_remove_previous_release_queued_hook_rejects_modified_body():
    block = "".join(patcher._render_queued_followup_hook_block("    ", "\n"))
    legacy = block.replace(
        "        from hermes_feishu_card.hook_runtime import interrupted_turn_locals as _hfc_interrupted_locals\n", ""
    ).replace(
        '_hfc_interrupted_locals(source, _hfc_original_message_id, result)',
        '{"source": source, "chat_id": getattr(source, "chat_id", None), '
        '"message_id": _hfc_original_message_id, "error": "用户已打断当前任务"}',
    )
    source = "async def followup():\n" + legacy + "    return None\n"
    assert patcher.remove_patch(source) == "async def followup():\n    return None\n"
    with pytest.raises(ValueError, match="queued follow-up patch markers"):
        patcher.remove_patch(source.replace("用户已打断当前任务", "unrecognized body"))
