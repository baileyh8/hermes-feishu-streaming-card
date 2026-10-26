"""Owned PM member transaction regressions; no production HOME or PM state."""
from hashlib import sha256
import json
import os
import stat
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


def _symlink_or_skip_windows_privilege(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows symlink creation requires privilege (WinError 1314)')
        raise


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
    if dirfd and not posix_transaction.DIRFD_BINDING_SUPPORTED:
        pytest.skip('directory descriptor operations unavailable')
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
    assert path.parent.parent == (env.home / '.hermes_feishu_card' / 'wheel-store').resolve()
    assert path.parent.name == sha256(path.read_bytes()).hexdigest()
    assert path.read_bytes() == (member / path.name).read_bytes()
    assert pm._owned(member)
    receipt.finish()


def test_external_wheel_tamper_blocks_fast_path(pm_env):
    receipt = register(pm_env)
    source = tomllib.loads((receipt.member / 'pyproject.toml').read_text())['tool']['uv']['sources']['hermes-feishu-streaming-card']
    Path(source['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        register(pm_env)


def test_external_store_symlink_refuses(pm_env, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    state = pm_env.home / '.hermes_feishu_card'
    state.mkdir()
    _symlink_or_skip_windows_privilege(state / 'wheel-store', outside, directory=True)
    with pytest.raises(ValueError):
        register(pm_env)
    assert not list(outside.iterdir())


def _external(member):
    return Path(json.loads((member / pm.OWNER).read_text())['external_wheel']['path'])


def test_same_filename_different_bytes_keeps_previous_generation_on_rollback(pm_env, monkeypatch):
    first = register(pm_env)
    first.finish()
    old = _external(first.member)
    old_bytes = old.read_bytes()
    build = pm.subprocess.run
    def changed(command, **kwargs):
        result = build(command, **kwargs)
        wheel = next(Path(command[command.index('--wheel-dir') + 1]).glob('*.whl'))
        with zipfile.ZipFile(wheel, 'a') as archive:
            archive.writestr('hermes_feishu_card/new.py', '# changed\n')
        return result
    monkeypatch.setattr(pm.subprocess, 'run', changed)
    second = pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                       spec='/fixture/new.whl', version=VERSION)
    new = _external(second.member)
    assert old != new and old.name == new.name
    second.rollback()
    assert _external(second.member) == old
    assert old.read_bytes() == old_bytes
    assert new.exists()  # Unknown PM generation references require retention.
    pm.remove(pm_env.root, Path('/fixture/python'), home=pm_env.home)
    assert old.exists() and new.exists()


def test_schema1_upgrade_binds_external_source(pm_env):
    old_wheel = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    assert pm._owned(member) == owner
    second = register(pm_env)
    assert second is not None
    assert pm._owned(second.member)['schema'] == 2
    second.rollback()
    assert pm._owned(second.member)['schema'] == 1
    assert store_wheel.read_bytes() == old_wheel


@pytest.mark.parametrize('alteration', ['missing', 'symlink'])
def test_external_wheel_missing_or_symlink_refuses(pm_env, tmp_path, alteration):
    receipt = register(pm_env)
    wheel = _external(receipt.member)
    content = wheel.read_bytes()
    wheel.unlink()
    if alteration == 'symlink':
        outside = tmp_path / 'unowned.whl'
        outside.write_bytes(content)
        _symlink_or_skip_windows_privilege(wheel, outside)
    with pytest.raises(ValueError):
        register(pm_env)


def test_wheel_store_parent_swap_during_publication_refuses(pm_env, monkeypatch):
    from hermes_feishu_card.install import pm_wheel_store as store
    from hermes_feishu_card.install import posix_transaction as io
    if not io.DIRFD_BINDING_SUPPORTED:
        pytest.skip('stage_file race injection requires directory descriptor operations')
    original = io.stage_file
    saved = []
    def swap(target, *args, **kwargs):
        staged = original(target, *args, **kwargs)
        moved = target.parent.with_name(target.parent.name + '.moved')
        target.parent.rename(moved)
        target.parent.mkdir(mode=0o700)
        saved.append(moved)
        return staged
    monkeypatch.setattr(io, 'stage_file', swap)
    with pytest.raises(ValueError):
        store.publish(pm_env.home, 'hermes_feishu_streaming_card-1-py3-none-any.whl', b'payload')
    assert saved
    assert not list(saved[0].iterdir())
    assert not (pm_env.home / 'plugins' / pm.NAME).exists()


def test_store_uninspectable_metadata_refuses(pm_env, monkeypatch):
    from hermes_feishu_card.install import pm_wheel_store as store
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path.name == 'wheel-store':
            raise PermissionError('injected metadata failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(ValueError, match='ownership'):
        store.publish(pm_env.home, 'hermes_feishu_streaming_card-1-py3-none-any.whl', b'payload')


def test_unknown_admission_retains_external_source(pm_env, monkeypatch):
    def unknown(root, python, home, request):
        response = pm_env.call(root, python, home, request)
        if request['operation'] == 'add':
            raise ValueError('lost receipt')
        return response
    monkeypatch.setattr(pm, '_call', unknown)
    with pytest.raises(ValueError, match='outcome uncertain'):
        register(pm_env)
    member = pm_env.home / 'plugins' / pm.NAME
    assert _external(member).is_file()
    assert pm._owned(member)


@pytest.mark.parametrize('failure', ['publish', 'admission'])
def test_update_failure_preserves_previous_external_reference(pm_env, monkeypatch, failure):
    receipt = register(pm_env)
    receipt.finish()
    old = _external(receipt.member)
    old_bytes = old.read_bytes()
    owner = pm._owned(receipt.member)
    build = pm.subprocess.run
    def changed(command, **kwargs):
        result = build(command, **kwargs)
        wheel = next(Path(command[command.index('--wheel-dir') + 1]).glob('*.whl'))
        with zipfile.ZipFile(wheel, 'a') as archive:
            archive.writestr('hermes_feishu_card/update.py', '# different bytes\n')
        return result
    monkeypatch.setattr(pm.subprocess, 'run', changed)
    if failure == 'publish':
        monkeypatch.setattr(pm, '_publish_member', lambda *a: (_ for _ in ()).throw(ValueError('publish refused')))
    else:
        def refused(root, python, home, request):
            if request['operation'] == 'add':
                raise ValueError('admission refused')
            return pm_env.call(root, python, home, request)
        monkeypatch.setattr(pm, '_call', refused)
    with pytest.raises(ValueError):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/update.whl', version=VERSION)
    assert pm._owned(receipt.member) == owner
    assert _external(receipt.member) == old and old.read_bytes() == old_bytes


def test_external_files_and_directories_are_private(pm_env):
    import os
    import stat
    if os.name == 'nt':
        pytest.skip('Windows inherits filesystem ACLs; Unix mode is not an ACL proof')
    receipt = register(pm_env)
    path = _external(receipt.member)
    for entry in [path, path.parent, path.parent.parent, path.parent.parent.parent]:
        assert stat.S_IMODE(entry.lstat().st_mode) & 0o077 == 0


def test_store_ancestor_swap_before_stage_does_not_write_unowned_directory(pm_env, monkeypatch, tmp_path):
    from hermes_feishu_card.install import pm_wheel_store as store
    from hermes_feishu_card.install import posix_transaction as io
    if not io.DIRFD_BINDING_SUPPORTED:
        pytest.skip('stage_file race injection requires directory descriptor operations')
    original = io.stage_file
    outside = tmp_path / 'outside'
    outside.mkdir()
    def swap(target, *args, **kwargs):
        state = pm_env.home / '.hermes_feishu_card'
        state.rename(state.with_name('owned-moved'))
        _symlink_or_skip_windows_privilege(state, outside, directory=True)
        return original(target, *args, **kwargs)
    monkeypatch.setattr(io, 'stage_file', swap)
    with pytest.raises(ValueError):
        store.publish(pm_env.home, 'hermes_feishu_streaming_card-1-py3-none-any.whl', b'payload')
    assert not list(outside.iterdir())


def test_external_owner_path_tampering_refuses(pm_env, tmp_path):
    receipt = register(pm_env)
    owner_path = receipt.member / pm.OWNER
    owner = json.loads(owner_path.read_text())
    owner['external_wheel']['path'] = str(tmp_path / Path(owner['external_wheel']['path']).name)
    owner_path.write_text(json.dumps(owner))
    with pytest.raises(ValueError, match='ownership'):
        register(pm_env)


@pytest.mark.parametrize('source_kind', ['missing_hash', 'wrong_path', 'repository', 'wrong_digest', 'changed_bytes'])
def test_uv_missing_archive_hash_requires_exact_owned_external_source(tmp_path, source_kind):
    import subprocess
    import sys
    from hermes_feishu_card.install import pm_wheel_store
    home = tmp_path / 'home'
    home.mkdir()
    reference = pm_wheel_store.publish(home, f'hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl', b'wheel fixture')
    installed = tmp_path / 'installed'
    package = installed / 'hermes_feishu_card'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'hook_runtime.py').write_bytes(b'# installed bytes\n')
    dist = installed / f'hermes_feishu_streaming_card-{VERSION}.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text(f'Name: hermes-feishu-streaming-card\nVersion: {VERSION}\n')
    wheel = Path(reference['path'])
    url = wheel.as_uri()
    archive = {}
    if source_kind == 'wrong_path':
        wrong = tmp_path / wheel.name
        wrong.write_bytes(wheel.read_bytes())
        url = wrong.as_uri()
    elif source_kind == 'repository':
        url = tmp_path.as_uri()
    elif source_kind == 'wrong_digest':
        archive = {'hashes': {'sha256': '0' * 64}}
    elif source_kind == 'changed_bytes':
        wheel.write_bytes(b'changed')
    (dist / 'direct_url.json').write_text(json.dumps({'url': url, 'archive_info': archive}))
    request = {'external_wheel': reference, 'wheel_sha256': reference['sha256'],
               'wheel_version': VERSION,
               'files': {'hermes_feishu_card/hook_runtime.py': sha256(b'# installed bytes\n').hexdigest()}}
    child = 'import sys;sys.path.insert(0,' + repr(str(installed)) + ');\n' + pm._VERIFY_CHILD
    result = subprocess.run([sys.executable, '-I', '-c', child], input=json.dumps(request),
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok'] is (source_kind == 'missing_hash')


SCHEMA1_VERSION = '5.0.7'


def _schema1_wheel(version=SCHEMA1_VERSION):
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            f'hermes_feishu_streaming_card-{version}.dist-info/METADATA',
            f'Name: hermes-feishu-streaming-card\nVersion: {version}\n',
        )
        archive.writestr('hermes_feishu_card/__init__.py', f'__version__ = "{version}"\n')
        archive.writestr('hermes_feishu_card/hook_runtime.py', '# PR458 fixture hook\n')
        archive.writestr('hermes_feishu_card/hermes_plugin.py', '# PR458 fixture plugin\n')
    return buffer.getvalue()


def _schema1_member(home, version=SCHEMA1_VERSION, wheel_bytes=None):
    """Write the exact flat member format from bb447f66946def97d43fa722132c9a98a432afb3."""
    wheel_bytes = _schema1_wheel(version) if wheel_bytes is None else wheel_bytes
    member = home / 'plugins' / pm.NAME
    member.mkdir(parents=True)
    state = home / '.hermes_feishu_card'
    state.mkdir(mode=0o700, exist_ok=True)
    state.chmod(0o700)
    store = state / 'wheel-store'
    store.mkdir()
    # PR458 used mkdir() without an explicit mode, so a normal umask left the
    # flat store at 0755. The recovery proof accepts that historical mode only
    # while the directory remains owned by the current user and not writable by
    # group/world.
    store.chmod(0o755)
    wheel_name = f'hermes_feishu_streaming_card-{version}-py3-none-any.whl'
    store_wheel = store / wheel_name
    store_wheel.write_bytes(wheel_bytes)
    store_wheel.chmod(0o644)
    member_wheel = member / wheel_name
    member_wheel.write_bytes(wheel_bytes)
    member_wheel.chmod(0o644)
    project = (
        '[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
        f'dependencies = ["hermes-feishu-streaming-card=={version}"]\n'
        '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
        f'hermes-feishu-streaming-card = {{ path = "{store_wheel.as_posix()}" }}\n'
    )
    (member / 'pyproject.toml').write_text(project)
    owner = {
        'schema': 1,
        'version': version,
        'spec_sha256': 'a' * 64,
        'selection_added': True,
        'files': {
            'pyproject.toml': sha256(project.encode()).hexdigest(),
            wheel_name: sha256(wheel_bytes).hexdigest(),
        },
    }
    (member / pm.OWNER).write_text(json.dumps(owner))
    return member, owner, store_wheel


def _installed_runtime(tmp_path, wheel_bytes, version, direct_url):
    import subprocess
    import sys
    runtime = tmp_path / 'runtime'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(runtime)],
                   check=True, capture_output=True, text=True)
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    probe = subprocess.run(
        [str(python), '-I', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'],
        check=True, capture_output=True, text=True, timeout=30,
    )
    site = Path(probe.stdout.strip())
    with zipfile.ZipFile(__import__('io').BytesIO(wheel_bytes)) as archive:
        for name in archive.namelist():
            if name.startswith('hermes_feishu_card/') and not name.endswith('/'):
                target = site / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    dist = site / f'hermes_feishu_streaming_card-{version}.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text(
        f'Name: hermes-feishu-streaming-card\nVersion: {version}\n'
    )
    (dist / 'direct_url.json').write_text(json.dumps(direct_url))
    return python, site


def test_pr458_schema1_flat_store_is_proven_by_owner_declaration_and_digest(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)

    assert pm._owned(member) == owner
    assert pm.legacy_external_wheel(member, owner) == {
        'path': str(store_wheel),
        'sha256': sha256(wheel_bytes).hexdigest(),
    }
    assert (member / 'pyproject.toml').read_text() == (
        '[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
        f'dependencies = ["hermes-feishu-streaming-card=={SCHEMA1_VERSION}"]\n'
        '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
        f'hermes-feishu-streaming-card = {{ path = "{store_wheel.as_posix()}" }}\n'
    )


def test_pr458_flat_proof_is_read_only(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    member, owner, store_wheel = _schema1_member(home)

    proof = pm.pr458_flat_proof(member, owner)

    assert proof is not None
    assert proof.store_wheel == store_wheel
    assert stat.S_IMODE(store_wheel.parent.lstat().st_mode) == 0o755


def test_pr458_schema1_flat_store_with_non_ascii_home_is_verified(tmp_path):
    home = tmp_path / '中文-Hermes'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)
    declared = (member / 'pyproject.toml').read_text()

    # bb447f6 wrote the raw as_posix() path through an f-string. This is not
    # the schema-2 json.dumps(ensure_ascii=True) representation.
    assert '中文-Hermes' in declared
    assert '\\u' not in declared
    assert declared != pm._project_source(
        SCHEMA1_VERSION, {'path': store_wheel.as_posix()}
    )
    assert pm._pr458_flat_wheel(member, owner) is not None
    assert pm._owned(member) == owner
    assert pm.legacy_external_wheel(member, owner) == {
        'path': str(store_wheel),
        'sha256': sha256(wheel_bytes).hexdigest(),
    }
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': store_wheel.as_uri(), 'archive_info': {}},
    )
    assert pm.verify_installed(member, python) is True


