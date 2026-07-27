"""Hermes CLI Gateway Bridge — import-hook-based FeishuAdapter patching.

For Hermes v0.17+ where the gateway runs from hermes_cli/gateway.py
instead of the legacy gateway/run.py. This module is placed in the
user site-packages and auto-imported via a launcher script.

Monkey-patches FeishuAdapter.send() to POST message.completed events
to the sidecar, which renders them as Feishu CardKit cards.
"""

import functools
import http.client
import json
import logging
import os
import re
import sys

logger = logging.getLogger("hermes_feishu_card.cli_bridge")

SIDECAR_HOST = "127.0.0.1"
SIDECAR_PORT = 8765
_patched = False
_token = None


def _discover_token() -> str | None:
    """Read sidecar token from running process cmdline."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", errors="replace")
                if "hermes_feishu_card.runner" in cmd:
                    m = re.search(r"--token\s+(\S+)", cmd)
                    if m:
                        return m.group(1)
        except (OSError, PermissionError):
            pass
    return None


def _post_event(event_type: str, **data) -> None:
    """Fire-and-forget POST to sidecar /events."""
    global _token
    if _token is None:
        _token = _discover_token()
    try:
        conn = http.client.HTTPConnection(
            SIDECAR_HOST, SIDECAR_PORT, timeout=1
        )
        headers = {"Content-Type": "application/json"}
        if _token:
            headers["Authorization"] = f"Bearer {_token}"
        payload = json.dumps({
            "event": event_type,
            "data": data,
            "schema_version": 2,
        })
        conn.request("POST", "/events", body=payload, headers=headers)
        conn.getresponse().read()
        conn.close()
    except Exception:
        pass  # fail-open — sidecar down = plain text


def _do_patch(adapter_module) -> bool:
    """Patch FeishuAdapter.send on the given module."""
    global _patched
    FeishuAdapter = getattr(adapter_module, "FeishuAdapter", None)
    if not FeishuAdapter:
        return False

    original_send = FeishuAdapter.send

    @functools.wraps(original_send)
    async def patched_send(self, chat_id, content, reply_to=None, metadata=None):
        result = await original_send(
            self, chat_id, content, reply_to, metadata
        )
        try:
            mid = getattr(result, "message_id", "") if result else ""
            _post_event(
                "message.completed",
                chat_id=chat_id,
                content=content[:3000],
                message_id=mid or "",
                reply_to=reply_to or "",
            )
        except Exception:
            pass
        return result

    FeishuAdapter.send = patched_send
    _patched = True
    return True


def patch_feishu_adapter() -> bool:
    """Find and patch FeishuAdapter.send among loaded modules."""
    if _patched:
        return True
    target = None
    for mod_name, mod in sorted(sys.modules.items(), key=lambda x: x[0]):
        if mod_name == __name__ or not hasattr(mod, "FeishuAdapter"):
            continue
        target = mod
        if "adapter" in mod_name:
            break
    if target is None:
        return False
    return _do_patch(target)


if isinstance(__builtins__, dict):
    _original_import = __builtins__["__import__"]
else:
    _original_import = __builtins__.__import__


def _hook_import(name, *args, **kwargs):
    result = _original_import(name, *args, **kwargs)
    if not _patched and ("feishu" in name.lower() or "adapter" in name.lower()):
        patch_feishu_adapter()
    return result


if isinstance(__builtins__, dict):
    __builtins__["__import__"] = _hook_import
else:
    __builtins__.__import__ = _hook_import
