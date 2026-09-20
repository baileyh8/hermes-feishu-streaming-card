"""Reading presets are opt-in scope defaults, never a configuration migration."""
import json

import pytest
import yaml

from hermes_feishu_card.bots import resolve_card_config
from hermes_feishu_card.cli import main
from hermes_feishu_card.config import load_config


def load_yaml(tmp_path, data):
    path = tmp_path / "card.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path, load_config(path)


def test_focused_preset_does_not_inherit_injected_classic_defaults(tmp_path):
    _, config = load_yaml(tmp_path, {"card": {"reading_preset": "focused"}})
    assert config["card"]["stream_thinking_to_body"] is False
    assert config["card"]["timeline_expanded"] is False
    assert config["card"]["_hide_successful_tool_activity"] is True
    # The legacy true switch still means both completed and failed.
    assert config["card"]["hide_completed_tool_activity"] is False


def test_reading_preset_scope_precedence_and_explicit_legacy_overrides(tmp_path):
    _, config = load_yaml(tmp_path, {
        "card": {"reading_preset": "focused", "show_reasoning": False},
        "profiles": {"work": {"card": {
            "reading_preset": "detailed", "timeline_expanded": False,
        }}},
    })
    resolved = resolve_card_config(config["card"], config["profiles"]["work"]["card"], {
        "reading_preset": "focused", "stream_thinking_to_body": True,
        "hide_completed_tool_activity": False,
    })
    assert resolved["reading_preset"] == "focused"
    assert resolved["stream_thinking_to_body"] is True
    assert resolved["timeline_expanded"] is False
    assert resolved["show_reasoning"] is True
    assert resolved["_hide_successful_tool_activity"] is False
    assert config["profiles"]["work"]["card"]["stream_thinking_to_body"] is False
    assert config["profiles"]["work"]["card"]["show_reasoning"] is True


@pytest.mark.parametrize("value", [True, False])
def test_legacy_hide_override_remains_terminal_wide_after_inheriting_focused(tmp_path, value):
    _, config = load_yaml(tmp_path, {"card": {"reading_preset": "focused"}})
    resolved = resolve_card_config(config["card"], {}, {"hide_completed_tool_activity": value})
    assert resolved["hide_completed_tool_activity"] is value
    assert resolved["_hide_successful_tool_activity"] is False


@pytest.mark.parametrize("data", [
    {"card": {"reading_preset": "unknown"}},
    {"profiles": {"work": {"card": {"reading_preset": True}}}},
    {"bots": {"items": {"work": {"card": {"reading_preset": None}}}}},
])
def test_invalid_reading_preset_is_rejected(tmp_path, data):
    with pytest.raises(ValueError, match="reading_preset"):
        load_yaml(tmp_path, data)


def test_card_config_explanation_is_read_only_and_credential_free(tmp_path, capsys):
    path, _ = load_yaml(tmp_path, {
        "card": {"reading_preset": "focused", "title": "SECRET_TITLE"},
        "profiles": {"work": {
            "card": {"stream_thinking_to_body": True},
            "bots": {"default": "assistant", "items": {"assistant": {
                "app_id": "SECRET_APP_ID", "app_secret": "SECRET_TOKEN",
                "card": {"reading_preset": "detailed", "show_reasoning": False},
            }}},
        }},
    })
    before = path.read_bytes()
    assert main(["card-config", "--config", str(path), "--profile-id", "work", "--json"]) == 0
    output = capsys.readouterr().out
    assert "SECRET" not in output
    report = json.loads(output)
    assert report["values"]["reading_preset"] == "detailed"
    assert report["values"]["stream_thinking_to_body"] is False
    assert report["values"]["show_reasoning"] is False
    assert report["sources"]["show_reasoning"] == "bot.card.show_reasoning (explicit)"
    assert report["sources"]["stream_thinking_to_body"] == "bot.card.reading_preset (detailed)"
    assert path.read_bytes() == before


def test_card_config_explanation_rejects_unknown_profile_without_leaking_config(tmp_path, capsys):
    path, _ = load_yaml(tmp_path, {"card": {"title": "SECRET_TITLE"}})
    assert main(["card-config", "--config", str(path), "--profile-id", "missing", "--json"]) == 2
    assert "SECRET" not in capsys.readouterr().out


def test_explanation_reports_rendered_defaults_for_malformed_legacy_values(tmp_path, capsys):
    path, _ = load_yaml(tmp_path, {"card": {
        "show_reasoning": 2, "stream_thinking_to_body": "false",
        "max_reasoning_chars": "SECRET_NOT_A_NUMBER", "reasoning_format": "PANEL",
        "max_timeline_items": 0,
    }})
    assert main(["card-config", "--config", str(path), "--json"]) == 0
    output = capsys.readouterr().out
    assert "SECRET" not in output
    values = json.loads(output)["values"]
    assert values["show_reasoning"] is True
    assert values["stream_thinking_to_body"] is False
    assert values["max_reasoning_chars"] == 1200
    assert values["max_timeline_items"] == 12
    assert values["reasoning_format"] == "panel"


@pytest.mark.parametrize("bots", [{"items": "SECRET"}, {"items": {"default": "SECRET"}}])
def test_explanation_rejects_malformed_bot_mapping_safely(tmp_path, capsys, bots):
    path, _ = load_yaml(tmp_path, {"bots": bots})
    assert main(["card-config", "--config", str(path), "--json"]) == 2
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err
