"""Deterministic, duration-balanced pytest selection and complete-run evidence.

Load explicitly with ``-p tools.ci_shard``. No product code or test assertions
are replaced. A gate verifies the disjoint union, including parameterized cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import pytest


def digest(nodes):
    return hashlib.sha256(json.dumps(sorted(nodes), ensure_ascii=True).encode()).hexdigest()


def profile_weights(data, profile):
    # Upstream fixtures turn skipped source contracts into expensive real work.
    # Their timings must not inherit the ordinary full-suite skip costs.
    return data.get("upstream_seconds", {}) if profile == "upstream" else data["seconds"]


def partition(nodes, count, weights, resources=None):
    if count < 1 or len(set(nodes)) != len(nodes):
        raise ValueError("invalid shard count or duplicate node IDs")
    resources = resources or {}
    bundles = {}
    costs = {}
    for node in nodes:
        weight = weights.get(node, 0.05)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("invalid duration weight")
        costs[node] = max(weight, 0.001)
        key = ("resource", resources[node]) if node in resources else ("node", node)
        bundles.setdefault(key, []).append(node)
    loads = [0.0] * count
    buckets = [[] for _ in range(count)]
    ordered = sorted((sorted(bundle) for bundle in bundles.values()),
                     key=lambda bundle: (-sum(costs[n] for n in bundle), min(bundle)))
    for bundle in ordered:
        index = min(range(count), key=lambda index: (loads[index], index))
        buckets[index].extend(sorted(bundle))
        loads[index] += sum(costs[node] for node in bundle)
    return buckets, loads


def pytest_addoption(parser):
    group = parser.getgroup("hfc-ci-shard")
    group.addoption("--hfc-shard-index", type=int)
    group.addoption("--hfc-shard-count", type=int, default=1)
    group.addoption("--hfc-shard-report")
    group.addoption("--hfc-shard-profile", choices=("full", "upstream"), default="full")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    index = config.getoption("--hfc-shard-index")
    if index is None:
        return
    count = config.getoption("--hfc-shard-count")
    if not 0 <= index < count:
        raise pytest.UsageError("invalid HFC shard index/count")
    nodes = [item.nodeid for item in items]
    weights_path = Path(__file__).with_name("ci_durations.json")
    profile = config.getoption("--hfc-shard-profile")
    weights = profile_weights(json.loads(weights_path.read_text()), profile)
    # This fixture writes a sensor and .pth into one borrowed ordinary-wheel
    # runtime. Its imports in other test modules must have the same
    # process owner; the fixture's existing refusal/restore checks stay intact.
    resources = {item.nodeid: "official_runtime" for item in items
                 if "official_runtime" in getattr(item, "fixturenames", ())}
    buckets, loads = partition(nodes, count, weights, resources)
    selected = set(buckets[index])
    excluded = [item for item in items if item.nodeid not in selected]
    items[:] = [item for item in items if item.nodeid in selected]
    config.hfc_shard = {
        "schema": 1, "index": index, "count": count, "profile": profile,
        "collection_count": len(nodes), "collection_sha256": digest(nodes),
        "selected": sorted(selected), "estimated_seconds": loads[index],
        "completed": [],
    }
    config.pluginmanager.register(_Completion(config.hfc_shard), "hfc-shard-completion")
    config.hook.pytest_deselected(items=excluded)


class _Completion:
    def __init__(self, report):
        self.report = report

    def pytest_runtest_logreport(self, report):
        if report.when == "teardown":
            self.report["completed"].append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    report = getattr(config, "hfc_shard", None)
    if report is None:
        return
    report["exit_code"] = int(exitstatus)
    path = config.getoption("--hfc-shard-report")
    if path:
        Path(path).write_text(json.dumps(report, indent=2) + "\n")


def verify(reports, count, expected_commit=None, expected_python=None, expected_platform=None):
    if len(reports) != count or {r["index"] for r in reports} != set(range(count)):
        raise ValueError("missing or duplicate shard")
    collections = {(r["collection_count"], r["collection_sha256"]) for r in reports}
    if len(collections) != 1:
        raise ValueError("shards collected different test suites")
    if len({r.get("commit") for r in reports}) != 1:
        raise ValueError("shards tested different commits")
    if len({r.get("profile") for r in reports}) != 1:
        raise ValueError("shards used different timing profiles")
    if expected_commit is not None and any(r.get("commit") != expected_commit for r in reports):
        raise ValueError("evidence does not match checked-out commit")
    if expected_python is not None and any(r.get("python") != expected_python for r in reports):
        raise ValueError("evidence does not match required Python")
    if expected_platform is not None and any(r.get("platform") != expected_platform for r in reports):
        raise ValueError("evidence does not match required platform")
    selected = []
    for report in reports:
        if report["count"] != count or report["exit_code"] != 0:
            raise ValueError("failed or inconsistent shard")
        if sorted(report["completed"]) != sorted(report["selected"]):
            raise ValueError("selected tests did not finish")
        selected.extend(report["selected"])
    expected_count, expected_digest = collections.pop()
    if len(selected) != len(set(selected)):
        raise ValueError("test was assigned more than once")
    if not selected or len(selected) != expected_count or digest(selected) != expected_digest:
        raise ValueError("test coverage is incomplete")
    return {"tests": expected_count, "shards": count, "collection_sha256": expected_digest,
            "slowest_seconds": max(r.get("seconds", 0) for r in reports)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--group", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text()) for path in args.directory.rglob(f"{args.group}-*.json")]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    print(json.dumps(verify(reports, args.count, commit, args.python_version, args.platform)))


if __name__ == "__main__":
    main()
