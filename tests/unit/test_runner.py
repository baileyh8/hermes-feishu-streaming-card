import logging
import re
import sys

import pytest
from pathlib import Path

from hermes_feishu_card.bots import FeishuClientFactory
from hermes_feishu_card.feishu_client import FeishuAPIError, FeishuClient
from hermes_feishu_card.delivery_policy import ReloadingDeliveryPolicyProvider
import hermes_feishu_card.runner as runner
from hermes_feishu_card.runner import (
    NoopFeishuClient,
    build_feishu_boundary,
    build_feishu_client,
    main,
)


def test_configure_logging_stamps_every_sidecar_line(monkeypatch):
    """The sidecar log must carry a timestamp instead of falling through to ``logging.lastResort``.

    Maintainer note (contract): with no root handler the process used lastResort — WARNING and above
    only, bare message, no timestamp. A live investigation then could not place 50 ``recall failed``
    lines in time or tie them to a run, and every INFO/DEBUG breadcrumb was dropped entirely.
    """
    monkeypatch.setattr(logging.root, "handlers", [])

    runner._configure_logging()

    handler = logging.root.handlers[0]
    assert handler.formatter is not None
    line = handler.formatter.format(
        logging.LogRecord(
            "hermes_feishu_card.server",
            logging.WARNING,
            __file__,
            1,
            "Feishu card recall failed: FeishuAPIError",
            None,
            None,
        )
    )
    assert "Feishu card recall failed: FeishuAPIError" in line
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", line), line


def test_build_feishu_client_uses_noop_when_credentials_missing():
    client = build_feishu_client({"feishu": {"app_id": "", "app_secret": ""}})

    assert isinstance(client, NoopFeishuClient)


async def test_noop_client_never_fabricates_message_id():
    with pytest.raises(FeishuAPIError) as exc_info:
        await NoopFeishuClient().send_card("test-chat", {})

    assert exc_info.value.outcome == "not_sent"


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


def test_main_passes_boundary_to_create_app_when_bot_credentials_exist(
    monkeypatch,
    tmp_path,
):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {"app_id": "cli_default", "app_secret": "default-secret"},
        "card": {"title": "Credentialed Card"},
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path, **_kwargs: config)
    private_state = tmp_path / "private-state"
    monkeypatch.setattr(runner, "state_dir", lambda: private_state, raising=False)

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
    assert captured["kwargs"]["operations_config_path"] == "config.yaml"
    assert captured["kwargs"]["integrity_mode"] == "notify"
    assert captured["kwargs"]["expected_runtime_package_version"] == runner.__version__
    assert captured["kwargs"]["package_version"] == runner.__version__
    assert captured["kwargs"]["python_identity"].startswith("python-sha256:")
    assert sys.executable not in captured["kwargs"]["python_identity"]
    assert captured["kwargs"]["runtime_integrity_state_directory"] == private_state
    with pytest.raises(runner.web.GracefulExit):
        captured["kwargs"]["shutdown_callback"]()
    assert isinstance(
        captured["kwargs"]["delivery_policy"],
        ReloadingDeliveryPolicyProvider,
    )


def test_main_rejects_non_loopback_listener_without_explicit_opt_in(monkeypatch):
    config = {
        "server": {"host": "0.0.0.0", "port": 8765},
        "feishu": {},
        "card": {},
    }
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda *_args, **_kwargs: pytest.fail("unsafe listener must not start"),
    )

    with pytest.raises(ValueError, match="allow_non_loopback"):
        main(["--config", "config.yaml"])


def test_main_requires_private_transport_root_for_non_loopback_listener(monkeypatch):
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8765,
            "allow_non_loopback": True,
        },
        "feishu": {},
        "card": {},
    }
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(
        runner,
        "ensure_transport_root_secret",
        lambda: (_ for _ in ()).throw(OSError("insecure state directory")),
    )
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda *_args, **_kwargs: pytest.fail("unauthenticated listener must not start"),
    )

    with pytest.raises(RuntimeError, match="event authentication"):
        main(["--config", "config.yaml"])


def test_main_enforces_event_auth_for_explicit_non_loopback_listener(monkeypatch):
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8765,
            "allow_non_loopback": True,
        },
        "feishu": {},
        "card": {},
    }
    captured = {}
    root_secret = b"r" * 32
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(runner, "ensure_transport_root_secret", lambda: root_secret)
    monkeypatch.setattr(
        runner, "transport_root_privacy_verified", lambda: True, raising=False
    )
    monkeypatch.setattr(
        runner,
        "create_app",
        lambda _client, **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda _app, **kwargs: captured.update({"run_app": kwargs}),
    )

    assert main(["--config", "config.yaml"]) == 0

    assert captured["event_auth_required"] is True
    assert captured["operations_transport_root_secret"] == root_secret
    assert captured["run_app"]["host"] == "0.0.0.0"


