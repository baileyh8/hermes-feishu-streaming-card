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
def test_pinned_source_requires_original_complete_preceding_tuple(mutation):
    run = RUN.replace('source=source', 'source=_turn_source').replace('event))','event), channel_prompt=_turn_channel_prompt)')
    pin = PIN
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
])
def test_unknown_pin_inheritance_is_rejected(internal):
    run = RUN.replace('source=source', 'source=_turn_source')
    with pytest.raises(ValueError, match='pinned source binding changed'):
        locate(PIN.replace('event.internal)', internal+')')+'\n'+run+'\n'+GUARD)
