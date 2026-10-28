"""Evidence-only tests for the first 9b904 adoption cohort."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
from types import SimpleNamespace
import venv
import zipfile

import pytest

from hermes_feishu_card.install import legacy_9b904, legacy_observer, plugin, pm_dependency, pm_wheel_store, posix_transaction


BASE = (
    b'# user comment\nplugins:\n'
    b'  enabled: [hermes-feishu-card, hfc-runtime-dependency]\n'
    b'  disabled: []\n'
    b'  entries:\n'
    b'    hermes-feishu-card:\n'
    b'      allow_tool_override: false\n'
    b'other: keep\n_config_version: 52\n'
)


def test_managed_scope_missing_override_and_uninspectable_are_distinct(tmp_path, monkeypatch):
    managed = tmp_path / 'managed'
    monkeypatch.setattr(legacy_9b904, '_MANAGED_SCOPE', managed)
    monkeypatch.delenv('HERMES_MANAGED_DIR', raising=False)
    legacy_9b904.verify_unmanaged_scope()
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(tmp_path / 'not-yet-created'))
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='managed configuration is unsupported'):
        legacy_9b904.verify_unmanaged_scope()
    monkeypatch.delenv('HERMES_MANAGED_DIR')
    managed.mkdir()
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='managed configuration is unsupported'):
        legacy_9b904.verify_unmanaged_scope()
    class Uninspectable:
        def lstat(self):
            raise PermissionError('private path')
    monkeypatch.setattr(legacy_9b904, '_MANAGED_SCOPE', Uninspectable())
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='managed configuration is uninspectable'):
        legacy_9b904.verify_unmanaged_scope()


@pytest.mark.parametrize('location', ['home-env', 'home-op-env', 'project-env'])
@pytest.mark.parametrize('content', [
    b'HERMES_MANAGED_DIR=/policy\n',
    b"export 'HERMES_MANAGED_DIR'=${POLICY:-/policy}\n",
    b'HERMES_MANAGED_DIR=\n',
    b'HERMES_MANAGED_\x00DIR=/policy\n',
    b'\xff\xfeH\x00E\x00R\x00M\x00E\x00S\x00',
])
def test_managed_dotenv_bootstrap_refuses_without_disclosing_contents(tmp_path, monkeypatch, location, content):
    python = Path(os.environ.get('HFC_TEST_DOTENV_PYTHON', sys.executable))
    available = subprocess.run([str(python), '-I', '-B', '-c', 'import dotenv.parser'],
                               capture_output=True, check=False)
    if available.returncode:
        pytest.skip('Hermes runtime dotenv parser unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    monkeypatch.setattr(legacy_9b904, '_MANAGED_SCOPE', tmp_path / 'missing-system-managed')
    monkeypatch.delenv('HERMES_MANAGED_DIR', raising=False)
    target = {'home-env': home / '.env', 'home-op-env': home / '.op.env', 'project-env': root / '.env'}[location]
    target.write_bytes(content)
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='environment bootstrap') as error:
        legacy_9b904.inspect_unmanaged_environment(root, python, home)
    assert '/policy' not in str(error.value)


def test_unmanaged_environment_detects_new_file(tmp_path, monkeypatch):
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    monkeypatch.setattr(legacy_9b904, '_MANAGED_SCOPE', tmp_path / 'missing-system-managed')
    monkeypatch.delenv('HERMES_MANAGED_DIR', raising=False)
    evidence = legacy_9b904.inspect_unmanaged_environment(root, Path(sys.executable), home)
    (home / '.env').write_text('UNRELATED=value\n')
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='environment changed'):
        legacy_9b904.verify_unmanaged_environment(evidence)


def test_unmanaged_dotenv_allows_other_values_without_exposing_them(tmp_path, monkeypatch):
    python = Path(os.environ.get('HFC_TEST_DOTENV_PYTHON', sys.executable))
    available = subprocess.run([str(python), '-I', '-B', '-c', 'import dotenv.parser'],
                               capture_output=True, check=False)
    if available.returncode:
        pytest.skip('Hermes runtime dotenv parser unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    monkeypatch.setattr(legacy_9b904, '_MANAGED_SCOPE', tmp_path / 'missing-system-managed')
    monkeypatch.delenv('HERMES_MANAGED_DIR', raising=False)
    dotenv = home / '.env'
    dotenv.write_text('OTHER="HERMES_MANAGED_DIR is data, 雪"\n')
    evidence = legacy_9b904.inspect_unmanaged_environment(root, python, home)
    legacy_9b904.verify_unmanaged_environment(evidence)
    dotenv.write_text('OTHER="changed"\n')
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='environment changed'):
        legacy_9b904.verify_unmanaged_environment(evidence)


@pytest.mark.parametrize('value', [b'true', b'1', b'"false"', b'null'])
def test_enabled_or_ambiguous_external_secret_source_is_rejected(value):
    content = BASE + b'secrets:\n  external_source:\n    enabled: ' + value + b'\n'
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='external secret sources'):
        legacy_9b904.inspect_existing_config(content)


def test_explicitly_disabled_external_secret_source_is_accepted():
    content = BASE + b'secrets:\n  external_source:\n    enabled: false\n'
    assert legacy_9b904.inspect_existing_config(content).contents == content


def test_existing_config_zero_delta_preserves_original_bytes():
    evidence = legacy_9b904.inspect_existing_config(BASE)
    assert evidence.contents == BASE
    assert evidence.digest == sha256(BASE).hexdigest()


def test_pm_canonical_gate_refuses_selection_reformat_before_admission(tmp_path):
    configured = os.environ.get('HFC_TEST_9B_SOURCE_ROOT')
    if not configured:
        pytest.skip('exact 9b904 source fixture not supplied')
    pytest.importorskip('ruamel.yaml')
    root = Path(configured)
    block = (b'_config_version: 52\nplugins:\n  enabled:\n    - hermes-feishu-card\n'
             b'    - hfc-runtime-dependency\n  disabled: []\n  entries:\n'
             b'    hermes-feishu-card:\n      allow_tool_override: false\nother: keep\n')
    flow = block.replace(b'  enabled:\n    - hermes-feishu-card\n    - hfc-runtime-dependency\n',
                         b'  enabled: [hermes-feishu-card, hfc-runtime-dependency]\n')
    legacy_9b904.inspect_existing_config(block)
    legacy_9b904.inspect_existing_config(flow)
    legacy_9b904.require_canonical(root, Path(sys.executable), tmp_path, block)
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='not canonical'):
        legacy_9b904.require_canonical(root, Path(sys.executable), tmp_path, flow)
    assert not (tmp_path / 'config.yaml').exists()


@pytest.mark.parametrize('content', [
    BASE.replace(b'_config_version: 52', b'_config_version: 51'),
    BASE.replace(b'_config_version: 52', b'_config_version: "52"'),
    BASE.replace(b'  disabled: []', b'  disabled: [hermes-feishu-card]'),
    BASE.replace(b'  disabled: []', b'  disabled: [unrelated]'),
    BASE.replace(b'  disabled: []', b'  disabled: []\n  disabled_by_update: {}'),
    BASE.replace(b'allow_tool_override: false', b'allow_tool_override: true'),
    BASE.replace(b'allow_tool_override: false', b'allow_tool_override: null'),
    BASE.replace(b'allow_tool_override: false', b'allow_tool_override: "false"'),
    BASE.replace(b'      allow_tool_override: false\n', b'      other: keep\n'),
    BASE.replace(b'hermes-feishu-card, hfc-runtime-dependency', b'hermes-feishu-card, extra-plugin'),
    BASE.replace(b'hermes-feishu-card, hfc-runtime-dependency', b'hermes-feishu-card, hermes-feishu-card'),
    BASE + b'other: duplicate\n',
    BASE.replace(b'other: keep', b'other: &shared keep\nanother: *shared'),
])
def test_existing_config_refuses_unproved_states(content):
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused):
        legacy_9b904.inspect_existing_config(content)


def test_zero_delta_adoption_and_restore_preserve_all_config_bytes(tmp_path, monkeypatch):
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative I/O is unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    config = home / 'config.yaml'
    config.write_bytes(BASE)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        'sha256:' + 'a' * 64, home, config, root, root)
    monkeypatch.setattr(plugin, 'probe_plugin_entrypoint', lambda *_args, **_kwargs: SimpleNamespace(status='verified'))
    monkeypatch.setattr(plugin, 'resolve_runtime_binding', lambda **_: binding)
    monkeypatch.setattr(pm_dependency.Registration, '__post_init__', lambda self: None)
    checked = []
    monkeypatch.setattr(pm_dependency.Registration, 'verify_adoption', lambda self, actual: checked.append(actual))
    monkeypatch.setattr(plugin, '_run_official_enable', lambda *_: pytest.fail('unexpected official CLI'))
    admission = pm_dependency.Registration(root, Path(sys.executable), home, home / 'member', None, False,
                                           source_family=legacy_9b904.ADAPTER_ID)
    registration = legacy_observer.prepare(binding, adoption=admission)
    marker = json.loads((root / legacy_observer.MARKER).read_text())
    assert marker['protocol'] == legacy_observer.ADOPTION_PROTOCOL
    assert marker['adapter'] == legacy_9b904.ADAPTER_ID
    assert marker['plugin_config']['enabled_before'] is True
    assert marker['plugin_config']['added_by_hfc'] is False
    assert len(checked) >= 3
    assert config.read_bytes() == BASE
    assert legacy_observer.inspect(root, home=home) is not None
    registration.rollback()
    assert config.read_bytes() == BASE
    assert not (root / legacy_observer.MARKER).exists()


@pytest.mark.parametrize('late_marker', [False, True])
def test_cli_uninstall_retains_real_adoption_receipt_before_source_restore(tmp_path, monkeypatch, late_marker):
    """A v2 receipt must survive even if it appears after the first CLI check."""
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative I/O is unavailable')
    from hermes_feishu_card import cli
    from hermes_feishu_card.install import legacy_9b904_uninstall

    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    config = home / 'config.yaml'
    config.write_bytes(BASE)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        'sha256:' + 'a' * 64, home, config, root, root)
    monkeypatch.setattr(plugin, 'probe_plugin_entrypoint', lambda *_args, **_kwargs: SimpleNamespace(status='verified'))
    monkeypatch.setattr(plugin, 'resolve_runtime_binding', lambda **_: binding)
    monkeypatch.setattr(pm_dependency.Registration, '__post_init__', lambda self: None)
    monkeypatch.setattr(pm_dependency.Registration, 'verify_adoption', lambda *_args: None)
    admission = pm_dependency.Registration(root, Path(sys.executable), home, home / 'member', None, False,
                                           source_family=legacy_9b904.ADAPTER_ID)
    legacy_observer.prepare(binding, adoption=admission)
    marker = root / legacy_observer.MARKER
    evidence = legacy_observer.inspect(root, home=home)
    assert evidence is not None
    owned_paths = [config, marker, evidence[2].backup_path, evidence[2].journal_path]
    before = {path: path.read_bytes() for path in owned_paths}

    if late_marker:
        held = marker.with_name('held-receipt')
        marker.rename(held)
        def fixed_tag(_args, *, command):
            held.rename(marker)
            return None
        monkeypatch.setattr(cli, '_run_fixed_tag_v3_restore', fixed_tag)
    else:
        monkeypatch.setattr(cli, '_run_fixed_tag_v3_restore',
                            lambda *_args, **_kwargs: pytest.fail('fixed-tag restore must not run'))
    monkeypatch.setattr(cli, '_restore_legacy_sources',
                        lambda *_args, **_kwargs: pytest.fail('source restore must not run'))
    def refuse(*_args, **_kwargs):
        raise ValueError('unverified HOME')
    monkeypatch.setattr(legacy_9b904_uninstall, 'uninstall', refuse)
    assert cli._run_uninstall(SimpleNamespace(hermes_dir=root, hermes_home=home)) == 1
    assert {path: path.read_bytes() for path in owned_paths} == before
    assert legacy_observer.inspect(root, home=home) is not None


def test_adoption_post_journal_generation_drift_retains_evidence(tmp_path, monkeypatch):
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative I/O is unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    config = home / 'config.yaml'
    config.write_bytes(BASE)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        'sha256:' + 'a' * 64, home, config, root, root)
    monkeypatch.setattr(plugin, 'probe_plugin_entrypoint', lambda *_args, **_kwargs: SimpleNamespace(status='verified'))
    monkeypatch.setattr(pm_dependency.Registration, '__post_init__', lambda self: None)
    calls = []
    def validate(_self, _binding):
        calls.append(True)
        if len(calls) == 3:
            raise ValueError('synthetic generation changed')
    monkeypatch.setattr(pm_dependency.Registration, 'verify_adoption', validate)
    admission = pm_dependency.Registration(root, Path(sys.executable), home, home / 'member', None, False,
                                           source_family=legacy_9b904.ADAPTER_ID)
    with pytest.raises(plugin.PluginAdoptionUncertain) as error:
        legacy_observer.prepare(binding, adoption=admission)
    assert str(error.value.__cause__) == 'synthetic generation changed'
    assert config.read_bytes() == BASE
    assert not (root / legacy_observer.MARKER).exists()
    journal, = (home / '.hermes_feishu_card/install').glob('*.json')
    assert json.loads(journal.read_text())['phase'] == 'plugin_enabled'
    assert len(list(journal.parent.glob('*.yaml'))) == 1


@pytest.mark.parametrize('changed', ['config', 'config-aba', 'marker', 'marker-aba', 'member', 'facts', 'lock'])
def test_source_restore_guard_rejects_changed_bound_evidence(tmp_path, monkeypatch, changed):
    from hermes_feishu_card.install import legacy_9b904_transaction

    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative I/O is unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    config = home / 'config.yaml'
    config.write_bytes(BASE)
    member = home / 'plugins' / pm_dependency.NAME
    member.mkdir(parents=True)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        'sha256:' + 'a' * 64, home, config, root, root)
    monkeypatch.setattr(plugin, 'probe_plugin_entrypoint', lambda *_args, **_kwargs: SimpleNamespace(status='verified'))
    monkeypatch.setattr(plugin, 'resolve_runtime_binding', lambda **_: binding)
    monkeypatch.setattr(pm_dependency.Registration, '__post_init__', lambda self: None)
    monkeypatch.setattr(pm_dependency.Registration, 'verify_adoption', lambda *_args: None)
    admission = pm_dependency.Registration(root, Path(sys.executable), home, member, None, False,
                                           source_family=legacy_9b904.ADAPTER_ID)
    legacy_observer.prepare(binding, adoption=admission)
    receipt = legacy_observer.inspect(root, home=home)
    assert receipt is not None
    marker = root / legacy_observer.MARKER
    receipt_paths = (marker, receipt[2].backup_path, receipt[2].journal_path)
    marker_files = {path: posix_transaction.read_path_snapshot(path, ValueError, 'changed')
                    for path in receipt_paths}
    marker_directories = {path.parent: posix_transaction.read_directory_identity(path.parent, ValueError, 'changed')
                          for path in receipt_paths}
    facts, lock = home / 'facts.json', home / 'workspace/uv.lock'
    lock.parent.mkdir()
    facts.write_bytes(b'facts')
    lock.write_bytes(b'lock')
    facts_identity, _, facts_version = legacy_9b904._read_versioned_evidence(facts, 'changed')
    lock_identity, _, lock_version = legacy_9b904._read_versioned_evidence(lock, 'changed')
    graph = legacy_9b904.GraphEvidence(facts, facts_identity, facts_version, lock, lock_identity, lock_version,
        {path: posix_transaction.read_directory_identity(path, ValueError, 'changed') for path in (home, lock.parent)})
    owner = {'schema': 3, 'version': '5.0.11'}
    evidence = SimpleNamespace(
        marker_identity=marker_files[marker], marker_files=marker_files, marker_directories=marker_directories,
        graph=graph, sdk_mode='owned', old_owner=owner,
        old_member_identity=posix_transaction.read_directory_identity(member, ValueError, 'changed'),
        home_identity=posix_transaction.read_directory_identity(home, ValueError, 'changed'),
        plugin_identity=posix_transaction.read_directory_identity(member.parent, ValueError, 'changed'),
        config_identity=posix_transaction.read_path_snapshot(config, ValueError, 'changed'),
        environment=object(), boot=object(), generation='fixture-generation',
        config=SimpleNamespace(digest=sha256(BASE).hexdigest()),
    )
    monkeypatch.setattr(pm_dependency, '_owned', lambda _path: owner)
    monkeypatch.setattr(pm_dependency, '_verified_call', lambda *_args: {})
    monkeypatch.setattr(legacy_9b904, 'capture_admission', lambda *_args: evidence)
    monkeypatch.setattr(legacy_9b904, 'verify_unmanaged_scope', lambda: None)
    monkeypatch.setattr(legacy_9b904, 'verify_unmanaged_environment', lambda _evidence: None)
    monkeypatch.setattr(legacy_9b904, 'verify_boot', lambda _evidence: None)
    monkeypatch.setattr(legacy_9b904, 'verify_source', lambda _root: None)
    monkeypatch.setattr(legacy_9b904, 'verify_runtime', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(legacy_9b904, 'verify_pm_graph', lambda *_args, **_kwargs: graph)

    guard, finish = legacy_9b904_transaction.prepare_source_restore(root, home, marker_files[marker])
    guard()
    finish()
    if changed == 'member':
        member.rename(home / 'previous-member')
        member.mkdir()
    elif changed in ('config-aba', 'marker-aba'):
        target = config if changed == 'config-aba' else marker
        data, metadata = target.read_bytes(), target.stat()
        target.write_bytes(data + b'changed')
        target.write_bytes(data)
        os.utime(target, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        if target.stat().st_ctime_ns == metadata.st_ctime_ns:
            pytest.skip('filesystem did not expose a ctime change')
    elif changed == 'marker':
        replacement = marker.with_name('replacement-marker')
        replacement.write_bytes(marker.read_bytes())
        replacement.replace(marker)
    else:
        target = {'config': config, 'facts': facts, 'lock': lock}[changed]
        target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='changed'):
        guard()


@pytest.mark.parametrize('state,extras,expected', [
    ({'count': 0, 'version': None, 'module_present': False, 'path_present': False}, [], 'absent'),
    ({'count': 1, 'version': '1.7.5', 'module_present': True, 'path_present': True}, ['feishu'], 'pm-feishu'),
    ({'count': 1, 'version': '1.6.8', 'module_present': True, 'path_present': True}, [], None),
    ({'count': 1, 'version': '1.7.5', 'module_present': True, 'path_present': True}, [], None),
    ({'count': 0, 'version': None, 'module_present': True, 'path_present': True}, [], None),
    ({'count': 0, 'version': None, 'module_present': False, 'path_present': True}, [], None),
    ({'count': 2, 'version': None, 'module_present': True, 'path_present': True}, [], None),
])
def test_sdk_preflight_distinguishes_missing_managed_and_unowned(tmp_path, monkeypatch, state, extras, expected):
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    key = sha256(str(root.resolve()).encode()).hexdigest()[:16]
    facts = home / 'installs' / key / 'facts.json'
    facts.parent.mkdir(parents=True)
    facts.write_text(json.dumps({'packages': {'venv': {'extras': extras}}}))
    monkeypatch.setattr(legacy_9b904, 'probe_sdk', lambda *_: state)
    if expected is None:
        with pytest.raises(legacy_9b904.LegacyAdoptionRefused):
            legacy_9b904.inspect_sdk_before(root, Path(sys.executable), home)
    else:
        mode, path, identity = legacy_9b904.inspect_sdk_before(root, Path(sys.executable), home)
        assert mode == expected
        assert path == facts
        assert identity[2] == sha256(facts.read_bytes()).hexdigest()


def test_sdk_verifier_binds_imports_and_ignores_poisoned_pyc(tmp_path, monkeypatch):
    """A matching installed wheel must not lend authority to a shadow package."""
    env = tmp_path / 'env'
    venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(env)
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = Path(subprocess.check_output(
        [str(python), '-I', '-B', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
        text=True,
    ).strip())
    sentinel = tmp_path / 'poison-executed'
    poison = f'open({str(sentinel)!r}, "w").write("poison")\n'.encode()
    good = b'pass' + b' ' * (len(poison) - 5) + b'\n'
    files = {
        'lark_oapi/__init__.py': good,
        'lark_oapi/ws.py': b'class Client:\n    def __init__(self, extra_ua_tags=None): pass\n',
        'lark_oapi-1.7.5.dist-info/METADATA': b'Metadata-Version: 2.1\nName: lark-oapi\nVersion: 1.7.5\n\n',
        'lark_oapi-1.7.5.dist-info/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as wheel:
        for name, content in files.items():
            wheel.writestr(name, content)
    payload = archive.getvalue()
    monkeypatch.setattr(pm_wheel_store, '_SDK_WHEEL_SHA256', sha256(payload).hexdigest())
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    sdk = pm_wheel_store.publish_sdk(home, payload)
    for name, content in files.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (site / 'lark_oapi-1.7.5.dist-info/direct_url.json').write_text(json.dumps({
        'url': Path(sdk['path']).as_uri(), 'archive_info': {},
    }))
    assert legacy_9b904.verify_installed_sdk(tmp_path, python, home, sdk)

    init = site / 'lark_oapi/__init__.py'
    init.write_bytes(poison)
    timestamp = init.stat().st_mtime_ns
    py_compile.compile(str(init), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    init.write_bytes(good)
    os.utime(init, ns=(timestamp, timestamp))
    assert legacy_9b904.verify_installed_sdk(tmp_path, python, home, sdk)
    assert not sentinel.exists()

    shadow = tmp_path / 'shadow/lark_oapi'
    shadow.mkdir(parents=True)
    (shadow / '__init__.py').write_bytes(poison)
    (site / 'a-shadow.pth').write_text(f'import sys; sys.path.insert(0, {str(shadow.parent)!r})\n')
    assert not legacy_9b904.verify_installed_sdk(tmp_path, python, home, sdk)
    assert not sentinel.exists()


def test_sdk_preflight_detects_manual_install_without_facts_change(tmp_path, monkeypatch):
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    (home / 'config.yaml').write_bytes(BASE)
    key = sha256(str(root.resolve()).encode()).hexdigest()[:16]
    facts = home / 'installs' / key / 'facts.json'
    facts.parent.mkdir(parents=True)
    facts.write_text(json.dumps({'packages': {'venv': {'extras': []}}}))
    def identity(path):
        return posix_transaction.read_path_snapshot(path, ValueError, 'changed')
    evidence = SimpleNamespace(
        home_identity=posix_transaction.read_directory_identity(home, ValueError, 'changed'),
        plugin_identity=(1, 2), config_identity=identity(home / 'config.yaml'), boot=object(),
        facts_path=facts, facts_identity=identity(facts), python=Path(sys.executable), sdk_mode='absent',
        marker_identity=None, environment=None,
    )
    monkeypatch.setattr(legacy_9b904, 'verify_source', lambda _root: None)
    monkeypatch.setattr(legacy_9b904, 'verify_unmanaged_scope', lambda: None)
    monkeypatch.setattr(legacy_9b904, 'verify_unmanaged_environment', lambda _evidence: None)
    monkeypatch.setattr(legacy_9b904, 'verify_boot', lambda _boot: None)
    monkeypatch.setattr(legacy_9b904, 'verify_plugin_namespace', lambda *_args, **_kwargs: (1, 2))
    monkeypatch.setattr(legacy_observer, '_read', lambda _path: None)
    state = {'count': 0, 'version': None, 'module_present': False, 'path_present': False}
    monkeypatch.setattr(legacy_9b904, 'probe_sdk', lambda *_args: dict(state))
    legacy_9b904.verify_before(root, home, evidence)
    state.update(count=1, version='1.6.8', module_present=True, path_present=True)
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='cannot preserve'):
        legacy_9b904.verify_before(root, home, evidence)
    assert identity(facts) == evidence.facts_identity


@pytest.mark.parametrize('changed', ['marker', 'journal', 'backup'])
def test_existing_schema3_marker_is_bound_on_repeat(tmp_path, monkeypatch, changed):
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative I/O is unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    config = home / 'config.yaml'
    config.write_bytes(BASE)
    (home / 'plugins' / pm_dependency.NAME).mkdir(parents=True)
    key = sha256(str(root.resolve()).encode()).hexdigest()[:16]
    facts = home / 'installs' / key / 'facts.json'
    facts.parent.mkdir(parents=True)
    facts.write_text(json.dumps({'packages': {'venv': {'extras': []}}}))
    marker = root / legacy_observer.MARKER
    marker.write_bytes(b'original marker')
    state = home / '.hermes_feishu_card/install'
    state.mkdir(parents=True)
    backup, journal = state / 'backup.yaml', state / 'journal.json'
    backup.write_bytes(b'original backup')
    journal.write_bytes(b'original journal')
    preimage = SimpleNamespace(backup_path=backup, journal_path=journal)
    python = tmp_path / 'generation/venv/bin/python'
    binding = plugin.HermesRuntimeBinding(root, python, python, 'sha256:' + 'a' * 64,
                                          home, config, root, root)
    record = {'protocol': legacy_observer.ADOPTION_PROTOCOL, 'adapter': legacy_9b904.ADAPTER_ID,
              'python_identity': binding.python_identity}
    monkeypatch.setattr(legacy_9b904, 'verify_source', lambda _root: None)
    monkeypatch.setattr(legacy_9b904, 'verify_unmanaged_scope', lambda: None)
    monkeypatch.setattr(legacy_9b904, 'require_canonical', lambda *_args: None)
    monkeypatch.setattr(legacy_9b904, 'inspect_boot', lambda *_args: object())
    monkeypatch.setattr(legacy_9b904, 'verify_boot', lambda _boot: None)
    monkeypatch.setattr(legacy_9b904, 'verify_runtime', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(legacy_9b904, 'verify_pm_graph', lambda *_args, **_kwargs: SimpleNamespace(
        facts_identity=posix_transaction.read_path_snapshot(facts, ValueError, 'changed')))
    monkeypatch.setattr(legacy_9b904, 'probe_sdk', lambda *_args: {
        'count': 1, 'version': '1.7.5', 'module_present': True, 'path_present': True,
    })
    monkeypatch.setattr(plugin, 'resolve_runtime_binding', lambda **_kwargs: binding)
    monkeypatch.setattr(legacy_observer, 'inspect',
                        lambda *_args, **_kwargs: (record, b'original marker', preimage, object(), BASE))
    monkeypatch.setattr(legacy_observer, '_config_state', lambda *_args: 'installed')
    probe = {'enabled': [pm_dependency.NAME], 'expected': sha256(BASE).hexdigest(),
             'generation': str(python.parent.parent)}
    evidence = legacy_9b904.capture_admission(root, python, home, probe, {'schema': 3, 'version': '5.0.11'})
    assert evidence.sdk_mode == 'owned'
    legacy_9b904.verify_before(root, home, evidence)
    target = {'marker': marker, 'journal': journal, 'backup': backup}[changed]
    replacement = target.with_name(target.name + '.replacement')
    replacement.write_bytes(target.read_bytes())
    replacement.replace(target)
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='receipt changed'):
        legacy_9b904.verify_before(root, home, evidence)


def test_pm_graph_binds_current_stamp_facts_lock_and_owned_wheels(tmp_path, monkeypatch):
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    generation = home / 'installs/test/environments/current/venv'
    generation.mkdir(parents=True)
    lock = generation.parent / 'workspace/uv.lock'
    lock.parent.mkdir()
    key = sha256(str(root.resolve()).encode()).hexdigest()[:16]
    facts = home / 'installs' / key / 'facts.json'
    facts.parent.mkdir(parents=True)
    facts.write_text(json.dumps({'packages': {'venv': {'environment': str(generation),
                                                        'resolved_lock': str(lock)}}}))
    member = home / 'plugins' / pm_dependency.NAME
    member_key = pm_dependency.NAME + '-' + sha256(str(member.resolve()).encode()).hexdigest()[:16]
    hfc = {'path': str(home / 'hfc.whl'), 'sha256': 'a' * 64}
    sdk = {'path': str(home / 'sdk.whl'), 'sha256': 'b' * 64}
    owner = {'version': '5.0.11', 'external_wheel': hfc, 'sdk_wheel': sdk}
    lock.write_text(f'''\n[[package]]
name = "hermes-feishu-streaming-card"
version = "5.0.11"
source = {{ path = {json.dumps(hfc['path'])} }}
wheels = [{{ filename = "hfc.whl", hash = "sha256:{hfc['sha256']}" }}]
[[package]]
name = "lark-oapi"
version = "1.7.5"
source = {{ path = {json.dumps(sdk['path'])} }}
wheels = [{{ filename = "sdk.whl", hash = "sha256:{sdk['sha256']}" }}]
[[package]]
name = "hermes-plugin-{member_key}"
source = {{ virtual = "plugin-sources/{member_key}" }}
dependencies = [{{ name = "hermes-feishu-streaming-card" }}, {{ name = "lark-oapi" }}]
[package.metadata]
requires-dist = [{{ name = "hermes-feishu-streaming-card", path = {json.dumps(hfc['path'])} }},
                 {{ name = "lark-oapi", path = {json.dumps(sdk['path'])} }}]
''')
    current = {'current': True, 'generation': str(generation), 'expected': 'config-digest',
               'enabled': [plugin.PLUGIN_KEY, pm_dependency.NAME]}
    monkeypatch.setattr(pm_dependency, '_verified_call', lambda *_args: dict(current))
    graph = legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation), 'config-digest')
    legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation),
                                'config-digest', expected=graph)
    original_facts = facts.read_bytes()
    def drift_during_current(*_args):
        changed = json.loads(original_facts)
        changed['packages']['venv']['stamp'] = 'changed-after-current-read'
        facts.write_text(json.dumps(changed))
        return dict(current)
    monkeypatch.setattr(pm_dependency, '_verified_call', drift_during_current)
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='PM facts changed'):
        legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation), 'config-digest')
    facts.write_bytes(original_facts)
    monkeypatch.setattr(pm_dependency, '_verified_call', lambda *_args: dict(current))
    if os.name != 'nt':
        metadata = facts.stat()
        def restore_bytes_during_current(*_args):
            facts.write_text('{"temporary": "replacement"}')
            facts.write_bytes(original_facts)
            os.utime(facts, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            return dict(current)
        monkeypatch.setattr(pm_dependency, '_verified_call', restore_bytes_during_current)
        with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='PM facts changed'):
            legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation), 'config-digest')
        monkeypatch.setattr(pm_dependency, '_verified_call', lambda *_args: dict(current))
    graph = legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation), 'config-digest')
    replacement = lock.with_name('replacement.lock')
    replacement.write_bytes(lock.read_bytes())
    replacement.replace(lock)
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='PM graph changed'):
        legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation),
                                    'config-digest', expected=graph)
    current['current'] = False
    with pytest.raises(legacy_9b904.LegacyAdoptionRefused, match='not current'):
        legacy_9b904.verify_pm_graph(root, Path(sys.executable), home, owner, str(generation), 'config-digest')
