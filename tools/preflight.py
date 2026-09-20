#!/usr/bin/env python3
"""Read-only contributor checks and explicitly selected, isolated pytest runs."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DEFAULT = Path("/private/tmp/hermes-agent-v2026.8.3-v430-audit")
GROUPS = {
    "runtime": ["tests/unit/test_hook_runtime.py", "tests/integration/test_server.py"],
    "render": ["tests/unit/test_render.py", "tests/unit/test_session.py"],
    "config": ["tests/unit/test_config.py", "tests/unit/test_package_metadata.py"],
    "install": ["tests/unit/test_patcher.py", "tests/integration/test_cli_install.py"],
    "process": ["tests/unit/test_process.py", "tests/integration/test_cli_process.py"],
    "docs": ["tests/unit/test_docs.py", "tests/unit/test_package_metadata.py"],
    "preflight": ["tests/unit/test_preflight.py"],
}
_PROBE = r'''
import importlib.util, importlib.metadata, json, pathlib, sys
from urllib.parse import urlparse, unquote
root = pathlib.Path.cwd().resolve()
spec = importlib.util.find_spec("hermes_feishu_card")
source = pathlib.Path(spec.origin).resolve() if spec and spec.origin else None
result = {"python": ".".join(map(str,sys.version_info[:3])), "venv": sys.prefix != sys.base_prefix,
          "package_source": "checkout" if source == root / "hermes_feishu_card/__init__.py" else "other_or_missing",
          "pytest": importlib.util.find_spec("pytest") is not None,
          "pytest_asyncio": importlib.util.find_spec("pytest_asyncio") is not None,
          "distribution": "missing",
          "dependencies": {name: importlib.util.find_spec(name) is not None for name in ("aiohttp", "yaml")}}
try:
    dist = importlib.metadata.distribution("hermes-feishu-streaming-card")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    if direct.get("dir_info", {}).get("editable"):
        location = pathlib.Path(unquote(urlparse(direct.get("url", "")).path)).resolve()
        result["distribution"] = "editable_checkout" if location == root else "editable_other_checkout"
    else:
        result["distribution"] = ("checkout_metadata" if pathlib.Path(dist.locate_file("")).resolve() == root
                                  else "installed_package")
except importlib.metadata.PackageNotFoundError:
    pass
print(json.dumps(result))
'''


def run_read(args, root):
    return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=15)


def repository_check(root, cwd):
    result = run_read(["git", "rev-parse", "--show-toplevel"], cwd)
    same = result.returncode == 0 and Path(result.stdout.strip()).resolve() == root
    metadata = root / "pyproject.toml"
    valid = metadata.is_file() and bool(re.search(
        r'^name\s*=\s*"hermes-feishu-streaming-card"\s*$', metadata.read_text(), re.M))
    return {"status": "ok" if same and valid else "wrong_repository", "cwd_is_root": cwd == root}


def fixture_check(root, environ):
    configured = bool(environ.get("HFC_FIXED_TAG_SOURCE_ROOT"))
    fixture = Path(environ.get("HFC_FIXED_TAG_SOURCE_ROOT") or FIXTURE_DEFAULT).expanduser()
    report = {"status": "missing", "configured": configured, "verified_files": 0}
    if not fixture.is_dir():
        return report, fixture
    if fixture.is_symlink():
        report["status"] = "unverified"
        return report, fixture
    provenance = json.loads((root / "hermes_feishu_card/install/_native_hook_provenance/provenance.json").read_text())
    for item in provenance["files"]:
        path = fixture / item["relative_path"]
        if not path.is_file():
            return report, fixture
        if path.is_symlink() or not path.resolve().is_relative_to(fixture.resolve()):
            report["status"] = "unverified"
            return report, fixture
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            report["status"] = "digest_mismatch"
            return report, fixture
        report["verified_files"] += 1
    report["status"] = "verified"
    return report, fixture


def resolve_base(root, base):
    revision = run_read(["git", "rev-parse", "--verify", "--end-of-options", base + "^{commit}"], root)
    if revision.returncode:
        raise ValueError("invalid_base")
    return revision.stdout.strip()


def changed_paths(root, base):
    revision = resolve_base(root, base)
    # Deletions are behavioral changes too. Treat renames as delete + add so
    # moving a runtime file cannot silently omit its original test group.
    diff = run_read(["git", "diff", "--name-only", "-z", "--no-renames", revision, "--"], root)
    untracked = run_read(["git", "ls-files", "--others", "--exclude-standard", "-z"], root)
    if diff.returncode or untracked.returncode:
        raise ValueError("changes_unavailable")
    return sorted(set(filter(None, (diff.stdout + untracked.stdout).split("\0"))))


def select_targets(paths, modules):
    groups, direct, unknown = set(modules), set(), 0
    for name in paths if not modules else []:
        if re.fullmatch(r"tests/(?:unit|integration)/test_[A-Za-z0-9_]+\.py", name):
            direct.add(name)
        elif name.startswith("docs/") or name in {"README.md", "README.en.md", "CHANGELOG.md"}:
            groups.add("docs")
        elif name == "tools/preflight.py":
            groups.add("preflight")
        elif name.startswith("hermes_feishu_card/install/") or name.startswith("install"):
            groups.add("install")
        elif name in {"hermes_feishu_card/hook_runtime.py", "hermes_feishu_card/server.py"}:
            groups.add("runtime")
        elif name in {"hermes_feishu_card/render.py", "hermes_feishu_card/session.py", "hermes_feishu_card/text.py"}:
            groups.add("render")
        elif name in {"hermes_feishu_card/process.py", "hermes_feishu_card/persistent_service.py"}:
            groups.add("process")
        elif name in {"hermes_feishu_card/config.py", "hermes_feishu_card/__init__.py", "config.yaml.example", "pyproject.toml"}:
            groups.add("config")
        else:
            unknown += 1
    targets = sorted(direct | {target for group in groups for target in GROUPS[group]})
    return targets, sorted(groups), unknown


def needs_fixture(root, targets):
    # The fixture consumers resolve the environment variable at module scope.
    # Inspect those declarations without importing tests (or mistaking a test
    # that exercises preflight itself for a real fixed-source dependency).
    if not targets:
        return True
    for path in targets:
        module = ast.parse((root / path).read_text())
        for statement in module.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(node, ast.Constant) and node.value == "HFC_FIXED_TAG_SOURCE_ROOT"
                for node in ast.walk(statement)
            ):
                return True
    return False


def child_environment(environ, private, fixture):
    env = dict(environ)
    for key in list(env):
        if key.startswith(("FEISHU_", "LARK_", "HERMES_", "HFC_")) or key in {"PYTHONPATH", "PYTEST_ADDOPTS"}:
            env.pop(key)
    env.update({"HERMES_FEISHU_CARD_STATE_DIR": str(private / "state"),
                "HERMES_HOME": str(private / "hermes"),
                # Operations resolve these independently of HERMES_HOME. Never
                # inherit a production checkout or fall back through cwd/HOME.
                "HERMES_DIR": str(private / "hermes-source"),
                "HFC_CONFIG": str(private / "state" / "config.yaml"),
                "HFC_ENV_FILE": str(private / "state" / ".env"),
                "HFC_FIXED_TAG_SOURCE_ROOT": str(fixture),
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def execute_suite(root, targets, fixture, environ, report, base="HEAD"):
    # Retain owner-only logs for local diagnosis; stdout JSON contains no paths
    # or raw pytest output (which can contain local paths and parametrized data).
    private = Path(tempfile.mkdtemp(prefix="hfc-preflight-"))
    private.chmod(0o700)
    (private / "state").mkdir(mode=0o700)
    (private / "hermes").mkdir(mode=0o700)
    (private / "hermes-source").mkdir(mode=0o700)
    for name, content in (("config.yaml", "{}\n"), (".env", "")):
        with os.fdopen(os.open(private / "state" / name,
                               os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            output.write(content)
    env = child_environment(environ, private, fixture)
    report["state_dir"] = {"status": "private", "created": True}
    log = private / "pytest.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--basetemp", str(private / "pytest"), "--junitxml", str(private / "results.xml"), *targets]
    print("Running pytest; private local log: " + str(log), file=sys.stderr, flush=True)
    with os.fdopen(fd, "w") as output:
        result = subprocess.run(args, cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
    report["pytest"] = {"status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode}
    if (private / "results.xml").is_file():
        try:
            suites = ET.parse(private / "results.xml").getroot().iter("testsuite")
            counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
            for suite in suites:
                for key in counts:
                    counts[key] += int(suite.get(key, "0"))
            report["pytest"]["counts"] = counts
        except (ET.ParseError, ValueError):
            report["pytest"]["summary"] = "unavailable"
    if result.returncode:
        return result.returncode if result.returncode > 0 else 1
    committed = run_read(["git", "diff", "--check", base, "HEAD", "--"], root)
    diff = run_read(["git", "diff", "--check"], root)
    staged = run_read(["git", "diff", "--cached", "--check"], root)
    code = committed.returncode or diff.returncode or staged.returncode
    report["diff_check"] = {"status": "passed" if code == 0 else "failed", "exit_code": code}
    return code


def main(argv=None, *, root=None, cwd=None, environ=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true", help="inspect readiness without running pytest (default)")
    mode.add_argument("--suite", choices=("focused", "full"), help="explicitly run pytest, then git diff --check")
    parser.add_argument("--module", choices=sorted(GROUPS), action="append", default=[], help="focused group; repeat to combine")
    parser.add_argument("--base", default="HEAD", help="focused auto-selection base; includes staged, unstaged and untracked files")
    args = parser.parse_args(argv)
    if args.module and args.suite != "focused":
        parser.error("--module requires --suite focused")
    root, cwd = (root or ROOT).resolve(), (cwd or Path.cwd()).resolve()
    environ = dict(os.environ if environ is None else environ)
    report = {"schema_version": 1, "mode": args.suite or "check-only", "status": "blocked",
              "pytest": {"status": "not_run", "exit_code": None},
              "state_dir": {"status": "private_per_run", "created": False}}
    code = 2
    try:
        report["repository"] = repository_check(root, cwd)
        if report["repository"]["status"] != "ok":
            raise ValueError("wrong_repository")
        probe = run_read([sys.executable, "-c", _PROBE], root)
        if probe.returncode:
            raise ValueError("interpreter_probe_failed")
        report["interpreter"] = json.loads(probe.stdout)
        report["fixture"], fixture = fixture_check(root, environ)
        interpreter_ok = (report["interpreter"]["package_source"] == "checkout"
                          and report["interpreter"]["pytest"] and report["interpreter"]["pytest_asyncio"]
                          and all(report["interpreter"]["dependencies"].values()))
        if not args.suite:
            ready = interpreter_ok and report["fixture"]["status"] == "verified"
            report["status"], code = ("ready", 0) if ready else ("incomplete", 2)
        else:
            if not interpreter_ok:
                raise ValueError("test_environment_incomplete")
            base = resolve_base(root, args.base)
            targets, groups, unknown = ([], [], 0)
            if args.suite == "focused":
                paths = [] if args.module else changed_paths(root, base)
                targets, groups, unknown = select_targets(paths, args.module)
                report["selection"] = {"modules": groups, "targets": targets, "unmapped_changes": unknown}
                if unknown or not targets:
                    raise ValueError("choose_focused_modules")
                if any(not (root / target).is_file() for target in targets):
                    raise ValueError("selected_test_missing")
            if needs_fixture(root, targets) and report["fixture"]["status"] != "verified":
                raise ValueError("fixed_fixture_required")
            code = execute_suite(root, targets, fixture, environ, report, base)
            report["status"] = ("passed" if report["fixture"]["status"] == "verified" else "partial") if code == 0 else "failed"
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as exc:
        # Only fixed reason tokens may enter a shareable result.
        allowed = {"wrong_repository", "interpreter_probe_failed", "test_environment_incomplete",
                   "choose_focused_modules", "selected_test_missing", "fixed_fixture_required",
                   "invalid_base", "changes_unavailable"}
        report["reason"] = str(exc) if str(exc) in allowed else "preflight_check_failed"
    report["exit_code"] = code
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
