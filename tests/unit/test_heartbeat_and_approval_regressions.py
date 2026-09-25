"""Regression boundaries extracted from #344/#345 without fork defaults."""
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher
from hermes_feishu_card.render import render_legacy_interaction_callback_card
from hermes_feishu_card.session import CardSession, InteractionState, InteractionOption
from tests.unit.test_long_running_recall_patch import SOURCE, HEARTBEAT


@pytest.mark.asyncio
async def test_three_heartbeat_cycles_keep_one_editable_message(monkeypatch):
    live = set()
    recalls = []

    async def recall(message_id, **kwargs):
        recalls.append(message_id)
        live.discard(message_id)  # Simulate expiry before the next 180-second tick.
        return True

    monkeypatch.setattr(hook_runtime, 'schedule_message_recall_async', recall)

    class Adapter:
        sends = 0
        edits = 0

        async def send(self, chat_id, text):
            self.sends += 1
            message_id = f'heartbeat-{self.sends}'
            live.add(message_id)
            return SimpleNamespace(success=True, message_id=message_id)

        async def edit_message(self, chat_id, message_id, text):
            self.edits += 1
            return SimpleNamespace(success=message_id in live, message_id=message_id)

    source = SOURCE.replace('        while True:',
        f'        _heartbeat_msg_id = None\n        _heartbeat_text = {HEARTBEAT!r}\n'
        '        cleanup = False\n        cleanup_ids = []\n        for _ in range(3):')
    namespace = {}
    exec(compile(patcher._apply_long_running_recall_patch(source), '<heartbeat>', 'exec'), namespace)
    adapter = Adapter()
    await namespace['Gateway']()._notify_long_running(
        adapter, SimpleNamespace(platform='feishu', chat_id='fixture-chat'))
    assert adapter.sends == 1
    assert adapter.edits == 2
    assert recalls == []


@pytest.mark.parametrize('count', [2, 4, 7])
def test_approval_compact_columns_preserve_callback_and_full_scope(count):
    session = CardSession(conversation_id='c', message_id='m', chat_id='fixture-chat')
    session.active_interaction = InteractionState(
        interaction_id='approval-fixture', kind='approval', prompt='Review operation',
        description='echo harmless-display-fixture', callback_token='fixture-token',
        options=[InteractionOption('Option ' + str(i), str(i)) for i in range(count)],
    )
    card = render_legacy_interaction_callback_card(
        session, interaction_profile_id='fixture-profile')
    rows = [e for e in card['elements'] if e['tag'] == 'column_set']
    assert len(rows) == (count + 3) // 4
    buttons = [c['elements'][0] for row in rows for c in row['columns']]
    assert [b['value']['choice'] for b in buttons] == list(map(str, range(count)))
    for row in rows:
        assert row['flex_mode'] == 'flow'
        assert all(c['width'] == 'auto' for c in row['columns'])
    for button in buttons:
        assert button['value']['token'] == 'fixture-token'
        assert button['value']['profile_id'] == 'fixture-profile'
        assert button['width'] == 'default'
        assert 'behaviors' not in button
    assert 'echo harmless-display-fixture' in str(card)
