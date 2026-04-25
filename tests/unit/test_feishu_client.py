import json

import pytest

from hermes_feishu_card.feishu_client import FeishuClient, FeishuClientConfig


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
