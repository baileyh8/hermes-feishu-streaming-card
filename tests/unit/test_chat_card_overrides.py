"""Chat-scoped display overrides (#492), layered on #489 tool_activity."""
import copy

import pytest

from hermes_feishu_card.config_rules import (
    apply_chat_card_overrides, card_chat_kind, merge_card_config,
    normalize_bindings, normalize_chat_overrides,
)
from hermes_feishu_card.config_snapshot import scope_identity
from hermes_feishu_card.reading import explain_reading_config


def test_chat_type_normalization_matches_group_rules():
    assert card_chat_kind("group") == "group"
    assert card_chat_kind("forum") == "group"
    assert card_chat_kind("channel") == "group"
    assert card_chat_kind("thread") == "group"
    assert card_chat_kind("p2p") == "p2p"
    assert card_chat_kind("dm") == "p2p"
    assert card_chat_kind("private") == "p2p"
    assert card_chat_kind("unknown") == ""
    assert card_chat_kind(None) == ""


def test_overrides_merge_across_scopes_by_field():
    global_card = {"tool_activity": "full", "chat_overrides": {
        "group": {"tool_activity": "summary"},
        "chats": {"oc_work": {"show_reasoning": False}},
    }}
    profile_card = {"chat_overrides": {
        "group": {"show_reasoning": False},
        "chats": {"oc_work": {"tool_activity": "hidden"}},
    }}
    resolved = merge_card_config(global_card, profile_card)
    overlays = resolved["chat_overrides"]
    assert overlays["group"] == {"tool_activity": "summary", "show_reasoning": False}
    assert overlays["chats"]["oc_work"] == {
        "show_reasoning": False, "tool_activity": "hidden"
    }


def test_apply_overlays_drop_selectors_and_keep_terminal_fields():
    card = {
        "tool_activity": "full", "show_reasoning": True,
        "footer_fields": ["duration"],
        "chat_overrides": {
            "group": {"tool_activity": "summary", "show_reasoning": False},
            "chats": {"oc_work": {"tool_activity": "hidden"}},
        },
    }
    group = apply_chat_card_overrides(card, chat_type="forum", chat_id="oc_other")
    assert group["tool_activity"] == "summary"
    assert group["show_reasoning"] is False
    assert "chat_overrides" not in group
    exact = apply_chat_card_overrides(card, chat_type="group", chat_id="oc_work")
    assert exact["tool_activity"] == "hidden"
    assert exact["footer_fields"] == ["duration"]
    assert "chat_overrides" not in exact
    unknown = apply_chat_card_overrides(card, chat_type="unknown", chat_id="oc_other")
    assert unknown["tool_activity"] == "full"
    assert unknown["show_reasoning"] is True


def test_explain_selectors_report_effective_values_and_sources():
    raw = {"card": {
        "chat_overrides": {
            "group": {"tool_activity": "summary", "show_reasoning": False},
            "chats": {"oc_work": {"tool_activity": "hidden"}},
        },
    }}
    baseline = explain_reading_config(raw)
    assert baseline["values"]["tool_activity"] == "full"
    assert "chat overrides configured" in baseline["chat_overrides_note"]

    group = explain_reading_config(raw, chat_type="group")
    assert group["values"]["tool_activity"] == "summary"
    assert group["values"]["show_reasoning"] is False
    assert "chat_overrides.group" in group["sources"]["tool_activity"]
    assert "chat_id" not in str(group)

    exact = explain_reading_config(raw, chat_type="group", chat_id="oc_work")
    assert exact["values"]["tool_activity"] == "hidden"
    assert "chats[selected]" in exact["sources"]["tool_activity"]
    assert "oc_work" not in str(exact)


def test_chat_overlay_normalization_rejects_unknown_fields_and_bad_values():
    with pytest.raises(ValueError):
        normalize_chat_overrides({"group": {"interaction_mode": "text"}})
    with pytest.raises(ValueError):
        normalize_chat_overrides({"topic": {"tool_activity": "hidden"}})
    with pytest.raises(ValueError):
        normalize_chat_overrides({"group": {"tool_activity": "compact"}})
    with pytest.raises(ValueError):
        normalize_chat_overrides({"chats": {"oc_bad" + chr(0): {"tool_activity": "hidden"}}})
    normalized = copy.deepcopy(normalize_chat_overrides({
        "group": {"show_reasoning": "false", "tool_activity": " HIDDEN "},
    }))
    assert normalized["group"] == {"show_reasoning": False, "tool_activity": "hidden"}


def test_bindings_normalization_conflict_and_defaults():
    bindings = {"default_delivery": {"group": "native", "p2p": "card"}}
    normalize_bindings(bindings)
    assert bindings == {
        "default_delivery": {"group": "native", "p2p": "card"},
        "native_chats": [], "card_chats": [],
    }
    with pytest.raises(ValueError):
        normalize_bindings({"native_chats": ["oc_x"], "card_chats": ["oc_x"]})


def test_scope_identity_is_unchanged_without_selectors():
    assert scope_identity("default", "support") == scope_identity("default", "support")
    assert scope_identity("default", "support") != scope_identity(
        "default", "support", chat_type="group"
    )
    assert scope_identity("default", "support", chat_type="group") != scope_identity(
        "default", "support", chat_type="group", chat_id="oc_work"
    )
