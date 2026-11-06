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


def test_load_config_defaults_leave_mentions_unset_but_enabled(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    # Defaults must NOT inject a per-kind mapping: an injected mapping would
    # shadow ``mentions_in_cards: false`` after the config merge.
    assert config["card"]["interaction_mentions"] is None
    assert config["card"]["completion_notify"] == {
        "enabled": False,
        "min_duration_seconds": 0,
    }
    assert card_interaction_mention_enabled(config["card"], kind="clarify") is True
    assert card_interaction_mention_enabled(config["card"], kind="approval") is True
    assert card_completion_mention_enabled(config["card"]) is True


# ---------------------------------------------------------------------------
# Regression: load_config() from a FULL YAML file, not isolated helpers.
# ---------------------------------------------------------------------------


def test_full_yaml_global_off_switch_disables_every_mention(tmp_path):
    """``mentions_in_cards: false`` must disable clarify/approval/completion."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "card": {
                    "mentions_in_cards": False,
                    "completion_notify": {"enabled": True},
                }
            }
        )
    )

    config = load_config(path)

    assert config["card"]["mentions_in_cards"] is False
    assert config["card"]["interaction_mentions"] is None
    assert (
        card_interaction_mention_enabled(config["card"], kind="clarify") is False
    )
    assert (
        card_interaction_mention_enabled(config["card"], kind="approval") is False
    )
    assert card_completion_mention_enabled(config["card"]) is False


def test_full_yaml_global_off_switch_wins_over_per_kind_true(tmp_path):
    """The master OFF switch beats an explicit per-kind true."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "card": {
                    "mentions_in_cards": False,
                    "interaction_mentions": {"approval": True},
                }
            }
        )
    )

    config = load_config(path)

    assert (
        card_interaction_mention_enabled(config["card"], kind="approval") is False
    )
    assert (
        card_interaction_mention_enabled(config["card"], kind="clarify") is False
    )


def test_full_yaml_per_kind_off_without_global_key(tmp_path):
    """Per-kind switches apply when the global key is absent."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"card": {"interaction_mentions": {"clarify": False, "approval": True}}}
        )
    )

    config = load_config(path)

    assert config["card"]["interaction_mentions"] == {
        "clarify": False,
        "approval": True,
    }
    assert (
        card_interaction_mention_enabled(config["card"], kind="clarify") is False
    )
    assert (
        card_interaction_mention_enabled(config["card"], kind="approval") is True
    )


def test_full_yaml_completion_notify_mention_override(tmp_path):
    """completion_notify.mention overrides the default/global for the notify."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"card": {"completion_notify": {"enabled": True, "mention": False}}}
        )
    )

    config = load_config(path)

    assert config["card"]["completion_notify"] == {
        "enabled": True,
        "mention": False,
    }
    assert card_completion_mention_enabled(config["card"]) is False
    # The notify switch does NOT leak into interaction mention resolution.
    assert (
        card_interaction_mention_enabled(config["card"], kind="clarify") is True
    )


def test_full_yaml_string_booleans_normalized(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "card": {
                    "mentions_in_cards": "false",
                    "interaction_mentions": {"clarify": "off"},
                }
            }
        )
    )

    config = load_config(path)

    assert config["card"]["mentions_in_cards"] is False
    assert config["card"]["interaction_mentions"] == {"clarify": False}
    assert card_completion_mention_enabled(config["card"]) is False


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


def test_load_config_keeps_legacy_mentions_in_cards_normalized(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"card": {"mentions_in_cards": "off"}}))

    config = load_config(path)

    assert config["card"]["mentions_in_cards"] is False


# ---------------------------------------------------------------------------
# Isolated helper resolution.
# ---------------------------------------------------------------------------


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
        is False
    )


def test_interaction_mention_enabled_unknown_kind_defaults_off():
    assert (
        card_interaction_mention_enabled({}, kind="slash_confirm") is False
    )
    assert (
        card_interaction_mention_enabled(
            {"mentions_in_cards": True}, kind="slash_confirm"
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
    assert (
        card_completion_mention_enabled(
            {"mentions_in_cards": False, "completion_notify": {"mention": True}}
        )
        is False
    )


# ---------------------------------------------------------------------------
# completion_notify.min_duration_seconds
# ---------------------------------------------------------------------------


def test_completion_notify_min_duration_defaults_to_no_gate(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config["card"]["completion_notify"]["min_duration_seconds"] == 0


def test_completion_notify_min_duration_accepts_number(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"card": {"completion_notify": {"enabled": True, "min_duration_seconds": 60}}}
        )
    )
    config = load_config(path)

    assert config["card"]["completion_notify"]["min_duration_seconds"] == 60


@pytest.mark.parametrize("bad", [-1, 90_000, "60", True, float("inf"), float("nan")])
def test_completion_notify_min_duration_rejects_invalid_values(tmp_path, bad):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {"card": {"completion_notify": {"enabled": True, "min_duration_seconds": bad}}}
        )
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_completion_notify_min_duration_absent_keeps_mapping_valid(tmp_path):
    """A YAML completion_notify mapping replaces the default mapping rather
    than deep-merging it; an omitted key must stay absent and be read as 0."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"card": {"completion_notify": {"enabled": True}}})
    )
    config = load_config(path)

    assert config["card"]["completion_notify"] == {"enabled": True}
