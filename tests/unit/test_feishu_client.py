import json

import aiohttp
import pytest

from hermes_feishu_card.card_limits import CardLimitExceeded, serialize_card_json

from hermes_feishu_card.feishu_client import (
    _OPEN_ID_CACHE_MAX,
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
    assert payload["content"] == serialize_card_json(card)
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


@pytest.mark.asyncio
async def test_send_card_uses_reply_api_for_reply_in_thread_without_thread_id(monkeypatch):
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    card = {"schema": "2.0", "header": {"title": "hello"}}
    calls = []

    async def fake_token():
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "token": token,
                "params": params,
                "json_body": json_body,
            }
        )
        return {"code": 0, "data": {"message_id": "om_card"}}

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    message_id = await client.send_card(
        "oc_abc",
        card,
        reply_to_message_id="om_user_message",
        reply_in_thread=True,
    )

    assert message_id == "om_card"
    assert calls == [
        {
            "method": "POST",
            "path": "/im/v1/messages/om_user_message/reply",
            "token": "tenant-token",
            "params": None,
            "json_body": {
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
                "reply_in_thread": True,
            },
        }
    ]


@pytest.mark.asyncio
async def test_send_card_rejects_reply_in_thread_without_reply_anchor(monkeypatch):
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    card = {"schema": "2.0", "header": {"title": "hello"}}

    async def unexpected_token():
        raise AssertionError("token lookup must not run without a reply anchor")

    monkeypatch.setattr(client, "_tenant_token", unexpected_token)

    with pytest.raises(ValueError, match="reply_to_message_id"):
        await client.send_card("oc_abc", card, reply_in_thread=True)


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


def test_build_message_payload_rejects_card_over_exact_delivery_limits():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    oversized = {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": "x" * 28_000},
            ]
        },
    }

    with pytest.raises(CardLimitExceeded, match="json_bytes"):
        client.build_message_payload("oc_abc", oversized)


@pytest.mark.asyncio
async def test_update_rejects_unsafe_card_before_token_or_network():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    oversized = {
        "body": {
            "elements": [
                {"tag": "markdown", "content": "x" * 28_000},
            ]
        }
    }

    async def forbidden_token():
        raise AssertionError("unsafe card reached token or network boundary")

    client._tenant_token = forbidden_token

    with pytest.raises(CardLimitExceeded, match="json_bytes"):
        await client.update_card_message("om_test", oversized)


async def _captured_session_kwargs(monkeypatch, base_url=None):
    captured: dict[str, object] = {}

    class _FakeResponse:
        status = 200
        headers: dict[str, str] = {}

        async def json(self, content_type=None):
            return {"code": 0, "data": {}}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    class _FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def request(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)
    overrides = {"base_url": base_url} if base_url is not None else {}
    client = FeishuClient(
        FeishuClientConfig(app_id="cli_a", app_secret="sec", **overrides)
    )

    await client._request_json("POST", "/im/v1/messages")

    return captured


@pytest.mark.asyncio
async def test_request_json_trusts_proxy_environment_for_remote_endpoint(monkeypatch):
    """Hosts without direct egress reach open.feishu.cn only via HTTP(S)_PROXY.

    aiohttp ignores the proxy environment unless the session opts in with
    trust_env, so without it every card delivery times out and the hook reports
    an unknown outcome.
    """

    captured = await _captured_session_kwargs(monkeypatch)

    assert captured.get("trust_env") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8080/open-apis",
        "http://localhost:8080/open-apis",
        "http://10.1.2.3/open-apis",
        "http://[::1]:8080/open-apis",
    ],
)
async def test_request_json_bypasses_proxy_for_local_endpoint(monkeypatch, base_url):
    """A loopback or intranet endpoint must not be routed through the proxy."""

    captured = await _captured_session_kwargs(monkeypatch, base_url=base_url)

    assert captured.get("trust_env") is False