def test_pr458_schema1_non_ascii_home_passes_rebind_runtime_verification(
    tmp_path, monkeypatch
):
    from hermes_feishu_card.install import legacy_rebind, plugin
    home = tmp_path / '中文-Hermes'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': store_wheel.as_uri(), 'archive_info': {}},
    )
    probes = []
    monkeypatch.setattr(
        plugin,
        'probe_plugin_entrypoint',
        lambda target, *, expected_version: probes.append(expected_version)
        or SimpleNamespace(status='verified'),
    )
    binding = SimpleNamespace(hermes_home=home, runtime_python=python)

    legacy_rebind._verify_runtime(binding)

    assert pm._owned(member) == owner
    assert probes == [SCHEMA1_VERSION]


def test_schema1_non_flat_declaration_is_not_treated_as_flat(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)
    other = tmp_path / store_wheel.name
    other.write_bytes(wheel_bytes)
    project = (member / 'pyproject.toml').read_text().replace(
        store_wheel.as_posix(), other.as_posix()
    )
    (member / 'pyproject.toml').write_text(project)
    owner['files']['pyproject.toml'] = sha256(project.encode()).hexdigest()
    (member / pm.OWNER).write_text(json.dumps(owner))

    # A non-flat schema-1 declaration remains a historical workspace member;
    # it must not be promoted to the PR458 flat proof merely because another
    # file with identical bytes exists.
    assert pm._owned(member) == owner
    with pytest.raises(ValueError):
        pm.legacy_external_wheel(member, owner)
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': other.as_uri(), 'archive_info': {}},
    )
    assert pm.verify_installed(member, python) is False


