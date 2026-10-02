import os
import stat

import pytest

from hermes_feishu_card import process, runner

TOKEN = '0123456789abcdef' * 2


def test_launcher_argv_has_only_credential_path(tmp_path):
    command = process._sidecar_command(tmp_path / 'config.yaml', env_file=None,
                                       token_file=tmp_path / 'sidecar-control.token')
    assert '--token-file' in command and '--token' not in command
    assert TOKEN not in ' '.join(command)


def test_private_token_roundtrip_and_rotation(monkeypatch, tmp_path):
    monkeypatch.setattr(process, 'state_dir', lambda: tmp_path)
    path = process.write_control_token_file(TOKEN)
    assert process.read_control_token_file(path) == TOKEN
    if os.name != 'nt':
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    process.write_control_token_file('abcdef0123456789' * 2)
    assert process.read_control_token_file(path) != TOKEN
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('value', ['', ' ', 'secret\nother', 'x' * 257])
def test_invalid_token_file_rejected(tmp_path, value):
    path = tmp_path / 'token'; path.write_text(value); path.chmod(0o600)
    with pytest.raises(ValueError):
        process.read_control_token_file(path)


def test_symlink_rejected_without_touching_target(monkeypatch, tmp_path):
    target = tmp_path / 'target'; target.write_text(TOKEN); target.chmod(0o600)
    link = tmp_path / 'sidecar-control.token'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    monkeypatch.setattr(process, 'state_dir', lambda: tmp_path)
    with pytest.raises((OSError, ValueError)):
        process.read_control_token_file(link)
    with pytest.raises((OSError, ValueError)):
        process.write_control_token_file('new-token')
    assert target.read_text() == TOKEN


@pytest.mark.skipif(os.name == 'nt', reason='POSIX file permissions')
def test_public_permissions_and_fifo_rejected(tmp_path):
    path = tmp_path / 'token'; path.write_text(TOKEN); path.chmod(0o644)
    with pytest.raises(ValueError):
        process.read_control_token_file(path)
    path.unlink(); os.mkfifo(path, 0o600)
    with pytest.raises(ValueError):
        process.read_control_token_file(path)


def test_unreadable_token_does_not_start_server_or_log_secret(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(runner, 'load_config', lambda *a, **kw: pytest.fail('must fail before loading config'))
    assert runner.main(['--token-file', str(tmp_path / 'missing'), '--managed-pidfile']) == 1
    assert TOKEN not in caplog.text


def test_file_failure_never_falls_back_to_argv(monkeypatch, tmp_path):
    monkeypatch.setattr(process, 'state_dir', lambda: tmp_path)
    monkeypatch.setattr(process, 'fetch_health', lambda _: None)
    monkeypatch.setattr(process, '_systemd_user_available', lambda: False)
    def fail(_):
        raise OSError('synthetic private data')
    monkeypatch.setattr(process, 'write_control_token_file', fail)
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **k: pytest.fail('no launch'))
    result = process.start_sidecar(tmp_path / 'config.yaml', {'service': {'manager': 'detached'}})
    assert result == 'failed: private control token file could not be prepared'
    assert 'synthetic private data' not in result


def test_runner_file_token_reaches_handshake_and_control_app(monkeypatch, tmp_path):
    path = tmp_path / 'token'; path.write_text(TOKEN); path.chmod(0o600)
    observed = {}
    monkeypatch.setattr(runner, 'wait_for_managed_pidfile', lambda pid, token: observed.setdefault('handshake', token) == TOKEN)
    monkeypatch.setattr(runner, 'load_config', lambda *a, **kw: {'server': {'host': '127.0.0.1', 'port': 0}})
    monkeypatch.setattr(runner, 'ensure_transport_root_secret', lambda: None)
    monkeypatch.setattr(runner, 'transport_root_privacy_verified', lambda: True)
    monkeypatch.setattr(runner, 'create_app', lambda *a, **kw: observed.update(kw))
    monkeypatch.setattr(runner.web, 'run_app', lambda *a, **kw: None)
    assert runner.main(['--token-file', str(path), '--managed-pidfile']) == 0
    assert observed['handshake'] == observed['process_token'] == TOKEN


def test_ambiguous_credential_sources_rejected(tmp_path):
    with pytest.raises(SystemExit) as exc:
        runner.main(['--token', 'legacy-token', '--token-file', str(tmp_path / 'token')])
    assert exc.value.code == 2


@pytest.mark.skipif(os.name == 'nt', reason='POSIX ownership')
def test_wrong_owner_and_hardlink_rejected(monkeypatch, tmp_path):
    path = tmp_path / 'token'; path.write_text(TOKEN); path.chmod(0o600)
    with monkeypatch.context() as mp:
        mp.setattr(process.os, 'getuid', lambda: path.stat().st_uid + 1)
        with pytest.raises(ValueError):
            process.read_control_token_file(path)
    other = tmp_path / 'hardlink'; os.link(path, other)
    with pytest.raises(ValueError):
        process.read_control_token_file(path)
