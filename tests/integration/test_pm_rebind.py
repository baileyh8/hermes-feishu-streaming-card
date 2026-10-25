"""Ordinary-wheel CLI recovery after a real pinned PM generation replacement.

Only the project dependency closure and pre-provisioned tools are substituted.
Official admission, PM lock/sync, observer config writer and source transactions
remain real. No Gateway, platform API or production HOME participates.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_legacy_observer_install import SENSOR, _run

PINS = {
    "0224": ("HFC_CONFIG_WRITER_0224_ROOT", "4d04303818b61bf3aa09278af48356315515965b"),
    "0225": ("HFC_CONFIG_WRITER_0225_ROOT", "bf0203a794a07aac3ff764972ab9087baa4e4f4e"),
}

SETUP = r'''
import json,sys,subprocess,os
from pathlib import Path
from hermes_feishu_card.install import pm_dependency as package
from hermes_feishu_card import __version__
root,home,wheel,uv,tools_python=map(Path,sys.argv[1:6])
tools_sites=json.loads(sys.argv[6])
assert Path(sys.prefix) in Path(package.__file__).parents
(root/'pyproject.toml').write_text('[project]\nname="pm-rebind-core"\nversion="1"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n')
other=home/'plugins'/'other';other.mkdir(parents=True)
(other/'other').mkdir();(other/'other'/'__init__.py').write_text('__version__="1"\n')
(other/'pyproject.toml').write_text('[project]\nname="other"\nversion="1"\ndependencies=[]\n[tool.uv]\npackage=true\n')
(home/'config.yaml').write_text('plugins:\n  enabled: [other]\n  disabled: [keep-disabled]\n  isolation: in_process\nplatform_toolsets:\n  feishu: [terminal]\nother: keep\n_config_version: 50\n')
locked=subprocess.run([str(uv),'lock','--offline','--python',str(tools_python)],cwd=root,capture_output=True,text=True,timeout=30)
assert locked.returncode==0,locked.stderr
injection=(f"sys.path.append({str(root)!r})\n"
           f"sys.path.extend({tools_sites!r})\n"
           "import pm.paths,pm._uv,pm.client,pm.install\n"
           f"pm.paths.repo_root=lambda:Path({str(root)!r})\n"
           f"pm._uv._toolchain=lambda **kwargs:(Path({str(uv)!r}),Path({str(tools_python)!r}))\n"
           "pm.client.sync_venv=lambda *a,**k:pm.install.sync_venv(*a,**k)\n")
package._CHILD=package._CHILD.replace('from hermes_constants import get_hermes_home',injection+'from hermes_constants import get_hermes_home')
receipt=package.ensure(root,tools_python,home=home,spec=str(wheel),version=__version__)
assert receipt is not None;receipt.finish()
exec(injection)
from pm.environments import committed_venv
first=committed_venv(root)
assert package.verify_installed(home/'plugins'/package.NAME,first/'bin/python')
(home/'hfc-pm-tools.json').write_text(json.dumps({'uv':str(uv),'python':str(tools_python),'project':str(root)}))
(home/'pm-generation-a.json').write_text(json.dumps(str(first)))
(home/'pm-config-before-observer.yaml').write_bytes((home/'config.yaml').read_bytes())
'''

INSTALL = r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
root=Path(sys.argv[1]);home=Path.home()
assert Path(sys.prefix) in Path(cli.__file__).parents, 'HFC must load from the PM wheel, not supplied dependency paths'
# This test already installed the exact wheel through real PM admission. The
# Feishu SDK is unrelated to source/observer recovery and never imported here.
cli._ensure_hermes_runtime_package=lambda *a,**k:None
cli._ensure_hermes_feishu_sdk=lambda *a,**k:None
assert cli.main(['install','--hermes-dir',str(root),'--hermes-home',str(home),'--yes']) == 0
'''

MIGRATE = r'''
import sys,json
from pathlib import Path
from hermes_feishu_card.install import pm_dependency as package
root=Path(sys.argv[1]);home=Path.home()
project=home/'plugins'/'other'/'pyproject.toml'
project.write_text(project.read_text().replace('version="1"','version="2"'))
# Configuration remains unchanged: only a real dependency member changes.
before=(home/'config.yaml').read_bytes()
probe=package._call(root,Path(sys.executable),home,{'operation':'probe'})
package._call(root,Path(sys.executable),home,{'operation':'add','name':package.NAME,'expected':probe['expected']})
from pm.environments import committed_venv
second=committed_venv(root)
assert str(second)!=json.loads((home/'pm-generation-a.json').read_text())
assert (home/'config.yaml').read_bytes()==before
(home/'pm-generation-b.json').write_text(json.dumps(str(second)))
'''


@pytest.mark.skipif(os.name != "posix", reason="POSIX real source recovery and process-group harness")
@pytest.mark.parametrize("snapshot", PINS)
def test_real_pm_generation_explicit_rebind_reinstall_restore(tmp_path, snapshot):
    variable, pin = PINS[snapshot]
    required = [variable, "HFC_LEGACY_PLUGIN_PYTHON", "HFC_TEST_WHEEL"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        if os.environ.get("HFC_REQUIRE_PM_REBIND") == "1":
            pytest.fail("real PM rebind inputs missing: " + ", ".join(missing))
        pytest.skip("real PM rebind inputs missing: " + ", ".join(missing))
    source = Path(os.environ[variable])
    tools_python = Path(os.environ["HFC_LEGACY_PLUGIN_PYTHON"])
    wheel = Path(os.environ["HFC_TEST_WHEEL"])
    assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == pin
    assert not subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()
    uv = shutil.which("uv")
    assert uv
    root, home = tmp_path / "hermes", tmp_path / "home"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    home.mkdir()
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.py")}
    # CI's ordinary tool venv can inherit dependencies from system sites. A
    # single purelib path drops those imports when supplying a fresh PM venv.
    # Forward only existing package directories, never the caller's checkout or
    # an editable finder. The PM venv's own installed HFC remains first.
    tools_sites = json.loads(subprocess.check_output([str(tools_python), "-I", "-c",
        "import json,sys;from pathlib import Path;"
        "print(json.dumps(list(dict.fromkeys(str(Path(p).resolve()) for p in sys.path "
        "if Path(p).name in {'site-packages','dist-packages'} and Path(p).is_dir()))))"], text=True))
    assert tools_sites, "pre-provisioned dependency package paths required"
    _run(tools_python, root, home, SETUP, home, wheel, uv, tools_python, json.dumps(tools_sites),
         stage="real-pm-setup", timeout=180)
    def prepare_runtime(generation):
        python = generation / "bin/python"
        site = Path(subprocess.check_output([str(python), "-I", "-c",
            "import sysconfig;print(sysconfig.get_path('purelib'))"], text=True).strip())
        (site / "hfc-rebind-fixture.pth").write_text("\n".join([str(root), *tools_sites]) + "\n")
        (site / "sitecustomize.py").write_text("import os\nos.environ['UV_OFFLINE']='1'\n" + SENSOR)
        return python
    first = Path(json.loads((home / "pm-generation-a.json").read_text()))
    python_a = prepare_runtime(first)
    _run(python_a, root, home, INSTALL, stage="real-install-a", timeout=300)
    marker = root / ".hermes_feishu_card_observer.json"
    marker_a = marker.read_bytes()
    config_a = (home / "config.yaml").read_bytes()
    _run(python_a, root, home, MIGRATE, stage="real-migrate-b", timeout=180)
    second = Path(json.loads((home / "pm-generation-b.json").read_text()))
    python_b = prepare_runtime(second)
    _run(python_b, root, home, r'''
import sys
from pathlib import Path
from hermes_feishu_card.install import legacy_observer,plugin
root=Path(sys.argv[1]);home=Path.home()
binding=plugin.resolve_runtime_binding(checkout_root=root,hermes_home=home,profile_id=None)
for action in (lambda:legacy_observer.prepare(binding),lambda:legacy_observer.restore(root,home=home)):
    try: action()
    except ValueError as error: assert 'runtime binding changed' in str(error)
    else: raise AssertionError('strict default accepted replaced runtime')
''', stage="strict-default-refusal")
    assert marker.read_bytes() == marker_a
    assert (home / "config.yaml").read_bytes() == config_a
    rebind = r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
assert cli.main(['integrity','rebind-legacy','--hermes-dir',sys.argv[1],'--hermes-home',str(Path.home()),'--yes'])==0
'''
    _run(python_b, root, home, rebind, stage="explicit-rebind")
    assert marker.read_bytes() != marker_a
    assert (home / "config.yaml").read_bytes() == config_a
    _run(python_b, root, home, INSTALL, stage="reinstall-b", timeout=180)
    _run(python_b, root, home, INSTALL, stage="repeat-install-b", timeout=180)
    _run(python_b, root, home, r'''
import sys,json,io,contextlib
from pathlib import Path
from hermes_feishu_card import cli
home=Path.home();config=home/'hfc.yaml'
config.write_text('server:\n  host: 127.0.0.1\n  port: 9\n')
output=io.StringIO()
with contextlib.redirect_stdout(output):
    status=cli.main(['doctor','--config',str(config),'--hermes-dir',sys.argv[1],'--hermes-home',str(home),'--json'])
assert status in (0,1)
report=json.loads(output.getvalue())
assert report['install_state']['status']=='installed'
assert report['install_state']['legacy_observer']['status']=='active'
''', stage="doctor-b")
    # Independent config-drift path on the now-current binding.
    (home / "config.yaml").write_bytes((home / "config.yaml").read_bytes() + b"unrelated_model: preserve-me\n")
    _run(python_b, root, home, rebind, stage="config-preserving-rebind")
    _run(python_b, root, home, r'''
import sys
from pathlib import Path
from hermes_feishu_card import cli
assert cli.main(['restore','--hermes-dir',sys.argv[1],'--hermes-home',str(Path.home()),'--yes'])==0
''', stage="restore-b", timeout=180)
    assert not marker.exists()
    assert (home / "config.yaml").read_bytes() == (home / "pm-config-before-observer.yaml").read_bytes() + b"unrelated_model: preserve-me\n"
    for name, contents in before.items():
        assert (root / name).read_bytes() == contents, name
    assert (home / "plugins/hfc-runtime-dependency/.hfc-owner.json").is_file()
