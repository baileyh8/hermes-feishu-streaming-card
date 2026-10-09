from pathlib import Path

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.config import load_config


def test_separate_sidecar_runtime_does_not_follow_hermes_environment(monkeypatch, tmp_path):
    current = tmp_path / 'managed/new/bin/python'
    stable = tmp_path / 'hfc/bin/python'
    stable.parent.mkdir(parents=True)
    stable.touch()
    package = tmp_path / 'hfc/lib/python/site-packages/hermes_feishu_card/__init__.py'
    package.parent.mkdir(parents=True)
    package.touch()
    monkeypatch.setattr(cli, '_resolve_start_runtime_identity', lambda root: (current, 'managed'))
    monkeypatch.setattr(cli, '_check_runtime_hook_import', lambda py: {
        'status': 'ok', 'version': cli.PACKAGE_VERSION,
        'location': str(package), 'prefix': str(tmp_path / 'hfc'),
        'purelib': str(package.parents[1]), 'platlib': str(package.parents[1]),
    })
    selected, identity = cli._resolve_sidecar_runtime_identity(tmp_path, {'service': {'python_executable': str(stable)}})
    assert selected == stable
    assert identity == cli.python_executable_identity(stable)


def test_default_keeps_hermes_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, '_resolve_start_runtime_identity', lambda root: (tmp_path, 'managed'))
    assert cli._resolve_sidecar_runtime_identity(tmp_path, {}) == (tmp_path, 'managed')


@pytest.mark.parametrize('value', [True, 42, [], '../python', 'python'])
def test_sidecar_python_config_rejects_ambiguous_paths(tmp_path, value):
    import yaml
    p = tmp_path / 'config.yaml'
    p.write_text(yaml.safe_dump({'service': {'python_executable': value}}))
    with pytest.raises(ValueError, match='service.python_executable'):
        load_config(p)


@pytest.mark.parametrize('version,status', [('old', 'ok'), (cli.PACKAGE_VERSION, 'failed')])
def test_separate_runtime_rejects_wrong_or_broken_package(monkeypatch, tmp_path, version, status):
    stable = tmp_path / 'bin/python'
    stable.parent.mkdir(); stable.touch()
    monkeypatch.setattr(cli, '_resolve_start_runtime_identity', lambda root: (tmp_path, 'managed'))
    monkeypatch.setattr(cli, '_check_runtime_hook_import', lambda py: {'status': status, 'version': version})
    with pytest.raises(ValueError, match='Sidecar'):
        cli._resolve_sidecar_runtime_identity(tmp_path, {'service': {'python_executable': str(stable)}})


def test_separate_runtime_cannot_bypass_hermes_hook_check(monkeypatch, tmp_path):
    def refused(root):
        raise ValueError('Hermes hook missing')
    monkeypatch.setattr(cli, '_resolve_start_runtime_identity', refused)
    with pytest.raises(ValueError, match='Hermes hook missing'):
        cli._resolve_sidecar_runtime_identity(tmp_path, {'service': {'python_executable': str(tmp_path / 'python')}})


def test_separate_runtime_rejects_checkout_import(monkeypatch, tmp_path):
    stable = tmp_path / 'bin/python'
    stable.parent.mkdir(); stable.touch()
    checkout = tmp_path / 'source/__init__.py'
    checkout.parent.mkdir(); checkout.touch()
    monkeypatch.setattr(cli, '_resolve_start_runtime_identity', lambda root: (stable, 'managed'))
    monkeypatch.setattr(cli, '_check_runtime_hook_import', lambda py: {
        'status': 'ok', 'version': cli.PACKAGE_VERSION, 'location': str(checkout),
        'prefix': str(tmp_path), 'purelib': str(tmp_path / 'site-packages'),
    })
    with pytest.raises(ValueError, match='ordinary site-packages'):
        cli._resolve_sidecar_runtime_identity(tmp_path, {'service': {'python_executable': str(stable)}})


def test_start_passes_configured_sidecar_runtime_to_owned_process(monkeypatch, tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text('service:\n  python_executable: /stable/bin/python\n')
    monkeypatch.setattr(cli, '_lifecycle_hook_check', lambda args: {'status': 'installed', 'blocking': False, 'root': tmp_path})
    calls = []
    def select(root, values):
        assert root == tmp_path
        assert values['service']['python_executable'] == '/stable/bin/python'
        return Path('/stable/bin/python'), 'stable-identity'
    monkeypatch.setattr(cli, '_resolve_sidecar_runtime_identity', select)
    monkeypatch.setattr(cli, 'persistent_sidecar_matches', lambda **kw: False)
    monkeypatch.setattr(cli, 'start_sidecar', lambda *args, **kw: calls.append(kw) or 'started')
    assert cli.main(['start', '--config', str(config), '--hermes-dir', str(tmp_path)]) == 0
    assert calls[0]['python_executable'] == Path('/stable/bin/python')
    assert calls[0]['expected_python_identity'] == 'stable-identity'