@pytest.mark.parametrize('damage', [
    'missing', 'wrong_digest', 'file_symlink', 'file_world_writable',
    'store_world_writable', 'state_not_private', 'store_symlink',
    'declaration_symlink',
])
def test_schema1_store_requires_safe_private_identity_and_digest(tmp_path, damage):
    home = tmp_path / 'home'
    home.mkdir()
    member, owner, store_wheel = _schema1_member(home)
    state = home / '.hermes_feishu_card'
    store = state / 'wheel-store'
    if damage == 'missing':
        store_wheel.unlink()
    elif damage == 'wrong_digest':
        store_wheel.write_bytes(b'not the owned wheel')
    elif damage == 'file_symlink':
        outside = tmp_path / 'outside.whl'
        outside.write_bytes(store_wheel.read_bytes())
        store_wheel.unlink()
        _symlink_or_skip_windows_privilege(store_wheel, outside)
    elif damage == 'file_world_writable':
        store_wheel.chmod(0o666)
    elif damage == 'store_world_writable':
        store.chmod(0o777)
    elif damage == 'state_not_private':
        state.chmod(0o755)
    elif damage == 'store_symlink':
        outside = tmp_path / 'outside-store'
        outside.mkdir()
        (outside / store_wheel.name).write_bytes(store_wheel.read_bytes())
        store_wheel.unlink()
        store.rmdir()
        _symlink_or_skip_windows_privilege(store, outside, directory=True)
    elif damage == 'declaration_symlink':
        outside = tmp_path / 'outside-pyproject.toml'
        outside.write_bytes((member / 'pyproject.toml').read_bytes())
        (member / 'pyproject.toml').unlink()
        _symlink_or_skip_windows_privilege(member / 'pyproject.toml', outside)

    with pytest.raises(ValueError):
        pm._owned(member)
    with pytest.raises(ValueError):
        pm.legacy_external_wheel(member, owner)


