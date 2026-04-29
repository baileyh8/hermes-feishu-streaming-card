import pytest

from hermes_feishu_card.bots import FeishuClientFactory
from hermes_feishu_card.feishu_client import FeishuClient
import hermes_feishu_card.runner as runner
from hermes_feishu_card.runner import (
    NoopFeishuClient,
    build_feishu_boundary,
    build_feishu_client,
    main,
)


def test_build_feishu_client_uses_noop_when_credentials_missing():
    client = build_feishu_client({"feishu": {"app_id": "", "app_secret": ""}})

    assert isinstance(client, NoopFeishuClient)


def test_build_feishu_client_uses_real_client_when_credentials_present():
    client = build_feishu_client(
        {
            "feishu": {
                "app_id": "cli_test",
                "app_secret": "secret",
                "base_url": "http://127.0.0.1/open-apis",
                "timeout_seconds": 3,
            }
        }
    )

    assert isinstance(client, FeishuClient)
    assert client.config.app_id == "cli_test"
    assert client.config.base_url == "http://127.0.0.1/open-apis"
    assert client.config.timeout_seconds == 3


def test_build_feishu_boundary_returns_factory_and_routes_named_bots():
    boundary = build_feishu_boundary(
        {
            "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
            "bots": {
                "default": "default",
                "items": {
                    "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"}
                },
            },
            "bindings": {"chats": {"oc_sales": "sales"}},
        }
    )

    result = boundary.router(type("Event", (), {"chat_id": "oc_sales", "data": {}})())

    assert isinstance(boundary.client, FeishuClientFactory)
    assert result.bot_id == "sales"


def test_feishu_boundary_router_accepts_optional_event_data_fields():
    boundary = build_feishu_boundary(
        {
            "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
            "bots": {
                "default": "default",
                "items": {
                    "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"}
                },
            },
            "bindings": {"chats": {"oc_sales": "sales"}},
        }
    )
    event = type(
        "Event",
        (),
        {
            "chat_id": "oc_sales",
            "data": {
                "chat_type": "group",
                "tenant_key": "tenant-1",
                "agent_id": "agent-1",
                "profile_id": "profile-1",
            },
        },
    )()

    result = boundary.router(event)

    assert result.bot_id == "sales"


def test_main_passes_boundary_to_create_app_when_bot_credentials_exist(monkeypatch):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
        "card": {"title": "Credentialed Card"},
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    def fake_create_app(feishu_client, **kwargs):
        captured["feishu_client"] = feishu_client
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runner, "create_app", fake_create_app)
    monkeypatch.setattr(runner.web, "run_app", lambda app, **kwargs: None)

    assert main(["--config", "config.yaml", "--token", "token-1"]) == 0

    assert isinstance(captured["feishu_client"], FeishuClientFactory)
    assert captured["kwargs"]["process_token"] == "token-1"
    assert captured["kwargs"]["card_config"] == {"title": "Credentialed Card"}
    assert captured["kwargs"]["bot_router"] is not None


def test_main_uses_noop_without_any_credentials(monkeypatch):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {},
        "card": {"title": "Noop Card"},
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    def fake_create_app(feishu_client, **kwargs):
        captured["feishu_client"] = feishu_client
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runner, "create_app", fake_create_app)
    monkeypatch.setattr(runner.web, "run_app", lambda app, **kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert isinstance(captured["feishu_client"], NoopFeishuClient)
    assert captured["kwargs"]["card_config"] == {"title": "Noop Card"}
    assert captured["kwargs"]["bot_router"] is None


def test_main_ignores_partial_legacy_feishu_when_named_bot_credentials_exist(
    monkeypatch,
):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {"app_id": "partial-default"},
        "bots": {
            "default": "sales",
            "items": {
                "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
            },
        },
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    def fake_create_app(feishu_client, **kwargs):
        captured["feishu_client"] = feishu_client
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runner, "create_app", fake_create_app)
    monkeypatch.setattr(runner.web, "run_app", lambda app, **kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert isinstance(captured["feishu_client"], FeishuClientFactory)
    assert captured["kwargs"]["bot_router"] is not None


def test_main_rejects_malformed_named_bot_without_leaking_secret(monkeypatch):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "bots": {
            "default": "sales",
            "items": {
                "sales": {"app_id": "cli_sales", "app_secret": "sales-secret"},
                "inactive": {"app_id": "cli_inactive"},
            },
        },
    }

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    with pytest.raises(ValueError) as exc_info:
        main(["--config", "config.yaml"])

    message = str(exc_info.value)
    assert "inactive" in message
    assert "app_secret is required" in message
    assert "sales-secret" not in message