def test_main_rejects_non_loopback_when_windows_acl_privacy_is_unverified(monkeypatch):
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8765,
            "allow_non_loopback": True,
        },
        "feishu": {},
        "card": {},
    }
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(runner, "ensure_transport_root_secret", lambda: b"r" * 32)
    monkeypatch.setattr(
        runner, "transport_root_privacy_verified", lambda: False, raising=False
    )
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda *_args, **_kwargs: pytest.fail("unverified listener must not start"),
    )

    with pytest.raises(RuntimeError, match="private state directory"):
        main(["--config", "config.yaml"])


def test_main_warns_when_loopback_transport_acl_privacy_is_unverified(
    monkeypatch, caplog
):
    config = {
        "server": {"host": "127.0.0.1", "port": 8765},
        "feishu": {},
        "card": {},
    }
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(runner, "ensure_transport_root_secret", lambda: b"r" * 32)
    monkeypatch.setattr(
        runner, "transport_root_privacy_verified", lambda: False, raising=False
    )
    monkeypatch.setattr(runner, "create_app", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runner.web, "run_app", lambda *_args, **_kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert "ACL privacy is unverified" in caplog.text
    assert "loopback local-process trust only" in caplog.text


def test_main_keeps_loopback_listener_backward_compatible(monkeypatch):
    config = {
        "server": {"host": "::1", "port": 8765},
        "feishu": {},
        "card": {},
    }
    captured = {}
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(
        runner,
        "create_app",
        lambda _client, **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(runner.web, "run_app", lambda *_args, **_kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert captured["event_auth_required"] is False


def test_main_rejects_non_boolean_non_loopback_opt_in(monkeypatch):
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8765,
            "allow_non_loopback": "true",
        },
        "feishu": {},
        "card": {},
    }
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda *_args, **_kwargs: pytest.fail("invalid opt-in must not start"),
    )

    with pytest.raises(ValueError, match="must be a boolean"):
        main(["--config", "config.yaml"])


def test_main_uses_noop_without_any_credentials(monkeypatch, caplog):
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

    with caplog.at_level("WARNING"):
        assert main(["--config", "config.yaml"]) == 0

    assert isinstance(captured["feishu_client"], NoopFeishuClient)
    assert captured["kwargs"]["card_config"] == {"title": "Noop Card"}
    assert captured["kwargs"]["bot_router"] is None
    assert captured["kwargs"]["noop_mode"] is True
    assert "running in no-op delivery mode" in caplog.text


