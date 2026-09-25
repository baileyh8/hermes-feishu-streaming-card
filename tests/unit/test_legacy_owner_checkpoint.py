from hermes_feishu_card.session import CardSession
from hermes_feishu_card.session_store import SessionStore
import json


def test_checkpoint_refuses_legacy_receipt_with_callback_controls(tmp_path):
    store = SessionStore(tmp_path)
    session = CardSession(conversation_id="c", message_id="m", chat_id="chat")
    session.legacy_owner_receipt = {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "Question"}},
        "elements": [{"tag": "action", "actions": [{"tag": "button", "value": {"token": "old-token"}}]}],
    }
    store.save("turn", session, "legacy-message", None, "", {}, "fixture-identity")
    assert store.load() == []


def test_ordinary_checkpoint_omits_new_empty_fields_for_v463_rollback(tmp_path):
    store = SessionStore(tmp_path)
    session = CardSession(conversation_id="c", message_id="m", chat_id="chat")
    store.save("turn", session, "body-message", None, "", {}, "fixture-identity")
    record = json.loads(next(store.root.glob("*.json")).read_text())["record"]
    assert "display_segment" not in record["session"]
    assert "legacy_owner_receipt" not in record["session"]
    restored = store.load()[0]["session"]
    assert restored.display_segment == {} and restored.legacy_owner_receipt == {}
