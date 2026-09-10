"""Hermes 0.21.1 split-ledger patcher contracts."""

from pathlib import Path

import pytest

from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_split_ledger_base.py"


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
async def test_installed_split_ledger_executes_record_hook_send_finalize(
    monkeypatch, success, obligation_id,
):
    import logging
    from types import SimpleNamespace
    from hermes_feishu_card import hook_runtime

    namespace = {'logger': logging.getLogger(__name__)}
    exec(compile(patcher.apply_base_patch(FIXTURE.read_text()), str(FIXTURE), 'exec'), namespace)
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

    adapter.name = 'test'
    adapter._final_delivery_adapter = lambda source: adapter
    adapter._record_delivery_obligation = record
    adapter._send_with_retry = send
    adapter._finalize_delivery_obligation = finalize
    monkeypatch.setattr(hook_runtime, 'prepare_decomposed_base_final_delivery', prepare)
    actual = await adapter.send_final_ledgered(event, 'test-session', 'answer', {'thread_id': 'test-thread'}, reply_to='topic-anchor')
    assert actual == (result, adapter)
    assert calls == ['record', 'hook', 'send'] + (['finalize'] if obligation_id else [])
