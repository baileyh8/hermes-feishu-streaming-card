"""Owned PM member transaction regressions; no production HOME or PM state."""
from hashlib import sha256
import json
import os
import py_compile
import stat
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install import legacy_9b904_transaction as adoption_io
from hermes_feishu_card.install import pm_dependency as pm


VERSION = cli.PACKAGE_VERSION
_POSIX_MODES = os.name != 'nt'


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


def test_unknown_observer_writer_refuses_before_pm_probe_or_source_publication(pm_env, monkeypatch):
    from hermes_feishu_card.install import legacy_observer, plugin_config_bytes

    monkeypatch.setattr(legacy_observer, 'available', lambda root: True)
    monkeypatch.setattr(
        plugin_config_bytes, 'verify_launch_sources',
        lambda root: (_ for _ in ()).throw(plugin_config_bytes.ConfigBytesRefused('unknown')),
    )
    before = dict(pm_env.state)
    with pytest.raises(ValueError, match='PM admission refused'):
        register(pm_env)
    assert pm_env.calls == []
    assert pm_env.state == before
    assert not (pm_env.home / 'plugins').exists()
    assert not (pm_env.home / '.hermes_feishu_card').exists()


def test_known_observer_writer_continues_existing_pm_admission(pm_env, monkeypatch):
    from hermes_feishu_card.install import legacy_observer, plugin_config_bytes

    monkeypatch.setattr(legacy_observer, 'available', lambda root: True)
    monkeypatch.setattr(plugin_config_bytes, 'verify_launch_sources', lambda root, **_: 'pm-0225')
    receipt = register(pm_env)
    assert receipt is not None
    assert [request['operation'] for request in pm_env.calls] == ['probe', 'add']


@pytest.mark.parametrize('failure', [PermissionError, OSError])
def test_uninspectable_observer_source_refuses_without_disclosing_error(pm_env, monkeypatch, failure):
    from hermes_feishu_card.install import legacy_observer

    monkeypatch.setattr(
        legacy_observer, 'available',
        lambda root: (_ for _ in ()).throw(failure('private sentinel')),
    )
    with pytest.raises(ValueError) as error:
        register(pm_env)
    assert str(error.value) == 'official config writer is unknown; PM admission refused'
    assert 'private sentinel' not in str(error.value)
    assert pm_env.calls == []
    assert not (pm_env.home / 'plugins').exists()


def test_missing_observer_source_retains_non_observer_path(pm_env):
    assert pm._verify_observer_writer_before_admission(pm_env.root) is None
    assert register(pm_env) is not None


def test_source_drift_after_probe_uses_original_family_and_refuses_before_add(pm_env, monkeypatch):
    from hermes_feishu_card.install import legacy_observer, plugin_config_bytes

    root = pm_env.root
    (root / 'hermes_cli').mkdir()
    sources = {
        'hermes_cli/main.py': b'original main\n',
        'hermes_cli/__init__.py': b'original init\n',
        'writer.py': b'original writer\n',
    }
    for name, content in sources.items():
        (root / name).write_bytes(content)
    digests = {name: sha256(content).hexdigest() for name, content in sources.items()}
    monkeypatch.setattr(plugin_config_bytes, 'FAMILIES', {'test-family': {'writer.py': digests['writer.py']}})
    monkeypatch.setattr(plugin_config_bytes, 'LAUNCH_SOURCES', {
        'test-family': {name: digests[name] for name in ('hermes_cli/main.py', 'hermes_cli/__init__.py')},
    })
    availability = [True]
    monkeypatch.setattr(legacy_observer, 'available', lambda root: availability[0])

    def drift(root, python, selected_home, request):
        result = pm_env.call(root, python, selected_home, request)
        if request['operation'] == 'probe':
            (root / 'hermes_cli/main.py').write_bytes(b'changed main\n')
            availability[0] = False
        return result

    monkeypatch.setattr(pm, '_call', drift)
    with pytest.raises(ValueError, match='PM admission refused'):
        register(pm_env)
    assert [request['operation'] for request in pm_env.calls] == ['probe']
    assert not (pm_env.home / 'plugins').exists()


