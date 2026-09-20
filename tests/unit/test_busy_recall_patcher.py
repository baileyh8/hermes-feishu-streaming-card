from types import SimpleNamespace
import pytest
from hermes_feishu_card.install import patcher

SOURCE = '''class Gateway:
    async def _send_busy_reply(self, event, content):
        adapter = self.adapter
        await adapter._send_with_retry(
            chat_id=event.chat_id,
            content=content,
        )
'''

@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_busy_recall_restores_original_send_byte_for_byte(newline):
    original = SOURCE.replace('\n', newline)
    patched = patcher._apply_busy_recall_patch(original)
    assert patched != original
    assert patcher._apply_busy_recall_patch(patched) == patched
    assert patcher.remove_patch(patched) == original
    assert patcher.remove_patch_lenient(patched) == original


def test_busy_recall_rejects_corrupt_owned_capture():
    patched = patcher._apply_busy_recall_patch(SOURCE)
    damaged = patched.replace('_hfc_recall_ack(event, content, _hfc_recall_result)', '_hfc_recall_ack(event, "different", _hfc_recall_result)')
    with pytest.raises(ValueError, match='busy recall'):
        patcher.remove_patch(damaged)
    with pytest.raises(ValueError, match='busy recall'):
        patcher._apply_busy_recall_patch(damaged)


def test_busy_recall_leaves_ambiguous_or_unterminated_send_unchanged():
    no_newline = SOURCE.rstrip('\n')
    assert patcher._apply_busy_recall_patch(no_newline) == no_newline
    ambiguous = SOURCE + '        await adapter._send_with_retry(chat_id=event.chat_id, content=content)\n'
    assert patcher._apply_busy_recall_patch(ambiguous) == ambiguous


@pytest.mark.asyncio
async def test_busy_recall_only_schedules_successful_redirect_ack(monkeypatch):
    from hermes_feishu_card import hook_runtime
    calls=[]
    async def schedule(*args, **kwargs):
        calls.append((args,kwargs));return True
    monkeypatch.setattr(hook_runtime, 'schedule_message_recall_async', schedule)
    event=SimpleNamespace(source=SimpleNamespace(platform='feishu'))
    text="↪ Redirected current run. I'll adjust using your correction."
    assert not await hook_runtime.recall_busy_redirect_ack_async(event,text,SimpleNamespace(success=False,message_id='om_ack'))
    assert not await hook_runtime.recall_busy_redirect_ack_async(event,'normal answer',SimpleNamespace(success=True,message_id='om_answer'))
    assert await hook_runtime.recall_busy_redirect_ack_async(event,text,SimpleNamespace(success=True,message_id='om_ack'))
    assert len(calls)==1 and calls[0][0]==('om_ack',)
