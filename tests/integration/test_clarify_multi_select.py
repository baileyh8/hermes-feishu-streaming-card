"""Integration tests for clarify multi-select + free-text 'Other' support.

Covers:
- interaction.requested with multi_select=true renders a native
  multi_select_static form with ONE confirm button (+ Other input)
- single-select renders choice buttons + an Other form
- /card/actions form submits (identified via button name, no behaviors):
  hfc_confirm_<id> (multi-select JSON array, typed text wins) and
  hfc_other_<id> (typed free text) complete the interaction correctly
- legacy direct button clicks still work
- interaction.noop callbacks from selection components are acknowledged
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.native_handoff import NativeHandoffStore


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
        self.sent.append((chat_id, card, thread_id, reply_to_message_id))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, card))


def event_payload(
    event,
    sequence,
    data=None,
    *,
    conversation_id="conversation-1",
    message_id="hermes-message-1",
    chat_id="oc_abc",
):
    payload = {
        "schema_version": "1",
        "event": event,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0 + sequence,
        "data": dict(data or {}),
    }
    return payload


def form_action_payload(interaction_id, *, button_name=None, form_value=None, hfc_action=""):
    """Form-submit callback: buttons carry NO behaviors, so the callback has
    an empty value dict and the interaction is in the button name."""
    action = {"tag": "button", "value": {}, "name": button_name or f"hfc_confirm_{interaction_id}"}
    if form_value is not None:
        action["form_value"] = form_value
    if hfc_action:
        action["value"] = {"hfc_action": hfc_action}
    return {
        "schema": "2.0",
        "event": {
            "operator": {"open_id": "ou_test", "name": "测试用户"},
            "action": action,
            "context": {"open_chat_id": "oc_abc", "open_message_id": "om_x"},
        },
    }


def button_action_payload(interaction_id, token, *, choice=None, choice_label=None, hfc_action="interaction.select"):
    """Legacy direct-choice button callback (single-select)."""
    value = {"hfc_action": hfc_action, "interaction_id": interaction_id, "token": token}
    if choice is not None:
        value["choice"] = choice
    if choice_label is not None:
        value["choice_label"] = choice_label
    return {
        "schema": "2.0",
        "event": {
            "operator": {"open_id": "ou_test", "name": "测试用户"},
            "action": {"tag": "button", "value": value, "name": "hfc_btn_0"},
            "context": {"open_chat_id": "oc_abc", "open_message_id": "om_x"},
        },
    }


@pytest.fixture
async def client(tmp_path):
    feishu_client = FakeFeishuClient()
    app = sidecar_server.create_app(
        feishu_client,
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff-state"),
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client, app, feishu_client
    finally:
        await test_client.close()


async def request_interaction(client, interaction_id, *, multi_select=False, options=None, kind="clarify", prompt="请选择", token=None):
    test_client, app, _ = client
    data = {
        "interaction_id": interaction_id,
        "kind": kind,
        "prompt": prompt,
        "options": options or [
            {"label": "选项A", "value": "A"},
            {"label": "选项B", "value": "B"},
        ],
        "multi_select": multi_select,
    }
    if token:
        data["callback_token"] = token
    response = await test_client.post(
        "/events", json=event_payload("interaction.requested", 1, data)
    )
    assert response.status == 200
    return app


def find_elements(elements, tag):
    """Recursively find components by tag (forms nest their children)."""
    found = []
    for element in elements:
        if element.get("tag") == tag:
            found.append(element)
        for key in ("elements", "columns"):
            nested = element.get(key)
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        found.extend(find_elements([item], tag))
    return found


async def test_multi_select_request_renders_single_confirm_button_form(client):
    app = await request_interaction(client, "clarify-ms-1", multi_select=True)

    session_key, session = next(iter(app[sidecar_server.SESSIONS_KEY].items()))
    interaction = session.active_interaction
    assert interaction is not None
    assert interaction.multi_select is True

    rendered = sidecar_server._render_session_card_for_app(app, session)
    elements = rendered["body"]["elements"]

    multi = find_elements(elements, "multi_select_static")
    assert multi, "multi_select_static component missing"
    assert multi[0]["name"] == "hfc_multi"
    assert [o["value"] for o in multi[0]["options"]] == ["A", "B"]

    buttons = find_elements(elements, "button")
    labels = [b["text"]["content"] for b in buttons]
    # ONE submit button only (user requirement)
    assert labels == ["✅ 确认选择"]
    confirm = buttons[0]
    assert confirm["form_action_type"] == "submit"
    assert confirm["name"] == "hfc_confirm_clarify-ms-1"
    # form-submit buttons must NOT carry behaviors callbacks
    assert "behaviors" not in confirm

    # Other input present
    inputs = find_elements(elements, "input")
    assert inputs and inputs[0]["name"] == "hfc_other"

    # noop behavior on the multi select so selection changes don't error
    assert multi[0]["behaviors"][0]["value"]["hfc_action"] == "interaction.noop"


async def test_single_select_request_renders_buttons_plus_other_form(client):
    app = await request_interaction(client, "clarify-ss-1", multi_select=False)

    session = next(iter(app[sidecar_server.SESSIONS_KEY].values()))
    assert session.active_interaction.multi_select is False

    rendered = sidecar_server._render_session_card_for_app(app, session)
    elements = rendered["body"]["elements"]

    buttons = find_elements(elements, "button")
    labels = [b["text"]["content"] for b in buttons]
    assert labels[:2] == ["1. 选项A", "2. 选项B"]
    assert "✏️ 提交自定义答案" in labels
    assert "✅ 确认选择" not in labels

    # Other input present
    inputs = find_elements(elements, "input")
    assert inputs and inputs[0]["name"] == "hfc_other"

    # choice buttons keep their behaviors callbacks (they work)
    assert buttons[0]["behaviors"][0]["value"]["choice"] == "A"


async def test_confirm_form_submit_completes_with_json_array(client):
    app = await request_interaction(client, "clarify-ms-2", multi_select=True)

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=form_action_payload(
            "clarify-ms-2",
            button_name="hfc_confirm_clarify-ms-2",
            form_value={"hfc_multi": ["A", "B"], "hfc_other": ""},
        ),
    )
    assert response.status == 200
    body = await response.json()
    assert body["ok"] is True

    result_response = await test_client.get("/interactions/clarify-ms-2")
    result = await result_response.json()
    assert result["status"] == "completed"
    assert json.loads(result["choice"]) == ["A", "B"]
    assert result["choice_label"] == "A, B"


async def test_confirm_form_submit_typed_text_merges_with_selections(client):
    """Single confirm button: typed custom answer merges with selections
    as '[自定义] <text>' (user requirement: keep BOTH)."""
    app = await request_interaction(client, "clarify-ms-4", multi_select=True)

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=form_action_payload(
            "clarify-ms-4",
            button_name="hfc_confirm_clarify-ms-4",
            form_value={"hfc_multi": ["A"], "hfc_other": "自定义答案"},
        ),
    )
    assert response.status == 200
    result_response = await test_client.get("/interactions/clarify-ms-4")
    result = await result_response.json()
    assert result["status"] == "completed"
    assert json.loads(result["choice"]) == ["A", "[自定义] 自定义答案"]
    assert result["choice_label"] == "A, [自定义] 自定义答案"


async def test_confirm_form_submit_typed_text_only_when_no_selection(client):
    """Typed text without selections stays a plain string answer."""
    app = await request_interaction(client, "clarify-ms-5", multi_select=True)

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=form_action_payload(
            "clarify-ms-5",
            button_name="hfc_confirm_clarify-ms-5",
            form_value={"hfc_multi": [], "hfc_other": "只有自定义"},
        ),
    )
    assert response.status == 200
    result_response = await test_client.get("/interactions/clarify-ms-5")
    result = await result_response.json()
    assert result["status"] == "completed"
    assert result["choice"] == "只有自定义"


async def test_confirm_form_submit_empty_selection_completes_with_empty_array(client):
    app = await request_interaction(client, "clarify-ms-3", multi_select=True)

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=form_action_payload(
            "clarify-ms-3",
            button_name="hfc_confirm_clarify-ms-3",
            form_value={"hfc_multi": [], "hfc_other": ""},
        ),
    )
    assert response.status == 200
    result_response = await test_client.get("/interactions/clarify-ms-3")
    result = await result_response.json()
    assert json.loads(result["choice"]) == []
    assert result["choice_label"] == "(未选择)"


async def test_other_form_submit_completes_with_typed_text(client):
    app = await request_interaction(client, "clarify-ot-1", multi_select=False)

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=form_action_payload(
            "clarify-ot-1",
            button_name="hfc_other_clarify-ot-1",
            form_value={"hfc_other": "自定义答案内容", "hfc_multi": []},
        ),
    )
    assert response.status == 200
    result_response = await test_client.get("/interactions/clarify-ot-1")
    result = await result_response.json()
    assert result["status"] == "completed"
    assert result["choice"] == "自定义答案内容"


async def test_other_form_submit_empty_input_rejected(client):
    app = await request_interaction(client, "clarify-ot-2", multi_select=False)

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=form_action_payload(
            "clarify-ot-2",
            button_name="hfc_other_clarify-ot-2",
            form_value={"hfc_other": "   ", "hfc_multi": []},
        ),
    )
    assert response.status == 400
    # interaction must still be pending
    result_response = await test_client.get("/interactions/clarify-ot-2")
    assert (await result_response.json())["status"] == "pending"


async def test_legacy_direct_button_click_still_works(client):
    app = await request_interaction(client, "clarify-ss-2", multi_select=False)
    session = next(iter(app[sidecar_server.SESSIONS_KEY].values()))
    token = session.active_interaction.callback_token

    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=button_action_payload(
            "clarify-ss-2", token, choice="A", choice_label="选项A"
        ),
    )
    assert response.status == 200
    result_response = await test_client.get("/interactions/clarify-ss-2")
    result = await result_response.json()
    assert result["status"] == "completed"
    assert result["choice"] == "A"
    assert result["choice_label"] == "选项A"


async def test_noop_callback_acknowledged_quietly(client):
    test_client, _, _ = client
    response = await test_client.post(
        "/card/actions",
        json=button_action_payload(
            "clarify-ms-x", "tok", hfc_action="interaction.noop"
        ),
    )
    assert response.status == 200
    body = await response.json()
    assert body["ok"] is True
