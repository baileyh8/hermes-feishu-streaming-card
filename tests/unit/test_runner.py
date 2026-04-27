from hermes_feishu_card.feishu_client import FeishuClient
from hermes_feishu_card.runner import NoopFeishuClient, build_feishu_client


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
