"""Uninstall denial follows Hermes' authoritative HOME, not event profile claims."""
from contextvars import ContextVar
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime, instance_binding
from hermes_feishu_card.install import legacy_home_revocation as revocation


@pytest.fixture
def homes(tmp_path, monkeypatch):
    root, first, second = tmp_path / 'checkout', tmp_path / 'first', tmp_path / 'second'
    root.mkdir()
    first.mkdir()
    second.mkdir()
    for home in (first, second):
        (home / '.hermes_feishu_card' / 'install').mkdir(parents=True)
        (home / 'config.yaml').write_text(
            'plugins:\n  enabled: [hfc-runtime-dependency]\n  disabled: [hermes-feishu-card]\n')
    selected = ContextVar('hermes-home', default=first)
    constants = SimpleNamespace(get_hermes_home=lambda: selected.get())
    monkeypatch.setitem(__import__('sys').modules, 'hermes_constants', constants)
    monkeypatch.setattr(instance_binding, 'gateway_process_binding', lambda: (root, first))
    monkeypatch.delenv('HERMES_FEISHU_CARD_ENABLED', raising=False)
    revocation._CACHE.clear()
    yield root, first, second, selected
    revocation._CACHE.clear()


def _ticket(root: Path, home: Path, *, phase: str = 'completed') -> Path:
    config = (home / 'config.yaml').read_bytes()
    graph = {'generation': str(home / 'generation/venv'), 'python_identity': 'sha256:' + 'c' * 64,
             'member': [1, 4], 'owner_sha256': 'd' * 64, 'facts_path': str(home / 'facts.json'),
             'facts': [1, 5, 'e' * 64], 'facts_version': [1, 5, 10, 11, 12],
             'lock_path': str(home / 'lock'), 'lock': [1, 6, 'f' * 64], 'lock_version': [1, 6, 10, 11, 12]}
    value = {'protocol': revocation.PROTOCOL, 'phase': phase, 'root_key': revocation.root_key(root),
             'home': str(home), 'marker': [1, 2, 'a' * 64], 'before': [1, 3, 'b' * 64],
             'backup': [1, 7, 'a' * 64], 'journal': [1, 8, 'b' * 64],
             'root_dir': [1, 9], 'home_dir': [1, 10], 'state_dir': [1, 11],
             'after_sha256': sha256(config).hexdigest(), 'pm_before': graph,
             'pm_after': graph if phase == 'completed' else None}
    path = revocation.ticket_path(root, home)
    path.write_text(json.dumps(value))
    return path


def test_revoked_home_denies_hooks_but_other_home_is_unaffected(homes, monkeypatch):
    root, first, second, selected = homes
    _ticket(root, first)
    assert revocation.inspect(root, first) == 'disabled'
    assert hook_runtime.load_runtime_config().enabled is False
    monkeypatch.setattr(hook_runtime, '_policy_gate_sync',
                        lambda *_args: pytest.fail('revoked HOME must not query policy'))
    assert hook_runtime.emit_from_hermes_locals({}, 'message.started') is False
    token = selected.set(second)
    try:
        assert hook_runtime.load_runtime_config().enabled is True
    finally:
        selected.reset(token)


def test_running_home_decision_is_stable_until_restart(homes):
    root, first, _second, _selected = homes
    assert hook_runtime.load_runtime_config().enabled is True
    _ticket(root, first)
    assert hook_runtime.load_runtime_config().enabled is True
    revocation._CACHE.clear()  # A new process has an empty cache.
    assert hook_runtime.load_runtime_config().enabled is False


@pytest.mark.parametrize('kind', ['planned', 'corrupt', 'config-drift', 'symlink'])
def test_pending_or_uninspectable_ticket_fails_closed_for_its_home(homes, kind):
    root, first, second, selected = homes
    path = _ticket(root, first, phase='planned' if kind == 'planned' else 'completed')
    if kind == 'corrupt':
        path.write_text('{invalid')
    elif kind == 'config-drift':
        (first / 'config.yaml').write_text('plugins: {}\n')
    elif kind == 'symlink':
        target = path.with_name('other')
        path.rename(target)
        path.symlink_to(target)
    assert revocation.inspect(root, first) in {'pending', 'review'}
    assert hook_runtime.load_runtime_config().enabled is False
    token = selected.set(second)
    try:
        assert hook_runtime.load_runtime_config().enabled is True
    finally:
        selected.reset(token)


