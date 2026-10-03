"""Preview uses safe effective settings and the production renderer, without I/O."""
import copy
import json

import pytest
import yaml

from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.cli import main
from hermes_feishu_card.preview import build_preview, write_preview
from hermes_feishu_card.reading import explain_reading_config


def test_task_configuration_reports_per_role_overrides_and_next_load():
    report = explain_reading_config({
        "card": {"reading_preset": "task", "text_sizes": {"body": "large"}},
        "profiles": {"work": {
            "card": {"width_mode": "fill", "text_sizes": {"reasoning": "normal"}},
            "bots": {"default": "worker", "items": {"worker": {
                "card": {"text_sizes": {"footer": "small"}},
            }}},
        }},
    })
    assert report["values"]["presentation"] == "task"
    assert report["values"]["text_sizes"] == {
        "body": "large", "reasoning": "normal", "footer": "small",
        "tool": "small", "notice": "normal",
    }
    assert report["values"]["width_mode"] == "fill"
    assert report["text_size_sources"]["body"] == "global.card.text_sizes.body (explicit)"
    assert report["text_size_sources"]["reasoning"] == "profile.card.text_sizes.reasoning (explicit)"
    assert report["text_size_sources"]["footer"] == "bot.card.text_sizes.footer (explicit)"
    assert report["text_size_sources"]["tool"] == "global.card.reading_preset (task)"
    assert report["effective_for"] == "next_load"
    assert report["running_config"] == "not_checked"


@pytest.mark.parametrize("preset", ["classic", "focused", "detailed"])
def test_bot_can_reset_task_presentation_without_erasing_explicit_sizes(preset):
    raw = {
        "card": {"reading_preset": "task", "text_sizes": {"body": "large"}},
        "bots": {"items": {"default": {"card": {"reading_preset": preset}}}},
    }
    report = explain_reading_config(raw)
    assert report["values"]["presentation"] == "classic"
    assert report["values"]["text_sizes"]["body"] == "large"
    assert report["sources"]["presentation"] == f"bot.card.reading_preset ({preset})"


def test_all_preview_states_keep_safe_payloads_and_complete_answer():
    report = explain_reading_config({"card": {"reading_preset": "task"}})
    before = copy.deepcopy(report)
    data = build_preview(report)
    assert report == before
    assert len(data["scenarios"]) == 9
    for sample in data["scenarios"]:
        for card in sample["cards"].values():
            assert inspect_card_limits(card).safe, sample["id"]
    complete = next(s for s in data["scenarios"] if s["id"] == "completed")
    for layout in ("classic", "task"):
        value = json.dumps(complete["cards"][layout], ensure_ascii=False)
        assert "真实客户端效果仍需单独验收" in value
        assert "weekly" not in value
        assert "card-config --config example.yaml" in value
    approval = next(s for s in data["scenarios"] if s["id"] == "approval")
    assert "schema" not in approval["cards"]["task"]
    assert "原配置先备份" in str(approval["cards"]["task"])


def test_preview_cli_is_offline_read_only_and_does_not_export_private_values(tmp_path, capsys, monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        pytest.fail("offline preview attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    raw = {
        "feishu": {"app_id": "SECRET_APP_ID", "app_secret": "SECRET_APP_SECRET"},
        "card": {"title": "SECRET_TITLE", "reading_preset": "task",
                 "footer_fields": ["model", "SECRET_FIELD"]},
    }
    path = tmp_path / "input.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = path.read_bytes()
    output = tmp_path / "preview"
    assert main(["card-config", "--config", str(path), "--preview-dir", str(output), "--json"]) == 0
    printed = capsys.readouterr()
    assert "SECRET" not in printed.out + printed.err
    assert path.read_bytes() == before
    assert sorted(p.name for p in output.iterdir()) == ["cards.json", "index.html"]
    for artifact in output.iterdir():
        assert "SECRET" not in artifact.read_text()
    assert json.loads((output / "cards.json").read_text())["configuration"]["values"]["footer_fields"] == ["model"]


@pytest.mark.parametrize("existing", ["index.html", "cards.json"])
def test_preview_never_overwrites_and_rolls_back_its_partial_files(tmp_path, existing):
    (tmp_path / existing).write_text("user content")
    with pytest.raises(FileExistsError):
        write_preview(tmp_path, explain_reading_config({}))
    assert (tmp_path / existing).read_text() == "user content"
    assert sorted(p.name for p in tmp_path.iterdir()) == [existing]


def test_preview_embedding_cannot_close_the_json_script_element(tmp_path):
    report = explain_reading_config({})
    report["note"] = '</script><script>alert("injection")</script>\u2028&'
    html_path, json_path = write_preview(tmp_path, report)
    html = html_path.read_text()
    assert html.count("</script>") == 2
    assert "\\u003c/script\\u003e" in html
    assert "\\u2028" in html
    assert "connect-src 'none'" in html
    assert json.loads(json_path.read_text())["configuration"]["note"] == report["note"]
