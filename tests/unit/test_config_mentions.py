from __future__ import annotations

import pytest
import yaml

from hermes_feishu_card.config import (
    card_completion_mention_enabled,
    card_interaction_mention_enabled,
    load_config,
)


CONFIG_ENV_VARS = (
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK",
    "HERMES_FEISHU_CARD_SERVICE_MANAGER",
    "HERMES_FEISHU_CARD_INTEGRITY_MODE",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
)


@pytest.fixture(autouse=True)
def clear_config_env(monkeypatch):
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_load_config_defaults_interaction_mentions_to_enabled(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config["card"]["interaction_mentions"] == {
        "clarify": True,
        "approval": True,
    }
    assert config["card"]["completion_notify"] == {"enabled": False, "mention": True}


def test_load_config_accepts_interaction_mentions_false(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"card": {"interaction_mentions": {"clarify": False, "approval": False}}}
        )
    )

    config = load_config(path)

    assert config["card"]["interaction_mentions"] == {
        "clarify": False,
        "approval": False,
    }


def test_load_config_normalizes_string_interaction_mentions(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"card": {"interaction_mentions": {"clarify": "false", "approval": "true"}}}
        )
    )

    config = load_config(path)

    assert config["card"]["interaction_mentions"] == {
        "clarify": False,
        "approval": True,
    }


def test_load_config_rejects_invalid_interaction_mentions_with_exact_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"card": {"interaction_mentions": {"clarify": "maybe"}}})
    )

    with pytest.raises(ValueError, match=r"card\.interaction_mentions\.clarify"):
        load_config(path)


def test_load_config_rejects_non_mapping_interaction_mentions(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"card": {"interaction_mentions": True}}))

    with pytest.raises(ValueError, match=r"card\.interaction_mentions"):
        load_config(path)


def test_load_config_normalizes_completion_notify_mention(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"card": {"completion_notify": {"enabled": True, "mention": "false"}}})
    )

    config = load_config(path)

    assert config["card"]["completion_notify"] == {
        "enabled": True,
        "mention": False,
    }


def test_load_config_keeps_legacy_mentions_in_cards_normalized(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"card": {"mentions_in_cards": False}}))

    config = load_config(path)

    assert config["card"]["mentions_in_cards"] is False


def test_interaction_mention_enabled_defaults_true_for_missing_or_malformed():
    assert card_interaction_mention_enabled(None, kind="clarify") is True
    assert card_interaction_mention_enabled(None, kind="approval") is True
    assert card_interaction_mention_enabled({}, kind="clarify") is True
    assert card_interaction_mention_enabled({}, kind="approval") is True
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"clarify": None}}, kind="clarify"
        )
        is True
    )
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"approval": "maybe"}}, kind="approval"
        )
        is True
    )


def test_interaction_mention_enabled_reads_explicit_boolean():
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"clarify": True}}, kind="clarify"
        )
        is True
    )
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"approval": False}}, kind="approval"
        )
        is False
    )


def test_interaction_mention_enabled_parses_string_values():
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"clarify": "true"}}, kind="clarify"
        )
        is True
    )
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"approval": "false"}}, kind="approval"
        )
        is False
    )
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"approval": "off"}}, kind="approval"
        )
        is False
    )
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"clarify": "yes"}}, kind="clarify"
        )
        is True
    )


def test_interaction_mention_enabled_kind_is_isolated():
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"clarify": False, "approval": True}},
            kind="clarify",
        )
        is False
    )
    assert (
        card_interaction_mention_enabled(
            {"interaction_mentions": {"clarify": False, "approval": True}},
            kind="approval",
        )
        is True
    )


def test_interaction_mention_enabled_honors_legacy_global_switch():
    assert (
        card_interaction_mention_enabled(
            {"mentions_in_cards": False}, kind="approval"
        )
        is False
    )
    assert (
        card_interaction_mention_enabled(
            {"mentions_in_cards": True}, kind="clarify"
        )
        is True
    )
    assert (
        card_interaction_mention_enabled(
            {"mentions_in_cards": False, "interaction_mentions": {"approval": True}},
            kind="approval",
        )
        is True
    )


def test_completion_mention_enabled_defaults_true():
    assert card_completion_mention_enabled(None) is True
    assert card_completion_mention_enabled({}) is True
    assert (
        card_completion_mention_enabled(
            {"completion_notify": {"mention": "maybe"}}
        )
        is True
    )


def test_completion_mention_enabled_reads_explicit_boolean():
    assert (
        card_completion_mention_enabled(
            {"completion_notify": {"mention": True}}
        )
        is True
    )
    assert (
        card_completion_mention_enabled(
            {"completion_notify": {"mention": False}}
        )
        is False
    )


def test_completion_mention_enabled_honors_legacy_global_switch():
    assert (
        card_completion_mention_enabled({"mentions_in_cards": False}) is False
    )
    assert (
        card_completion_mention_enabled({"mentions_in_cards": True}) is True
    )
