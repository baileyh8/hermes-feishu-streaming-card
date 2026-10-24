"""Owned PM member transaction regressions; no production HOME or PM state."""
from hashlib import sha256
import json
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install import pm_dependency as pm


VERSION = cli.PACKAGE_VERSION


@pytest.fixture
def pm_env(tmp_path, monkeypatch):
    root, home = tmp_path / 'hermes', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    (root / 'pm').mkdir()
    (root / 'pm/environments.py').write_text('')
    state = {'home': str(home), 'enabled': ['other'], 'disabled': ['disabled'], 'expected': 'original'}
    calls = []
    def call(root, python, selected_home, request):
        calls.append(request.copy())
        if request['operation'] != 'probe':
            assert request['expected'] == state['expected']
            if request['operation'] == 'add':
                state['enabled'] = list(dict.fromkeys([*state['enabled'], pm.NAME]))
            else:
                state['enabled'] = [name for name in state['enabled'] if name != pm.NAME]
            state['expected'] = sha256(json.dumps(state['enabled']).encode()).hexdigest()
        return state.copy()
    monkeypatch.setattr(pm, '_call', call)
    def wheel(command, **kwargs):
        built = Path(command[command.index('--wheel-dir') + 1])
        path = built / f'hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(f'hermes_feishu_streaming_card-{VERSION}.dist-info/METADATA',
                             f'Name: hermes-feishu-streaming-card\nVersion: {VERSION}\n')
            archive.writestr('hermes_feishu_card/hook_runtime.py', '# fixture\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pm.subprocess, 'run', wheel)
    return SimpleNamespace(root=root, home=home, state=state, calls=calls, call=call)


def register(env):
    return pm.ensure(env.root, Path('/fixture/python'), home=env.home,
                     spec='/fixture/exact.whl', version=VERSION)


def test_registration_retains_other_selection_and_repeat_rollback(pm_env):
    env = pm_env
    receipt = register(env)
    assert receipt is not None
    member = env.home / 'plugins' / pm.NAME
    owner = pm._owned(member)
    assert owner['selection_added'] is True
    assert not (member / 'plugin.yaml').exists()
    assert not (member / '__init__.py').exists()
    assert env.state['enabled'] == ['other', pm.NAME]
    assert env.state['disabled'] == ['disabled']
    receipt.finish()
    assert register(env) is None
    # An owned source that was disabled by removing its enabled entry is still
    # a new selection change, even though no wheel rebuild is needed.
    env.state['enabled'].remove(pm.NAME)
    receipt = register(env)
    assert receipt is not None
    receipt.rollback()
    assert env.state['enabled'] == ['other']
    assert pm._owned(member) == owner
    receipt = register(env)
    receipt.finish()
    pm.remove(env.root, Path('/fixture/python'), home=env.home)
    assert env.state['enabled'] == ['other']
    assert not member.exists()


def test_first_registration_rollback_removes_only_new_owned_member(pm_env):
    receipt = register(pm_env)
    receipt.rollback()
    assert pm_env.state['enabled'] == ['other']
    assert not (pm_env.home / 'plugins' / pm.NAME).exists()


def test_preexisting_selection_is_not_uninstalled(pm_env):
    pm_env.state['enabled'].append(pm.NAME)
    receipt = register(pm_env)
    receipt.finish()
    with pytest.raises(ValueError, match='pre-existing'):
        pm.remove(pm_env.root, Path('/fixture/python'), home=pm_env.home)
    assert pm.NAME in pm_env.state['enabled']


def test_unknown_directory_and_explicit_disable_refuse(pm_env):
    member = pm_env.home / 'plugins' / pm.NAME
    member.mkdir(parents=True)
    (member / 'user.txt').write_text('keep')
    with pytest.raises(ValueError, match='not owned'):
        register(pm_env)
    assert (member / 'user.txt').read_text() == 'keep'
    shutil.rmtree(member)
    pm_env.state['disabled'].append(pm.NAME)
    with pytest.raises(ValueError, match='explicitly disabled'):
        register(pm_env)


def test_user_drift_blocks_update_and_receipt_cleanup(pm_env):
    receipt = register(pm_env)
    project = receipt.member / 'pyproject.toml'
    project.write_text(project.read_text() + '\n# user edit\n')
    with pytest.raises(ValueError, match='ownership'):
        register(pm_env)
    with pytest.raises(ValueError, match='ownership'):
        receipt.rollback()
    assert '# user edit' in project.read_text()


def test_refused_admission_restores_member_and_selection(pm_env, monkeypatch):
    def refuse(root, python, home, request):
        if request['operation'] == 'add':
            raise ValueError('resolver conflict')
        return pm_env.call(root, python, home, request)
    monkeypatch.setattr(pm, '_call', refuse)
    with pytest.raises(ValueError, match='previous dependency source preserved'):
        register(pm_env)
    assert pm_env.state['enabled'] == ['other']
    assert not (pm_env.home / 'plugins' / pm.NAME).exists()


def test_ensure_matching_runtime_still_registers_and_resolves_new_python(tmp_path, monkeypatch):
    root = tmp_path / 'hermes'
    root.mkdir()
    detection = SimpleNamespace(root=root)
    old, new = tmp_path / 'generation-a/bin/python', tmp_path / 'generation-b/bin/python'
    selected = iter([old, new])
    monkeypatch.setattr(cli, '_detect_hermes_runtime_python', lambda *a, **kw: next(selected))
    monkeypatch.setattr(pm, 'available', lambda root: True)
    receipt = SimpleNamespace(rollback=lambda: pytest.fail('unexpected rollback'))
    seen = []
    monkeypatch.setattr(pm, 'ensure', lambda root, python, **kw: seen.append(python) or receipt)
    monkeypatch.setattr(pm, '_call', lambda *a, **kw: {'home': str(tmp_path)})
    monkeypatch.setattr(pm, 'verify_installed', lambda member, python: python == new)
    monkeypatch.setattr(cli, '_check_runtime_hook_import', lambda python: {
        'status': 'ok', 'version': cli.PACKAGE_VERSION, 'location': str(python)})
    monkeypatch.setattr(cli, '_runtime_report_uses_installed_package', lambda *a: True)
    assert cli._ensure_hermes_runtime_package(detection) is receipt
    assert seen == [old]


def test_pm_resolution_failure_rolls_back_registration(tmp_path, monkeypatch):
    root = tmp_path / 'hermes'
    root.mkdir()
    selected = iter([tmp_path / 'python', None])
    monkeypatch.setattr(cli, '_detect_hermes_runtime_python', lambda *a, **kw: next(selected))
    monkeypatch.setattr(pm, 'available', lambda root: True)
    rolled = []
    monkeypatch.setattr(pm, 'ensure', lambda *a, **kw: SimpleNamespace(rollback=lambda: rolled.append(True)))
    with pytest.raises(ValueError, match='did not select'):
        cli._ensure_hermes_runtime_package(SimpleNamespace(root=root))
    assert rolled == [True]


def test_build_time_member_drift_is_not_replaced(pm_env, monkeypatch):
    receipt = register(pm_env)
    receipt.finish()
    project = receipt.member / 'pyproject.toml'
    build = pm.subprocess.run
    def drift(command, **kwargs):
        result = build(command, **kwargs)
        project.write_text(project.read_text() + '\n# concurrent user edit\n')
        return result
    monkeypatch.setattr(pm.subprocess, 'run', drift)
    with pytest.raises(ValueError, match='ownership'):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/different.whl', version=VERSION)
    assert '# concurrent user edit' in project.read_text()
    assert not list(project.parent.parent.glob('.hfc-runtime-*'))


def test_concurrent_installers_build_one_owned_member(pm_env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    started, release = Event(), Event()
    build = pm.subprocess.run
    builds = []
    def slow(command, **kwargs):
        builds.append(True)
        started.set()
        assert release.wait(5)
        return build(command, **kwargs)
    monkeypatch.setattr(pm.subprocess, 'run', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register, pm_env)
        assert started.wait(5)
        second = pool.submit(register, pm_env)
        release.set()
        receipt = first.result(timeout=10)
        assert second.result(timeout=10) is None
        receipt.finish()
    assert len(builds) == 1
    assert pm_env.state['enabled'] == ['other', pm.NAME]
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_uncertain_admission_keeps_exact_owned_source(pm_env, monkeypatch):
    def uncertain(root, python, home, request):
        response = pm_env.call(root, python, home, request)
        if request['operation'] == 'add':
            raise ValueError('transport lost after commit')
        return response
    monkeypatch.setattr(pm, '_call', uncertain)
    with pytest.raises(ValueError, match='outcome uncertain'):
        register(pm_env)
    assert pm.NAME in pm_env.state['enabled']
    assert pm._owned(pm_env.home / 'plugins' / pm.NAME)


def test_same_version_local_wheel_content_changes_identity(tmp_path):
    wheel = tmp_path / 'exact.whl'
    wheel.write_bytes(b'first')
    first = pm._install_spec_digest(str(wheel))
    wheel.write_bytes(b'second')
    assert pm._install_spec_digest(str(wheel)) != first


@pytest.mark.parametrize('dirfd', [True, False])
def test_second_publication_rename_failure_restores_previous_source(pm_env, monkeypatch, dirfd):
    from hermes_feishu_card.install import posix_transaction
    monkeypatch.setattr(posix_transaction, 'DIRFD_BINDING_SUPPORTED', dirfd)
    receipt = register(pm_env)
    receipt.finish()
    member = receipt.member
    before = {p.name: p.read_bytes() for p in member.iterdir()}
    rename = pm.os.rename if dirfd else Path.rename
    failed = []
    def fail_staging(source, target, **kwargs):
        if (not failed and Path(source).name.startswith('.hfc-runtime-')
                and not str(source).endswith('.previous') and Path(target).name == pm.NAME):
            failed.append(True)
            raise OSError('injected second rename failure')
        return rename(source, target, **kwargs)
    # Older pathlib caches os.rename in its accessor, so patch the actual
    # portable call site instead of relying on a dynamic os.rename lookup.
    monkeypatch.setattr(pm.os if dirfd else Path, 'rename', fail_staging)
    with pytest.raises(OSError, match='second rename failure'):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/new-source.whl', version=VERSION)
    assert failed == [True]
    assert {p.name: p.read_bytes() for p in member.iterdir()} == before
    assert not list(member.parent.glob('.hfc-runtime-*'))
    assert pm_env.state['enabled'] == ['other', pm.NAME]


def test_portable_publication_and_verified_cache_reuse_without_spec(pm_env, monkeypatch):
    from hermes_feishu_card.install import posix_transaction
    monkeypatch.setattr(posix_transaction, 'DIRFD_BINDING_SUPPORTED', False)
    receipt = register(pm_env)
    receipt.finish()
    assert pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                     spec=None, version=VERSION) is None
    pm.remove(pm_env.root, Path('/fixture/python'), home=pm_env.home)
    assert pm_env.state['enabled'] == ['other']


def test_rollback_uninspectable_staging_refuses_before_rename(pm_env, monkeypatch):
    original = register(pm_env)
    original.finish()
    receipt = pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                        spec='/fixture/updated.whl', version=VERSION)
    failed = receipt.member.with_name(receipt.member.name + '.failed')
    lstat = Path.lstat
    renames = []
    def uninspectable(path, *args, **kwargs):
        if path == failed:
            raise PermissionError('injected uninspectable rollback staging')
        return lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', uninspectable)
    monkeypatch.setattr(pm.os, 'rename', lambda *a, **kw: renames.append(a))
    with pytest.raises(PermissionError, match='uninspectable rollback staging'):
        receipt.rollback()
    assert not renames
    assert pm._owned(receipt.member)
    assert pm._owned(receipt.backup)
    assert pm_env.state['enabled'] == ['other', pm.NAME]


def test_staged_project_sources_hfc_wheel_outside_plugin_workspace(pm_env):
    env = pm_env
    receipt = register(env)
    assert receipt is not None
    member = env.home / 'plugins' / pm.NAME
    staged = tomllib.loads((member / 'pyproject.toml').read_text(encoding='utf-8'))
    sources = staged['tool']['uv']['sources']['hermes-feishu-streaming-card']
    path = Path(sources['path'])
    # A path source that lands inside the plugin workspace member is resolved
    # by newer uv as a workspace reference and fails the lock; the staged
    # source must therefore stay outside every plugin member directory and
    # live in this hermes home's install-state wheel store instead.
    assert not path.is_relative_to((env.home / 'plugins').resolve())
    assert path.parent == (env.home / '.hermes_feishu_card' / 'wheel-store').resolve()
    assert path.read_bytes() == (member / path.name).read_bytes()
    assert pm._owned(member)
    receipt.finish()
