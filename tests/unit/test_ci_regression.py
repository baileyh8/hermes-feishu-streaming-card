"""Regression selection must be complete, deterministic and fail closed."""
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tools.ci_plan import ROOT, select
from tools.ci_shard import digest, partition, profile_weights, verify


def evidence(nodes, count):
    buckets, _ = partition(nodes, count, {})
    return [{"index": index, "count": count, "collection_count": len(nodes),
             "collection_sha256": digest(nodes), "selected": bucket, "completed": bucket,
             "exit_code": 0, "commit": "a" * 40}
            for index, bucket in enumerate(buckets)]


def test_duration_balancing_preserves_every_case_and_is_order_independent():
    nodes = [f"tests/test_cases.py::test_case[{i}]" for i in range(100)]
    weights = {node: float(100 - index) for index, node in enumerate(nodes)}
    buckets, loads = partition(nodes, 6, weights)
    assert sorted(node for bucket in buckets for node in bucket) == sorted(nodes)
    assert len({node for bucket in buckets for node in bucket}) == 100
    assert max(loads) - min(loads) < 10
    assert partition(list(reversed(nodes)), 6, weights) == (buckets, loads)


def test_shared_runtime_has_one_process_owner_without_losing_cases():
    nodes = [f"case-{index}" for index in range(30)]
    resources = {node: "official_runtime" for node in nodes[:12]}
    parts, _ = partition(nodes, 8, {}, resources)
    assert len([part for part in parts if set(part) & set(resources)]) == 1
    assert sorted(node for part in parts for node in part) == sorted(nodes)
    assert partition(list(reversed(nodes)), 8, {}, resources)[0] == parts


@pytest.mark.parametrize("damage", ["missing", "duplicate", "coverage", "unfinished", "failure", "collection", "commit"])
def test_gate_rejects_incomplete_or_mixed_test_evidence(damage):
    reports = deepcopy(evidence(["a", "b", "c", "d"], 2))
    if damage == "missing":
        reports.pop()
    elif damage == "duplicate":
        reports[1] = deepcopy(reports[0])
    elif damage == "coverage":
        reports[0]["selected"].pop()
    elif damage == "unfinished":
        reports[0]["completed"] = []
    elif damage == "failure":
        reports[0]["exit_code"] = 1
    elif damage == "collection":
        reports[0]["collection_sha256"] = "different"
    elif damage == "commit":
        reports[0]["commit"] = "b" * 40
    with pytest.raises(ValueError):
        verify(reports, 2)


def test_gate_accepts_exact_disjoint_union():
    assert verify(evidence(["a", "b", "c"], 2), 2)["tests"] == 3


def test_upstream_installers_do_not_inherit_full_suite_skip_costs():
    metrics = [f"metrics-{index}" for index in range(3)]
    installs = [f"source-install-{index:02}" for index in range(12)]
    data = {"seconds": dict(zip(metrics, [6.0, 5.0, 4.0]))}
    full, _ = partition(metrics + installs, 6, profile_weights(data, "full"))
    upstream, _ = partition(metrics + installs, 6, profile_weights(data, "upstream"))
    assert any(not set(bucket) & set(installs) for bucket in full)
    assert all(len(set(bucket) & set(installs)) == 2 for bucket in upstream)
    data["upstream_seconds"] = {node: 100.0 for node in installs}
    assert profile_weights(data, "upstream") == data["upstream_seconds"]


@pytest.mark.parametrize("key,value", [("python", "3.10"), ("platform", "win32")])
def test_gate_checks_actual_python_and_platform(key, value):
    reports = evidence(["a", "b", "c"], 2)
    for report in reports:
        report.update(python="3.9", platform="linux")
    assert verify(reports, 2, expected_python="3.9", expected_platform="linux")
    reports[0][key] = value
    with pytest.raises(ValueError):
        verify(reports, 2, expected_python="3.9", expected_platform="linux")


@pytest.mark.parametrize("paths,mode", [
    (["docs/testing.md"], "docs"),
    (["AGENTS.md", "README.md"], "docs"),
    (["hermes_feishu_card/card_view.py"], "focused"),
    (["docs/testing.md", "hermes_feishu_card/server.py"], "full"),
    (["hermes_feishu_card/install/patcher.py"], "full"),
    (["hermes_feishu_card/interaction_resolver.py"], "full"),
    (["hermes_feishu_card/new_module.py"], "full"),
    (["pyproject.toml"], "full"),
    ([".github/workflows/tests.yml"], "full"),
    (["tests/fixtures/hermes_upstream_sources.json"], "full"),
    ([], "full"),
])
def test_conservative_change_scope(paths, mode):
    selected, targets = select(paths)
    assert selected == mode
    if mode != "full":
        assert "tests/unit/test_docs.py" in targets
        assert "tests/unit/test_package_metadata.py" in targets


