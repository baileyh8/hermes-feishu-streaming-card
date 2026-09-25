"""Hermes 0.21.1 split-ledger patcher contracts."""

from pathlib import Path

import pytest

from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_split_ledger_base.py"


RELEASE_GUARD = "            if obligation_id is not None:\n                await self._release_turn_marker(event)\n"
SEND_ANCHOR = "            result = await delivery_adapter._send_with_retry("


def _with_guarded_release(original):
    assert original.count(SEND_ANCHOR) == 1
    return original.replace(SEND_ANCHOR, RELEASE_GUARD + SEND_ANCHOR)


def test_guarded_release_roundtrip():
    original = _with_guarded_release(FIXTURE.read_text())
    installed = patcher.apply_base_patch(original)
    assert patcher.apply_base_patch(installed) == installed
    assert patcher.remove_base_patch(installed) == original
    assert installed.count(RELEASE_GUARD) == 1


@pytest.mark.parametrize('mutation', ['guard', 'event', 'duplicate', 'before_record', 'unguarded', 'after_send'])
def test_guarded_release_rejects_drift(mutation):
    original = _with_guarded_release(FIXTURE.read_text())
    if mutation == 'guard':
        changed = original.replace(RELEASE_GUARD, RELEASE_GUARD.replace('is not None', 'is None'))
    elif mutation == 'event':
        changed = original.replace('_release_turn_marker(event)', '_release_turn_marker(other_event)')
    elif mutation == 'duplicate':
        changed = original.replace(RELEASE_GUARD, RELEASE_GUARD * 2)
    elif mutation == 'unguarded':
        changed = original.replace(RELEASE_GUARD, '            await self._release_turn_marker(event)\n')
    elif mutation == 'before_record':
        changed = original.replace(RELEASE_GUARD, '').replace(
            '            obligation_id = await self._record_delivery_obligation(',
            RELEASE_GUARD + '            obligation_id = await self._record_delivery_obligation(')
    else:
        changed = original.replace(RELEASE_GUARD, '').replace(
            '            return result, delivery_adapter', RELEASE_GUARD + '            return result, delivery_adapter')
    assert changed != original
    compile(changed, 'drifted-ledger', 'exec')
    with pytest.raises(ValueError, match='safe BasePlatformAdapter contract'):
        patcher.apply_base_patch(changed)


def test_split_ledger_install_is_idempotent_and_reversible() -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    installed = patcher.apply_base_patch(original)
    assert "send_final_ledgered" in installed
    assert patcher.apply_base_patch(installed) == installed
    assert patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN in installed
    compile(installed, str(FIXTURE), "exec")
    assert patcher.remove_base_patch(installed) == original


def test_split_ledger_contract_rejects_the_old_inline_ledger_shape() -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    broken = original.replace(
        "async def send_final_ledgered(",
        "async def send_final_ledgered_broken(",
    )
    with pytest.raises(ValueError, match="safe BasePlatformAdapter contract"):
        patcher.apply_base_patch(broken)


ATTACHMENT_KWARG_TAIL = "anything_sent=delivery_attempted or _tts_caption_delivered)"
ATTACHMENT_KWARG_TAIL_EXTENDED = (
    "anything_sent=delivery_attempted or _tts_caption_delivered, "
    "record_delivery=_record_delivery)"
)


def test_split_ledger_accepts_record_delivery_kwarg_on_attachment_anchor() -> None:
    """Hermes passes ``record_delivery=...`` to ``_deliver_attachments``.

    That keyword is orthogonal to the exact-delivery contract, so the anchor must
    still bind and stay reversible instead of failing the whole install.
    """
    original = FIXTURE.read_text(encoding="utf-8")
    assert original.count(ATTACHMENT_KWARG_TAIL) == 1
    extended = original.replace(ATTACHMENT_KWARG_TAIL, ATTACHMENT_KWARG_TAIL_EXTENDED)
    installed = patcher.apply_base_patch(extended)
    assert patcher.apply_base_patch(installed) == installed
    assert patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN in installed
    compile(installed, str(FIXTURE), "exec")
    assert patcher.remove_base_patch(installed) == extended


def test_split_ledger_still_rejects_unknown_attachment_kwarg() -> None:
    """Only known spellings are tolerated; a renamed keyword is still drift."""
    original = FIXTURE.read_text(encoding="utf-8")
    assert ATTACHMENT_KWARG_TAIL in original
    drifted = original.replace(
        ATTACHMENT_KWARG_TAIL,
        ATTACHMENT_KWARG_TAIL.replace("anything_sent=", "anything_sent_renamed="),
    )
    with pytest.raises(ValueError, match="safe BasePlatformAdapter contract"):
        patcher.apply_base_patch(drifted)


