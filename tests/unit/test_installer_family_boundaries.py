"""Frozen ownership rules and real one-way installer implementation owners."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

FROZEN = json.loads(
    (Path(__file__).parents[1] / 'fixtures' / 'installer_family_b77599d.json').read_text()
)


@pytest.mark.parametrize('case', FROZEN['manifest_cases'], ids=[
    f'shape-{n}' for n in range(len(FROZEN['manifest_cases']))
])
def test_manifest_validators_preserve_original_shape_and_exception_contract(case):
    from hermes_feishu_card.install import decomposed, manifest

    for name, validator in [('general', manifest.validate_install_manifest),
                            ('decomposed', decomposed.validate_manifest)]:
        expected = case['expected'][name]
        if 'error' in expected:
            cls = getattr(manifest, expected['error'])
            with pytest.raises(cls) as caught:
                validator(case['input'])
            assert str(caught.value) == expected['message']
        else:
            assert validator(case['input']) == expected['value']


@pytest.mark.parametrize('case', FROZEN['fingerprints'])
def test_snapshot_fingerprint_preserves_absence_and_bytes(case):
    from hermes_feishu_card.install import decomposed_ownership

    snapshot = {k: None if v is None else v.encode() for k, v in case['input'].items()}
    assert decomposed_ownership._fingerprint(snapshot) == case['expected']


@pytest.mark.parametrize('owner', ['manifest', 'source_layout', 'git_evidence', 'decomposed_ownership'])
def test_fresh_readonly_owners_do_not_load_recovery_or_integrity(tmp_path, owner):
    import hermes_feishu_card.install.manifest as manifest

    root = Path(manifest.__file__).resolve().parents[2]
    code = '''
import importlib, pathlib, sys
sys.path.insert(0, sys.argv[1])
m = importlib.import_module('hermes_feishu_card.install.'+sys.argv[2])
assert pathlib.Path(m.__file__).resolve().parents[2] == pathlib.Path(sys.argv[1])
for name in ('decomposed','detect','recovery','integrity'):
    assert 'hermes_feishu_card.install.'+name not in sys.modules, name
assert 'hermes_feishu_card.cli' not in sys.modules
print('readonly closure verified')
'''
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               HERMES_FEISHU_CARD_STATE_DIR=str(tmp_path / 'state'))
    result = subprocess.run([sys.executable, '-I', '-c', code, str(root), owner],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_decomposed_compatibility_entry_contains_only_direct_reexports():
    from hermes_feishu_card.install import decomposed

    tree = ast.parse(Path(decomposed.__file__).read_text())
    for node in tree.body:
        assert isinstance(node, (ast.ImportFrom, ast.Import)) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )


def test_git_evidence_and_integrity_share_the_original_exception_identity():
    from hermes_feishu_card.install import errors, git_evidence, integrity

    assert git_evidence.IntegrityRepairRefused is errors.IntegrityRepairRefused
    assert integrity.IntegrityRepairRefused is errors.IntegrityRepairRefused
    assert issubclass(errors.IntegrityRepairRefused, ValueError)


@pytest.mark.parametrize('failure', ['oserror', 'timeout', 'returncode'])
def test_git_evidence_query_failures_are_closed_with_original_reason(monkeypatch, tmp_path, failure):
    from hermes_feishu_card.install import git_evidence, integrity

    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if failure == 'oserror':
            raise OSError('synthetic failure')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 5)
        return subprocess.CompletedProcess(args, 2, b'', b'')

    monkeypatch.setattr(git_evidence.subprocess, 'run', run)
    with pytest.raises(integrity.IntegrityRepairRefused, match='^git_evidence_unavailable$') as caught:
        git_evidence._run_git(tmp_path, 'rev-parse', 'HEAD')
    assert len(calls) == 1
    assert calls[0][0] == ['git', '-C', str(tmp_path), 'rev-parse', 'HEAD']
    assert calls[0][1] == {'check': False, 'capture_output': True, 'timeout': 5}
    assert (caught.value.__cause__ is not None) == (failure != 'returncode')


@pytest.mark.parametrize('code,expected', [(0, True), (1, False), (2, None)])
def test_git_ancestor_status_has_only_zero_one_semantics(monkeypatch, tmp_path, code, expected):
    from hermes_feishu_card.install import git_evidence, integrity

    def run(args, **kwargs):
        assert kwargs['timeout'] == 5
        assert args[-3:] == ['--is-ancestor', 'a' * 40, 'b' * 40]
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr(git_evidence.subprocess, 'run', run)
    if expected is None:
        with pytest.raises(integrity.IntegrityRepairRefused, match='^git_history_unavailable$'):
            git_evidence._is_ancestor(tmp_path, 'a' * 40, 'b' * 40)
    else:
        assert git_evidence._is_ancestor(tmp_path, 'a' * 40, 'b' * 40) is expected


def test_compatibility_exports_are_exact_implementation_objects():
    from hermes_feishu_card.install import decomposed, decomposed_ownership, manifest, patcher, recovery, source_layout

    assert decomposed.validate_manifest is manifest.validate_decomposed_manifest
    assert decomposed.plan is recovery._plan_decomposed_recovery
    assert decomposed.install is recovery.install_decomposed
    assert decomposed.restore is recovery.restore_decomposed
    assert decomposed.SOURCE_TARGETS is source_layout.DECOMPOSED_SOURCE_TARGETS
    assert decomposed.MANIFEST_VERSION == source_layout.DECOMPOSED_MANIFEST_VERSION == 4
    assert patcher.DECOMPOSED_GATEWAY_TARGETS is source_layout.DECOMPOSED_GATEWAY_TARGETS
    assert patcher.DECOMPOSED_TARGETS is source_layout.DECOMPOSED_TARGETS
    for name in ('is_managed', '_snapshot', '_fingerprint', 'render',
                 'snapshot_provenance', 'verified_snapshot_provenance',
                 '_remove_target', '_verified_reapplied_source', '_legacy_upgrade_sources',
                 '_inspect', 'VERSION_TARGETS', 'EXPLICIT_UPGRADE_STATES'):
        assert getattr(decomposed, name) is getattr(decomposed_ownership, name)


@pytest.mark.parametrize('raw,valid', [(b'a'*40, True), (b'b'*64, True),
                                       (b'ABCDEF'*6+b'ABCD', True),
                                       (b'a'*39, False), (b'g'*40, False)])
def test_git_head_preserves_original_hash_validation(monkeypatch, tmp_path, raw, valid):
    from hermes_feishu_card.install import git_evidence, integrity

    monkeypatch.setattr(git_evidence, '_run_git',
                        lambda *_args: subprocess.CompletedProcess([], 0, raw+b'\n', b''))
    if valid:
        assert git_evidence._git_head(tmp_path) == raw.decode().lower()
    else:
        with pytest.raises(integrity.IntegrityRepairRefused, match='^git_head_invalid$'):
            git_evidence._git_head(tmp_path)


@pytest.mark.parametrize('raw', [b'\xff', b'not-a-valid-root'])
def test_git_root_must_equal_exact_resolved_source_root(monkeypatch, tmp_path, raw):
    from hermes_feishu_card.install import git_evidence, integrity

    monkeypatch.setattr(git_evidence, '_run_git',
                        lambda *_args: subprocess.CompletedProcess([], 0, raw, b''))
    with pytest.raises(integrity.IntegrityRepairRefused, match='^git_root_invalid$'):
        git_evidence._exact_git_root(tmp_path)


def test_git_blob_invalid_unicode_retains_refusal_cause(monkeypatch, tmp_path):
    from hermes_feishu_card.install import git_evidence, integrity

    monkeypatch.setattr(git_evidence, '_run_git',
                        lambda *_args: subprocess.CompletedProcess([], 0, b'\xff', b''))
    with pytest.raises(integrity.IntegrityRepairRefused, match='^git_blob_invalid$') as caught:
        git_evidence._git_blob(tmp_path, 'a'*40, 'gateway/run.py')
    assert isinstance(caught.value.__cause__, UnicodeError)


def test_install_package_exports_retain_identity_and_discoverability():
    import hermes_feishu_card.install as package
    from hermes_feishu_card.install import detect, manifest, recovery

    for name in package.__all__:
        owner = detect if name in {'HermesDetection', 'detect_hermes'} else manifest if name == 'file_sha256' else recovery
        assert getattr(package, name) is getattr(owner, name)
        assert name in dir(package)
    with pytest.raises(AttributeError):
        package.synthetic_unknown_export


def test_recovery_keeps_shared_lock_and_refusal_identities():
    from hermes_feishu_card.install import errors, recovery, transaction_lock

    assert recovery._root_lock is transaction_lock._root_lock
    assert recovery._PROCESS_LOCKS is transaction_lock._PROCESS_LOCKS
    assert recovery.RecoveryRefused is errors.RecoveryRefused


def test_family_runtime_import_graph_has_no_cycle_involving_new_owners():
    from hermes_feishu_card.install import source_layout

    folder = Path(source_layout.__file__).parent
    modules = {p.stem: p for p in folder.glob('*.py')}
    graph = {name: set() for name in modules}

    def visit(node, edges):
        if isinstance(node, ast.If) and (
            isinstance(node.test, ast.Name) and node.test.id == 'TYPE_CHECKING'
            or isinstance(node.test, ast.Attribute) and node.test.attr == 'TYPE_CHECKING'
        ):
            for child in node.orelse:
                visit(child, edges)
            return
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                names = [node.module.split('.')[0]] if node.module else [alias.name for alias in node.names]
                edges.update(name for name in names if name in modules)
            elif node.module and node.module.startswith('hermes_feishu_card.install.'):
                name = node.module.split('.')[2]
                if name in modules:
                    edges.add(name)
        for child in ast.iter_child_nodes(node):
            visit(child, edges)

    for name, path in modules.items():
        visit(ast.parse(path.read_text()), graph[name])
    for start in ('detect', 'decomposed', 'decomposed_ownership', 'integrity',
                  'manifest', 'recovery', 'git_evidence', 'source_layout', 'errors'):
        def walk(name, path, start=start):
            for neighbor in graph[name]:
                assert neighbor != start, f'cycle: {path + [neighbor]}'
                if neighbor not in path:
                    walk(neighbor, path + [neighbor])
        walk(start, [start])
