"""Owned PM member transaction regressions; no production HOME or PM state."""
from hashlib import sha256
import json
import os
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
    first = register(pm_env)
    first.finish()
    owner_path = first.member / pm.OWNER
    owner = json.loads(owner_path.read_text())
    owner.pop('external_wheel')
    owner['schema'] = 1
    owner_path.write_text(json.dumps(owner))
    second = register(pm_env)
    assert second is not None
    assert pm._owned(second.member)['schema'] == 2
    second.rollback()
    assert pm._owned(second.member)['schema'] == 1


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
               'files': {'hermes_feishu_card/hook_runtime.py': sha256(b'# installed bytes\n').hexdigest()}}
    child = 'import sys;sys.path.insert(0,' + repr(str(installed)) + ');\n' + pm._VERIFY_CHILD
    result = subprocess.run([sys.executable, '-I', '-c', child], input=json.dumps(request),
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok'] is (source_kind == 'missing_hash')


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
