from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import time
from types import ModuleType

import hermes_feishu_card
from hermes_feishu_card.hook_runtime import _get_json, _post_json_response


HERMES_ROOT = Path(os.environ.get("HERMES_DIR", "/opt/hermes"))
DATA_ROOT = Path(os.environ.get("HFC_SMOKE_DATA_DIR", "/opt/data"))
RUN_PY = HERMES_ROOT / "gateway" / "run.py"
RESULT_PATH = DATA_ROOT / "gateway-result.json"


class SmokeMessage:
    chat_id = "smoke-card-chat"
    conversation_id = "smoke-conversation"
    message_id = "smoke-message"
    platform = "feishu"


class SmokeHooks:
    def __init__(self) -> None:
        self.events: list[str] = []

    def emit(self, event_name: str, _payload: object) -> None:
        self.events.append(event_name)


def _load_patched_gateway() -> tuple[ModuleType, str]:
    source = RUN_PY.read_text(encoding="utf-8")
    assert "HERMES_FEISHU_CARD_PATCH_BEGIN" in source
    assert "HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN" in source
    spec = importlib.util.spec_from_file_location("hfc_docker_gateway", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, source


async def _wait_for_patched_gateway_signals(
    event_url: str,
) -> tuple[dict[str, object], int]:
    health_url = event_url.removesuffix("/events") + "/health"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        health = await _get_json(health_url, 2.0)
        readiness = health.get("readiness") if isinstance(health, dict) else None
        metrics = health.get("metrics") if isinstance(health, dict) else None
        events_received = (
            metrics.get("events_received", 0) if isinstance(metrics, dict) else 0
        )
        auth_rejections = (
            metrics.get("event_auth_rejections", 0)
            if isinstance(metrics, dict)
            else -1
        )
        if (
            isinstance(readiness, dict)
            and readiness.get("status") == "degraded"
            and readiness.get("reason") == "runtime_owner_unverified"
            and readiness.get("active_work_count_complete") is not True
            and isinstance(events_received, int)
            and events_received >= 1
            and auth_rejections == 0
        ):
            return readiness, events_received
        await asyncio.sleep(0.2)
    raise AssertionError(
        "patched Gateway events and runtime.hello were not accepted before the deadline"
    )


async def _exercise_gateway() -> dict[str, object]:
    gateway, source = _load_patched_gateway()
    hooks = SmokeHooks()
    response = await gateway._handle_message_with_agent(SmokeMessage(), hooks)
    # This old message-only fixture has no verified Gateway aggregate/home.
    # Signed hook events must work, but must not claim maintenance readiness.
    event_url = os.environ["HERMES_FEISHU_CARD_EVENT_URL"]
    runtime_readiness, patched_events_before_direct = (
        await _wait_for_patched_gateway_signals(event_url)
    )
    event_response = await _post_json_response(
        event_url,
        {
            "schema_version": "1",
            "event": "message.completed",
            "conversation_id": "smoke-native-conversation",
            "message_id": "smoke-native-message",
            "chat_id": "smoke-native-chat",
            "platform": "feishu",
            "sequence": 1,
            "created_at": time.time(),
            "data": {"answer": "signed gateway smoke"},
        },
        2.0,
    )
    assert isinstance(event_response, dict)
    assert event_response.get("disposition") == "native"
    package_path = Path(hermes_feishu_card.__file__).resolve()
    return {
        "hook_installed": True,
        "start_marker_count": source.count("HERMES_FEISHU_CARD_PATCH_BEGIN"),
        "complete_marker_count": source.count(
            "HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN"
        ),
        "response": response,
        "gateway_hook_events": hooks.events,
        "event_response": event_response,
        "runtime_readiness": runtime_readiness,
        "patched_events_before_direct": patched_events_before_direct,
        "package_location": str(package_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stay-alive", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_exercise_gateway())
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    os.replace(temporary, RESULT_PATH)
    if args.stay_alive:
        # Keep the actual heartbeat producer alive for the separate probe.
        while True:
            time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