def test_main_passes_config_scoped_hermes_root_to_operations(monkeypatch, tmp_path):
    config = {"server": {"host": "127.0.0.1", "port": 0}, "feishu": {}, "card": {}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server: {}\n", encoding="utf-8")
    hermes_root = tmp_path / "custom-hermes"
    captured = {}
    monkeypatch.setattr(runner, "load_config", lambda path: config)
    monkeypatch.setattr(
        runner,
        "resolve_operations_hermes_root",
        lambda _explicit=None, **_kwargs: hermes_root,
    )
    monkeypatch.setattr(
        runner, "create_app", lambda _client, **kwargs: captured.update(kwargs) or object()
    )
    monkeypatch.setattr(runner.web, "run_app", lambda *_args, **_kwargs: None)

    assert main(["--config", str(config_path)]) == 0
    assert captured["operations_hermes_root"] == hermes_root


def test_main_explicit_hermes_root_wins_over_conflicting_selected_env(
    monkeypatch, tmp_path
):
    config = {"server": {"host": "127.0.0.1", "port": 0}, "feishu": {}, "card": {}}
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "selected.env"
    explicit_root = tmp_path / "verified-hermes"
    conflicting_root = tmp_path / "wrong-hermes"
    config_path.write_text("server: {}\n", encoding="utf-8")
    env_path.write_text(f"HERMES_DIR={conflicting_root}\n", encoding="utf-8")
    captured = {}
    resolutions = []
    monkeypatch.setattr(runner, "load_config", lambda path, **_kwargs: config)
    monkeypatch.setattr(
        runner,
        "resolve_operations_hermes_root",
        lambda explicit=None, **kwargs: resolutions.append((explicit, kwargs))
        or Path(explicit),
    )
    monkeypatch.setattr(
        runner, "create_app", lambda _client, **kwargs: captured.update(kwargs) or object()
    )
    monkeypatch.setattr(runner.web, "run_app", lambda *_args, **_kwargs: None)

    assert main(
        [
            "--config",
            str(config_path),
            "--env-file",
            str(env_path),
            "--hermes-dir",
            str(explicit_root),
        ]
    ) == 0
    assert resolutions == [
        (
            str(explicit_root),
            {"config_path": str(config_path), "env_file": str(env_path)},
        )
    ]
    assert captured["operations_hermes_root"] == explicit_root


@pytest.mark.parametrize(
    ("configured_host", "expected_host"),
    [
        ("192.0.2.10", ["192.0.2.10", "127.0.0.1"]),
        ("2001:db8::10", ["2001:db8::10", "::1"]),
        ("0.0.0.0", "0.0.0.0"),
        ("::", "::"),
    ],
)
def test_main_adds_loopback_control_listener_only_for_specific_non_loopback_host(
    configured_host, expected_host, monkeypatch
):
    config = {
        "server": {
            "host": configured_host,
            "port": 8765,
            "allow_non_loopback": True,
        },
        "feishu": {},
        "card": {},
    }
    captured = {}
    monkeypatch.setattr(runner, "load_config", lambda _path: config)
    monkeypatch.setattr(runner, "ensure_transport_root_secret", lambda: b"r" * 32)
    monkeypatch.setattr(runner, "transport_root_privacy_verified", lambda: True)
    monkeypatch.setattr(runner, "create_app", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda _app, **kwargs: captured.update(kwargs),
    )

    assert main(["--config", "config.yaml"]) == 0
    assert captured["host"] == expected_host


def test_managed_detached_runner_exits_before_listen_without_matching_pidfile(
    monkeypatch,
):
    config = {"server": {"host": "127.0.0.1", "port": 8765}, "feishu": {}, "card": {}}
    handshakes = []
    monkeypatch.setattr(runner, "load_config", lambda _path: config)
    monkeypatch.setattr(
        runner,
        "wait_for_managed_pidfile",
        lambda pid, token: handshakes.append((pid, token)) or False,
    )
    monkeypatch.setattr(
        runner.web,
        "run_app",
        lambda *_args, **_kwargs: pytest.fail("unmanaged runner must not listen"),
    )

    assert main(
        [
            "--config",
            "config.yaml",
            "--token",
            "owned-token",
            "--managed-pidfile",
        ]
    ) == 1
    assert handshakes == [(runner.os.getpid(), "owned-token")]


def test_main_passes_selected_env_file_to_operations_root_resolution(monkeypatch, tmp_path):
    config = {"server": {"host": "127.0.0.1", "port": 0}, "feishu": {}, "card": {}}
    config_path = tmp_path / "config.yaml"
    selected_env = tmp_path / "selected.env"
    hermes_root = tmp_path / "selected-hermes"
    config_path.write_text("server: {}\n", encoding="utf-8")
    selected_env.write_text(f"HERMES_DIR={hermes_root}\n", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(runner, "load_config", lambda path, **_kwargs: config)
    monkeypatch.setattr(
        runner, "create_app", lambda _client, **kwargs: captured.update(kwargs) or object()
    )
    monkeypatch.setattr(runner.web, "run_app", lambda *_args, **_kwargs: None)

    assert main(["--config", str(config_path), "--env-file", str(selected_env)]) == 0
    assert captured["operations_hermes_root"] == hermes_root


def test_main_loads_feishu_credentials_from_selected_env(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    selected_env = tmp_path / "selected.env"
    config_path.write_text(
        "server:\n  host: 127.0.0.1\n  port: 8765\n",
        encoding="utf-8",
    )
    selected_env.write_text(
        "FEISHU_APP_ID=selected_app\nFEISHU_APP_SECRET=selected_secret\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    captured = {}
    monkeypatch.setattr(
        runner,
        "create_app",
        lambda client, **kwargs: captured.update(
            {"client": client, "kwargs": kwargs}
        )
        or object(),
    )
    monkeypatch.setattr(runner.web, "run_app", lambda *_args, **_kwargs: None)

    assert main(
        ["--config", str(config_path), "--env-file", str(selected_env)]
    ) == 0

    assert isinstance(captured["client"], FeishuClientFactory)
    assert captured["kwargs"]["noop_mode"] is False
    assert captured["kwargs"]["operations_env_file"] == str(selected_env)


def test_main_uses_callback_interactions_for_localhost_auto_mode(monkeypatch):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {},
        "card": {"title": "Local Card", "interaction_mode": "auto"},
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    def fake_create_app(feishu_client, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runner, "create_app", fake_create_app)
    monkeypatch.setattr(runner.web, "run_app", lambda app, **kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert captured["kwargs"]["card_config"]["interaction_mode"] == "callback"


def test_main_preserves_explicit_text_interactions_on_localhost(monkeypatch):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {},
        "card": {"title": "Local Card", "interaction_mode": "text"},
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    def fake_create_app(feishu_client, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runner, "create_app", fake_create_app)
    monkeypatch.setattr(runner.web, "run_app", lambda app, **kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert captured["kwargs"]["card_config"]["interaction_mode"] == "text"


def test_main_preserves_explicit_callback_interactions_on_localhost(monkeypatch):
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "feishu": {},
        "card": {"title": "Callback Card", "interaction_mode": "callback"},
    }
    captured = {}

    monkeypatch.setattr(runner, "load_config", lambda path: config)

    def fake_create_app(feishu_client, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runner, "create_app", fake_create_app)
    monkeypatch.setattr(runner.web, "run_app", lambda app, **kwargs: None)

    assert main(["--config", "config.yaml"]) == 0

    assert captured["kwargs"]["card_config"]["interaction_mode"] == "callback"


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


def test_build_feishu_boundary_single_profile_unchanged():
    """单 profile 模式下，client 应为 FeishuClientFactory（非 dict），行为不变。"""
    config = {
        "feishu": {"app_id": "cli_a", "app_secret": "s_a"},
        "bots": {"default": "default", "items": {}},
        "bindings": {"chats": {}, "fallback_bot": "default"},
        "server": {"host": "127.0.0.1", "port": 8765},
    }
    boundary = build_feishu_boundary(config)
    assert isinstance(boundary.client, FeishuClientFactory)
    assert hasattr(boundary.client, "get_client")


def test_build_feishu_boundary_multi_profile():
    """多 profile 模式下，client 应为 dict[str, FeishuClientFactory]，router 可调用。"""
    config = {
        "profiles": {
            "default": {
                "feishu": {"app_id": "cli_a", "app_secret": "s_a"},
                "bots": {"default": "default", "items": {}},
                "bindings": {"chats": {}, "fallback_bot": "default"},
            },
            "work": {
                "feishu": {"app_id": "cli_b", "app_secret": "s_b"},
                "bots": {"default": "default", "items": {}},
                "bindings": {"chats": {}, "fallback_bot": "default"},
            },
        },
        "server": {"host": "127.0.0.1", "port": 8765},
    }
    boundary = build_feishu_boundary(config)
    assert isinstance(boundary.client, dict)
    assert set(boundary.client.keys()) == {"default", "work"}
    for factory in boundary.client.values():
        assert isinstance(factory, FeishuClientFactory)
    assert callable(boundary.router)


def test_build_feishu_boundary_multi_profile_preserves_profile_card():
    config = {
        "card": {"title": "Global"},
        "profiles": {
            "default": {
                "feishu": {"app_id": "cli_a", "app_secret": "s_a"},
                "bots": {"default": "default", "items": {}},
                "bindings": {"chats": {}, "fallback_bot": "default"},
                "card": {"title": "Default Profile"},
            },
            "work": {
                "feishu": {"app_id": "cli_b", "app_secret": "s_b"},
                "bots": {"default": "default", "items": {}},
                "bindings": {"chats": {}, "fallback_bot": "default"},
                "card": {"title": "Work Profile"},
            },
        },
        "server": {"host": "127.0.0.1", "port": 8765},
    }

    boundary = build_feishu_boundary(config)

    assert boundary.client["work"].profile_card == {"title": "Work Profile"}


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


def test_has_any_feishu_credentials_detects_profiles():
    """profiles-only 配置也应被识别为有凭据。"""
    assert runner._has_any_feishu_credentials(
        {
            "profiles": {
                "default": {
                    "feishu": {"app_id": "cli_a", "app_secret": "s_a"},
                }
            }
        }
    ) is True


def test_main_uses_boundary_with_profiles_only(monkeypatch):
    """仅有 profiles、无顶层 feishu/bots 时，仍应走 build_feishu_boundary 路径。"""
    config = {
        "server": {"host": "127.0.0.1", "port": 0},
        "profiles": {
            "default": {
                "feishu": {"app_id": "cli_a", "app_secret": "s_a"},
                "bots": {"default": "default", "items": {}},
                "bindings": {"chats": {}, "fallback_bot": "default"},
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

    # profiles-only → multi-profile path → client 是 dict
    assert isinstance(captured["feishu_client"], dict)
    assert "default" in captured["feishu_client"]
    assert captured["kwargs"]["bot_router"] is not None


def test_resolve_card_config_merges_global_profile_and_bot_in_priority_order():
    resolved = runner.resolve_card_config(
        {"title": "Global", "footer_fields": ["model"], "max_chars": 120},
        {"title": "Profile", "footer_fields": ["duration"]},
        {"title": "Bot"},
    )

    assert resolved == {
        "title": "Bot",
        "footer_fields": ["duration"],
        "max_chars": 120,
    }
