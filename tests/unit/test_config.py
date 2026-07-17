from pathlib import Path

import pytest
import yaml

from hermes_feishu_card.config import load_config, resolve_operations_hermes_root


CONFIG_ENV_VARS = (
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "HERMES_DIR",
    "HFC_HERMES_DIR",
    "HERMES_AGENT_ROOT",
)


@pytest.fixture(autouse=True)
def clear_config_env(monkeypatch):
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_load_config_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config == {
        "server": {
            "host": "127.0.0.1",
            "port": 8765,
            "allow_non_loopback": False,
        },
        "feishu": {"app_id": "", "app_secret": ""},
        "profiles": {},
        "bots": {"default": "default", "items": {}},
        "bindings": {
            "chats": {},
            "group_rules": {"enabled": False},
        },
        "card": {
            "max_wait_ms": 800,
            "max_chars": 240,
            "flush_interval_ms": 200,
            "final_drain_timeout_ms": 900,
            "title": "Hermes Agent",
            "interaction_mode": "auto",
            "show_reasoning": True,
            "timeline_expanded": False,
            "max_timeline_items": 12,
            "max_reasoning_chars": 1200,
            "max_tool_result_chars": 600,
            "footer_fields": [
                "duration",
                "model",
                "input_tokens",
                "output_tokens",
                "context",
            ],
        },
    }


def test_operations_hermes_root_uses_process_hint_without_user_config(
    monkeypatch, tmp_path
):
    hinted = tmp_path / "hermes-agent"
    monkeypatch.setenv("HFC_HERMES_DIR", str(hinted))

    assert resolve_operations_hermes_root() == hinted


