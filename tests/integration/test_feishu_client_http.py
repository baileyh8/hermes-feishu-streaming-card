import asyncio
import pytest
import subprocess
import sys
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.feishu_client import (
    FeishuAPIError,
    FeishuClient,
    FeishuClientConfig,
)


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
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


async def test_http_error_status_includes_response_code_and_message():
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
        return web.json_response(
            {"code": 9499, "msg": "invalid card payload tenant-token-1"},
            status=400,
        )

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

        with pytest.raises(FeishuAPIError) as exc_info:
            await client.send_card("oc_abc", {"schema": "2.0"})
    finally:
        await test_client.close()

    message = str(exc_info.value)
    assert "HTTP 400" in message
    assert "9499" in message
    assert "invalid card payload" in message
    assert "tenant-token-1" not in message


async def test_smoke_command_sends_and_updates_card(feishu_api, tmp_path):
    test_client, requests, _ = feishu_api
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "feishu:\n"
        "  app_id: cli_test\n"
        "  app_secret: secret\n"
        f"  base_url: {test_client.make_url('/')}\n",
        encoding="utf-8",
    )

    result = await asyncio.to_thread(
        run_cli,
        "smoke-feishu-card",
        "--config",
        str(config_path),
        "--chat-id",
        "oc_abc",
    )

    assert result.returncode == 0, result.stderr
    assert "smoke ok" in result.stdout
    assert "om_message_1" in result.stdout
    assert [request[0] for request in requests] == ["token", "send", "update"]


async def test_smoke_command_uses_configured_card_title(feishu_api, tmp_path):
    test_client, requests, _ = feishu_api
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "feishu:\n"
        "  app_id: cli_test\n"
        "  app_secret: secret\n"
        f"  base_url: {test_client.make_url('/')}\n"
        "card:\n"
        "  title: 研发助手\n",
        encoding="utf-8",
    )

    result = await asyncio.to_thread(
        run_cli,
        "smoke-feishu-card",
        "--config",
        str(config_path),
        "--chat-id",
        "oc_abc",
    )

    assert result.returncode == 0, result.stderr
    assert "研发助手" in requests[1][2]["content"]
    assert "研发助手" in requests[2][2]["content"]


async def test_smoke_command_requires_credentials(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("feishu:\n  app_id: ''\n  app_secret: ''\n", encoding="utf-8")

    result = await asyncio.to_thread(
        run_cli,
        "smoke-feishu-card",
        "--config",
        str(config_path),
        "--chat-id",
        "oc_abc",
    )

    assert result.returncode != 0
    assert "FEISHU_APP_ID" in result.stderr


async def test_smoke_command_send_failure_does_not_leak_secret(tmp_path):
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
        return web.json_response({"code": 999, "msg": "send failed super-secret-value"})

    app = web.Application()
    app.router.add_post("/auth/v3/tenant_access_token/internal", tenant_token)
    app.router.add_post("/im/v1/messages", failing_send)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "feishu:\n"
            "  app_id: cli_test\n"
            "  app_secret: super-secret-value\n"
            f"  base_url: {test_client.make_url('/')}\n",
            encoding="utf-8",
        )

        result = await asyncio.to_thread(
            run_cli,
            "smoke-feishu-card",
            "--config",
            str(config_path),
            "--chat-id",
            "oc_abc",
        )
    finally:
        await test_client.close()

    assert result.returncode != 0
    assert "send failed" in result.stderr
    assert "super-secret-value" not in result.stderr
    assert "tenant-token-1" not in result.stderr


async def test_smoke_command_failure_does_not_leak_tenant_token(tmp_path):
    async def tenant_token(request):
        return web.json_response(
            {
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "opaque-sensitive-token-abc123",
                "expire": 7200,
            }
        )

    async def failing_send(request):
        return web.json_response(
            {"code": 999, "msg": "Authorization opaque-sensitive-token-abc123"}
        )

    app = web.Application()
    app.router.add_post("/auth/v3/tenant_access_token/internal", tenant_token)
    app.router.add_post("/im/v1/messages", failing_send)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "feishu:\n"
            "  app_id: cli_test\n"
            "  app_secret: secret\n"
            f"  base_url: {test_client.make_url('/')}\n",
            encoding="utf-8",
        )

        result = await asyncio.to_thread(
            run_cli,
            "smoke-feishu-card",
            "--config",
            str(config_path),
            "--chat-id",
            "oc_abc",
        )
    finally:
        await test_client.close()

    assert result.returncode != 0
    assert "opaque-sensitive-token-abc123" not in result.stderr


async def test_smoke_command_update_failure_returns_nonzero(feishu_api, tmp_path):
    async def tenant_token(request):
        return web.json_response(
            {
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "tenant-token-1",
                "expire": 7200,
            }
        )

    async def send_message(request):
        return web.json_response(
            {"code": 0, "msg": "ok", "data": {"message_id": "om_message_1"}}
        )

    async def failing_update(request):
        return web.json_response({"code": 999, "msg": "update failed"})

    app = web.Application()
    app.router.add_post("/auth/v3/tenant_access_token/internal", tenant_token)
    app.router.add_post("/im/v1/messages", send_message)
    app.router.add_patch("/im/v1/messages/{message_id}", failing_update)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "feishu:\n"
            "  app_id: cli_test\n"
            "  app_secret: secret\n"
            f"  base_url: {test_client.make_url('/')}\n",
            encoding="utf-8",
        )

        result = await asyncio.to_thread(
            run_cli,
            "smoke-feishu-card",
            "--config",
            str(config_path),
            "--chat-id",
            "oc_abc",
        )
    finally:
        await test_client.close()

    assert result.returncode != 0
    assert "update failed" in result.stderr