def test_schema1_verify_installed_accepts_empty_archive_info_only_for_exact_file_url(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)
    direct_url = {'url': store_wheel.as_uri(), 'archive_info': {}}
    python, site = _installed_runtime(tmp_path, wheel_bytes, SCHEMA1_VERSION, direct_url)
    dist = next(site.glob('hermes_feishu_streaming_card-*.dist-info'))

    assert pm.verify_installed(member, python) is True

    other = tmp_path / store_wheel.name
    other.write_bytes(wheel_bytes)
    (dist / 'direct_url.json').write_text(json.dumps({
        'url': other.as_uri(), 'archive_info': {},
    }))
    assert pm.verify_installed(member, python) is False

    (dist / 'direct_url.json').write_text(json.dumps(direct_url))
    metadata = dist / 'METADATA'
    metadata.write_text('Name: hermes-feishu-streaming-card\nVersion: 5.0.6\n')
    assert pm.verify_installed(member, python) is False

    metadata.write_text(
        f'Name: hermes-feishu-streaming-card\nVersion: {SCHEMA1_VERSION}\n'
    )
    hook = site / 'hermes_feishu_card/hook_runtime.py'
    hook.write_bytes(b'# changed resource\n')
    assert pm.verify_installed(member, python) is False

    hook.write_bytes(b'# PR458 fixture hook\n')
    (dist / 'direct_url.json').write_text(json.dumps({
        'url': store_wheel.as_uri(),
        'archive_info': {'hashes': {'sha256': '0' * 64}},
    }))
    assert pm.verify_installed(member, python) is False

    (dist / 'direct_url.json').write_text(json.dumps({
        'url': 'https://github.com/example/hermes-feishu-streaming-card.git',
        'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40},
    }))
    assert pm.verify_installed(member, python) is False


