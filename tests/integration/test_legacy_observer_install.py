"""Pinned official plugin enable and byte-exact legacy recovery, without Gateway.

CI must supply all three inputs. Local generic suites explicitly skip this module;
HFC_REQUIRE_LEGACY_PLUGIN_INSTALL=1 makes missing inputs a failure, never a pass.
The ordinary wheel is installed by the caller in one reusable private runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

import pytest


PIN = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"

# Installed in the test runtime only. Do not change admission or force observe.
# Fail safely before any HFC transport/ownership operation can have side effects,
# recording attempted calls even when production code catches their exceptions.
SENSOR = r'''
import json, os, sys
from pathlib import Path
home = Path(os.environ.get("HERMES_HOME", os.environ.get("HOME", ".")))
def profile(frame, event, arg):
    if event != "call":
        return
    module = frame.f_globals.get("__name__", "") or ""
    name = frame.f_code.co_name
    if module.startswith("hermes_feishu_card") and name in {
        "acquire_runtime_control", "start_runtime_interaction_listener",
        "read_transport_root_secret", "post_event", "_post_event", "_post_json",
    }:
        with (home / "hfc-forbidden.jsonl").open("a") as out:
            out.write(json.dumps([module, name]) + "\n")
        raise RuntimeError("isolated test forbids HFC ownership/transport")
sys.setprofile(profile)
def audit(event, args):
    if event in {"socket.connect", "socket.bind"}:
        # Official CLI can probe its private HOME control socket. All probes are
        # denied, so it cannot contact a real service or bind a listener.
        raise PermissionError("offline official CLI integration")
sys.addaudithook(audit)
# Supply tools/project only; execute pinned upstream admission, resolver and
# configuration publication unchanged, as the PM-generation integration does.
provision = home / "hfc-pm-tools.json"
if provision.exists():
    tools = json.loads(provision.read_text())
    import pm.paths, pm._uv, pm.client, pm.install
    pm.paths.repo_root = lambda: Path(tools["project"])
    pm._uv._toolchain = lambda **kwargs: (Path(tools["uv"]), Path(tools["python"]))
    pm.client.sync_venv = lambda *args, **kwargs: pm.install.sync_venv(*args, **kwargs)
'''


@pytest.fixture(scope="module")
def official_runtime(tmp_path_factory):
    names = ("HFC_LEGACY_PLUGIN_SOURCE_ROOT", "HFC_LEGACY_PLUGIN_PYTHON", "HFC_TEST_WHEEL")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        message = "ordinary-wheel official enable inputs missing: " + ", ".join(missing)
        if os.environ.get("HFC_REQUIRE_LEGACY_PLUGIN_INSTALL") == "1":
            pytest.fail(message)
        pytest.skip(message)
    source = Path(os.environ[names[0]]).resolve()
    python = Path(os.environ[names[1]]).absolute()
    wheel = Path(os.environ[names[2]]).resolve()
    result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True, timeout=10)
    assert result.stdout.strip() == PIN
    result = subprocess.run(["git", "-C", str(source), "status", "--porcelain"],
                            capture_output=True, text=True, check=True, timeout=10)
    assert not result.stdout.strip(), "pinned fixture must be clean"
    assert wheel.is_file()
    probe = subprocess.run([str(python), "-I", "-c",
        "import json,sys,sysconfig,hermes_feishu_card as h;"
        "print(json.dumps([sys.prefix,sysconfig.get_paths()['purelib'],h.__file__]))"],
        capture_output=True, text=True, check=True, timeout=20)
    prefix, site, origin = map(Path, json.loads(probe.stdout))
    assert prefix in origin.parents, "HFC must come from ordinary runtime site-packages"
    assert "site-packages" in origin.parts
    import zipfile
    with zipfile.ZipFile(wheel) as archive:
        resources = [name for name in archive.namelist() if name.startswith("hermes_feishu_card/") and not name.endswith("/")]
        assert resources
        for name in resources:
            assert (site / name).read_bytes() == archive.read(name), name
    direct_url = next(site.glob("hermes_feishu_streaming_card-*.dist-info/direct_url.json"))
    provenance = json.loads(direct_url.read_text())
    assert not provenance.get("dir_info", {}).get("editable")
    assert provenance["archive_info"]["hashes"]["sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert not (site / "sitecustomize.py").exists(), "do not replace caller customization"
    sensor = site / "sitecustomize.py"
    sensor.write_text(SENSOR)
    path_file = site / "hfc-official-fixture.pth"
    assert not path_file.exists()
    try:
        smoke = tmp_path_factory.mktemp("sensor-smoke")
        from hermes_feishu_card.install.plugin_config_bytes import isolated_child_env
        probe = subprocess.run([str(python), "-I", "-c",
            "from hermes_feishu_card.hermes_plugin_runtime import read_transport_root_secret\n"
            "try: read_transport_root_secret()\n"
            "except RuntimeError: pass\n"],
            cwd=smoke, env=isolated_child_env(smoke, include_path=True),
            capture_output=True, text=True, timeout=20)
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert "read_transport_root_secret" in (smoke / "hfc-forbidden.jsonl").read_text()
        yield source, python, prefix, site, path_file
    finally:
        sensor.unlink()
        path_file.unlink(missing_ok=True)


def _stop_stage_group(child):
    """Only this stage's new POSIX session is eligible for termination."""
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The direct child may exit on TERM while an official CLI grandchild ignores
    # it. Always address the original group again, even after wait() succeeded.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def _run(python, root, home, code, *args, expected=0, stage, timeout=180):
    assert os.name == "posix", "official CLI harness requires POSIX process-group isolation"
    log = home / (stage + ".log")
    started = time.monotonic()
    print(f"HFC stage={stage} BEGIN budget={timeout}s log={log}", flush=True)
    command = [str(python), "-I", "-u", "-c",
               f"print('HFC child stage={stage} START', flush=True)\n" + code,
               str(root), *map(str, args)]
    timed_out = False
    with log.open("w", encoding="utf-8") as output:
        child = subprocess.Popen(command, cwd=home,
            env={"HOME": str(home), "HERMES_HOME": str(home),
                 "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=output, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # Also clean descendants if the stage exited early or was interrupted.
            _stop_stage_group(child)
    text = log.read_text(encoding="utf-8")
    elapsed = time.monotonic() - started
    print(text, end="", flush=True)
    print(f"HFC stage={stage} END elapsed={elapsed:.2f}s rc={child.returncode} timeout={timed_out}", flush=True)
    assert not timed_out, f"HFC stage={stage} timed out after {timeout}s; process group terminated; log={log}\n{text}"
    assert child.returncode == expected, text
    assert not (home / "hfc-forbidden.jsonl").exists(), (
        (home / "hfc-forbidden.jsonl").read_text() if (home / "hfc-forbidden.jsonl").exists() else "")
    return subprocess.CompletedProcess(command, child.returncode, stdout=text, stderr="")


@pytest.mark.parametrize("command,migrate", [("restore", False), ("uninstall", True)])
def test_official_enable_legacy_install_repeat_doctor_exact_restore(official_runtime, tmp_path, command, migrate):
    source, python, prefix, _site, path_file = official_runtime
    root, home = tmp_path / "hermes", tmp_path / "home"
    # Copy only immutable tracked source. No existing .venv, private state or
    # production HOME can enter this fixture.
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    home.mkdir()
    original = (b"# exact comments and CRLF\r\nplugins:\r\n  enabled: []\r\n"
                b"  disabled: [keep-disabled]\r\n  isolation: in_process\r\n"
                b"platform_toolsets:\r\n  feishu: [terminal]\r\nother: keep\r\n_config_version: 50\r\n")
    (home / "config.yaml").write_bytes(original)
    (root / ".venv").symlink_to(prefix, target_is_directory=True)
    path_file.write_text(str(root) + "\n")
    uv = shutil.which("uv")
    assert uv, "pre-provisioned uv required for the actual PM transaction"
    project = home / "pm-project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="official-enable-test"\nversion="1"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n')
    subprocess.run([uv, "lock", "--offline"], cwd=project, check=True, capture_output=True, timeout=30)
    (home / "hfc-pm-tools.json").write_text(json.dumps({"uv": uv, "python": str(python), "project": str(project)}))
    from hermes_feishu_card.install import legacy_observer
    assert legacy_observer.available(root)
    sources = json.loads((Path(__file__).parents[1] / "fixtures/hermes_upstream_sources.json").read_text())["baseline_0222"]["sha256"]
    before = {name: (root / name).read_bytes() for name in sources}
    if migrate:
        _run(python, root, home, r'''
import sys
from pathlib import Path
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.recovery import install_decomposed
print("legacy source transaction BEGIN", flush=True)
assert install_decomposed(detect_hermes(Path(sys.argv[1])))
print("legacy source transaction END", flush=True)
''', stage="legacy-preinstall", timeout=300)
    install_code = r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
root=Path(sys.argv[1]);home=Path.home()
# Retain real official CLI diagnostics. These wrappers only observe real calls.
import subprocess
original_run=subprocess.run
def logged_run(*args,**kwargs):
    official = "hermes_cli.main" in args[0]
    if official:
        print("official enable BEGIN", flush=True)
    result=original_run(*args,**kwargs)
    if official:
        print("official enable END:",result.returncode,result.stdout,result.stderr,flush=True)
    return result
subprocess.run=logged_run
# Exact wheel is already installed; no Feishu adapter is imported by this test.
cli._ensure_hermes_runtime_package=lambda *a,**k:None
cli._ensure_hermes_feishu_sdk=lambda *a,**k:None
print("HFC install dispatch BEGIN", flush=True)
assert cli.main(["install","--hermes-dir",str(root),"--hermes-home",str(home),"--yes"]) == 0
print("HFC install dispatch END", flush=True)
'''
    _run(python, root, home, install_code, stage="install", timeout=300)
    _run(python, root, home, install_code, stage="repeat-install")
    _run(python, root, home, r'''
import sys,json,contextlib,io
from pathlib import Path
from hermes_feishu_card import cli
from hermes_feishu_card.install import legacy_observer
root=Path(sys.argv[1]);home=Path.home()
config=home/"hfc.yaml"
config.write_text("server:\n  host: 127.0.0.1\n  port: 9\n")
report=io.StringIO()
print("doctor dispatch BEGIN", flush=True)
with contextlib.redirect_stdout(report):
    result=cli.main(["doctor","--config",str(config),"--hermes-dir",str(root),"--hermes-home",str(home),"--json"])
print("doctor dispatch END", flush=True)
assert result in (0,1)
report=json.loads(report.getvalue())
assert report["install_state"]["status"] == "installed"
assert report["install_state"]["legacy_observer"]["status"] == "active"
assert legacy_observer.observer_status(root,home=home)["status"] == "active"
''', stage="doctor")
    installed = {name: (root / name).read_bytes() for name in before}
    assert installed != before
    # Public discovery and hook delivery: no private _scan/_load entrypoint,
    # synthetic observer-mode override or source HFC import is used.
    _run(python, root, home, r'''
import sys
from pathlib import Path
import hermes_cli.plugins as plugins
from hermes_feishu_card import hermes_plugin_runtime as runtime
plugins.discover_plugins()
rt=runtime.active_plugin_runtime()
assert rt is not None and rt.observe_only
values=dict(session_id="synthetic-session",turn_id="synthetic-turn",platform="feishu",
    api_request_id="synthetic-request",model="synthetic-model",provider="synthetic-provider",
    usage={"input_tokens":10,"output_tokens":80},api_duration=2.0)
plugins.invoke_hook("pre_llm_call",**values)
plugins.invoke_hook("post_api_request",**values)
assert rt.take_observed_rate("synthetic-session")[1] == 80
assert rt.runtime_activity_snapshot() == (0,True)
assert rt.runtime_interaction_listener_snapshot()["accepting"] is False
runtime.reset_production_plugin_runtime_for_tests()
''', stage="public-discovery")
    _run(python, root, home, r'''
import sys
from hermes_feishu_card import cli
assert cli.main([sys.argv[2],"--hermes-dir",sys.argv[1],"--hermes-home",str(__import__("pathlib").Path.home()),"--yes"]) == 0
''', command, stage=command)
    assert (home / "config.yaml").read_bytes() == original
    assert {name: (root / name).read_bytes() for name in before} == before
    assert not (root / ".hermes_feishu_card_observer.json").exists()
    assert not (root / ".hermes_feishu_card_manifest").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group harness")
def test_stage_timeout_kills_grandchild_but_preserves_unrelated_process(tmp_path):
    """Exercise the actual deadline/kill path, including a TERM-resistant child."""
    import sys
    home = tmp_path / "home"
    home.mkdir()
    sibling = subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(60)"],
                               env={"HOME": str(home), "HERMES_HOME": str(home)},
                               start_new_session=True)
    code = r'''
import subprocess,sys,time
from pathlib import Path
home=Path.home()
grandchild=r"""
import os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
home=Path.home()
(home/"grandchild.pid").write_text(str(os.getpid()))
while True:
    with (home/"heartbeat").open("a") as out: out.write("tick\n")
    time.sleep(.02)
"""
subprocess.Popen([sys.executable,"-I","-u","-c",grandchild])
while not (home/"grandchild.pid").exists(): time.sleep(.01)
print("grandchild ready",flush=True)
while True: time.sleep(.05)
'''
    try:
        with pytest.raises(AssertionError, match="stage=forced-timeout timed out"):
            _run(sys.executable, tmp_path, home, code, stage="forced-timeout", timeout=2)
        assert "grandchild ready" in (home / "forced-timeout.log").read_text()
        pid = int((home / "grandchild.pid").read_text())
        # A reparented zombie can await OS reaping; it is no longer executable.
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               text=True, capture_output=True, timeout=5).stdout.strip()
        assert not state or state.startswith("Z"), state
        before = (home / "heartbeat").read_bytes()
        time.sleep(.1)
        assert (home / "heartbeat").read_bytes() == before
        assert sibling.poll() is None, "another process group must survive"
    finally:
        _stop_stage_group(sibling)
