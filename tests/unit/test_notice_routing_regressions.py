"""Regressions from the setup/compaction screenshots in issues 275 and 278."""
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime


@pytest.mark.parametrize('text,kind', [
    ('📬 No home channel is set for Feishu. A home channel is where Hermes delivers cron job results and cross-platform messages.\n\nType /sethome to make this chat your home channel, or ignore to skip.', 'home-channel'),
    ('ℹ️ 上下文压缩已推迟 — 摘要仍在生成中。本回合将不压缩继续。', 'compression'),
])
def test_observed_operational_notices_are_routed_as_notices(text, kind):
    result = hook_runtime._hfc_classify_system_notice(text)
    assert result is not None
    assert result['notice_kind'] == kind


def test_current_event_anchor_wins_over_stale_source_message():
    source = SimpleNamespace(platform='feishu', chat_id='oc_group',
                             thread_id='omt_thread', message_id='om_previous')
    event = SimpleNamespace(source=source, message_id='om_current')
    context = hook_runtime._hfc_notice_context_from_source(source, event=event)
    assert context['message_id'] == 'om_current'
    assert context['thread_id'] == 'omt_thread'


def test_source_without_public_message_id_keeps_bound_incoming_anchor():
    source = SimpleNamespace(platform='feishu', chat_id='oc_group', thread_id='omt_thread')
    event = SimpleNamespace(source=source, message_id='om_current')
    assert hook_runtime.build_event('message.started', {'source': source, 'event': event})
    context = hook_runtime._hfc_notice_context_from_source(source)
    assert context['message_id'] == 'om_current'


def test_opaque_turn_identity_is_not_used_as_feishu_reply_anchor():
    source = SimpleNamespace(platform='feishu', chat_id='oc_group', thread_id='omt_thread')
    setattr(source, hook_runtime._CANONICAL_TURN_ATTR, 'native-turn-opaque')
    assert hook_runtime._hfc_notice_context_from_source(source)['message_id'] == ''


def test_private_setup_notice_keeps_original_private_delivery(monkeypatch):
    adapter = SimpleNamespace(name='feishu', config=SimpleNamespace(extra={'notice_delivery': 'private'}))
    runner = SimpleNamespace(adapters={'feishu': adapter})
    source = SimpleNamespace(platform='feishu', chat_id='oc_group', message_id='om_current', thread_id='omt_thread')
    monkeypatch.setattr(hook_runtime, '_hfc_direct_card_allowed_sync', lambda chat_id: True)
    scheduled = []
    monkeypatch.setattr(hook_runtime, '_hfc_schedule_platform_notice_card', lambda **kwargs: scheduled.append(kwargs))
    assert not hook_runtime.handle_platform_notice_from_hermes(runner, source, '📬 No home channel is set for Feishu. Type /sethome.')
    assert scheduled == []