def test_full_jobs_keep_all_versions_and_stable_required_contexts():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    assert jobs["pytest"]["strategy"]["matrix"]["python-version"] == [
        "3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]
    for name in ("pytest", "pytest-macos", "upstream-hermes-compat"):
        assert jobs[name]["if"] == "always() && needs.plan.result == 'success'"
        assert "tools.ci_shard" in str(jobs[name])
        assert "test \"$PRODUCER_RESULT\" = success" in str(jobs[name])
    assert jobs["upstream-shards"]["strategy"]["matrix"]["slice"] == [0, 1]
    assert jobs["macos-shards"]["strategy"]["matrix"]["slice"] == [0, 1]
    assert {"upstream-hermes-compat", "quality", "feishu-sdk-compat-python314"} <= set(jobs["pytest"]["needs"])
    gate = str(jobs["pytest"])
    for variable in ("UPSTREAM_RESULT", "QUALITY_RESULT", "SDK314_RESULT"):
        assert f'test "${variable}" = success' in gate


@pytest.mark.parametrize("profile", ["full", "upstream"])
def test_real_parallel_runner_isolates_source_home_state_and_temp(tmp_path, profile):
    root = tmp_path / "fixture-repo"
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir()
    for name in ("ci_run.py", "ci_shard.py", "ci_durations.json", "preflight.py", "preflight_routes.json"):
        shutil.copyfile(ROOT / "tools" / name, root / "tools" / name)
    (root / "tests/test_isolation.py").write_text('''
import os
from pathlib import Path
import pytest

@pytest.mark.parametrize("case", [0, 1])
def test_private_worker(case):
    assert "FEISHU_APP_SECRET" not in os.environ
    assert "HERMES_HOME" not in os.environ
    assert "HFC_CONTROL_TOKEN" not in os.environ
    assert Path.home() != Path(os.environ["FORBIDDEN_HOME"])
    for path in (Path("exclusive"), Path.home()/"exclusive",
                 Path(os.environ["TMPDIR"])/"exclusive",
                 Path(os.environ["HERMES_FEISHU_CARD_STATE_DIR"])/"exclusive"):
        with path.open("x") as output:
            output.write(str(case))
''')
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "isolated runner fixture"], cwd=root, check=True)
    env = dict(os.environ, HFC_FIXED_TAG_SOURCE_ROOT=str(tmp_path / "fixed-read-only"),
               FORBIDDEN_HOME=str(Path.home()), FEISHU_APP_SECRET="secret-canary",
               HERMES_HOME="/do-not-touch-production", HFC_CONTROL_TOKEN="token-canary")
    env.pop("PYTHONPATH", None)
    result = subprocess.run([
        sys.executable, "-m", "tools.ci_run", "--group", "probe", "--shards", "2", "--workers", "2",
        "--profile", profile,
        "--results", str(tmp_path / "results"), "--scratch", str(tmp_path / "scratch"),
        "--", "tests/test_isolation.py",
    ], check=False, cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = [json.loads(p.read_text()) for p in (tmp_path / "results").glob("probe-*.json")]
    assert verify(reports, 2)["tests"] == 2
    assert {report["profile"] for report in reports} == {profile}
    assert not (root / "exclusive").exists()


def test_parallel_shared_wheel_fixture_keeps_its_write_guard(tmp_path):
    root = tmp_path / "fixture-repo"
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir()
    for name in ("ci_run.py", "ci_shard.py", "ci_durations.json", "preflight.py", "preflight_routes.json"):
        shutil.copyfile(ROOT / "tools" / name, root / "tools" / name)
    fixture = '''
import os, time
from pathlib import Path
import pytest

@pytest.fixture(scope="module")
def official_runtime():
    sensor = Path(os.environ["CI_TEST_SHARED_RUNTIME"])/"sitecustomize.py"
    with sensor.open("x") as output:
        output.write("preserve caller customization guard")
    try:
        yield sensor
    finally:
        sensor.unlink()

def test_official(official_runtime):
    assert official_runtime.is_file()
    time.sleep(0.2)
'''
    for name in ("test_official_a.py", "test_official_b.py"):
        (root / "tests" / name).write_text(fixture)
    (root / "tests/test_plain.py").write_text("def test_a(): pass\ndef test_b(): pass\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "shared fixture guard"], cwd=root, check=True)
    shared = tmp_path / "shared-runtime"
    shared.mkdir()
    env = dict(os.environ, CI_TEST_SHARED_RUNTIME=str(shared),
               HFC_FIXED_TAG_SOURCE_ROOT=str(tmp_path / "fixed-read-only"))
    env.pop("PYTHONPATH", None)
    result = subprocess.run([
        sys.executable, "-m", "tools.ci_run", "--group", "upstream-probe", "--profile", "upstream",
        "--shards", "2", "--workers", "2", "--results", str(tmp_path / "results"),
        "--scratch", str(tmp_path / "scratch"), "--", "tests",
    ], check=False, cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = [json.loads(p.read_text()) for p in (tmp_path / "results").glob("upstream-probe-*.json")]
    assert verify(reports, 2)["tests"] == 4
    owners = [report for report in reports
              if any("test_official" in node for node in report["selected"])]
    assert len(owners) == 1
    assert not (shared / "sitecustomize.py").exists()


def test_pytest_plugin_reports_actual_completion_and_preserves_failure(tmp_path):
    (tmp_path / "test_nodes.py").write_text('''
import pytest
@pytest.mark.parametrize("case", range(8))
def test_case(case):
    assert case != 3
''')
    reports = []
    codes = []
    for index in range(2):
        path = tmp_path / f"report-{index}.json"
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTEST_ADDOPTS="")
        result = subprocess.run([
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tools.ci_shard",
            "--hfc-shard-index", str(index), "--hfc-shard-count", "2",
            "--hfc-shard-report", str(path), "test_nodes.py",
        ], check=False, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        codes.append(result.returncode)
        reports.append(json.loads(path.read_text()))
    assert sorted(codes) == [0, 1]
    assert len({node for report in reports for node in report["selected"]}) == 8
    assert sum(len(report["completed"]) for report in reports) == 8
    with pytest.raises(ValueError, match="failed"):
        verify(reports, 2)
