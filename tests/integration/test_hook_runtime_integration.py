import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"


class Message:
    chat_id = "oc_fixture"
    message_id = "msg_fixture"
    text = "fixture answer"


class Hooks:
    def __init__(self):
        self.events = []

    def emit(self, name, data):
        self.events.append((name, data))


def copy_hermes(tmp_path):
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    return hermes_dir


def run_cli(*args):
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def load_run_py(path):
    spec = importlib.util.spec_from_file_location("fixture_run", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def wait_for_event_count(received, expected_count, event, timeout=1):
    if len(received) >= expected_count:
        return
    await asyncio.wait_for(event.wait(), timeout=timeout)
    assert len(received) >= expected_count


async def test_installed_hook_preserves_handler_return_when_sender_fails(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    sender_called = asyncio.Event()

    async def failing_post_json(url, payload, timeout):
        sender_called.set()
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(hook_runtime, "_post_json", failing_post_json)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install.returncode == 0, install.stderr
    module = load_run_py(hermes_dir / "gateway" / "run.py")
    hooks = Hooks()

    result = await module._handle_message_with_agent(Message(), hooks)

    assert result == "fixture answer"
    await asyncio.wait_for(sender_called.wait(), timeout=1)
    assert len(hooks.events) == 1
    assert hooks.events[0][0] == "agent:end"
    assert hooks.events[0][1]["message"].chat_id == "oc_fixture"


async def test_installed_hook_posts_started_event_to_mock_sidecar(tmp_path, monkeypatch):
    received = []
    received_event = asyncio.Event()

    async def events(request):
        received.append(await request.json())
        received_event.set()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/events", events)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        hermes_dir = copy_hermes(tmp_path)
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL",
            str(client.make_url("/events")),
        )
        install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
        assert install.returncode == 0, install.stderr
        module = load_run_py(hermes_dir / "gateway" / "run.py")

        result = await module._handle_message_with_agent(Message(), Hooks())

        assert result in (None, "fixture answer")
        await asyncio.wait_for(received_event.wait(), timeout=1)
        assert received
        assert received[0]["event"] == "message.started"
        assert received[0]["chat_id"] == "oc_fixture"
        assert received[0]["message_id"] == "msg_fixture"
    finally:
        await client.close()


async def test_installed_hook_forwards_streaming_tool_and_completion_events(
    tmp_path, monkeypatch
):
    received = []
    received_count = asyncio.Event()

    async def events(request):
        received.append(await request.json())
        if len(received) >= 5:
            received_count.set()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/events", events)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        hermes_dir = copy_hermes(tmp_path)
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL",
            str(client.make_url("/events")),
        )
        hook_runtime.reset_runtime_state()
        install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
        assert install.returncode == 0, install.stderr
        module = load_run_py(hermes_dir / "gateway" / "run.py")

        result = await module._handle_message_with_agent(Message(), Hooks())

        assert result in (None, "fixture answer")
        await wait_for_event_count(received, 5, received_count)
        assert [item["event"] for item in received] == [
            "message.started",
            "thinking.delta",
            "tool.updated",
            "answer.delta",
            "message.completed",
        ]
        assert {item["chat_id"] for item in received} == {"oc_fixture"}
        assert {item["message_id"] for item in received} == {"msg_fixture"}
        assert received[1]["data"] == {"text": "thinking fixture delta"}
        assert received[2]["data"] == {
            "tool_id": "fixture_tool",
            "name": "fixture_tool",
            "status": "running",
            "detail": "fixture tool preview",
        }
        assert received[3]["data"] == {"text": "answer fixture delta"}
        assert received[4]["data"] == {
            "answer": "fixture answer",
            "duration": 0.25,
            "model": "Unknown",
            "tokens": {"input_tokens": 7, "output_tokens": 11},
            "context": {"used_tokens": 0, "max_tokens": 0},
        }
    finally:
        await client.close()
