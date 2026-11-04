"""Reading presets are opt-in scope defaults, never a configuration migration."""
import json

import pytest
import yaml

from hermes_feishu_card.bots import resolve_card_config
from hermes_feishu_card.cli import main
from hermes_feishu_card.config import load_config
from hermes_feishu_card.reading import explain_reading_config


def load_yaml(tmp_path, data):
    path = tmp_path / "card.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path, load_config(path)


@pytest.mark.parametrize('chat_type,chat_id,mode,source', [
    ('group', None, 'hidden', 'bot.chat_overrides.group (explicit)'),
    ('p2p', None, 'summary', 'global.chat_overrides.p2p (explicit)'),
    ('group', 'oc_example', 'full', 'global.chat_overrides.chats[selected] (explicit)'),
    (None, 'oc_example', 'full', 'global.chat_overrides.chats[selected] (explicit)'),
])
def test_explain_chat_sources_after_all_base_scopes(chat_type, chat_id, mode, source):
    raw = {'card': {'chat_overrides': {'group': {'live_tool_activity': 'summary'},
        'p2p': {'live_tool_activity': 'summary'}, 'chats': {'oc_example': {'live_tool_activity': 'full'}}}},
        'profiles': {'default': {'card': {'live_tool_activity': 'summary'},
            'bots': {'items': {'default': {'card': {'live_tool_activity': 'full',
                'chat_overrides': {'group': {'live_tool_activity': 'hidden'}}}}}}}}}
    report = explain_reading_config(raw, chat_type=chat_type, chat_id=chat_id)
    assert report['values']['live_tool_activity'] == mode
    assert report['sources']['live_tool_activity'] == source
    assert 'oc_example' not in json.dumps(report)


@pytest.mark.parametrize('flag', ['--chat-id', '--chat-type'])
def test_minimal_yaml_rejects_chat_selectors(tmp_path, capsys, flag):
    path, _ = load_yaml(tmp_path, {})
    assert main(['card-config', '--config', str(path), '--minimal-yaml', flag,
                 'oc_example' if flag == '--chat-id' else 'group']) == 2
    assert 'oc_example' not in str(capsys.readouterr())


def test_no_selector_reports_only_overlay_kinds_and_count(tmp_path, capsys):
    path, _ = load_yaml(tmp_path, {'card': {'chat_overrides': {'group': {'live_tool_activity': 'hidden'},
        'chats': {'oc_example': {'show_reasoning': False}, 'oc_other': {}}}}})
    assert main(['card-config', '--config', str(path)]) == 0
    output = capsys.readouterr().out
    assert 'chat overrides configured: group, chats=2; use --chat-type/--chat-id' in output
    assert 'oc_example' not in output and 'oc_other' not in output


def test_raw_explanation_normalizes_chat_keys_without_mutation():
    import copy
    raw = {'card': {'chat_overrides': {'chats': {' oc_example ': {'live_tool_activity': ' Hidden '}}}}}
    before = copy.deepcopy(raw)
    report = explain_reading_config(raw, chat_id=' oc_example ')
    assert report['values']['live_tool_activity'] == 'hidden'
    assert report['sources']['live_tool_activity'] == 'global.chat_overrides.chats[selected] (explicit)'
    assert raw == before
    assert 'oc_example' not in json.dumps(report)


def test_focused_preset_does_not_inherit_injected_classic_defaults(tmp_path):
    _, config = load_yaml(tmp_path, {"card": {"reading_preset": "focused"}})
    assert config["card"]["stream_thinking_to_body"] is False
    assert config["card"]["timeline_expanded"] is False
    assert config["card"]["_hide_successful_tool_activity"] is True
    # The legacy true switch still means both completed and failed.
    assert config["card"]["hide_completed_tool_activity"] is False


@pytest.mark.parametrize("preset,expanded,hide_success", [
    ("task", True, True), ("focused", False, True),
    ("detailed", True, False), ("classic", False, False),
])
def test_approved_task_direction_does_not_change_other_preset_defaults(tmp_path, preset, expanded, hide_success):
    _, config = load_yaml(tmp_path, {"card": {"reading_preset": preset}})
    assert config["card"]["timeline_expanded"] is expanded
    assert config["card"]["_hide_successful_tool_activity"] is hide_success


@pytest.mark.parametrize("hidden", [True, False])
@pytest.mark.parametrize("scope", ["global", "profile", "bot"])
def test_task_explicit_closed_process_and_legacy_hide_survive_all_scopes(tmp_path, scope, hidden):
    card = {"reading_preset": "task", "timeline_expanded": False, "hide_completed_tool_activity": hidden}
    data = {"card": card} if scope == "global" else {"card": {"reading_preset": "task"}}
    if scope == "profile":
        data["profiles"] = {"work": {"card": card}}
    elif scope == "bot":
        data["bots"] = {"items": {"default": {"card": card}}}
    _, config = load_yaml(tmp_path, data)
    effective = resolve_card_config(config["card"], config.get("profiles", {}).get("work", {}).get("card", {}), config.get("bots", {}).get("items", {}).get("default", {}).get("card", {}))
    assert effective["timeline_expanded"] is False
    assert effective["hide_completed_tool_activity"] is hidden
    assert effective["_hide_successful_tool_activity"] is False


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