def test_pm_child_checks_source_before_importing_upstream(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    sentinel = tmp_path / 'upstream-executed'
    (root / 'hermes_constants.py').write_text(
        f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("executed")\n'
    )
    result = subprocess.run(
        [sys.executable, '-I', '-c', pm._CHILD, str(root)],
        input=json.dumps({'operation': 'probe', 'source_digests': {'hermes_constants.py': '0' * 64}}),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert not sentinel.exists()


def _pm_child_source_fixture(tmp_path):
    root = tmp_path / 'source'
    home = tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    (root / 'pm').mkdir()
    (root / 'hermes_cli').mkdir()
    files = {
        'hermes_constants.py': f'from pathlib import Path\ndef get_hermes_home(): return Path({str(home)!r})\n',
        'pm/__init__.py': '',
        'pm/plugins_state.py': 'def read_home_selection(home): return {}\n',
        'pm/filesystem.py': 'def file_digest(path): return None\n',
        'pm/environments.py': 'def committed_venv(root): return root / "venv"\n',
        'hermes_cli/__init__.py': '',
        'hermes_cli/plugins_admission.py': (
            'def admit_plugin_set_change(enabled, disabled, *, active_plugins_dir, expected_config, plugin): pass\n'
        ),
    }
    for name, content in files.items():
        (root / name).write_text(content)
    return root, files


def test_guarded_pm_current_is_a_read_only_source_bound_probe(tmp_path):
    root, files = _pm_child_source_fixture(tmp_path)
    files['pm/install.py'] = 'def venv_is_current(*, project_root): return True\n'
    (root / 'pm/install.py').write_text(files['pm/install.py'])
    sources = {name: sha256(content.encode()).hexdigest() for name, content in files.items()}
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c', pm._CHILD, str(root)],
        input=json.dumps({'operation': 'current', 'source_digests': sources}),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)['current'] is True


@pytest.mark.parametrize('target,bound', [('hermes_constants.py', True), ('pm/filesystem.py', False)])
def test_pm_child_executes_source_not_poisoned_checkout_bytecode(tmp_path, target, bound):
    root, files = _pm_child_source_fixture(tmp_path)
    good = tmp_path / 'cache-good'
    evil = tmp_path / 'cache-evil'
    assert len(str(good)) == len(str(evil))
    good_code = f'from pathlib import Path\nPath({str(good)!r}).write_text("source")\n' + files[target]
    evil_code = good_code.replace(str(good), str(evil))
    path = root / target
    path.write_text(evil_code)
    previous = path.stat()
    py_compile.compile(str(path), doraise=True)
    path.write_text(good_code)
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert path.stat().st_size == previous.st_size
    files[target] = good_code
    sources = {name: sha256(content.encode()).hexdigest() for name, content in files.items()}
    if not bound:
        del sources[target]  # Older cohorts do not have a complete PM import contract.
    result = subprocess.run(
        [sys.executable, '-I', '-c', pm._CHILD, str(root)],
        input=json.dumps({'operation': 'probe', 'source_digests': sources}),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert good.read_text() == 'source'
    assert not evil.exists()


def test_pm_child_rejects_preloaded_pm_modules_before_upstream_import(tmp_path):
    root, files = _pm_child_source_fixture(tmp_path)
    sources = {name: sha256(content.encode()).hexdigest() for name, content in files.items()}
    script = 'import types, sys; sys.modules["pm"] = types.ModuleType("pm")\n' + pm._CHILD
    result = subprocess.run(
        [sys.executable, '-I', '-c', script, str(root)],
        input=json.dumps({'operation': 'probe', 'source_digests': sources}),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert 'module was loaded before source verification' in result.stderr


def test_pm_child_refuses_source_drift_before_import_execution(tmp_path):
    root, files = _pm_child_source_fixture(tmp_path)
    sentinel = tmp_path / 'unverified-executed'
    target = root / 'pm/plugins_state.py'
    injected = f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("executed")\n'
    files['pm/__init__.py'] = (
        f'from pathlib import Path\nPath({str(target)!r}).write_text({injected!r})\n'
    )
    (root / 'pm/__init__.py').write_text(files['pm/__init__.py'])
    sources = {name: sha256(content.encode()).hexdigest() for name, content in files.items()}
    result = subprocess.run(
        [sys.executable, '-I', '-c', pm._CHILD, str(root)],
        input=json.dumps({'operation': 'probe', 'source_digests': sources}),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert not sentinel.exists()


def test_pm_child_refuses_config_drift_between_selection_and_digest(tmp_path):
    root, files = _pm_child_source_fixture(tmp_path)
    config = tmp_path / 'home/config.yaml'
    config.write_text('plugins: {enabled: [], disabled: []}\n')
    sentinel = tmp_path / 'admission-executed'
    files['pm/plugins_state.py'] = (
        'def read_home_selection(home):\n'
        '    (home / "config.yaml").write_text("plugins: {enabled: [changed], disabled: []}\\n")\n'
        '    return {"plugins": {"enabled": [], "disabled": []}}\n'
    )
    files['hermes_cli/plugins_admission.py'] = (
        'from pathlib import Path\n'
        'def admit_plugin_set_change(enabled, disabled, *, active_plugins_dir, expected_config, plugin):\n'
        f'    Path({str(sentinel)!r}).write_text("executed")\n'
    )
    for name in ('pm/plugins_state.py', 'hermes_cli/plugins_admission.py'):
        (root / name).write_text(files[name])
    sources = {name: sha256(content.encode()).hexdigest() for name, content in files.items()}
    result = subprocess.run(
        [sys.executable, '-I', '-c', pm._CHILD, str(root)],
        input=json.dumps({
            'operation': 'add', 'name': pm.NAME,
            'expected': sha256(b'plugins: {enabled: [changed], disabled: []}\n').hexdigest(),
            'source_digests': sources,
        }),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert not sentinel.exists()


def test_pm_child_post_admission_source_drift_has_uncertain_outcome(tmp_path):
    root, files = _pm_child_source_fixture(tmp_path)
    admission = root / 'hermes_cli/plugins_admission.py'
    committed = tmp_path / 'admission-committed'
    files['hermes_cli/plugins_admission.py'] = (
        'from pathlib import Path\n'
        'def admit_plugin_set_change(enabled, disabled, *, active_plugins_dir, expected_config, plugin):\n'
        f'    Path({str(committed)!r}).write_text("committed")\n'
        f'    Path({str(admission)!r}).write_text("changed")\n'
    )
    admission.write_text(files['hermes_cli/plugins_admission.py'])
    sources = {name: sha256(content.encode()).hexdigest() for name, content in files.items()}
    with pytest.raises(pm.PMAdmissionOutcomeUncertain, match='outcome uncertain'):
        pm._call(root, Path(sys.executable), tmp_path / 'home', {
            'operation': 'add', 'name': pm.NAME, 'expected': 'missing', 'source_digests': sources,
        })
    assert committed.read_text() == 'committed'
    assert admission.read_text() == 'changed'


def test_guarded_pm_child_failure_is_uncertain_without_exposing_output(tmp_path, monkeypatch):
    monkeypatch.setattr(pm.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout='private sentinel', stderr='private sentinel'
    ))
    with pytest.raises(pm.PMAdmissionOutcomeUncertain) as error:
        pm._call(tmp_path, Path(sys.executable), tmp_path, {
            'operation': 'add', 'source_digests': {'writer.py': '0' * 64},
        })
    assert 'outcome uncertain' in str(error.value)
    assert 'private sentinel' not in str(error.value)


def test_guarded_uncertain_admission_retains_published_member_and_backup(pm_env, monkeypatch):
    from hermes_feishu_card.install import plugin_config_bytes

    old_receipt = register(pm_env)
    old_receipt.finish()
    member = pm_env.home / 'plugins' / pm.NAME
    old_owner = pm._owned(member)
    monkeypatch.setattr(pm, '_verify_observer_writer_before_admission', lambda *args, **kwargs: 'pm-0225')
    monkeypatch.setattr(plugin_config_bytes, 'FAMILIES', {'pm-0225': {'writer.py': '0' * 64}})
    monkeypatch.setattr(plugin_config_bytes, 'LAUNCH_SOURCES', {'pm-0225': {}})
    observed = []
    def uncertain(root, python, home, request):
        observed.append(request['operation'])
        if request['operation'] == 'add':
            raise pm.PMAdmissionOutcomeUncertain('PM admission outcome uncertain')
        return pm_env.call(root, python, home, request)
    monkeypatch.setattr(pm, '_call', uncertain)
    with pytest.raises(pm.PMAdmissionOutcomeUncertain):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/different.whl', version=VERSION)
    backups = list(member.parent.glob('.hfc-runtime-*.previous'))
    assert observed == ['probe', 'add']
    assert pm._owned(member) != old_owner
    assert len(backups) == 1
    assert pm._owned(backups[0]) == old_owner


def _synthetic_adoption_environment(env, monkeypatch):
    from hermes_feishu_card.install import legacy_9b904, pm_wheel_store, posix_transaction as io
    if not io._cli_dirfd_binding_supported() or os.rmdir not in os.supports_dir_fd:
        pytest.skip('secure directory-relative cleanup is unavailable')
    receipt = register(env)
    receipt.finish()
    member = env.home / 'plugins' / pm.NAME
    owner = pm._owned(member)
    evidence = SimpleNamespace(
        old_owner=owner,
        old_member_identity=io.read_directory_identity(member, ValueError, 'changed'),
        plugin_identity=io.read_directory_identity(member.parent, ValueError, 'changed'),
        sdk_mode='absent',
    )
    payload = b'fixture SDK wheel'
    monkeypatch.setattr(pm_wheel_store, '_SDK_WHEEL_SHA256', sha256(payload).hexdigest())
    sdk = pm_wheel_store.publish_sdk(env.home, payload)
    monkeypatch.setattr(legacy_9b904, 'prepare_sdk_wheel', lambda _home: sdk)
    monkeypatch.setattr(legacy_9b904, 'capture_admission', lambda *_args: evidence)
    monkeypatch.delenv('HERMES_MANAGED_DIR', raising=False)
    monkeypatch.setattr(legacy_9b904, '_MANAGED_SCOPE', env.home / 'missing-managed-scope')
    monkeypatch.setattr(pm, '_verify_observer_writer_before_admission',
                        lambda *_args, **_kwargs: legacy_9b904.ADAPTER_ID)
    monkeypatch.setattr(pm, '_ADOPTION_ENABLED', True)
    env.state['generation'] = '/fixture/original-generation'
    env.calls.clear()
    return legacy_9b904, member, owner, evidence


def test_managed_override_refuses_before_pm_probe(pm_env, monkeypatch):
    _legacy, member, owner, _evidence = _synthetic_adoption_environment(pm_env, monkeypatch)
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(pm_env.home / 'untrusted-policy'))
    with pytest.raises(ValueError, match='managed configuration is unsupported'):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/different.whl', version=VERSION)
    assert pm_env.calls == []
    assert pm._owned(member) == owner


def test_adoption_pre_pm_refusal_restores_exact_previous_member(pm_env, monkeypatch):
    legacy, member, owner, evidence = _synthetic_adoption_environment(pm_env, monkeypatch)
    def refuse_after_publish(_root, _home, _evidence, **kwargs):
        backup = kwargs.get('backup')
        if backup is not None and backup.name.endswith('.previous'):
            raise legacy.LegacyAdoptionRefused('SDK changed before admission')
    monkeypatch.setattr(legacy, 'verify_before', refuse_after_publish)
    with pytest.raises(ValueError, match='previous dependency source restored'):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/different.whl', version=VERSION)
    assert pm._owned(member) == owner
    assert (member.stat().st_dev, member.stat().st_ino) == evidence.old_member_identity
    assert [call['operation'] for call in pm_env.calls] == ['probe', 'probe']
    assert not list(member.parent.glob('.hfc-runtime-*'))


def test_unpreservable_sdk_explains_pre_pm_refusal(pm_env, monkeypatch):
    legacy, member, owner, _evidence = _synthetic_adoption_environment(pm_env, monkeypatch)
    def cannot_preserve(*_args):
        raise legacy.LegacySDKUnpreservable('unpreservable SDK')
    monkeypatch.setattr(legacy, 'capture_admission', cannot_preserve)
    with pytest.raises(ValueError, match='existing SDK was retained and PM admission was not started'):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/different.whl', version=VERSION)
    assert [call['operation'] for call in pm_env.calls] == ['probe']
    assert pm._owned(member) == owner


def test_adoption_staging_does_not_claim_a_rewritten_valid_owner(pm_env, monkeypatch):
    legacy, member, owner, _evidence = _synthetic_adoption_environment(pm_env, monkeypatch)
    monkeypatch.setattr(legacy, 'verify_before', lambda *_args, **_kwargs: None)
    def alter_before_publish(_member, staging, _old, _parent_identity, _old_identity):
        current = json.loads((staging / pm.OWNER).read_text())
        current['spec_sha256'] = '1' * 64
        (staging / pm.OWNER).write_text(json.dumps(current))
        raise ValueError('staging changed')
    monkeypatch.setattr(pm, '_publish_member', alter_before_publish)
    with pytest.raises(pm.PMAdmissionOutcomeUncertain, match='staging retained'):
        pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                  spec='/fixture/different.whl', version=VERSION)
    staging, = member.parent.glob('.hfc-runtime-*')
    assert pm._owned(staging)['spec_sha256'] == '1' * 64
    assert pm._owned(member) == owner
    assert [call['operation'] for call in pm_env.calls] == ['probe']


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


@pytest.mark.parametrize('race', ['member', 'parent'])
def test_adoption_cleanup_refuses_replaced_member_or_parent(pm_env, monkeypatch, race):
    from hermes_feishu_card.install import posix_transaction as io
    if not io._cli_dirfd_binding_supported() or os.rmdir not in os.supports_dir_fd:
        pytest.skip('secure directory-relative cleanup is unavailable')
    register(pm_env)
    replacement = pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                            spec='/fixture/second.whl', version=VERSION)
    backup = replacement.backup
    assert backup is not None
    owner = pm._owned(backup)
    identity = io.read_directory_identity(backup, ValueError, 'changed')
    parent_identity = io.read_directory_identity(backup.parent, ValueError, 'changed')
    unknown = pm_env.home / 'unknown'
    unknown.mkdir()
    (unknown / 'user-data').write_text('must remain')
    if race == 'member':
        remove = io._remove_verified_restore_evidence
        def swap_then_remove(paths, identities, directories):
            backup.rename(pm_env.home / 'saved-backup')
            unknown.rename(backup)
            return remove(paths, identities, directories)
        monkeypatch.setattr(io, '_remove_verified_restore_evidence', swap_then_remove)
        expected = backup / 'user-data'
    else:
        bind = io.bind_target
        def swap_after_bind(*args, **kwargs):
            binding = bind(*args, **kwargs)
            backup.parent.rename(pm_env.home / 'saved-plugins')
            unknown.rename(backup.parent)
            return binding
        monkeypatch.setattr(io, 'bind_target', swap_after_bind)
        expected = backup.parent / 'user-data'
    with pytest.raises(pm.PMAdmissionOutcomeUncertain):
        adoption_io.remove_member(pm_env.home, backup, identity, parent_identity, owner)
    assert expected.read_text() == 'must remain'
    assert (pm_env.home / ('saved-backup' if race == 'member' else 'saved-plugins')).is_dir()


def test_adoption_cleanup_removes_only_verified_member(pm_env):
    from hermes_feishu_card.install import posix_transaction as io
    if not io._cli_dirfd_binding_supported() or os.rmdir not in os.supports_dir_fd:
        pytest.skip('secure directory-relative cleanup is unavailable')
    register(pm_env)
    replacement = pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                            spec='/fixture/second.whl', version=VERSION)
    backup = replacement.backup
    assert backup is not None
    identity = io.read_directory_identity(backup, ValueError, 'changed')
    parent_identity = io.read_directory_identity(backup.parent, ValueError, 'changed')
    owner = pm._owned(backup)
    adoption_io.remove_member(pm_env.home, backup, identity, parent_identity, owner)
    assert not backup.exists()
    assert pm._owned(replacement.member) == replacement.owner


