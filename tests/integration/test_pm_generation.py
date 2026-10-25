"""Real upstream PM dependency transaction, ordinary wheel and child imports.

Opt-in exact upstream fixture; provisioning alone is injected with the local
uv/Python toolchain, as in Hermes' own PM lifecycle tests. Resolution, workspace
copy, generation selection, facts and config publication execute upstream code.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install import pm_dependency as pm


PM_ROOTS = {
    "previous": ("HFC_UPSTREAM_PM_ROOT", "7bda2a3368024f7ae1c70cef16583e7c2c891674"),
    "0224": ("HFC_CONFIG_WRITER_0224_ROOT", "4d04303818b61bf3aa09278af48356315515965b"),
    "0225": ("HFC_CONFIG_WRITER_0225_ROOT", "bf0203a794a07aac3ff764972ab9087baa4e4f4e"),
    "main-4e58": ("HFC_CONFIG_WRITER_MAIN_4E58_ROOT", "4e58c721c8cf9b51c46636ea84ea6a5cb540880a"),
}


@pytest.mark.parametrize("snapshot", PM_ROOTS)
def test_exact_wheel_survives_real_pm_generations(tmp_path, monkeypatch, snapshot):
    variable, expected_head = PM_ROOTS[snapshot]
    configured = os.environ.get(variable)
    if not configured or not os.environ.get("HFC_TEST_WHEEL"):
        message = "pinned PM source or ordinary wheel missing: " + variable
        if os.environ.get("HFC_REQUIRE_PM_GENERATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    upstream = Path(configured)
    head = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'],
                          check=True, text=True, capture_output=True, timeout=10).stdout.strip()
    assert head == expected_head
    clean = subprocess.run(['git', '-C', str(upstream), 'status', '--porcelain'],
                           check=True, text=True, capture_output=True, timeout=10)
    assert not clean.stdout.strip(), 'pinned PM fixture must be clean'
    uv = shutil.which('uv')
    assert uv
    wheel = Path(os.environ['HFC_TEST_WHEEL'])
    root, home = tmp_path / 'core', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    (home / 'config.yaml').write_text('plugins:\n  enabled: []\n  disabled: [keep-disabled]\nother: keep\n')
    (root / 'pyproject.toml').write_text('[project]\nname="pm-test-core"\nversion="1"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n')
    subprocess.run([uv, 'lock'], cwd=root, check=True, capture_output=True, timeout=60)
    # All source modules remain the exact pinned checkout. Only the selected
    # project and pre-provisioned toolchain differ from a public Hermes install.
    injection = (
        f"sys.path.append({str(upstream)!r})\n"
        "import pm.paths, pm._uv, pm.client, pm.install\n"
        f"pm.paths.repo_root = lambda: Path({str(root)!r})\n"
        f"pm._uv._toolchain = lambda **kwargs: (Path({uv!r}), Path({sys.executable!r}))\n"
        "pm.client.sync_venv = lambda *args, **kwargs: pm.install.sync_venv(*args, **kwargs)\n"
    )
    child = pm._CHILD.replace('from hermes_constants import get_hermes_home', injection + 'from hermes_constants import get_hermes_home')
    monkeypatch.setattr(pm, '_CHILD', child)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_RUNTIME_DIR', str(tmp_path / 'tools'))
    receipt = pm.ensure(root, Path(sys.executable), home=home, spec=str(wheel), version=cli.PACKAGE_VERSION)
    assert receipt is not None
    receipt.finish()
    select_code = (f"import sys;sys.path.insert(0,{str(upstream)!r});from pathlib import Path;"
                   f"from pm.environments import committed_venv;print(committed_venv(Path({str(root)!r})))")
    def selected():
        return Path(subprocess.run([sys.executable, '-I', '-c', select_code], cwd=tmp_path,
                                   text=True, capture_output=True, check=True, timeout=30).stdout.strip())
    first = selected()
    python = first / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    member = home / 'plugins' / pm.NAME
    assert pm.verify_installed(member, python)
    owner_before = (member / pm.OWNER).read_bytes()
    owner = json.loads(owner_before)
    external = Path(owner["external_wheel"]["path"])
    assert member not in external.parents
    assert external.parent == home / ".hermes_feishu_card" / "wheel-store" / owner["external_wheel"]["sha256"]
    external_bytes = external.read_bytes()
    assert hashlib.sha256(external_bytes).hexdigest() == owner["external_wheel"]["sha256"]
    assert external_bytes == wheel.read_bytes()
    report = cli._check_runtime_hook_import(python)
    assert report['version'] == cli.PACKAGE_VERSION
    assert cli._runtime_report_uses_installed_package(report, python)
    # Trigger a genuinely new generation by adding an unrelated dependency
    # member through the same public admission path. HFC is rediscovered from
    # config, not passed as a special candidates/members override.
    other = home / 'plugins' / 'other'
    other.mkdir()
    # v0.22.5 deliberately excludes metadata-only projects from PM members;
    # make this unrelated member genuinely installable so both the older and
    # current workspace rules cross a generation.
    (other / 'other').mkdir()
    (other / 'other' / '__init__.py').write_text('__version__ = "1"\n')
    (other / 'pyproject.toml').write_text(
        '[project]\nname="other"\nversion="1"\ndependencies=[]\n'
        '[tool.uv]\npackage=true\n'
    )
    again = child.replace("enabled = plugins.get('enabled') or []", "enabled = [*(plugins.get('enabled') or []), 'other']")
    monkeypatch.setattr(pm, '_CHILD', again)
    probe = pm._call(root, Path(sys.executable), home, {'operation': 'probe'})
    pm._call(root, Path(sys.executable), home, {'operation': 'add', 'name': pm.NAME, 'expected': probe['expected']})
    second = selected()
    assert second != first
    python2 = second / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    assert pm.verify_installed(member, python2)
    # The copied workspace member must retain the immutable external source.
    copied_projects = list(second.parent.glob("workspace/**/pyproject.toml"))
    assert any(str(external) in project.read_text() for project in copied_projects)
    assert external.read_bytes() == external_bytes
    assert pm.ensure(root, Path(sys.executable), home=home, spec=str(wheel),
                     version=cli.PACKAGE_VERSION) is None
    assert selected() == second
    assert (member / pm.OWNER).read_bytes() == owner_before

    # A valid same-name wheel with a changed ZIP comment has identical package
    # resources but distinct archive provenance. Real admission commits it; the
    # public receipt rollback must restore the old member and re-admit its source.
    replacement_dir = tmp_path / "replacement"
    replacement_dir.mkdir()
    replacement = replacement_dir / wheel.name
    shutil.copyfile(wheel, replacement)
    with zipfile.ZipFile(replacement, "a") as archive:
        archive.comment = b"HFC real PM rollback acceptance"
    replacement_receipt = pm.ensure(root, Path(sys.executable), home=home,
                                   spec=str(replacement), version=cli.PACKAGE_VERSION)
    assert replacement_receipt is not None
    replacement_owner = json.loads((member / pm.OWNER).read_bytes())
    replacement_external = Path(replacement_owner["external_wheel"]["path"])
    assert replacement_external != external
    assert pm.verify_installed(member, selected() / python2.relative_to(second))
    assert external.read_bytes() == external_bytes
    # Generation A remains independently importable while another source wins.
    assert cli._runtime_report_uses_installed_package(cli._check_runtime_hook_import(python), python)
    replacement_receipt.rollback()
    assert (member / pm.OWNER).read_bytes() == owner_before
    assert external.read_bytes() == external_bytes
    assert replacement_external.is_file(), "unknown old-generation references require retention"
    assert pm.verify_installed(member, selected() / python2.relative_to(second))
    assert cli._runtime_report_uses_installed_package(cli._check_runtime_hook_import(python2), python2)
    # A dependency-only directory is deliberately absent from runtime plugin
    # discovery, while the single wheel entry point remains available.
    discovery = (f"import sys;sys.path.insert(0,{str(upstream)!r});"
                 "from pathlib import Path;from hermes_cli.plugins_discovery import scan_directory;"
                 f"assert scan_directory(Path({str(home / 'plugins')!r}), 'user') == []")
    subprocess.run([sys.executable, '-I', '-c', discovery], cwd=tmp_path,
                   check=True, capture_output=True, text=True, timeout=30)
    # RECORD/direct_url validation above proves ordinary non-editable package
    # installation; an independent process checks the unique producer entry.
    code = "import importlib.metadata as m; print(sum(e.name=='hermes-feishu-card' for e in m.entry_points(group='hermes_agent.plugins')))"
    result = subprocess.run([str(python2), '-I', '-c', code], cwd=tmp_path,
                            check=True, text=True, capture_output=True, timeout=30)
    assert result.stdout.strip() == '1'
    # Exercise the installed wheel's real CLI in a fresh process, outside the
    # source checkout and without inherited Python import paths.
    cli_result = subprocess.run([str(python2), '-I', '-m', 'hermes_feishu_card.cli', '--help'],
                                cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    assert 'hermes-feishu-card' in cli_result.stdout
    # The registration logic itself also runs from the ordinary wheel in an
    # isolated child. Only upstream PM source and fixture dependencies/tools
    # are supplied; the HFC checkout is never on that child's import path.
    import sysconfig
    installed_script = r'''
import os,sys
from pathlib import Path
from hermes_feishu_card.install import pm_dependency as package
from hermes_feishu_card import __version__
assert Path(sys.prefix) in Path(package.__file__).parents
root,home,upstream,wheel,tools_python,fixture_site,uv=map(Path,sys.argv[1:])
root.mkdir();home.mkdir()
(root/'pyproject.toml').write_text('[project]\nname="installed-core"\nversion="1"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n')
(home/'config.yaml').write_text('plugins:\n  enabled: []\n')
os.environ['HERMES_HOME']=str(home)
import subprocess
subprocess.run([str(uv),'lock'],cwd=root,check=True,capture_output=True,timeout=60)
injection=(f"sys.path.append({str(upstream)!r})\n"
           f"sys.path.append({str(fixture_site)!r})\n"
           "import pm.paths,pm._uv,pm.client,pm.install\n"
           f"pm.paths.repo_root=lambda:Path({str(root)!r})\n"
           f"pm._uv._toolchain=lambda **kwargs:(Path({str(uv)!r}),Path({str(tools_python)!r}))\n"
           "pm.client.sync_venv=lambda *a,**k:pm.install.sync_venv(*a,**k)\n")
package._CHILD=package._CHILD.replace('from hermes_constants import get_hermes_home',injection+'from hermes_constants import get_hermes_home')
exec(injection)
receipt=package.ensure(root,Path(sys.executable),home=home,spec=str(wheel),version=__version__)
assert receipt is not None
receipt.finish()
from pm.environments import committed_venv
first=committed_venv(root)
member=home/'plugins'/package.NAME
assert package.verify_installed(member,first/'bin/python')
future=home/'plugins'/'future';future.mkdir()
(future/'future').mkdir()
(future/'future'/'__init__.py').write_text('__version__ = "1"\n')
(future/'pyproject.toml').write_text('[project]\nname="future"\nversion="1"\ndependencies=[]\n[tool.uv]\npackage=true\n')
package._CHILD=package._CHILD.replace("enabled = plugins.get('enabled') or []", "enabled = [*(plugins.get('enabled') or []), 'future']")
probe=package._call(root,Path(sys.executable),home,{'operation':'probe'})
package._call(root,Path(sys.executable),home,{'operation':'add','name':package.NAME,'expected':probe['expected']})
second=committed_venv(root)
assert first!=second
assert package.verify_installed(member,second/'bin/python')
print('ordinary-installed PM branch crossed generations')
'''
    installed_result = subprocess.run([str(python2), '-I', '-c', installed_script,
        str(tmp_path / 'installed-core'), str(tmp_path / 'installed-home'), str(upstream),
        str(wheel), sys.executable, sysconfig.get_path('purelib'), uv],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=180)
    assert installed_result.returncode == 0, installed_result.stderr
    assert 'ordinary-installed PM branch crossed generations' in installed_result.stdout
    monkeypatch.setattr(pm, '_CHILD', child)
    pm.remove(root, Path(sys.executable), home=home)
    config = (home / 'config.yaml').read_text()
    assert 'other' in config and 'keep-disabled' in config and pm.NAME not in config
    assert not member.exists()


@pytest.mark.parametrize("snapshot,metadata_member", [("0224", True), ("0225", False)])
def test_pinned_pm_metadata_only_and_installable_member_predicates(
    tmp_path, snapshot, metadata_member
):
    """Keep the older metadata-only behavior distinct from a generation trigger."""
    variable, expected_head = PM_ROOTS[snapshot]
    configured = os.environ.get(variable)
    if not configured:
        if os.environ.get("HFC_REQUIRE_PM_GENERATION") == "1":
            pytest.fail("pinned PM source missing: " + variable)
        pytest.skip("pinned PM source missing: " + variable)
    upstream = Path(configured)
    head = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    assert head == expected_head
    member = tmp_path / "member"
    member.mkdir()
    metadata = '[project]\nname="member"\nversion="1"\ndependencies=[]\n'
    (member / "pyproject.toml").write_text(metadata + '[tool.uv]\npackage=false\n')
    code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{str(upstream)!r});"
        "from pm.workspace import _is_member_candidate;"
        f"print(json.dumps(_is_member_candidate(Path({str(member)!r}))))"
    )
    def candidate():
        result = subprocess.run(
            [sys.executable, "-I", "-c", code], cwd=tmp_path,
            env={"HOME": str(tmp_path), "HERMES_HOME": str(tmp_path)},
            capture_output=True, text=True, check=True, timeout=30,
        )
        return result.stdout.strip() == "true"
    assert candidate() is metadata_member
    (member / "pyproject.toml").write_text(metadata + '[tool.uv]\npackage=true\n')
    assert candidate() is True