@pytest.mark.asyncio
async def test_resolve_open_id_caches_success_and_retries_failures(monkeypatch):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    calls = []
    fail = [True]

    async def fake_token():
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        calls.append(path)
        if fail[0]:
            raise RuntimeError("timeout")
        return {"code": 0, "data": {"user": {"open_id": "ou_resolved"}}}

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    assert await client.resolve_open_id("u_tenant") == ""
    fail[0] = False
    assert await client.resolve_open_id("u_tenant") == "ou_resolved"
    assert await client.resolve_open_id("u_tenant") == "ou_resolved"
    assert len(calls) == 2
    assert await client.resolve_open_id("ou_passthrough") == "ou_passthrough"
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["", "   ", 123, None])
async def test_resolve_open_id_rejects_invalid_keys_without_io(monkeypatch, user_id):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append(path)
        return {"code": 0, "data": {"user": {"open_id": "ou_resolved"}}}

    monkeypatch.setattr(client, "_request_json", fake_request)

    # Only the request hook is patched: any token fetch would also land here,
    # so an empty result with zero calls proves no I/O of any kind happened.
    assert await client.resolve_open_id(user_id) == ""
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_open_id_strips_surrounding_whitespace(monkeypatch):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    calls = []

    async def fake_token():
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        calls.append(path)
        return {"code": 0, "data": {"user": {"open_id": "ou_resolved"}}}

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    assert await client.resolve_open_id("  u_tenant  ") == "ou_resolved"
    assert calls == ["/contact/v3/users/u_tenant"]


@pytest.mark.asyncio
async def test_resolve_open_id_user_not_found_stays_uncached(monkeypatch):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    calls = []
    fail = [True]

    async def fake_token():
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        calls.append(path)
        if fail[0]:
            raise FeishuAPIError(
                "Feishu API HTTP failure", status_code=404, retryable=False
            )
        return {"code": 0, "data": {"user": {"open_id": "ou_resolved"}}}

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    # A definitive "user not found" is not cached either: retrying costs one
    # API call per notice, while caching "" would pin a member created after
    # the first notice (or a lookup-order bug) as permanently unmentionable.
    assert await client.resolve_open_id("u_tenant") == ""
    assert await client.resolve_open_id("u_tenant") == ""
    assert len(calls) == 2
    fail[0] = False
    assert await client.resolve_open_id("u_tenant") == "ou_resolved"
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"code": 0, "data": {"user": None}},
        {"code": 0, "data": {"user": {"open_id": "not-an-open-id"}}},
        {"code": 99991663, "msg": "permission denied"},
    ],
)
async def test_resolve_open_id_ignores_malformed_or_denied_responses(
    monkeypatch, body
):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    calls = []

    async def fake_token():
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        calls.append(path)
        return body

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    assert await client.resolve_open_id("u_tenant") == ""
    assert await client.resolve_open_id("u_tenant") == ""
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_resolve_open_id_clears_cache_when_full(monkeypatch):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    calls = []

    async def fake_token():
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        calls.append(path)
        return {"code": 0, "data": {"user": {"open_id": "ou_new"}}}

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    # One entry per distinct requester; at the cap the memo clears wholesale
    # instead of growing unbounded in a long-lived sidecar process.
    for index in range(_OPEN_ID_CACHE_MAX):
        client._open_id_cache[f"u_{index}"] = f"ou_{index}"
    assert await client.resolve_open_id("u_fresh") == "ou_new"
    assert client._open_id_cache == {"u_fresh": "ou_new"}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_to_message_id", [None, "", "   ", 123])
async def test_send_text_rejects_thread_request_without_valid_reply_anchor(
    monkeypatch, reply_to_message_id
):
    client = FeishuClient(FeishuClientConfig(app_id="cli_a", app_secret="sec"))
    token_calls = []
    api_calls = []

    async def fake_token():
        token_calls.append(1)
        return "tenant-token"

    async def fake_request(method, path, *, token=None, params=None, json_body=None):
        api_calls.append((method, path))
        return {"code": 0, "data": {"message_id": "om_text"}}

    monkeypatch.setattr(client, "_tenant_token", fake_token)
    monkeypatch.setattr(client, "_request_json", fake_request)

    with pytest.raises(ValueError, match="reply_to_message_id"):
        await client.send_text_message(
            "oc_abc",
            "thread completion notice",
            thread_id="omt_topic",
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=True,
        )

    # The guard fires before any I/O: no token fetch, no HTTP request.
    assert token_calls == []
    assert api_calls == []