def test_operations_hermes_root_prefers_explicit_then_config_env_file(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server: {}\n", encoding="utf-8")
    dotenv_root = tmp_path / "from-dotenv"
    explicit_root = tmp_path / "explicit"
    process_root = tmp_path / "from-process"
    (tmp_path / ".env").write_text(f"HERMES_DIR={dotenv_root}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_DIR", str(process_root))

    assert resolve_operations_hermes_root(config_path=config_path) == dotenv_root
    assert (
        resolve_operations_hermes_root(explicit_root, config_path=config_path)
        == explicit_root
    )


def test_operations_hermes_root_prefers_selected_env_file_over_config_and_process(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    selected_env = tmp_path / "selected.env"
    config_path.write_text("server: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("HERMES_DIR=config-root\n", encoding="utf-8")
    selected_env.write_text("HERMES_DIR=selected-root\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_DIR", "process-root")

    assert resolve_operations_hermes_root(
        config_path=config_path, env_file=selected_env
    ) == Path("selected-root")


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

    assert config["server"] == {
        "host": "127.0.0.1",
        "port": 9000,
        "allow_non_loopback": False,
    }
    assert config["feishu"] == {"app_id": "cli_test", "app_secret": ""}
    assert config["card"] == {
        "max_wait_ms": 800,
        "max_chars": 120,
        "flush_interval_ms": 200,
        "final_drain_timeout_ms": 900,
        "title": "Hermes Agent",
        "interaction_mode": "auto",
        "show_reasoning": True,
        "timeline_expanded": False,
        "max_timeline_items": 12,
        "max_reasoning_chars": 1200,
        "max_tool_result_chars": 600,
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


def test_load_config_defaults_include_v38_card_options(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config["card"]["flush_interval_ms"] == 200
    assert config["card"]["final_drain_timeout_ms"] == 900
    assert config["card"]["show_reasoning"] is True
    assert config["card"]["timeline_expanded"] is False
    assert config["card"]["max_timeline_items"] == 12
    assert config["card"]["max_reasoning_chars"] == 1200
    assert config["card"]["max_tool_result_chars"] == 600


def test_load_config_accepts_v38_card_options(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "card:\n"
        "  flush_interval_ms: 80\n"
        "  final_drain_timeout_ms: 1500\n"
        "  show_reasoning: false\n"
        "  timeline_expanded: true\n"
        "  max_timeline_items: 6\n"
        "  max_reasoning_chars: 800\n"
        "  max_tool_result_chars: 300\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["card"]["flush_interval_ms"] == 80
    assert config["card"]["final_drain_timeout_ms"] == 1500
    assert config["card"]["show_reasoning"] is False
    assert config["card"]["timeline_expanded"] is True
    assert config["card"]["max_timeline_items"] == 6
    assert config["card"]["max_reasoning_chars"] == 800
    assert config["card"]["max_tool_result_chars"] == 300


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

    assert config["server"] == {
        "host": "0.0.0.0",
        "port": 9001,
        "allow_non_loopback": False,
    }
    assert config["feishu"] == {"app_id": "cli_app", "app_secret": "cli_secret"}


def test_load_config_applies_dotenv_next_to_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "HERMES_FEISHU_CARD_HOST=0.0.0.0",
                "HERMES_FEISHU_CARD_PORT=9012",
                "FEISHU_APP_ID=dotenv_app",
                "FEISHU_APP_SECRET='dotenv secret'",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["server"] == {
        "host": "0.0.0.0",
        "port": 9012,
        "allow_non_loopback": False,
    }
    assert config["feishu"] == {
        "app_id": "dotenv_app",
        "app_secret": "dotenv secret",
    }


def test_load_config_environment_overrides_dotenv(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "HERMES_FEISHU_CARD_PORT=9012\n"
        "FEISHU_APP_ID=dotenv_app\n"
        "FEISHU_APP_SECRET=dotenv_secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", "9013")
    monkeypatch.setenv("FEISHU_APP_ID", "env_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "env_secret")

    config = load_config(config_path)

    assert config["server"]["port"] == 9013
    assert config["feishu"] == {"app_id": "env_app", "app_secret": "env_secret"}


def test_load_config_rejects_invalid_environment_port(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", "not-a-port")

    with pytest.raises(ValueError, match="HERMES_FEISHU_CARD_PORT"):
        load_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize("port", ["0", "-1", "65536", "true"])
def test_load_config_rejects_out_of_range_environment_port(tmp_path, monkeypatch, port):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PORT", port)

    with pytest.raises(ValueError, match="HERMES_FEISHU_CARD_PORT"):
        load_config(tmp_path / "missing.yaml")


# ── profile support ──────────────────────────────────────────────


def test_load_config_with_profiles(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
profiles:
  default:
    feishu:
      app_id: cli_a
      app_secret: s_a
    bots:
      default: default
      items: {}
    bindings:
      chats: {}
      fallback_bot: default
  work:
    feishu:
      app_id: cli_b
      app_secret: s_b
""",
        encoding="utf-8",
    )
    config = load_config(path)

    assert "profiles" in config
    assert config["profiles"]["default"]["feishu"]["app_id"] == "cli_a"
    # 验证 work profile 自动继承了默认的 bots / bindings
    assert config["profiles"]["work"]["bots"]["default"] == "default"
    assert config["profiles"]["work"]["bindings"] == {"chats": {}, "group_rules": {"enabled": False}}


def test_load_config_without_profiles_still_works(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "feishu:\n  app_id: cli_top\n  app_secret: s_top\n",
        encoding="utf-8",
    )
    config = load_config(path)

    assert config["feishu"]["app_id"] == "cli_top"
    assert "profiles" in config  # DEFAULT_CONFIG 带入了空 dict
    assert not config["profiles"]  # 为空


def test_profiles_env_override_skipped_when_profiles_present(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
profiles:
  default:
    feishu:
      app_id: cli_a
      app_secret: s_a
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "s_env")

    config = load_config(path)

    # 环境变量不应覆盖 profile 中的凭据
    assert config["profiles"]["default"]["feishu"]["app_id"] == "cli_a"
    assert config["profiles"]["default"]["feishu"]["app_secret"] == "s_a"


def test_load_config_rejects_non_mapping_profile_value(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
profiles:
  bad: 123
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="profile"):
        load_config(path)


def test_profile_card_config_inherits_global_card_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
card:
  title: Global
  footer_fields: [model]
profiles:
  work:
    card:
      title: Work
    feishu:
      app_id: cli_work
      app_secret: s
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["profiles"]["work"]["card"]["title"] == "Work"
    assert config["profiles"]["work"]["card"]["footer_fields"] == ["model"]


def test_load_config_rejects_non_mapping_profile_card(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
profiles:
  work:
    card: bad
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="profile 'work' card must be a mapping"):
        load_config(path)
