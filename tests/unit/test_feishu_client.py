import pytest

from hermes_feishu_card.feishu_client import FeishuClient, FeishuClientConfig


def test_config_requires_credentials_for_real_client():
    with pytest.raises(ValueError, match="app_id"):
        FeishuClientConfig(app_id="", app_secret="secret")


def test_build_message_payload_serializes_card():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    payload = client.build_message_payload("oc_abc", {"schema": "2.0"})
    assert payload["receive_id"] == "oc_abc"
    assert payload["msg_type"] == "interactive"
    assert '"schema": "2.0"' in payload["content"]