@pytest.mark.parametrize(('before', 'after'), [
    ('metadata=metadata)', 'metadata=metadata, **extra)'),
    ('obligation_id, result, event, delivery_adapter)', 'obligation_id, result, event, delivery_adapter, extra=True)'),
    ('if obligation_id is not None:', 'if obligation_id is None:'),
    ('delivery_adapter = self._final_delivery_adapter(event.source)', 'delivery_adapter = self'),
    ('return result, delivery_adapter', 'return result, self'),
    ('result = await delivery_adapter._send_with_retry(', 'return\n            result = await delivery_adapter._send_with_retry('),
])
def test_split_ledger_rejects_delivery_contract_drift(before, after):
    original = FIXTURE.read_text(encoding='utf-8')
    assert before in original
    with pytest.raises(ValueError, match='safe BasePlatformAdapter contract'):
        patcher.apply_base_patch(original.replace(before, after))


@pytest.mark.asyncio
@pytest.mark.parametrize('success', [True, False])
@pytest.mark.parametrize('obligation_id', ['ledger-test', None])
@pytest.mark.parametrize('record_delivery', [True, False])
@pytest.mark.parametrize('guarded_release', [True, False])
async def test_installed_split_ledger_executes_record_hook_send_finalize(
    monkeypatch, success, obligation_id, record_delivery, guarded_release,
):
    import logging
    from types import SimpleNamespace
    from hermes_feishu_card import hook_runtime

    namespace = {'logger': logging.getLogger(__name__)}
    original = FIXTURE.read_text()
    if guarded_release:
        original = _with_guarded_release(original)
    if record_delivery:
        original = original.replace(ATTACHMENT_KWARG_TAIL, ATTACHMENT_KWARG_TAIL_EXTENDED)
    exec(compile(patcher.apply_base_patch(original), str(FIXTURE), 'exec'), namespace)
    adapter = namespace['BasePlatformAdapter']()
    calls = []
    event = SimpleNamespace(source=SimpleNamespace(chat_id='test-chat'))
    result = SimpleNamespace(success=success, message_id='test-message')

    async def record(*args):
        assert args == (event, 'test-session', 'answer', adapter, False)
        calls.append('record')
        return obligation_id

    async def prepare(context):
        assert context['obligation_id'] == obligation_id
        assert context['source'] is event.source
        calls.append('hook')
        return adapter, context['content'], context['reply_to'], context['metadata']

    async def send(**kwargs):
        assert kwargs == dict(chat_id='test-chat', content='answer', reply_to='topic-anchor', metadata={'thread_id': 'test-thread'})
        calls.append('send')
        return result

    async def finalize(*args):
        assert args == (obligation_id, result, event, adapter)
        calls.append('finalize')

    async def release(actual_event):
        assert actual_event is event
        calls.append('release')

    adapter._release_turn_marker = release
    adapter.name = 'test'
    adapter._final_delivery_adapter = lambda source: adapter
    adapter._record_delivery_obligation = record
    adapter._send_with_retry = send
    adapter._finalize_delivery_obligation = finalize
    monkeypatch.setattr(hook_runtime, 'prepare_decomposed_base_final_delivery', prepare)
    actual = await adapter.send_final_ledgered(event, 'test-session', 'answer', {'thread_id': 'test-thread'}, reply_to='topic-anchor')
    assert actual == (result, adapter)
    assert calls == (['record'] + (['release'] if guarded_release and obligation_id else [])
                     + ['hook', 'send'] + (['finalize'] if obligation_id else []))


def test_installed_split_ledger_is_detected_for_staging(monkeypatch):
    import sys
    from types import SimpleNamespace
    from hermes_feishu_card import hook_runtime
    namespace = {}
    exec(compile(patcher.apply_base_patch(FIXTURE.read_text()), str(FIXTURE), 'exec'), namespace)
    adapter = namespace['BasePlatformAdapter']
    monkeypatch.setitem(sys.modules, 'gateway.platforms.base', SimpleNamespace(BasePlatformAdapter=adapter))
    assert hook_runtime.can_stage_exact_base_completion({
        'platform': 'feishu', 'answer': 'answer', 'agent_result': {},
    })


@pytest.mark.parametrize('fixture_name', ['hermes_split_ledger_base.py', 'hermes_decomposed/gateway/platforms/base.py'])
@pytest.mark.parametrize('tail', [
    'record_delivery=other_callback)',
    'record_delivery=_record_delivery, unexpected=True)',
    '**extra)',
    'record_delivery=_record_delivery)\n                await self._deliver_attachments(event, extracted, _final_thread_metadata, anything_sent=delivery_attempted or _tts_caption_delivered)',
    'record_delivery=_record_delivery)\n                await self._deliver_attachments(event, extracted, _final_thread_metadata, anything_sent=False)',
])
def test_attachment_anchor_rejects_unknown_or_ambiguous_calls(fixture_name, tail):
    original = (FIXTURE.parent / fixture_name).read_text()
    changed = original.replace(ATTACHMENT_KWARG_TAIL, ATTACHMENT_KWARG_TAIL[:-1] + ', ' + tail)
    with pytest.raises(ValueError, match='safe BasePlatformAdapter contract'):
        patcher.apply_base_patch(changed)
