import ast

import pytest

from hermes_feishu_card.install.gateway_contracts import find_stale_interrupted_location


RUN = 'agent_result = await self._run_agent(source=source, session_key=session_key, run_generation=run_generation, event_message_id=self._reply_anchor_for_event(event))'
GUARD = ('if not self._is_session_run_current(_quick_key, run_generation):\n'
         '    self._hmwa_discard_stale_result(source, _quick_key, run_generation)\n'
         '    return None')
PIN = '_turn_channel_prompt, _turn_source = self._pinned_channel_inputs(session_key, event.channel_prompt, source, internal=event.internal)'


def parse(body):
    text = 'async def handler(self):\n' + '\n'.join('    '+line for line in body.splitlines()) + '\n'
    return ast.parse(text).body[0], text.splitlines(keepends=True)


def locate(body):
    return find_stale_interrupted_location(*parse(body))


def test_exact_location_and_no_mutation():
    handler, lines = parse(RUN+'\n'+GUARD)
    before = ast.dump(handler, include_attributes=True), list(lines)
    assert find_stale_interrupted_location(handler, lines) == (3, '        ')
    assert (ast.dump(handler, include_attributes=True), lines) == before
    assert find_stale_interrupted_location(None, []) is None
    assert locate('return None') is None


@pytest.mark.parametrize('body,reason', [
    (RUN+'\n'+GUARD+'\n'+GUARD, 'discard contract'),
    (RUN+'\n'+GUARD.replace('return None','return agent_result'), 'discard contract'),
    (GUARD+'\n'+RUN, 'assignment'),
    (RUN+'\n'+RUN+'\n'+GUARD, 'assignment'),
    ('if ready:\n    '+RUN+'\n'+GUARD, 'assignment'),
    (RUN.replace('agent_result =','agent_result: dict =')+'\n'+GUARD, 'runner binding'),
    (RUN.replace('agent_result =','agent_result +=')+'\n'+GUARD, 'runner binding'),
    (RUN.replace('await ','')+'\n'+GUARD, 'runner binding'),
    (RUN.replace('source=source','source=other')+'\n'+GUARD, 'runner binding'),
    (RUN.replace('source=source','**parameters')+'\n'+GUARD, 'runner binding'),
])
def test_ambiguous_malformed_or_reordered_contract_is_rejected(body, reason):
    with pytest.raises(ValueError, match='stale result '+reason):
        locate(body)


def test_shared_ast_guard_in_two_blocks_rejects_control_flow_ambiguity():
    handler, lines = parse(RUN+'\n'+GUARD)
    guard = handler.body[-1]
    # Deliberately malformed graph: the same node has two owning statement lists.
    handler.extra_block = [guard]
    handler._fields = (*handler._fields, 'extra_block')
    # ast.walk now sees the guard twice, so the earlier exact-count gate rejects.
    with pytest.raises(ValueError, match='stale result discard contract changed'):
        find_stale_interrupted_location(handler, lines)


@pytest.mark.parametrize('mutation', ['missing', 'rebound', 'late', 'partial', 'wrong-prompt'])
@pytest.mark.parametrize('internal', [
    'event.internal',
    'event.internal or getattr(event, "inherit_channel_pin", False)',
])
def test_pinned_source_requires_original_complete_preceding_tuple(mutation, internal):
    run = RUN.replace('source=source', 'source=_turn_source').replace('event))','event), channel_prompt=_turn_channel_prompt)')
    pin = PIN.replace('event.internal)', internal+')')
    if mutation == 'missing': pin = ''
    if mutation == 'rebound': pin += '\n_turn_source = other'
    if mutation == 'partial': pin = pin.replace('_turn_channel_prompt, _turn_source', '_turn_source')
    if mutation == 'wrong-prompt': pin = pin.replace('event.channel_prompt', 'other_prompt')
    body = pin+'\n'+run+'\n'+GUARD
    if mutation == 'late': body = run+'\n'+pin+'\n'+GUARD
    with pytest.raises(ValueError, match='stale result pinned source binding changed'):
        locate(body)


@pytest.mark.parametrize('internal', [
    'event.internal',
    'event.internal or getattr(event, "inherit_channel_pin", False)',
])
def test_complete_pinned_tuple_accepts_only_exact_bindings(internal):
    run = RUN.replace('source=source', 'source=_turn_source').replace('event))','event), channel_prompt=_turn_channel_prompt)')
    assert locate(PIN.replace('event.internal)', internal+')')+'\n'+run+'\n'+GUARD) == (4, '        ')


