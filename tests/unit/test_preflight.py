"""Contributor preflight reports real results without exposing local state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_spec = importlib.util.spec_from_file_location("hfc_contributor_preflight", Path(__file__).resolve().parents[2] / "tools/preflight.py")
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "private-checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "hermes-feishu-streaming-card"\nversion = "0.0.0"\n')
    package = root / "hermes_feishu_card"
    package.mkdir()
    (package / "__init__.py").write_text('__version__ = "0.0.0"\n')
    provenance = package / "install/_native_hook_provenance/provenance.json"
    provenance.parent.mkdir(parents=True)
    source = b"fixed fixture\n"
    provenance.write_text(json.dumps({"files": [{"relative_path": "gateway/run.py",
        "sha256": "sha256:" + hashlib.sha256(source).hexdigest()}]}))
    fixture = tmp_path / "private-fixed-source"
    (fixture / "gateway").mkdir(parents=True)
    (fixture / "gateway/run.py").write_bytes(source)
    (root / "tests/unit").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], cwd=root, check=True)
    return root, fixture


def invoke(capsys, repo, args, *, missing=False):
    root, fixture = repo
    env = dict(os.environ, HFC_FIXED_TAG_SOURCE_ROOT=str(fixture if not missing else fixture / "missing"),
               FEISHU_APP_SECRET="sensitive-canary", HERMES_HOME="/private/production-hermes")
    code = preflight.main(args, root=root, cwd=root, environ=env)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert str(root) not in output.out
    assert str(fixture) not in output.out
    assert "sensitive-canary" not in output.out
    assert "/private/production-hermes" not in output.out
    assert env["HERMES_HOME"] == "/private/production-hermes"
    return code, report, output.err


def test_check_only_is_readonly_and_never_claims_test_pass(repo, capsys, monkeypatch):
    monkeypatch.setattr(preflight.tempfile, "mkdtemp", lambda **kwargs: pytest.fail("check-only created state"))
    code, report, _ = invoke(capsys, repo, ["--check-only"])
    assert code == 0
    assert report["status"] == "ready"
    assert report["fixture"]["status"] == "verified"
    assert report["pytest"] == {"status": "not_run", "exit_code": None}
    assert report["state_dir"]["created"] is False


def test_missing_fixture_reports_incomplete_and_blocks_full(repo, capsys, monkeypatch):
    monkeypatch.setattr(preflight, "execute_suite", lambda *args: pytest.fail("pytest started without required fixture"))
    for args in ([], ["--suite", "full"]):
        code, report, _ = invoke(capsys, repo, args, missing=True)
        assert code == 2
        assert report["fixture"]["status"] == "missing"
        assert report["pytest"]["status"] == "not_run"
        assert report["status"] in {"incomplete", "blocked"}


def test_wrong_fixture_is_not_reported_ready(repo, capsys):
    (repo[1] / "gateway/run.py").write_text("different source")
    code, report, _ = invoke(capsys, repo, ["--check-only"])
    assert code == 2
    assert report["fixture"]["status"] == "digest_mismatch"


@pytest.mark.parametrize("passes", [True, False])
def test_focused_runs_real_pytest_with_private_state_and_preserves_exit_code(repo, capsys, monkeypatch, passes):
    root, _ = repo
    monkeypatch.setitem(preflight.GROUPS, "preflight", ["tests/unit/test_example.py"])
    (root / "tests/unit/test_example.py").write_text('''import os
from pathlib import Path

def test_isolated_run():
    state = Path(os.environ["HERMES_FEISHU_CARD_STATE_DIR"])
    assert state.is_dir()
    if os.name != "nt":
        assert state.stat().st_mode & 0o777 == 0o700
    assert os.environ["HERMES_HOME"] != "/private/production-hermes"
    assert "FEISHU_APP_SECRET" not in os.environ
    assert %r
''' % passes)
    code, report, stderr = invoke(capsys, repo, ["--suite", "focused", "--module", "preflight"], missing=True)
    assert code == (0 if passes else 1)
    assert report["pytest"]["exit_code"] == code
    assert report["pytest"]["counts"]["tests"] == 1
    assert report["pytest"]["counts"]["failures"] == (0 if passes else 1)
    assert report["fixture"]["status"] == "missing"
    assert report["status"] == ("partial" if passes else "failed")
    assert report["state_dir"]["status"] == "private"
    log = Path(stderr.strip().split("private local log: ")[-1])
    assert log.is_file()
    if os.name != "nt":
        assert log.stat().st_mode & 0o777 == 0o600
        assert log.parent.stat().st_mode & 0o777 == 0o700


def test_full_runs_existing_pytest_then_diff_gate(repo, capsys):
    root, _ = repo
    (root / "tests/unit/test_example.py").write_text("def test_example():\n    assert True\n")
    code, report, _ = invoke(capsys, repo, ["--suite", "full"])
    assert code == 0
    assert report["pytest"]["counts"]["tests"] == 1
    assert report["diff_check"]["status"] == "passed"
    assert report["status"] == "passed"


def test_wrong_cwd_never_executes_tests(repo, capsys, monkeypatch):
    root, _ = repo
    monkeypatch.setattr(preflight, "execute_suite", lambda *args: pytest.fail("wrong repository ran tests"))
    assert preflight.main(["--suite", "full"], root=root, cwd=root.parent) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason"] == "wrong_repository"


def test_changes_include_new_tests_and_unmapped_changes_do_not_imply_full(repo):
    root, _ = repo
    (root / "tests/unit/test_added.py").write_text("def test_ok(): pass\n")
    paths = preflight.changed_paths(root, "HEAD")
    assert "tests/unit/test_added.py" in paths
    targets, groups, unknown = preflight.select_targets(paths, [])
    assert targets == ["tests/unit/test_added.py"]
    assert not groups and not unknown
    targets, groups, unknown = preflight.select_targets(["private-notes.txt"], [])
    assert not targets and unknown == 1


def test_installer_focus_requires_verified_fixture(repo, capsys, monkeypatch):
    root, _ = repo
    monkeypatch.setitem(preflight.GROUPS, "install", ["tests/unit/test_fixture.py"])
    (root / "tests/unit/test_fixture.py").write_text('fixture = "HFC_FIXED_TAG_SOURCE_ROOT"\n')
    code, report, _ = invoke(capsys, repo, ["--suite", "focused", "--module", "install"], missing=True)
    assert code == 2
    assert report["reason"] == "fixed_fixture_required"
    assert report["pytest"]["status"] == "not_run"


def test_full_gate_reports_whitespace_failure_after_real_pytest_success(repo, capsys):
    root, _ = repo
    (root / "tests/unit/test_example.py").write_text("def test_example():\n    assert True\n")
    (root / "hermes_feishu_card/__init__.py").write_text('__version__ = "0.0.0"  \n')
    code, report, _ = invoke(capsys, repo, ["--suite", "full"])
    assert code != 0
    assert report["status"] == "failed"
    assert report["pytest"]["status"] == "passed"
    assert report["diff_check"]["status"] == "failed"


def test_preflight_own_tests_do_not_require_real_hermes_fixture():
    assert preflight.needs_fixture(preflight.ROOT, ["tests/unit/test_preflight.py"]) is False
    assert preflight.needs_fixture(preflight.ROOT, ["tests/unit/test_patcher.py"]) is True


def test_child_environment_cannot_resolve_inherited_production_targets(tmp_path):
    private = tmp_path / "private-run"
    private.mkdir()
    production = tmp_path / "production-canary-never-created"
    original = dict(os.environ, HERMES_DIR=str(production), HFC_HERMES_DIR=str(production),
                    HERMES_AGENT_ROOT=str(production), HFC_CONFIG=str(production / "config.yaml"),
                    HFC_ENV_FILE=str(production / ".env"), HERMES_CRON_AUTO_DELIVER_CHAT_ID="PRIVATE_CHAT",
                    HFC_INSTALL_SPEC="PRIVATE_INSTALL_TARGET", FEISHU_APP_SECRET="PRIVATE_SECRET")
    env = preflight.child_environment(original, private, tmp_path / "fixed-source")
    # Exercise the consumers that choose a writable source/config, without
    # opening the canary or invoking install/process/maintenance operations.
    probe = subprocess.run([sys.executable, "-c", '''
import json, os
from types import SimpleNamespace
from hermes_feishu_card.config import resolve_operations_hermes_root
from hermes_feishu_card.cli import _configured_lifecycle_hermes_root
args = SimpleNamespace(config=os.environ.get("HFC_CONFIG", ""), hermes_dir=None, env_file=None)
print(json.dumps({"operations": str(resolve_operations_hermes_root()),
                  "lifecycle": str(_configured_lifecycle_hermes_root(args)),
                  "config": os.environ.get("HFC_CONFIG"), "env": os.environ.get("HFC_ENV_FILE")}))
'''], cwd=preflight.ROOT, env=env, capture_output=True, text=True, check=True)
    targets = json.loads(probe.stdout)
    for value in targets.values():
        assert value is not None and Path(value).is_relative_to(private)
    assert all(value != "PRIVATE_CHAT" and value != "PRIVATE_INSTALL_TARGET" and value != "PRIVATE_SECRET"
               for value in env.values())
    assert original["HERMES_DIR"] == str(production)
    assert not production.exists()


def _commit_all(root, message):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", message], cwd=root, check=True)


def test_focused_base_gate_checks_committed_whitespace_not_only_worktree(repo, capsys):
    root, _ = repo
    test = root / "tests/unit/test_example.py"
    test.write_text("def test_example():\n    assert True\n")
    _commit_all(root, "clean baseline")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    test.write_text("def test_example():\n    assert True  \n")
    _commit_all(root, "committed whitespace")
    code, report, _ = invoke(capsys, repo, ["--suite", "focused", "--base", base])
    assert code != 0
    assert report["status"] == "failed"
    assert report["pytest"]["status"] == "passed"
    assert report["diff_check"]["status"] == "failed"


def test_deleted_runtime_source_cannot_disappear_from_focused_selection(repo, capsys):
    root, _ = repo
    source = root / "hermes_feishu_card/render.py"
    source.write_text("VALUE = 1\n")
    _commit_all(root, "runtime source")
    source.unlink()
    (root / "tests/unit/test_example.py").write_text("def test_example():\n    assert True\n")
    code, report, _ = invoke(capsys, repo, ["--suite", "focused"])
    # The real project's render/session matrix would run. This minimal fixture
    # lacks it, so fail as incomplete rather than passing the unrelated test.
    assert code == 2
    assert report["selection"]["modules"] == ["render"]
    assert report["reason"] == "selected_test_missing"
    assert report["pytest"]["status"] == "not_run"


def test_deleted_test_requires_a_replacement_matrix_instead_of_silent_omission(repo, capsys):
    root, _ = repo
    removed = root / "tests/unit/test_removed.py"
    removed.write_text("def test_removed():\n    assert True\n")
    _commit_all(root, "test to remove")
    removed.unlink()
    (root / "tests/unit/test_example.py").write_text("def test_example():\n    assert True\n")
    code, report, _ = invoke(capsys, repo, ["--suite", "focused"])
    assert code == 2
    assert report["pytest"]["status"] == "not_run"