def test_historical_schema1_workspace_member_keeps_original_verification(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    wheel_bytes = _schema1_wheel()
    workspace_wheel = workspace / f'hermes_feishu_streaming_card-{SCHEMA1_VERSION}-py3-none-any.whl'
    workspace_wheel.write_bytes(wheel_bytes)
    member = home / 'plugins' / pm.NAME
    member.mkdir(parents=True)
    member_wheel = member / workspace_wheel.name
    member_wheel.write_bytes(wheel_bytes)
    project = (
        '[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
        f'dependencies = ["hermes-feishu-streaming-card=={SCHEMA1_VERSION}"]\n'
        '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
        f'hermes-feishu-streaming-card = {{ path = "{workspace_wheel.as_posix()}" }}\n'
    )
    (member / 'pyproject.toml').write_text(project)
    owner = {
        'schema': 1,
        'version': SCHEMA1_VERSION,
        'spec_sha256': 'a' * 64,
        'selection_added': True,
        'files': {
            'pyproject.toml': sha256(project.encode()).hexdigest(),
            member_wheel.name: sha256(wheel_bytes).hexdigest(),
        },
    }
    (member / pm.OWNER).write_text(json.dumps(owner))
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': workspace_wheel.as_uri(), 'archive_info': {}},
    )

    assert pm._owned(member) == owner
    with pytest.raises(ValueError):
        pm.legacy_external_wheel(member, owner)
    assert pm.verify_installed(member, python) is True