@pytest.mark.parametrize('internal', [
    'True', 'event.internal or other',
    'event.internal or getattr(event, "inherit_channel_pin", True)',
    'getattr(event, "inherit_channel_pin", False)',
    'event.internal and getattr(event, "inherit_channel_pin", False)',
    'event.internal or getattr(other, "inherit_channel_pin", False)',
    'event.internal or getattr(event, "other_flag", False)',
    'event.internal or getattr(event, "inherit_channel_pin", False) or other',
])
def test_unknown_pin_inheritance_is_rejected(internal):
    run = RUN.replace('source=source', 'source=_turn_source')
    with pytest.raises(ValueError, match='pinned source binding changed'):
        locate(PIN.replace('event.internal)', internal+')')+'\n'+run+'\n'+GUARD)


SETUP_GUARD = '''
async def run_preagent_setup(runner, event, source, quick_key: str, run_generation: int):
    resolved = await runner._hmwa_resolve_session(event, source)
    if resolved is None:
        return None, None
    source, session_entry, session_key = resolved
    prepared, env_tokens = await runner._hmwa_prepare_turn(
        event, source, session_entry, session_key, quick_key, run_generation,
    )
    hook_ctx = {"session_id": session_entry.session_id}
    if not heartbeat_owner_is_current(runner, event, session_key):
        return (source, session_entry, session_key, prepared, env_tokens, None, None), None
    channel = runner._pinned_channel_inputs(
        session_key, event.channel_prompt, source,
        internal=event.internal or getattr(event, "inherit_channel_pin", False))
    return (source, session_entry, session_key, prepared, env_tokens, hook_ctx, channel), None
'''

SETUP_HANDLER = '''
async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):
    from gateway.run_turn_setup_guard import run_preagent_setup
    setup, reply = await run_preagent_setup(self, event, source, _quick_key, run_generation)
    if setup is None:
        return reply
    source, session_entry, session_key, prepared, _session_env_tokens, hook_ctx, channel = setup
    if hook_ctx is None:
        self._clear_session_env(_session_env_tokens)
        return None
    _turn_channel_prompt, _turn_source = channel
    agent_result = await self._run_agent(
        source=_turn_source, session_key=session_key, run_generation=run_generation,
        event_message_id=self._reply_anchor_for_event(event),
        channel_prompt=_turn_channel_prompt)
    if not self._is_session_run_current(_quick_key, run_generation):
        self._hmwa_discard_stale_result(source, _quick_key, run_generation)
        return None
'''


def _setup_peers(guard=SETUP_GUARD):
    return {'gateway/run_turn_setup_guard.py': guard}


def locate_setup(handler_src=SETUP_HANDLER, guard=SETUP_GUARD):
    tree = ast.parse(handler_src)
    handler = tree.body[0]
    lines = handler_src.splitlines(keepends=True)
    return find_stale_interrupted_location(handler, lines, peer_sources=_setup_peers(guard))


def test_setup_guard_channel_pin_is_accepted():
    location = locate_setup()
    assert location is not None
    index, indent = location
    assert index >= 0
    assert indent


@pytest.mark.parametrize('mutation', [
    'other-setup',
    'rebind-source',
    'foreign-channel',
    'drop-hook-guard',
    'other-session-pin',
    'missing-peer',
    'unknown-internal',
])
def test_setup_guard_channel_pin_rejects_drift(mutation):
    handler = SETUP_HANDLER
    guard = SETUP_GUARD
    peers = _setup_peers(guard)
    if mutation == 'other-setup':
        handler = handler.replace(
            'run_preagent_setup(self, event, source, _quick_key, run_generation)',
            'run_preagent_setup(self, event, other_source, _quick_key, run_generation)',
        )
    elif mutation == 'rebind-source':
        handler = handler.replace(
            '_turn_channel_prompt, _turn_source = channel',
            '_turn_channel_prompt, _turn_source = channel\n    _turn_source = other',
        )
    elif mutation == 'foreign-channel':
        handler = handler.replace(
            '_turn_channel_prompt, _turn_source = channel',
            '_turn_channel_prompt, _turn_source = other_channel',
        )
    elif mutation == 'drop-hook-guard':
        handler = handler.replace(
            '    if hook_ctx is None:\n        self._clear_session_env(_session_env_tokens)\n        return None\n',
            '',
        )
    elif mutation == 'other-session-pin':
        guard = guard.replace('session_key, event.channel_prompt, source,',
                              'other_key, event.channel_prompt, source,')
        peers = _setup_peers(guard)
    elif mutation == 'missing-peer':
        peers = {}
    elif mutation == 'unknown-internal':
        guard = guard.replace(
            'internal=event.internal or getattr(event, "inherit_channel_pin", False)',
            'internal=True',
        )
        peers = _setup_peers(guard)
    tree = ast.parse(handler)
    lines = handler.splitlines(keepends=True)
    with pytest.raises(ValueError, match='pinned source binding changed'):
        find_stale_interrupted_location(tree.body[0], lines, peer_sources=peers)
