import pytest

from hermes_feishu_card.bots import BotRegistry, FeishuClientFactory, RoutingContext
from hermes_feishu_card.config import load_config


def test_legacy_feishu_config_becomes_implicit_default_bot():
    registry = BotRegistry.from_config(
        {
            "feishu": {
                "app_id": "cli_default",
                "app_secret": "secret",
                "base_url": "https://open.feishu.cn/open-apis",
                "timeout_seconds": 30,
            }
        }
    )

    bot = registry.get("default")
    assert registry.default_bot_id == "default"
    assert bot.app_id == "cli_default"
    assert bot.app_secret == "secret"


def test_explicit_default_bot_overrides_legacy_implicit_default():
    registry = BotRegistry.from_config(
        {
            "feishu": {"app_id": "cli_legacy", "app_secret": "legacy-secret"},
            "bots": {
                "items": {
                    "default": {
                        "app_id": "cli_explicit",
                        "app_secret": "explicit-secret",
                    },
                },
            },
        }
    )

    bot = registry.get("default")
    assert registry.default_bot_id == "default"
    assert bot.app_id == "cli_explicit"
    assert bot.app_secret == "explicit-secret"


def test_multiple_named_bots_and_explicit_default_are_loaded():
    registry = BotRegistry.from_config(
        {
            "bots": {
                "default": "support",
                "items": {
                    "support": {"app_id": "cli_support", "app_secret": "support-secret"},
                    "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
                },
            }
        }
    )

    assert registry.default_bot_id == "support"
    assert [bot.bot_id for bot in registry.list_bots()] == ["sales", "support"]
    assert registry.get("sales").app_id == "cli_sales"


def test_named_chat_binding_wins_before_fallback():
    registry = BotRegistry.from_config(
        {
            "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
            "bots": {
                "default": "default",
                "items": {
                    "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
                },
            },
            "bindings": {
                "fallback_bot": "default",
                "chats": {"oc_sales": "sales"},
            },
        }
    )

    result = registry.resolve(RoutingContext(chat_id="oc_sales"))

    assert result.bot_id == "sales"
    assert result.reason == "bindings.chats"


def test_unbound_chat_uses_fallback_bot():
    registry = BotRegistry.from_config(
        {
            "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
            "bindings": {"fallback_bot": "default"},
        }
    )

    result = registry.resolve(RoutingContext(chat_id="oc_unknown"))

    assert result.bot_id == "default"
    assert result.reason == "bindings.fallback_bot"


def test_fallback_bot_takes_precedence_over_bots_default_for_unbound_chat():
    registry = BotRegistry.from_config(
        {
            "bots": {
                "default": "support",
                "items": {
                    "default": {"app_id": "cli_default", "app_secret": "default-secret"},
                    "support": {"app_id": "cli_support", "app_secret": "support-secret"},
                },
            },
            "bindings": {"fallback_bot": "default"},
        }
    )

    result = registry.resolve(RoutingContext(chat_id="oc_unknown"))

    assert result.bot_id == "default"
    assert result.reason == "bindings.fallback_bot"


def test_loaded_config_without_fallback_uses_bots_default_for_unbound_chat(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
bots:
  default: support
  items:
    support:
      app_id: cli_support
      app_secret: support-secret
""",
        encoding="utf-8",
    )
    config = load_config(path)
    registry = BotRegistry.from_config(config)

    result = registry.resolve(RoutingContext(chat_id="oc_unknown"))

    assert result.bot_id == "support"
    assert result.reason == "bots.default"


def test_loaded_config_explicit_fallback_overrides_bots_default_for_unbound_chat(
    tmp_path,
):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
bots:
  default: support
  items:
    default:
      app_id: cli_default
      app_secret: default-secret
    support:
      app_id: cli_support
      app_secret: support-secret
bindings:
  fallback_bot: default
""",
        encoding="utf-8",
    )
    config = load_config(path)
    registry = BotRegistry.from_config(config)

    result = registry.resolve(RoutingContext(chat_id="oc_unknown"))

    assert result.bot_id == "default"
    assert result.reason == "bindings.fallback_bot"


def test_unknown_binding_target_is_rejected():
    with pytest.raises(ValueError, match="unknown bot.*ghost"):
        BotRegistry.from_config(
            {
                "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
                "bindings": {"chats": {"oc_bad": "ghost"}},
            }
        )


def test_invalid_bot_id_is_rejected():
    with pytest.raises(ValueError, match="invalid bot id.*bad bot"):
        BotRegistry.from_config(
            {
                "bots": {
                    "items": {
                        "bad bot": {"app_id": "cli_bad", "app_secret": "bad-secret"},
                    }
                }
            }
        )


def test_duplicate_normalized_bot_id_is_rejected():
    with pytest.raises(ValueError, match="duplicate bot id.*sales"):
        BotRegistry.from_config(
            {
                "bots": {
                    "items": {
                        "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
                        " sales ": {"app_id": "cli_sales2", "app_secret": "sales2-secret"},
                    }
                }
            }
        )


def test_legacy_implicit_default_requires_both_credentials_when_started():
    with pytest.raises(ValueError, match="bot default app_secret is required"):
        BotRegistry.from_config({"feishu": {"app_id": "cli_default", "app_secret": ""}})


def test_safe_diagnostics_redact_secrets():
    registry = BotRegistry.from_config(
        {
            "feishu": {"app_id": "cli_default", "app_secret": "super-secret"},
            "bots": {
                "items": {
                    "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
                }
            },
            "bindings": {"chats": {"oc_sales": "sales"}},
        }
    )

    diagnostics = registry.safe_diagnostics()
    text = str(diagnostics)

    assert diagnostics["bot_count"] == 2
    assert diagnostics["chat_binding_count"] == 1
    assert "super-secret" not in text
    assert "sales-secret" not in text


def test_feishu_client_factory_lazily_builds_one_client_per_bot():
    built_configs = []

    def build_client(config):
        built_configs.append(config)
        return {"app_id": config.app_id}

    registry = BotRegistry.from_config(
        {
            "bots": {
                "default": "sales",
                "items": {
                    "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
                    "support": {"app_id": "cli_support", "app_secret": "support-secret"},
                }
            }
        }
    )
    factory = FeishuClientFactory(registry, client_builder=build_client)

    sales_a = factory.get_client("sales")
    sales_b = factory.get_client("sales")
    support = factory.get_client("support")

    assert sales_a is sales_b
    assert sales_a == {"app_id": "cli_sales"}
    assert support == {"app_id": "cli_support"}
    assert [config.app_id for config in built_configs] == ["cli_sales", "cli_support"]
    assert built_configs[0].app_secret == "sales-secret"