def test_schema2_store_parent_0755_without_flat_wheel_refuses(pm_env):
    from hermes_feishu_card.install import pm_wheel_store
    state = pm_env.home / '.hermes_feishu_card'
    state.mkdir(mode=0o700)
    store = state / 'wheel-store'
    store.mkdir()
    store.chmod(0o755)

    with pytest.raises(ValueError):
        pm_wheel_store.publish(
            pm_env.home,
            f'hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl',
            b'payload',
        )


def test_schema2_parent_0755_with_unrelated_wheel_still_refuses(pm_env):
    from hermes_feishu_card.install import pm_wheel_store
    state = pm_env.home / '.hermes_feishu_card'
    state.mkdir(mode=0o700)
    store = state / 'wheel-store'
    store.mkdir()
    store.chmod(0o755)
    (store / 'unrelated.whl').write_bytes(b'not owned PR458 evidence')

    with pytest.raises(ValueError):
        pm_wheel_store.publish(
            pm_env.home,
            f'hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl',
            b'payload',
        )


def test_pr458_schema1_member_upgrades_without_deleting_flat_store(pm_env):
    old_wheel = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    assert pm._owned(member) == owner

    receipt = pm.ensure(
        pm_env.root, Path('/fixture/python'), home=pm_env.home,
        spec='/fixture/new.whl', version=VERSION,
    )

    assert receipt is not None
    upgraded = pm._owned(receipt.member)
    assert upgraded['schema'] == 2
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'
    assert stat.S_IMODE(store.lstat().st_mode) == 0o700
    assert pm.pm_wheel_store.validate(pm_env.home, upgraded['external_wheel'])
    assert store_wheel.is_file()
    assert store_wheel.read_bytes() == old_wheel
    receipt.finish()
    assert pm._owned(receipt.member) == upgraded
    assert pm.pm_wheel_store.validate(pm_env.home, upgraded['external_wheel'])
    assert pm.ensure(
        pm_env.root, Path('/fixture/python'), home=pm_env.home,
        spec=None, version=VERSION,
    ) is None
    assert pm._owned(receipt.member) == upgraded


def test_schema1_flat_upgrade_tighten_failure_preserves_old_member(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, owner, _store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    before = {path.name: path.read_bytes() for path in member.iterdir()}
    monkeypatch.setattr(
        pm.pm_wheel_store,
        'tighten_legacy_parent',
        lambda *_, **__: (_ for _ in ()).throw(ValueError('tighten refused')),
    )

    with pytest.raises(ValueError, match='tighten refused'):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert pm._owned(member) == owner
    assert {path.name: path.read_bytes() for path in member.iterdir()} == before
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))
    assert pm_env.state['enabled'] == ['other']