def test_full_cache_preserves_existing_home_decisions(homes, monkeypatch):
    root, first, _second, _selected = homes
    monkeypatch.setattr(revocation, '_CACHE_LIMIT', 1)
    assert hook_runtime.load_runtime_config().enabled is True
    other = root.parent / 'third'
    other.mkdir()
    assert revocation._cached_denial(root, other) is False
    _ticket(root, first)
    assert hook_runtime.load_runtime_config().enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_entry', ['emit', 'stage'])
async def test_full_cache_allows_unrevoked_home_and_finishes_owned_turn(
        homes, monkeypatch, terminal_entry):
    root, first, second, selected = homes
    monkeypatch.setattr(revocation, '_CACHE_LIMIT', 1)
    hook_runtime.reset_runtime_state()
    assert hook_runtime.load_runtime_config().enabled is True  # Fill with another HOME.
    token = selected.set(second)
    policy_calls, posted = [], []

    def policy(*_args):
        policy_calls.append(True)
        return {'ok': True, 'disposition': 'card', 'ttl_ms': 0}

    async def post(_url, payload, _timeout):
        posted.append(payload['event'])
        return {'ok': True, 'applied': True}

    monkeypatch.setattr(hook_runtime, '_ensure_runtime_control_started', lambda _config: None)
    monkeypatch.setattr(hook_runtime, '_fetch_delivery_policy_sync', policy)
    monkeypatch.setattr(hook_runtime, '_post_json_response', post)
    turn = {'platform': 'feishu', 'chat_id': 'oc_revocation_overflow',
            'message_id': 'om_revocation_overflow'}
    try:
        assert await hook_runtime.emit_from_hermes_locals_async(turn, 'message.started')
        assert policy_calls == [True]
        _ticket(root, second)
        assert not hook_runtime.load_runtime_config().enabled
        completed = {**turn, 'answer': 'done'}
        if terminal_entry == 'emit':
            assert await hook_runtime.emit_from_hermes_locals_async(completed, 'message.completed')
            assert posted == ['message.started', 'message.completed']
        else:
            assert await hook_runtime.stage_message_completed_from_hermes_locals_async(completed)
            stage = hook_runtime._HFC_EXACT_COMPLETION_STAGE.get()
            assert stage['payload']['event'] == 'message.completed'
            assert posted == ['message.started']
        assert not await hook_runtime.emit_from_hermes_locals_async(
            {**turn, 'message_id': 'om_new_turn'}, 'message.started')
        assert policy_calls == [True]
        revocation._CACHE.clear()  # Simulate process restart.
        assert not hook_runtime.load_runtime_config().enabled
    finally:
        selected.reset(token)
        hook_runtime.reset_runtime_state()


@pytest.mark.asyncio
async def test_revoked_home_cannot_use_another_homes_identical_turn_pin(homes, monkeypatch):
    root, first, second, selected = homes
    hook_runtime.reset_runtime_state()
    policy_calls, posted = [], []

    def policy(*_args):
        policy_calls.append(True)
        return {'ok': True, 'disposition': 'card', 'ttl_ms': 0}

    async def post(_url, payload, _timeout):
        posted.append(payload['event'])
        return {'ok': True, 'applied': True}

    monkeypatch.setattr(hook_runtime, '_ensure_runtime_control_started', lambda _config: None)
    monkeypatch.setattr(hook_runtime, '_fetch_delivery_policy_sync', policy)
    monkeypatch.setattr(hook_runtime, '_post_json_response', post)
    turn = {'platform': 'feishu', 'chat_id': 'same_chat', 'message_id': 'same_message'}
    try:
        assert await hook_runtime.emit_from_hermes_locals_async(turn, 'message.started')
        _ticket(root, second)
        token = selected.set(second)
        try:
            assert not hook_runtime.load_runtime_config().enabled
            assert not await hook_runtime.emit_from_hermes_locals_async(
                {**turn, 'answer': 'other HOME'}, 'message.completed')
            assert not await hook_runtime.stage_message_completed_from_hermes_locals_async(
                {**turn, 'answer': 'other HOME'})
        finally:
            selected.reset(token)
        assert await hook_runtime.emit_from_hermes_locals_async(
            {**turn, 'answer': 'original HOME'}, 'message.completed')
        assert policy_calls == [True]
        assert posted == ['message.started', 'message.completed']
    finally:
        hook_runtime.reset_runtime_state()
