"""The card.over_limit key: validation, defaults and render-option flow."""
import pytest

from hermes_feishu_card import config_rules as rules
from hermes_feishu_card.render_options import card_render_options


def test_over_limit_defaults_to_native():
    assert rules.DEFAULT_CONFIG["card"]["over_limit"] == "native"


def test_normalize_over_limit_mode_accepts_both_modes():
    assert rules.normalize_over_limit_mode("native") == "native"
    assert rules.normalize_over_limit_mode(" Segmented ") == "segmented"


@pytest.mark.parametrize("value", [None, 0, "auto", "split", {"mode": "native"}, True])
def test_normalize_over_limit_mode_rejects_unknown_values(value):
    with pytest.raises(ValueError, match="card.over_limit must be native or segmented"):
        rules.normalize_over_limit_mode(value)


def test_normalize_card_config_normalizes_over_limit_in_place():
    value = {"over_limit": "SEGMENTED"}
    rules.normalize_card_config(value, path="card")
    assert value["over_limit"] == "segmented"


def test_normalize_card_config_rejects_invalid_over_limit():
    with pytest.raises(ValueError, match="profiles.research.card.over_limit"):
        rules.normalize_card_config(
            {"over_limit": "pdf"}, path="profiles.research.card"
        )


def test_render_options_pass_over_limit_through_and_sanitize():
    assert card_render_options({"over_limit": "segmented"})["over_limit"] == "segmented"
    assert card_render_options({})["over_limit"] == "native"
    assert card_render_options({"over_limit": "native"})["over_limit"] == "native"
    assert card_render_options({"over_limit": "broken"})["over_limit"] == "native"
