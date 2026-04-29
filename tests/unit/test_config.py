from pathlib import Path

import pytest
import yaml

from hermes_feishu_card.config import load_config


CONFIG_ENV_VARS = (
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
)


@pytest.fixture(autouse=True)
def clear_config_env(monkeypatch):
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config == {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {"app_id": "", "app_secret": ""},
        "bots": {"default": "default", "items": {}},
        "bindings": {
            "chats": {},
            "group_rules": {"enabled": False},
        },
        "card": {
            "max_wait_ms": 800,
            "max_chars": 240,
            "title": "Hermes Agent",
            "footer_fields": [
                "duration",
                "model",
                "input_tokens",
                "output_tokens",
                "context",
            ],
        },
    }


def test_example_config_uses_current_sidecar_schema():
    config = load_config("config.yaml.example")
    raw = yaml.safe_load(Path("config.yaml.example").read_text(encoding="utf-8"))

    assert "feishu" in raw
    assert "cardkit" not in raw
    assert config["feishu"] == {
        "app_id": "",
        "app_secret": "",
        "base_url": "https://open.feishu.cn/open-apis",
        "timeout_seconds": 30,
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
    assert config["card"] == {
        "max_wait_ms": 800,
        "max_chars": 120,
        "title": "Hermes Agent",
        "footer_fields": [
            "duration",
            "model",
            "input_tokens",
            "output_tokens",
            "context",
        ],
    }


def test_load_config_defaults_include_multi_bot_sections(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config["bots"] == {"default": "default", "items": {}}
    assert config["bindings"] == {
        "chats": {},
        "group_rules": {"enabled": False},
    }


def test_load_config_accepts_multi_bot_sections(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
feishu:
  app_id: cli_default
  app_secret: default-secret
bots:
  default: default
  items:
    sales:
      app_id: cli_sales
      app_secret: sales-secret
bindings:
  fallback_bot: default
  chats:
    oc_sales: sales
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["bots"]["items"]["sales"]["app_id"] == "cli_sales"
    assert config["bindings"]["chats"] == {"oc_sales": "sales"}


def test_load_config_accepts_custom_footer_fields(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "card:\n"
        "  footer_fields:\n"
        "    - model\n"
        "    - duration\n"
        "    - context\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["card"]["footer_fields"] == ["model", "duration", "context"]


def test_load_config_accepts_custom_card_title(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("card:\n  title: 研发助手\n", encoding="utf-8")

    config = load_config(path)

    assert config["card"]["title"] == "研发助手"


def test_load_config_accepts_yaml_string_port_and_normalizes_to_int(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: '9003'\n", encoding="utf-8")

    config = load_config(path)

    assert config["server"]["port"] == 9003


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


@pytest.mark.parametrize("section", ["server", "feishu", "card"])
def test_load_config_rejects_non_mapping_known_sections(tmp_path, section):
    path = tmp_path / "bad.yaml"
    path.write_text(f"{section}: 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match=f"{section}.*mapping"):
        load_config(path)


@pytest.mark.parametrize("port", [True, False, 0, -1, 65536, "not-a-port"])
def test_load_config_rejects_invalid_yaml_ports(tmp_path, port):
    path = tmp_path / "bad.yaml"
    value = repr(port).lower() if isinstance(port, bool) else repr(port)
    path.write_text(f"server:\n  port: {value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="server.port"):
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


@pytest.mark.parametrize("port", ["0", "-1", "65536", "true"])
def test_load_config_rejects_out_of_range_environment_port(tmp_path, monkeypatch, port):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", port)

    with pytest.raises(ValueError, match="HERMES_FEISHU_CARD_PORT"):
        load_config(tmp_path / "missing.yaml")