def test_adoption_cleanup_refuses_file_changed_after_owner_check(pm_env, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    if not io._cli_dirfd_binding_supported() or os.rmdir not in os.supports_dir_fd:
        pytest.skip('secure directory-relative cleanup is unavailable')
    register(pm_env)
    replacement = pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home,
                            spec='/fixture/second.whl', version=VERSION)
    backup = replacement.backup
    assert backup is not None
    owner = pm._owned(backup)
    identity = io.read_directory_identity(backup, ValueError, 'changed')
    parent_identity = io.read_directory_identity(backup.parent, ValueError, 'changed')
    owned = pm._owned
    def modify_after_check(path):
        result = owned(path)
        if path == backup:
            (path / 'pyproject.toml').write_text('new user data')
        return result
    monkeypatch.setattr(pm, '_owned', modify_after_check)
    with pytest.raises(pm.PMAdmissionOutcomeUncertain):
        adoption_io.remove_member(pm_env.home, backup, identity, parent_identity, owner)
    assert (backup / 'pyproject.toml').read_text() == 'new user data'
    assert (backup / pm.OWNER).exists()


def test_adoption_move_reports_source_swap_without_deleting_user_data(pm_env, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    if not io._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative moves are unavailable')
    receipt = register(pm_env)
    source = receipt.member
    destination = source.with_name(source.name + '.failed')
    parent_identity = io.read_directory_identity(source.parent, ValueError, 'changed')
    unknown = pm_env.home / 'unknown'
    unknown.mkdir()
    (unknown / 'user-data').write_text('must remain')
    rename = os.rename
    def swap_before_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        rename(source, pm_env.home / 'saved-member')
        rename(unknown, source)
        return rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    monkeypatch.setattr(adoption_io.os, 'rename', swap_before_rename)
    with pytest.raises(pm.PMAdmissionOutcomeUncertain, match='outcome is uncertain'):
        adoption_io.move_member(source, destination, receipt.identity, parent_identity, receipt.owner)
    assert (destination / 'user-data').read_text() == 'must remain'
    assert (pm_env.home / 'saved-member').is_dir()


def test_schema3_owns_exact_sdk_wheel_without_changing_schema2(pm_env, monkeypatch):
    from hermes_feishu_card.install import pm_wheel_store

    receipt = register(pm_env)
    receipt.finish()
    member = pm_env.home / 'plugins' / pm.NAME
    old = pm._owned(member)
    assert old['schema'] == 2
    payload = b'fixture SDK wheel'
    monkeypatch.setattr(pm_wheel_store, '_SDK_WHEEL_SHA256', sha256(payload).hexdigest())
    sdk = pm_wheel_store.publish_sdk(pm_env.home, payload)
    project = pm._sdk_project_source(old['version'], old['external_wheel'], sdk)
    (member / 'pyproject.toml').write_text(project)
    owner = {**old, 'schema': 3, 'sdk_wheel': sdk,
             'files': {**old['files'], 'pyproject.toml': sha256(project.encode()).hexdigest()}}
    (member / pm.OWNER).write_text(json.dumps(owner))
    assert pm._owned(member) == owner
    calls_before = len(pm_env.calls)
    with pytest.raises(ValueError, match='PM removal is not yet supported'):
        pm.remove(pm_env.root, Path('/fixture/python'), home=pm_env.home)
    assert [call['operation'] for call in pm_env.calls[calls_before:]] == ['probe']
    assert pm._owned(member) == owner
    Path(sdk['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='ownership cannot be verified'):
        pm._owned(member)


def test_same_schema3_receipt_is_revalidated_without_new_pm_admission(pm_env, monkeypatch):
    from hermes_feishu_card.install import legacy_9b904, legacy_observer, plugin, pm_wheel_store

    receipt = register(pm_env)
    receipt.finish()
    member = pm_env.home / 'plugins' / pm.NAME
    old = pm._owned(member)
    payload = b'fixture SDK wheel'
    monkeypatch.setattr(pm_wheel_store, '_SDK_WHEEL_SHA256', sha256(payload).hexdigest())
    sdk = pm_wheel_store.publish_sdk(pm_env.home, payload)
    project = pm._sdk_project_source(old['version'], old['external_wheel'], sdk)
    (member / 'pyproject.toml').write_text(project)
    owner = {**old, 'schema': 3, 'sdk_wheel': sdk,
             'files': {**old['files'], 'pyproject.toml': sha256(project.encode()).hexdigest()}}
    (member / pm.OWNER).write_text(json.dumps(owner))
    evidence = object()
    monkeypatch.setattr(legacy_9b904, 'capture_admission', lambda *_args: evidence)
    monkeypatch.setattr(pm, '_verify_observer_writer_before_admission',
                        lambda *_args, **_kwargs: legacy_9b904.ADAPTER_ID)
    monkeypatch.setattr(pm, '_ADOPTION_ENABLED', True)
    def complete(registration, proof):
        assert proof is evidence
        registration.adoption = proof
        return registration
    monkeypatch.setattr(pm, '_complete_adoption', complete)
    pm_env.calls.clear()
    repeat = pm.ensure(pm_env.root, Path('/fixture/python'), home=pm_env.home, spec=None, version=VERSION)
    assert repeat.repeat is True
    assert repeat.source_family == legacy_9b904.ADAPTER_ID
    assert [call['operation'] for call in pm_env.calls] == ['probe']
    monkeypatch.setattr(plugin, 'resolve_runtime_binding', lambda **_kwargs: object())
    monkeypatch.setattr(pm.Registration, 'verify_adoption', lambda *_args: None)
    monkeypatch.setattr(legacy_observer, 'inspect', lambda *_args, **_kwargs: ('verified',))
    repeat.rollback()
    assert [call['operation'] for call in pm_env.calls] == ['probe']
    assert pm._owned(member) == owner


@pytest.mark.parametrize('sdk_status,expected', [('ok', 0), ('failed', 1)])
def test_schema3_install_never_runs_pip_sdk_fallback(tmp_path, monkeypatch, sdk_status, expected):
    detection = SimpleNamespace(supported=True, root=tmp_path)
    registration = SimpleNamespace(adoption=object(), python=Path('/fixture/python'))
    monkeypatch.setattr(cli, 'detect_hermes', lambda _root: detection)
    monkeypatch.setattr(cli, '_read_manifest', lambda _path: None)
    monkeypatch.setattr(cli, '_ensure_hermes_runtime_package', lambda *_args, **_kwargs: registration)
    monkeypatch.setattr(cli, '_hermes_requires_feishu_sdk_capability', lambda _root: True)
    monkeypatch.setattr(cli, '_check_runtime_feishu_sdk', lambda _python: {'status': sdk_status})
    monkeypatch.setattr(cli, '_ensure_hermes_feishu_sdk',
                        lambda *_args, **_kwargs: pytest.fail('pip SDK fallback must not run'))
    monkeypatch.setattr(cli, '_run_fixed_tag_v3_install', lambda *_args: 0)
    registrations = []
    assert cli._run_install_core(SimpleNamespace(hermes_dir=tmp_path), registrations) == expected
    assert registrations == [registration]


def test_schema3_uninstall_refuses_before_source_or_config_restore(tmp_path, monkeypatch):
    from hermes_feishu_card.install import legacy_9b904_uninstall, legacy_observer
    monkeypatch.setattr(legacy_observer, '_read', lambda _path: b'v2 marker')
    monkeypatch.setattr(legacy_observer, '_decode', lambda _raw: {'protocol': legacy_observer.ADOPTION_PROTOCOL})
    monkeypatch.setattr(cli, '_run_fixed_tag_v3_restore',
                        lambda *_args, **_kwargs: pytest.fail('source restore must not run'))
    monkeypatch.setattr(cli, '_restore', lambda *_args, **_kwargs: pytest.fail('config restore must not run'))
    def refuse(*_args, **_kwargs):
        raise ValueError('unverified HOME')
    monkeypatch.setattr(legacy_9b904_uninstall, 'uninstall', refuse)
    assert cli._run_uninstall(SimpleNamespace(hermes_dir=tmp_path)) == 1


def test_schema3_marker_appearing_after_precheck_refuses_uninstall(tmp_path, monkeypatch):
    from hermes_feishu_card.install import legacy_observer
    monkeypatch.setattr(legacy_observer, '_read', lambda _path: None)
    monkeypatch.setattr(legacy_observer, 'inspect', lambda *_args, **_kwargs: (
        {'protocol': legacy_observer.ADOPTION_PROTOCOL}, b'marker', object(), object(), b'config'))
    monkeypatch.setattr(cli, '_run_fixed_tag_v3_restore', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, '_restore_legacy_sources', lambda *_args: pytest.fail('source restore must not run'))
    assert cli._run_uninstall(SimpleNamespace(hermes_dir=tmp_path)) == 1


def test_restore_does_not_remove_marker_created_after_initial_absence(tmp_path, monkeypatch):
    from hermes_feishu_card.install import legacy_observer
    monkeypatch.setattr(legacy_observer, 'inspect', lambda *_args, **_kwargs: None)
    def restore_sources(root):
        (root / legacy_observer.MARKER).write_bytes(b'new observer')
    monkeypatch.setattr(cli, '_restore_legacy_sources', restore_sources)
    monkeypatch.setattr(legacy_observer, 'restore', lambda *_args, **_kwargs: pytest.fail('new marker removed'))
    cli._restore(tmp_path)
    assert (tmp_path / legacy_observer.MARKER).read_bytes() == b'new observer'


def test_uninstall_source_recovery_rechecks_v2_under_root_lock(tmp_path, monkeypatch):
    from hermes_feishu_card.install import legacy_observer, recovery
    if not recovery._secure_dirfd_transactions_supported():
        pytest.skip('secure recovery is unavailable')
    (tmp_path / legacy_observer.MARKER).write_bytes(b'late v2')
    monkeypatch.setattr(legacy_observer, '_decode', lambda _raw: {'protocol': legacy_observer.ADOPTION_PROTOCOL})
    monkeypatch.setattr(recovery.decomposed_ownership, '_inspect',
                        lambda *_args, **_kwargs: pytest.fail('source must not be inspected or changed'))
    with pytest.raises(ValueError, match='coordinated PM transaction'):
        recovery.restore_decomposed(SimpleNamespace(root=tmp_path), refuse_adoption=True)


def test_adoption_marker_publication_waits_for_existing_root_lock(tmp_path, monkeypatch):
    import threading
    from hermes_feishu_card.install import legacy_observer, recovery
    attempted, entered = threading.Event(), threading.Event()
    monkeypatch.setattr(legacy_observer, '_prepare', lambda *_args, **_kwargs: entered.set())
    def publish():
        attempted.set()
        legacy_observer.prepare(SimpleNamespace(checkout_root=tmp_path), adoption=object())
    with recovery._root_lock(tmp_path):
        thread = threading.Thread(target=publish)
        thread.start()
        assert attempted.wait(2)
        assert not entered.wait(0.1)
    thread.join(2)
    assert not thread.is_alive()
    assert entered.is_set()


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
    source = tomllib.loads((receipt.member / 'pyproject.toml').read_text(encoding='utf-8'))['tool']['uv']['sources']['hermes-feishu-streaming-card']
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


def test_wheel_provenance_probe_does_not_import_package_before_verification(tmp_path):
    from hermes_feishu_card.install import pm_wheel_store
    home = tmp_path / 'home'
    home.mkdir()
    reference = pm_wheel_store.publish(home, f'hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl', b'wheel')
    installed = tmp_path / 'installed'
    package = installed / 'hermes_feishu_card'
    package.mkdir(parents=True)
    sentinel = tmp_path / 'package-executed'
    init = f'open({str(sentinel)!r}, "w").write("executed")\n'.encode()
    (package / '__init__.py').write_bytes(init)
    dist = installed / f'hermes_feishu_streaming_card-{VERSION}.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text(f'Name: hermes-feishu-streaming-card\nVersion: {VERSION}\n')
    (dist / 'direct_url.json').write_text(json.dumps({'url': Path(reference['path']).as_uri(), 'archive_info': {}}))
    request = {'external_wheel': reference, 'wheel_sha256': reference['sha256'], 'wheel_version': VERSION,
               'files': {'hermes_feishu_card/__init__.py': sha256(init).hexdigest()}}
    child = 'import sys;sys.path.insert(0,' + repr(str(installed)) + ');\n' + pm._VERIFY_CHILD
    result = subprocess.run([sys.executable, '-I', '-B', '-c', child], input=json.dumps(request),
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok'] is True
    assert not sentinel.exists()


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
    project_bytes = project.encode('utf-8')
    (member / 'pyproject.toml').write_bytes(project_bytes)
    owner = {
        'schema': 1,
        'version': version,
        'spec_sha256': 'a' * 64,
        'selection_added': True,
        'files': {
            'pyproject.toml': sha256(project_bytes).hexdigest(),
            wheel_name: sha256(wheel_bytes).hexdigest(),
        },
    }
    (member / pm.OWNER).write_bytes(json.dumps(owner).encode('utf-8'))
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
    assert (member / 'pyproject.toml').read_text(encoding='utf-8') == (
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
    if _POSIX_MODES:
        assert stat.S_IMODE(store_wheel.parent.lstat().st_mode) == 0o755


def test_pr458_schema1_flat_store_with_non_ascii_home_is_verified(tmp_path):
    home = tmp_path / '中文-Hermes'
    home.mkdir()
    wheel_bytes = _schema1_wheel()
    member, owner, store_wheel = _schema1_member(home, wheel_bytes=wheel_bytes)
    declared = (member / 'pyproject.toml').read_text(encoding='utf-8')

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
    project = (member / 'pyproject.toml').read_text(encoding='utf-8').replace(
        store_wheel.as_posix(), other.as_posix()
    )
    project_bytes = project.encode('utf-8')
    (member / 'pyproject.toml').write_bytes(project_bytes)
    owner['files']['pyproject.toml'] = sha256(project_bytes).hexdigest()
    (member / pm.OWNER).write_bytes(json.dumps(owner).encode('utf-8'))

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
    'missing', 'wrong_digest', 'file_symlink',
    pytest.param('file_world_writable', marks=pytest.mark.skipif(
        not _POSIX_MODES, reason='Windows ACL does not map chmod mode bits'
    )),
    pytest.param('store_world_writable', marks=pytest.mark.skipif(
        not _POSIX_MODES, reason='Windows ACL does not map chmod mode bits'
    )),
    pytest.param('state_not_private', marks=pytest.mark.skipif(
        not _POSIX_MODES, reason='Windows ACL does not map chmod mode bits'
    )),
    'store_symlink',
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
    project_bytes = project.encode('utf-8')
    (member / 'pyproject.toml').write_bytes(project_bytes)
    owner = {
        'schema': 1,
        'version': SCHEMA1_VERSION,
        'spec_sha256': 'a' * 64,
        'selection_added': True,
        'files': {
            'pyproject.toml': sha256(project_bytes).hexdigest(),
            member_wheel.name: sha256(wheel_bytes).hexdigest(),
        },
    }
    (member / pm.OWNER).write_bytes(json.dumps(owner).encode('utf-8'))
    python, _site = _installed_runtime(
        tmp_path, wheel_bytes, SCHEMA1_VERSION,
        {'url': workspace_wheel.as_uri(), 'archive_info': {}},
    )

    assert pm._owned(member) == owner
    with pytest.raises(ValueError):
        pm.legacy_external_wheel(member, owner)
    assert pm.verify_installed(member, python) is True


@pytest.mark.skipif(not _POSIX_MODES, reason='POSIX mode permission policy')
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


@pytest.mark.skipif(not _POSIX_MODES, reason='POSIX mode permission policy')
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
    if _POSIX_MODES:
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
    before_mode = stat.S_IMODE(store.lstat().st_mode)
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

    assert stat.S_IMODE(store.lstat().st_mode) == before_mode
    assert pm._owned(member) == owner
    assert {path.name: path.read_bytes() for path in member.iterdir()} == before
    assert not list((pm_env.home / 'plugins').glob('.hfc-runtime-*'))


def test_schema1_flat_upgrade_refused_admission_restores_parent_mode(pm_env, monkeypatch):
    old_wheel = _schema1_wheel()
    member, owner, _store_wheel = _schema1_member(
        pm_env.home, SCHEMA1_VERSION, old_wheel
    )
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'
    before_mode = stat.S_IMODE(store.lstat().st_mode)
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

    assert stat.S_IMODE(store.lstat().st_mode) == before_mode
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

    if _POSIX_MODES:
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
    replacement_modes = []

    def swap(home, **kwargs):
        store.rename(old_store)
        store.mkdir()
        store.chmod(0o755)
        (store / store_wheel.name).write_bytes(replacement)
        replacement_modes.append(stat.S_IMODE(store.lstat().st_mode))
        return original(home, **kwargs)

    monkeypatch.setattr(pm.pm_wheel_store, 'tighten_legacy_parent', swap)
    with pytest.raises(ValueError):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == replacement_modes[0]
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
    before_mode = stat.S_IMODE(store.lstat().st_mode)
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

    assert stat.S_IMODE(store.lstat().st_mode) == before_mode
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
    replacement_modes = []

    def swap(home, filename, payload, **kwargs):
        store.rename(old_store)
        store.mkdir()
        store.chmod(0o755)
        (store / store_wheel.name).write_bytes(replacement)
        replacement_modes.append(stat.S_IMODE(store.lstat().st_mode))
        return original(home, filename, payload, **kwargs)

    monkeypatch.setattr(pm.pm_wheel_store, 'publish', swap)
    with pytest.raises(ValueError):
        pm.ensure(
            pm_env.root, Path('/fixture/python'), home=pm_env.home,
            spec='/fixture/new.whl', version=VERSION,
        )

    assert stat.S_IMODE(store.lstat().st_mode) == replacement_modes[0]
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
    project = (member / 'pyproject.toml').read_text(encoding='utf-8').replace(
        store_wheel.as_posix(), workspace_wheel.as_posix()
    )
    project_bytes = project.encode('utf-8')
    (member / 'pyproject.toml').write_bytes(project_bytes)
    owner['files']['pyproject.toml'] = sha256(project_bytes).hexdigest()
    (member / pm.OWNER).write_bytes(json.dumps(owner).encode('utf-8'))
    store = pm_env.home / '.hermes_feishu_card' / 'wheel-store'
    before_mode = stat.S_IMODE(store.lstat().st_mode)

    assert pm._owned(member) == owner
    with pm._legacy_parent_guard(
        pm_env.home, member, owner, pm.pr458_flat_proof(member, owner)
    ):
        pass

    assert stat.S_IMODE(store.lstat().st_mode) == before_mode


@pytest.mark.parametrize('damage', [
    'symlink',
    pytest.param('world_writable', marks=pytest.mark.skipif(
        not _POSIX_MODES, reason='Windows ACL does not map chmod mode bits'
    )),
])
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
    declared = (receipt.member / 'pyproject.toml').read_text(encoding='utf-8')
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
    project_bytes = project.encode('utf-8')
    (member / 'pyproject.toml').write_bytes(project_bytes)
    owner = {
        'schema': 1,
        'version': SCHEMA1_VERSION,
        'spec_sha256': 'a' * 64,
        'selection_added': True,
        'files': {
            'pyproject.toml': sha256(project_bytes).hexdigest(),
            member_wheel.name: sha256(wheel_bytes).hexdigest(),
        },
    }
    (member / pm.OWNER).write_bytes(json.dumps(owner).encode('utf-8'))
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
