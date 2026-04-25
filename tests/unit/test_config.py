import pytest

from hermes_feishu_card.config import load_config


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config == {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "", "app_secret": ""},
        "card": {"max_wait_ms": 800, "max_chars": 240},
    }


def test_load_config_shallow_merges_yaml_sections(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
server:
  port: 9000
feishu:
  app_id: cli_test
card:
  max_chars: 120
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["server"] == {"host": "127.0.0.1", "port": 9000}
    assert config["feishu"] == {"app_id": "cli_test", "app_secret": ""}
    assert config["card"] == {"max_wait_ms": 800, "max_chars": 120}


def test_load_config_empty_file_returns_defaults(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    config = load_config(path)

    assert config["server"]["host"] == "127.0.0.1"
    assert config["server"]["port"] == 8765


def test_load_config_rejects_non_mapping_top_level(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- server\n- feishu\n", encoding="utf-8")

    with pytest.raises(ValueError, match="top-level"):
        load_config(path)


def test_load_config_applies_supported_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_HOST", "0.0.0.0")
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", "9001")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "cli_secret")

    config = load_config(tmp_path / "missing.yaml")

    assert config["server"] == {"host": "0.0.0.0", "port": 9001}
    assert config["feishu"] == {"app_id": "cli_app", "app_secret": "cli_secret"}


def test_load_config_rejects_invalid_environment_port(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", "not-a-port")

    with pytest.raises(ValueError, match="HERMES_FEISHU_CARD_PORT"):
        load_config(tmp_path / "missing.yaml")
