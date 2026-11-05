"""Per-chat-type and exact-chat delivery defaults (#492)."""
import pytest

from hermes_feishu_card.delivery_policy import ChatDeliveryPolicy
from hermes_feishu_card.server import _valid_delivery_policy_payload


def policy(bindings):
    return ChatDeliveryPolicy.from_config({"bindings": bindings})


def test_untouched_config_keeps_every_chat_on_cards():
    decision = policy({}).decide("oc_any", chat_type="group")
    assert (decision.disposition, decision.reason) == ("card", "default_card")
    assert policy({}).decide("oc_any", chat_type="p2p").disposition == "card"


def test_type_default_and_exact_chat_exceptions():
    configured = policy({
        "default_delivery": {"group": "native", "p2p": "card"},
        "card_chats": ["oc_card"],
        "native_chats": ["oc_native"],
    })
    assert configured.decide("oc_group", chat_type="group").reason == "bindings.default_delivery.group"
    assert configured.decide("oc_group", chat_type="group").disposition == "native"
    assert configured.decide("oc_p2p", chat_type="p2p").disposition == "card"
    assert configured.decide("oc_card", chat_type="group").reason == "bindings.card_chats"
    assert configured.decide("oc_card", chat_type="group").disposition == "card"
    assert configured.decide("oc_native", chat_type="p2p").reason == "bindings.native_chats"
    assert configured.decide("oc_native", chat_type="p2p").disposition == "native"


@pytest.mark.parametrize("chat_type", ["", "unknown", "forum", "channel", "thread"])
def test_group_like_chat_types_share_the_group_default(chat_type):
    configured = policy({"default_delivery": {"group": "native", "p2p": "card"}})
    expected = "native" if chat_type in {"forum", "channel", "thread"} else "card"
    assert configured.decide("oc_any", chat_type=chat_type).disposition == expected


def test_missing_chat_type_keeps_single_chat_rules_and_global_default():
    configured = policy({
        "default_delivery": "native",
        "card_chats": ["oc_card"],
        "native_chats": ["oc_native"],
    })
    assert configured.decide("oc_unknown").disposition == "native"
    assert configured.decide("oc_unknown").reason == "bindings.default_delivery"
    assert configured.decide("oc_card").reason == "bindings.card_chats"
    assert configured.decide("oc_native").reason == "bindings.native_chats"


def test_scalar_default_delivery_is_backward_compatible():
    assert policy({"default_delivery": "card"}).decide("oc_any", chat_type="group").disposition == "card"
    assert policy({"default_delivery": "native"}).decide("oc_any", chat_type="p2p").disposition == "native"


def test_profiles_do_not_inherit_top_level_bindings():
    configured = ChatDeliveryPolicy.from_config({
        "bindings": {"default_delivery": "native"},
        "profiles": {
            "work": {"bindings": {"default_delivery": {"group": "native", "p2p": "card"}}},
        },
    })
    assert configured.decide("oc_work", profile_id="work", chat_type="p2p").disposition == "card"
    assert configured.decide("oc_work", profile_id="work", chat_type="group").disposition == "native"
    assert configured.decide("oc_work", profile_id="missing", chat_type="p2p").reason == "profile_unknown"


def test_conflicting_lists_and_invalid_values_are_rejected():
    with pytest.raises(ValueError):
        policy({"native_chats": ["oc_same"], "card_chats": ["oc_same"]})
    with pytest.raises(ValueError):
        policy({"default_delivery": "sometimes"})
    with pytest.raises(ValueError):
        policy({"default_delivery": {"group": "card", "topic": "native"}})
    with pytest.raises(ValueError):
        policy({"card_chats": "oc_not_a_list"})


def test_policy_request_accepts_chat_type_without_leaking_controls():
    assert _valid_delivery_policy_payload({
        "schema_version": "1", "chat_id": "oc_a", "chat_type": "group",
    }) is True
    assert _valid_delivery_policy_payload({
        "schema_version": "1", "chat_id": "oc_a", "chat_type": "x" * 100,
    }) is False
    assert _valid_delivery_policy_payload({
        "schema_version": "1", "chat_id": "oc_a", "chat_type": "group" + chr(0),
    }) is False
