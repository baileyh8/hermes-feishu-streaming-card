import json

import pytest

from hermes_feishu_card.feishu_client import (
    FeishuAPIError,
    FeishuClient,
    FeishuClientConfig,
    build_delivery_uuid,
)


def test_delivery_uuid_is_stable_bounded_and_route_isolated():
    values = dict(
        bot_id="default",
        chat_id="oc_secret",
        reply_to_message_id="om_secret",
        session_key="profile:message-1",
        delivery_kind="notice",
    )

    first = build_delivery_uuid(**values)

    assert first == build_delivery_uuid(**values)
    assert first.startswith("hfc_")
    assert len(first) == 44
    assert first != build_delivery_uuid(**{**values, "bot_id": "sales"})
    assert "oc_secret" not in first
    assert "om_secret" not in first


def test_feishu_api_error_exposes_only_structured_safe_metadata():
    error = FeishuAPIError(
        "Feishu API HTTP failure",
        status_code=503,
        api_code=999,
        retryable=True,
        outcome="unknown",
        retry_after_seconds=1.5,
        retry_count=2,
    )

    assert error.status_code == 503
    assert error.api_code == 999
    assert error.retryable is True
    assert error.outcome == "unknown"
    assert error.retry_after_seconds == 1.5
    assert error.retry_count == 2
    assert "secret" not in str(error).lower()


@pytest.mark.parametrize("app_id", ["", "   "])
def test_config_requires_app_id_for_real_client(app_id):
    with pytest.raises(ValueError, match="app_id"):
        FeishuClientConfig(app_id=app_id, app_secret="secret")


@pytest.mark.parametrize("app_secret", ["", "   "])
def test_config_requires_app_secret_for_real_client(app_secret):
    with pytest.raises(ValueError, match="app_secret"):
        FeishuClientConfig(app_id="cli_a", app_secret=app_secret)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "   ",
        "ftp://open.feishu.cn",
        "https://",
        "https://:443/open-apis",
        "https://@/open-apis",
        "https://open.feishu.cn/open-apis ",
        "https:// open.feishu.cn/open-apis",
        "https://open.feishu.cn:bad/open-apis",
        "https://user:pass@open.feishu.cn/open-apis",
    ],
)
def test_config_requires_http_base_url(base_url):
    with pytest.raises(ValueError, match="base_url"):
        FeishuClientConfig(app_id="cli_a", app_secret="sec", base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    ["http://open.feishu.cn/open-apis", "https://open.feishu.cn/open-apis"],
)
def test_config_accepts_http_base_url(base_url):
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec", base_url=base_url)
    assert cfg.base_url == base_url


@pytest.mark.parametrize("timeout_seconds", [0, -1, True, False, "30", float("nan"), float("inf")])
def test_config_requires_positive_numeric_timeout(timeout_seconds):
    with pytest.raises(ValueError, match="timeout_seconds"):
        FeishuClientConfig(
            app_id="cli_a",
            app_secret="sec",
            timeout_seconds=timeout_seconds,
        )


@pytest.mark.parametrize("chat_id", ["", "   "])
def test_build_message_payload_requires_chat_id(chat_id):
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    with pytest.raises(ValueError, match="chat_id"):
        client.build_message_payload(chat_id, {"schema": "2.0"})


@pytest.mark.parametrize("card", [None, [], "card"])
def test_build_message_payload_requires_dict_card(card):
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    with pytest.raises(TypeError, match="card"):
        client.build_message_payload("oc_abc", card)


def test_build_message_payload_serializes_card():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    card = {"schema": "2.0", "header": {"title": "hello"}}
    payload = client.build_message_payload("oc_abc", card)
    assert payload["receive_id"] == "oc_abc"
    assert payload["msg_type"] == "interactive"
    assert '"schema": "2.0"' in payload["content"]
    assert json.loads(payload["content"]) == card


def test_build_message_payload_keeps_chat_id_for_thread_reply_anchor():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    card = {"schema": "2.0", "header": {"title": "hello"}}

    payload = client.build_message_payload(
        "oc_abc",
        card,
        thread_id="omt_thread",
        reply_to_message_id="om_user_message",
    )

    assert payload["receive_id"] == "oc_abc"
    assert payload["msg_type"] == "interactive"
    assert json.loads(payload["content"]) == card


def test_build_message_payload_preserves_non_ascii_content():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    card = {"schema": "2.0", "header": {"title": "你好"}}
    payload = client.build_message_payload("oc_abc", card)
    assert "你好" in payload["content"]
    assert "\\u" not in payload["content"]
    assert json.loads(payload["content"]) == card


def test_build_message_payload_rejects_unserializable_card():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    with pytest.raises(TypeError):
        client.build_message_payload("oc_abc", {"bad": object()})
