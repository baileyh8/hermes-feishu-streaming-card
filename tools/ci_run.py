"""Run bounded pytest processes in separate Git clones and private state.

CI provides immutable read-only upstream fixtures. Worker clones avoid racing
wheel builds, package metadata or source mutation; each process gets its own
HOME, state, TMPDIR and basetemp. No additional parallel-test dependency is used.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.preflight import child_environment, cleanup_scratch

ROOT = Path(__file__).resolve().parents[1]
FORWARD = {
    "HFC_TEST_WHEEL", "HFC_LEGACY_PLUGIN_PYTHON", "HFC_LEGACY_PLUGIN_SOURCE_ROOT",
    "HFC_REQUIRE_LEGACY_PLUGIN_INSTALL", "HFC_REQUIRE_CONFIG_WRITER_CONTRACT",
    "HFC_REQUIRE_UPSTREAM_RATE_CONTRACT", "HFC_UPSTREAM_PM_ROOT",
    "HFC_CONFIG_WRITER_PM_MAIN_ROOT", "HFC_CONFIG_WRITER_CF4_ROOT",
    "HFC_CONFIG_WRITER_RC2_ROOT", "HFC_CONFIG_WRITER_0224_ROOT",
    "HFC_CONFIG_WRITER_MAIN_4E58_ROOT", "HFC_REQUIRE_PM_GENERATION",
}


def run_worker(index, args, targets, fixture):
    private = Path(tempfile.mkdtemp(prefix="hfc-ci-", dir=args.scratch)).resolve()
    original = private.stat()
    for name in ("home", "state", "pytest", "tmp"):
        (private / name).mkdir(mode=0o700)
    source = private / "source"
    # The checked-out commit is the authority, including synthetic PR merges.
    subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(source)], check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run(["git", "checkout", "--quiet", "--detach", commit], cwd=source, check=True)
    env = child_environment(os.environ, private, fixture)
    for name, value in os.environ.items():
        if name in FORWARD or name.startswith("HFC_UPSTREAM_") and name.endswith("_ROOT"):
            env[name] = value
    env.update(TMPDIR=str(private / "tmp"), TEMP=str(private / "tmp"), TMP=str(private / "tmp"))
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    stem = f"{args.group}-{index}"
    log = args.results / f"{stem}.log"
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tools.ci_shard",
        "--hfc-shard-index", str(index), "--hfc-shard-count", str(args.shards),
        "--hfc-shard-profile", args.profile,
        "--hfc-shard-report", str(args.results / f"{stem}.json"),
        "--basetemp", str(private / "pytest"), "--junitxml", str(args.results / f"{stem}.xml"),
        "--durations=20", "--durations-min=1.0", *targets,
    ]
    started = time.monotonic()
    with log.open("w") as output:
        result = subprocess.run(command, check=False, cwd=source, env=env, stdout=output, stderr=subprocess.STDOUT)
    seconds = round(time.monotonic() - started, 2)
    print(f"{stem}: exit={result.returncode}, seconds={seconds}", flush=True)
    report_path = args.results / f"{stem}.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        report.update(commit=commit, seconds=seconds,
                      python=".".join(map(str, sys.version_info[:2])), platform=sys.platform)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    if result.returncode:
        print("\n".join(log.read_text(errors="replace").splitlines()[-70:]), flush=True)
    else:
        cleanup_scratch(private, (original.st_dev, original.st_ino))
    # A runner's ephemeral checkout is retained on failure for diagnosis. The
    # hosted runner reclaims it after artifact upload; local use owns its root.
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--profile", choices=("full", "upstream"), default="full")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("targets", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0 <= args.offset < args.offset + args.workers <= args.shards:
        parser.error("worker range must fit shard count")
    args.results = args.results.resolve()
    args.results.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)
    targets = args.targets[1:] if args.targets[:1] == ["--"] else args.targets
    if args.plan:
        plan = json.loads(args.plan.read_text())
        targets = plan["targets"]
    fixture = Path(os.environ["HFC_FIXED_TAG_SOURCE_ROOT"])
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda index: run_worker(index, args, targets, fixture),
                                range(args.offset, args.offset + args.workers)))
    return int(any(results))


if __name__ == "__main__":
    raise SystemExit(main())