def test_schema1_flat_upgrade_publish_failure_restores_parent_mode(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, owner, _store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'
    before = {path.name: path.read_bytes() for path in member.iterdir()}
    assert stat.S_IMODE(store.lstat().st_mode) == 0o755
    monkeypatch.setattr(
        pm.pm_wheel_store,
        'publish',
        lambda *_, **__: (_ for _ in ()).throw(ValueError('publish refused')),
    )

    with pytest.raises(ValueError, match='publish refused'):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == 0o755
    assert pm._owned(member) == owner
    assert {path.name: path.read_bytes() for path in member.iterdir()} == before
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_schema1_flat_upgrade_refused_admission_restores_parent_mode(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, owner, _store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'
    original_call = pm_env.call

    def refuse(root, python, home, request):
        if request['operation'] == 'add':
            raise ValueError('resolver conflict')
        return original_call(root, python, home, request)

    monkeypatch.setattr(pm, '_call', refuse)
    with pytest.raises(ValueError, match='previous dependency source preserved'):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == 0o755
    assert pm._owned(member) == owner
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_schema1_flat_upgrade_unknown_admission_keeps_tightened_parent(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, _owner, _store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'
    original_call = pm_env.call

    def uncertain(root, python, home, request):
        response = original_call(root, python, home, request)
        if request['operation'] == 'add':
            raise ValueError('transport lost after commit')
        return response

    monkeypatch.setattr(pm, '_call', uncertain)
    with pytest.raises(ValueError, match='outcome uncertain'):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == 0o700
    assert pm._owned(member)['schema'] == 2


def test_schema1_flat_upgrade_store_replacement_before_tighten_refuses(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = store_wheel.parent
    old_store = store.with_name('known-old-store')
    replacement = b'unverified replacement wheel'
    original = pm.pm_wheel_store.tighten_legacy_parent

    def swap(home, **kwargs):
        store.rename(old_store)
        store.mkdir()
        store.chmod(0o755)
        (store / store_wheel.name).write_bytes(replacement)
        return original(home, **kwargs)

    monkeypatch.setattr(pm.pm_wheel_store, 'tighten_legacy_parent', swap)
    with pytest.raises(ValueError):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == 0o755
    assert (store / store_wheel.name).read_bytes() == replacement
    assert (old_store / store_wheel.name).read_bytes() == old_wheel
    assert json.loads((member / pm.OWNER).read_text()) == owner
    assert (member / store_wheel.name).read_bytes() == old_wheel
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_schema1_flat_upgrade_same_path_wrong_digest_refuses_without_chmod(
    pm_env, monkeypatch
):
    old_wheel = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = store_wheel.parent
    wrong = b'wrong flat wheel digest'
    original = pm.pm_wheel_store.tighten_legacy_parent

    def corrupt(home, **kwargs):
        store_wheel.write_bytes(wrong)
        return original(home, **kwargs)

    monkeypatch.setattr(pm.pm_wheel_store, 'tighten_legacy_parent', corrupt)
    with pytest.raises(ValueError):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == 0o755
    assert store_wheel.read_bytes() == wrong
    assert json.loads((member / pm.OWNER).read_text()) == owner
    assert (member / store_wheel.name).read_bytes() == old_wheel
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_schema1_flat_upgrade_store_replacement_after_tighten_refuses(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = store_wheel.parent
    old_store = store.with_name('known-old-store-after-tighten')
    replacement = b'unverified replacement after tighten'
    original = pm.pm_wheel_store.publish

    def swap(home, filename, payload, **kwargs):
        store.rename(old_store)
        store.mkdir()
        store.chmod(0o755)
        (store / store_wheel.name).write_bytes(replacement)
        return original(home, filename, payload, **kwargs)

    monkeypatch.setattr(pm.pm_wheel_store, 'publish', swap)
    with pytest.raises(ValueError):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == 0o755
    assert (store / store_wheel.name).read_bytes() == replacement
    assert (old_store / store_wheel.name).read_bytes() == old_wheel
    assert json.loads((member / pm.OWNER).read_text()) == owner
    assert (member / store_wheel.name).read_bytes() == old_wheel
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_legacy_parent_guard_does_not_tighten_non_flat_schema1(pm_env):
    old_wheel = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    workspace_wheel = pm_env.home / 'workspace.whl'
    workspace_wheel.write_bytes(old_wheel)
    project = (member / 'pyproject.toml').read_text().replace(
        store_wheel.as_posix(), workspace_wheel.as_posix()
    )
    (member / 'pyproject.toml').write_text(project)
    owner['files']['pyproject.toml'] = sha256(project.encode()).hexdigest()
    (member / pm.OWNER).write_text(json.dumps(owner))
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'

    assert pm._owned(member) == owner
    with pm._legacy_parent_guard(
        pm_env.home, member, owner, pm.pr458_flat_proof(member, owner)
    ):
        pass

    assert stat.S_IMODE(store.lstat().st_mode) == 0o755


@pytest.mark.parametrize('damage', ['symlink', 'world_writable'])
def test_tighten_legacy_parent_refuses_unsafe_store(tmp_path, damage):
    home = tmp_path / 'home'
    home.mkdir()
    state = home / '.hermes_feishu_card'
    state.mkdir(mode=0o700)
    store = state / 'wheel-store'
    if damage == 'symlink':
        outside = tmp_path / 'outside-store'
        outside.mkdir()
        _symlink_or_skip_windows_privilege(store, outside, directory=True)
    else:
        store.mkdir()
        store.chmod(0o777)

    with pytest.raises(ValueError):
        pm.pm_wheel_store.tighten_legacy_parent(
            home,
            expected_identity=(0, 0),
            wheel_name=f'hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl',
            digest='a' * 64,
            reprove=lambda: True,
        )


def test_schema2_registration_with_non_ascii_home_keeps_serialized_declaration(pm_env):
    old_home = pm_env.home
    new_home = old_home.parent / '中文-home'
    old_home.rename(new_home)
    pm_env.home = new_home
    pm_env.state['home'] = str(new_home)

    receipt = register(pm_env)

    assert receipt is not None
    assert pm._owned(receipt.member)['schema'] == 2
    declared = (receipt.member / 'pyproject.toml').read_text()
    assert '\\u4e2d\\u6587' in declared
    receipt.finish()


def test_pr458_schema1_owned_runtime_passes_rebind_runtime_verification(
    tmp_path, monkeypatch
):
    from hermes_feishu_card.install import legacy_rebind, plugin
    home = tmp_path / 'home'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': store_wheel.as_uri(), 'archive_info': {}},
    )
    probes = []
    monkeypatch.setattr(
        plugin,
        'probe_plugin_entrypoint',
        lambda target, *, expected_version: probes.append(expected_version)
        or SimpleNamespace(status='verified'),
    )
    binding = SimpleNamespace(hermes_home=home, runtime_python=python)

    legacy_rebind._verify_runtime(binding)

    assert pm._owned(member) == owner
    assert probes == [SCHEMA1_VERSION]


def test_historical_schema1_workspace_runtime_passes_rebind_verification(
    tmp_path, monkeypatch
):
    from hermes_feishu_card.install import legacy_rebind, plugin
    home = tmp_path / 'home'
    home.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    wheel_bytes = _schema1_wheel()
    workspace_wheel = workspace / f'hermes_feishu_streaming_card-{SCHEMA1_VERSION}-py3-none-any.whl'
    workspace_wheel.write_bytes(wheel_bytes)
    member = home / 'plugins' / pm.NAME
    member.mkdir(parents=True)
    member_wheel = member / workspace_wheel.name
    member_wheel.write_bytes(wheel_bytes)
    project = (
        '[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
        f'dependencies = ["hermes-feishu-streaming-card=={SCHEMA1_VERSION}"]\n'
        '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
        f'hermes-feishu-streaming-card = {{ path = "{workspace_wheel.as_posix()}" }}\n'
    )
    (member / 'pyproject.toml').write_text(project)
    owner = {
        'schema': 1,
        'version': SCHEMA1_VERSION,
        'spec_sha256': 'a' * 64,
        'selection_added': True,
        'files': {
            'pyproject.toml': sha256(project.encode()).hexdigest(),
            member_wheel.name: sha256(wheel_bytes).hexdigest(),
        },
    }
    (member / pm.OWNER).write_text(json.dumps(owner))
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': workspace_wheel.as_uri(), 'archive_info': {}},
    )
    probes = []
    monkeypatch.setattr(
        plugin,
        'probe_plugin_entrypoint',
        lambda target, *, expected_version: probes.append(expected_version)
        or SimpleNamespace(status='verified'),
    )
    binding = SimpleNamespace(hermes_home=home, runtime_python=python)

    legacy_rebind._verify_runtime(binding)

    assert pm._owned(member) == owner
    assert probes == [SCHEMA1_VERSION]


def test_pm_directory_reparse_attribute_refuses(pm_env, monkeypatch):
    from tests.unit.test_pm_wheel_store import _ReparseMetadata
    target = pm_env.home / 'plugins'
    target.mkdir()
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        metadata = original(path, *args, **kwargs)
        return _ReparseMetadata(metadata) if path == target else metadata
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(ValueError):
        register(pm_env)
    assert not list(target.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows PM member junction boundary')
@pytest.mark.parametrize('boundary', ['plugins', 'member'])
def test_actual_windows_pm_junction_never_writes_outside_home(pm_env, tmp_path, boundary):
    import subprocess
    outside = tmp_path / 'outside-member'
    outside.mkdir()
    sentinel = outside / 'sentinel.txt'
    sentinel.write_bytes(b'keep')
    plugins = pm_env.home / 'plugins'
    junction = plugins if boundary == 'plugins' else plugins / pm.NAME
    junction.parent.mkdir(parents=True, exist_ok=True)
    # pm_env substitutes the builder's subprocess call; use the standard
    # Windows API via Popen so junction setup is an actual OS operation.
    child = subprocess.Popen(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, stdout + stderr
    try:
        with pytest.raises(ValueError):
            register(pm_env)
        assert sentinel.read_bytes() == b'keep'
        assert set(outside.iterdir()) == {sentinel}
    finally:
        os.rmdir(junction)
