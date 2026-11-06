"""Ordinary-wheel install transaction on the exact Hermes v0.23.0 checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from test_legacy_observer_install import SENSOR, _run


COMMIT = "2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19"
ENV = "HFC_CONFIG_WRITER_0230_ROOT"
FULL_SEED = (
    b"plugins:\n  enabled: []\n  disabled: [keep-disabled]\n"
    b"  isolation: in_process\nplatform_toolsets:\n  feishu: [terminal]\n"
    b"other: keep\n_config_version: 50\n"
)

# Only the read-only backend child may skip the fixture's PM toolchain port.
# The product preloaded_source guard stays unchanged; install/repair/repeat
# children still load the pinned PM tool port through sitecustomize.
BACKEND_PROBE_PRELUDE = r'''
from hermes_feishu_card.install import config_backend_0226 as backend
_original_probe = backend._run_child_probe
def _probe_without_toolchain_port(root, python, home, env, layers, sources):
    return _original_probe(
        root, python, home,
        {**env, "HFC_TEST_NO_PM_TOOLCHAIN_PORT": "1"},
        layers, sources,
    )
backend._run_child_probe = _probe_without_toolchain_port
'''


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.23.0 wheel input missing: " + name)
        pytest.skip("v0.23.0 wheel input missing: " + name)
    return value


def _root() -> Path:
    root = Path(_required(ENV))
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    assert head == COMMIT, (head, COMMIT)
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout
    assert status == "", "pinned v0.23.0 checkout must be clean"
    return root


def _ordinary_runtime() -> tuple[Path, Path, Path, str]:
    python = Path(_required("HFC_LEGACY_PLUGIN_PYTHON")).absolute()
    wheel = Path(_required("HFC_TEST_WHEEL")).resolve()
    probe = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json,sys,sysconfig,hermes_feishu_card as h;"
         "print(json.dumps([sys.prefix,sysconfig.get_paths()['purelib'],h.__file__,h.__version__]))"],
        check=True, capture_output=True, text=True, timeout=20,
    )
    prefix, site, origin, version = map(Path, json.loads(probe.stdout))
    assert prefix in origin.parents, "HFC must come from ordinary runtime site-packages"
    assert "site-packages" in origin.parts
    with zipfile.ZipFile(wheel) as archive:
        resources = [name for name in archive.namelist()
                     if name.startswith("hermes_feishu_card/") and not name.endswith("/")]
        assert resources
        for name in resources:
            assert (site / name).read_bytes() == archive.read(name), name
    direct_url = next(site.glob("hermes_feishu_streaming_card-*.dist-info/direct_url.json"))
    provenance = json.loads(direct_url.read_text())
    assert not provenance.get("dir_info", {}).get("editable")
    assert provenance["archive_info"]["hashes"]["sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    return python, prefix, site, str(version)


def _official_migrate(python: Path, source: Path, home: Path) -> bytes:
    (home / "config.yaml").write_bytes(FULL_SEED)
    code = r'''
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli.config_version_stamp import check_config_version
from scripts.docker_config_migrate import main as migrate
rc = migrate()
if rc:
    raise SystemExit(rc)
stamp, latest = check_config_version(raise_on_parse_error=True)
print(json.dumps({"stamp": stamp, "latest": latest,
                  "default": DEFAULT_CONFIG.get("_config_version")}, sort_keys=True))
'''
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", code, str(source)],
        cwd=str(source), env={"HOME": str(home), "HERMES_HOME": str(home)},
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["stamp"] == payload["latest"] == payload["default"] == 52
    return (home / "config.yaml").read_bytes()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group harness")
def test_v0230_ordinary_wheel_install_repair_repeat_restore(tmp_path):
    source = _root()
    python, prefix, site, version = _ordinary_runtime()
    root, home = tmp_path / "hermes", tmp_path / "home"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    home.mkdir(mode=0o700)
    original_config = _official_migrate(python, root, home)
    (root / ".venv").symlink_to(prefix, target_is_directory=True)
    path_file = site / "hfc-v0230-fixture.pth"
    assert not path_file.exists()
    path_file.write_text(str(root) + "\nimport sys; sys.dont_write_bytecode=True\n")
    sensor = site / "sitecustomize.py"
    assert not sensor.exists()
    sensor.write_text(SENSOR)
    uv = shutil.which("uv")
    assert uv, "pre-provisioned uv required for the actual PM transaction"
    project = home / "pm-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="v0230-wheel-test"\nversion="1"\nrequires-python=">=3.11"\n'
        'dependencies=[]\n[tool.uv]\npackage=false\n'
    )
    subprocess.run([uv, "lock", "--offline"], cwd=project, check=True,
                   capture_output=True, timeout=30)
    (home / "hfc-pm-tools.json").write_text(json.dumps(
        {"uv": uv, "python": str(python), "project": str(project)}
    ))
    evidence = tmp_path / "wheel-runtime.json"
    evidence.write_text(json.dumps({
        "version": version,
        "prefix": str(prefix),
        "site": str(site),
        "origin": str(site / "hermes_feishu_card/__init__.py"),
        "wheel": str(Path(os.environ["HFC_TEST_WHEEL"]).resolve()),
        "wheel_sha256": hashlib.sha256(Path(os.environ["HFC_TEST_WHEEL"]).read_bytes()).hexdigest(),
        "commit": COMMIT,
    }, indent=2, sort_keys=True) + "\n")
    before = {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    }
    install_code = r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
root = Path(sys.argv[1]); home = Path.home()
cli._ensure_hermes_runtime_package = lambda *a, **k: None
cli._ensure_hermes_feishu_sdk = lambda *a, **k: None
assert cli.main(["install", "--hermes-dir", str(root), "--hermes-home", str(home), "--yes"]) == 0
'''
    try:
        _run(python, root, home, BACKEND_PROBE_PRELUDE + install_code,
             stage="install", timeout=300)
        _run(python, root, home, BACKEND_PROBE_PRELUDE + r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
root = Path(sys.argv[1]); home = Path.home()
assert cli.main(["repair", "--hermes-dir", str(root), "--hermes-home", str(home), "--yes"]) == 0
''', stage="repair", timeout=300)
        _run(python, root, home, BACKEND_PROBE_PRELUDE + install_code,
             stage="repeat-install", timeout=300)
        _run(python, root, home, BACKEND_PROBE_PRELUDE + r'''
import sys, json, contextlib, io
from pathlib import Path
from hermes_feishu_card import cli
from hermes_feishu_card.install import legacy_observer
root = Path(sys.argv[1]); home = Path.home()
config = home / "hfc.yaml"
config.write_text("server:\n  host: 127.0.0.1\n  port: 9\n")
report = io.StringIO()
with contextlib.redirect_stdout(report):
    result = cli.main(["doctor", "--config", str(config), "--hermes-dir", str(root),
                       "--hermes-home", str(home), "--json"])
assert result in (0, 1)
payload = json.loads(report.getvalue())
assert payload["install_state"]["status"] == "installed"
assert payload["install_state"]["legacy_observer"]["status"] == "active"
assert legacy_observer.observer_status(root, home=home)["status"] == "active"
''', stage="doctor", timeout=300)
        installed = {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*.py")
            if ".venv" not in path.parts and "__pycache__" not in path.parts
        }
        assert installed != before, "install did not patch the v0.23.0 source"
        _run(python, root, home, BACKEND_PROBE_PRELUDE + r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
root = Path(sys.argv[1]); home = Path.home()
assert cli.main(["restore", "--hermes-dir", str(root), "--hermes-home", str(home), "--yes"]) == 0
''', stage="restore", timeout=300)
        assert (home / "config.yaml").read_bytes() == original_config
        assert not (root / ".hermes_feishu_card_observer.json").exists()
        assert not (root / ".hermes_feishu_card_manifest").exists()
        for name, contents in before.items():
            assert (root / name).read_bytes() == contents, name
        print(json.dumps({"version": version, "commit": COMMIT,
                          "wheel_install_repair_repeat_restore": "ok"}), flush=True)
    finally:
        sensor.unlink(missing_ok=True)
        path_file.unlink(missing_ok=True)
