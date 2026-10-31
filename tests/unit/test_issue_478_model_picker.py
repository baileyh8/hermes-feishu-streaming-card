from __future__ import annotations

import asyncio
import json
import math
import time
from types import SimpleNamespace

import pytest

from hermes_feishu_card import command_cards, hook_runtime


PROVIDERS = [
    {
        "slug": "magpie",
        "name": "Magpie",
        "models": ["gpt-5.5", "deepseek-v4-pro"],
    },
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "models": ["openai/gpt-5.5"],
    },
]


def callback(*, chat: str | None = None, message: str = "om_picker", operator: str = "ou_owner"):
    context = SimpleNamespace(open_message_id=message)
    if chat is not None:
        context.open_chat_id = chat
    return SimpleNamespace(
        event=SimpleNamespace(
            context=context,
            operator=SimpleNamespace(open_id=operator, user_id="user-1"),
        )
    )


class Adapter:
    _app_id = "cli_bound_bot"
    _loop = object()

    def _loop_accepts_callbacks(self, loop):
        return loop is self._loop

    def _allow_group_message(self, *_args, **_kwargs):
        return False


def pending(**changes):
    value = {
        "chat_id": "oc_dm",
        "chat_type": "dm",
        "operator_open_id": "ou_owner",
        "bot_app_id": "cli_bound_bot",
        "message_id": "om_picker",
        "expires_at": time.monotonic() + 60,
        "session_key": "session",
        "providers": PROVIDERS,
        "current_provider": "magpie",
        "current_model": "gpt-5.5",
        "selected_provider": "",
        "on_model_selected": None,
    }
    for key, value_to_set in changes.items():
        if value_to_set is _MISSING:
            value.pop(key, None)
        else:
            value[key] = value_to_set
    return value


_MISSING = object()


def test_provider_stage_has_no_noop_initial_selection_and_requests_multi_update():
    card = command_cards.hfc_native_model_picker_card(
        picker_id="picker-1",
        providers=PROVIDERS,
        current_provider="magpie",
        current_model="gpt-5.5",
    )

    select = card["elements"][1]["actions"][0]
    assert "initial_option" not in select
    assert card["config"] == {"wide_screen_mode": True, "update_multi": True}


@pytest.mark.parametrize(
    "changes",
    [
        {"chat_id": _MISSING},
        {"chat_type": "group"},
        {"operator_open_id": "ou_other"},
        {"bot_app_id": "cli_other_bot"},
        {"bot_app_id": _MISSING},
        {"message_id": "om_other"},
        {"message_id": _MISSING},
        {"expires_at": time.monotonic() - 1},
        {"expires_at": math.inf},
        {"expires_at": "not-a-time"},
        {"expires_at": _MISSING},
    ],
)
def test_missing_chat_model_callback_rejects_incomplete_or_stale_pending(changes):
    adapter = Adapter()
    adapter._hfc_model_picker_state = {"picker-1": pending(**changes)}

    assert hook_runtime._hfc_prepare_native_model_action(
        adapter,
        callback(chat=None),
        {"hfc_model_picker_id": "picker-1", "hfc_model_picker_view": "providers"},
    ) is None


def test_missing_chat_model_callback_accepts_only_exact_pending_binding():
    adapter = Adapter()
    adapter._hfc_model_picker_state = {"picker-1": pending()}

    prepared = hook_runtime._hfc_prepare_native_model_action(
        adapter,
        callback(chat=None),
        {"hfc_model_picker_id": "picker-1", "hfc_model_picker_view": "providers"},
    )

    assert prepared is not None
    assert prepared["chat_id"] == ""
    assert prepared["expected_chat_id"] == "oc_dm"


