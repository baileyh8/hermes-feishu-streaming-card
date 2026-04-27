import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.feishu_client import (
    FeishuAPIError,
    FeishuClient,
    FeishuClientConfig,
)


@pytest.fixture
async def feishu_api():
    requests = []
    token_calls = 0

    async def tenant_token(request):
        nonlocal token_calls
        token_calls += 1
        requests.append(("token", await request.json(), dict(request.headers)))
        return web.json_response(
            {
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "tenant-token-1",
                "expire": 7200,
            }
        )

    async def send_message(request):
        requests.append(
            (
                "send",
                request.query.get("receive_id_type"),
                await request.json(),
                dict(request.headers),
            )
        )
        return web.json_response(
            {"code": 0, "msg": "ok", "data": {"message_id": "om_message_1"}}
        )

    async def update_message(request):
        requests.append(
            (
                "update",
                request.match_info["message_id"],
                await request.json(),
                dict(request.headers),
            )
        )
        return web.json_response({"code": 0, "msg": "ok", "data": {}})

    app = web.Application()
    app.router.add_post("/auth/v3/tenant_access_token/internal", tenant_token)
    app.router.add_post("/im/v1/messages", send_message)
    app.router.add_patch("/im/v1/messages/{message_id}", update_message)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, requests, lambda: token_calls
    finally:
        await client.close()


async def test_send_card_fetches_token_and_posts_interactive_message(feishu_api):
    test_client, requests, token_calls = feishu_api
    client = FeishuClient(
        FeishuClientConfig(
            app_id="cli_test",
            app_secret="secret",
            base_url=str(test_client.make_url("/")),
        )
    )

    message_id = await client.send_card("oc_abc", {"schema": "2.0", "body": "你好"})

    assert message_id == "om_message_1"
    assert token_calls() == 1
    token_request = requests[0]
    assert token_request[0] == "token"
    assert token_request[1] == {"app_id": "cli_test", "app_secret": "secret"}
    send_request = requests[1]
    assert send_request[0] == "send"
    assert send_request[1] == "chat_id"
    assert send_request[2]["receive_id"] == "oc_abc"
    assert send_request[2]["msg_type"] == "interactive"
    assert "你好" in send_request[2]["content"]
    assert send_request[3]["Authorization"] == "Bearer tenant-token-1"


async def test_update_card_reuses_cached_token_and_patches_message(feishu_api):
    test_client, requests, token_calls = feishu_api
    client = FeishuClient(
        FeishuClientConfig(
            app_id="cli_test",
            app_secret="secret",
            base_url=str(test_client.make_url("/")),
        )
    )
    await client.send_card("oc_abc", {"schema": "2.0"})

    await client.update_card_message("om_message_1", {"schema": "2.0", "body": "更新"})

    assert token_calls() == 1
    update_request = requests[-1]
    assert update_request[0] == "update"
    assert update_request[1] == "om_message_1"
    assert "更新" in update_request[2]["content"]
    assert update_request[3]["Authorization"] == "Bearer tenant-token-1"


async def test_api_error_raises_without_exposing_secret(unused_tcp_port):
    async def failing_token(request):
        return web.json_response({"code": 999, "msg": "bad secret"}, status=200)

    app = web.Application()
    app.router.add_post("/auth/v3/tenant_access_token/internal", failing_token)
    server = TestServer(app, port=unused_tcp_port)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        client = FeishuClient(
            FeishuClientConfig(
                app_id="cli_test",
                app_secret="super-secret-value",
                base_url=str(test_client.make_url("/")),
            )
        )
        with pytest.raises(FeishuAPIError) as exc_info:
            await client.send_card("oc_abc", {"schema": "2.0"})
    finally:
        await test_client.close()

    message = str(exc_info.value)
    assert "bad secret" in message
    assert "super-secret-value" not in message


async def test_http_error_status_raises():
    async def tenant_token(request):
        return web.json_response(
            {
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "tenant-token-1",
                "expire": 7200,
            }
        )
    async def failing_send(request):
        return web.json_response({"code": 0, "msg": "ok"}, status=500)

    app = web.Application()
    app.router.add_post("/auth/v3/tenant_access_token/internal", tenant_token)
    app.router.add_post("/im/v1/messages", failing_send)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        client = FeishuClient(
            FeishuClientConfig(
                app_id="cli_test",
                app_secret="secret",
                base_url=str(test_client.make_url("/")),
            )
        )

        with pytest.raises(FeishuAPIError, match="HTTP 500"):
            await client.send_card("oc_abc", {"schema": "2.0"})
    finally:
        await test_client.close()