def test_send_snapshots_bot_and_command_expiry_for_later_callback(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return True

    async def send(**_kwargs):
        return SimpleNamespace(success=True, message_id="om_picker")

    monkeypatch.setattr(hook_runtime, "_hfc_direct_card_allowed_async", allowed)
    adapter = Adapter()
    adapter._client = object()
    adapter._feishu_send_with_retry = send
    expires_at = time.monotonic() + 60
    token = hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(
        {
            "chat_id": "oc_dm",
            "chat_type": "dm",
            "operator_open_id": "ou_owner",
            "expires_at": expires_at,
        }
    )
    try:
        result = asyncio.run(
            hook_runtime._hfc_send_native_model_picker(
                adapter,
                "oc_dm",
                PROVIDERS,
                current_model="gpt-5.5",
                current_provider="magpie",
                session_key="session",
            )
        )
    finally:
        hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.reset(token)

    assert result.success is True
    item = next(iter(adapter._hfc_model_picker_state.values()))
    assert item["bot_app_id"] == "cli_bound_bot"
    assert item["expires_at"] == expires_at
    assert hook_runtime._hfc_prepare_native_model_action(
        adapter,
        callback(chat=None),
        {"hfc_model_picker_id": next(iter(adapter._hfc_model_picker_state))},
    ) is not None


def test_missing_chat_exception_is_not_added_to_other_interactions(monkeypatch):
    adapter = Adapter()
    item = pending()
    adapter._hfc_slash_confirm_state = {"confirm-1": item}
    adapter._hfc_model_picker_state = {"picker-1": item}

    assert hook_runtime._hfc_card_operator_allowed(
        adapter, callback(chat=None), "oc_dm", pending=item
    ) is False
    assert hook_runtime._hfc_prepare_native_slash_action(
        adapter,
        callback(chat=None),
        {"hfc_confirm_id": "confirm-1", "hfc_choice": "once"},
        claim=True,
    ) is None
    assert "confirm-1" in adapter._hfc_slash_confirm_state


def test_expiry_snapshot_keeps_original_slash_chat_and_claim_path():
    adapter = Adapter()
    token = hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(
        {
            "chat_id": "oc_dm",
            "chat_type": "dm",
            "operator_open_id": "ou_owner",
            "expires_at": time.monotonic() + 60,
        }
    )
    try:
        identity = hook_runtime._hfc_pending_command_identity("oc_dm")
    finally:
        hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.reset(token)
    adapter._hfc_slash_confirm_state = {
        "confirm-1": {
            **identity,
            "chat_id": "oc_dm",
            "message_id": "om_confirm",
            "session_key": "session",
        }
    }

    prepared = hook_runtime._hfc_prepare_native_slash_action(
        adapter,
        callback(chat="oc_dm"),
        {"hfc_confirm_id": "confirm-1", "hfc_choice": "once"},
        claim=True,
    )

    assert prepared is not None
    assert "confirm-1" not in adapter._hfc_slash_confirm_state


def test_missing_chat_model_navigation_renders_selected_provider(monkeypatch):
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_raw_feishu_callback_response",
        lambda _adapter, card: card,
    )
    adapter = Adapter()
    adapter._hfc_model_picker_state = {"picker-1": pending()}

    card = hook_runtime._hfc_handle_native_model_action(
        adapter,
        callback(chat=None),
        {
            "hfc_model_picker_id": "picker-1",
            "hfc_model_picker_view": "providers",
            "hfc_choice": "magpie",
        },
    )

    assert card["header"]["title"]["content"] == "选择模型 · Magpie"
    assert adapter._hfc_model_picker_state["picker-1"]["selected_provider"] == "magpie"


def test_missing_chat_model_selection_uses_original_pending_chat():
    selected = []

    async def on_model_selected(chat_id, model_id, provider_slug):
        selected.append((chat_id, model_id, provider_slug))
        return "switched"

    adapter = Adapter()
    adapter._hfc_model_picker_state = {
        "picker-1": pending(
            selected_provider="magpie",
            on_model_selected=on_model_selected,
        )
    }

    resolved = asyncio.run(
        hook_runtime._hfc_resolve_native_model_action_async(
            adapter,
            callback(chat=None),
            {
                "hfc_model_picker_id": "picker-1",
                "hfc_choice": json.dumps(
                    {"provider": "magpie", "model": "deepseek-v4-pro"}
                ),
            },
        )
    )

    assert resolved is not None
    assert selected == [("oc_dm", "deepseek-v4-pro", "magpie")]
    assert "picker-1" not in adapter._hfc_model_picker_state
