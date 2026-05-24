#!/usr/bin/env python3
"""
Send a single progress update to the Hermes Feishu streaming card.
For continuous monitoring, use auto_progress.py instead.

Usage:
  python3 send_progress.py wget 45 "45% 下载中" 120
  python3 send_progress.py compile 80 "compiling..."
"""
import json
import sys
import urllib.request
import urllib.error

SIDECAR_URL = "http://127.0.0.1:8765"


def get_active_message_id() -> str | None:
    try:
        req = urllib.request.Request(f"{SIDECAR_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read())
        sessions = health.get("sessions", {})
        active = {k: v for k, v in sessions.items()
                  if v.get("status") not in ("completed", "failed")}
        if active:
            return list(active.keys())[-1]
        return None
    except Exception as e:
        print(f"Warning: {e}", file=sys.stderr)
        return None


def send_progress(message_id: str, tool_id: str, percent: int, detail: str = "", eta: int = 0) -> bool:
    payload = json.dumps({
        "message_id": message_id,
        "tool_id": tool_id,
        "percent": percent,
        "detail": detail,
        "eta": eta,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{SIDECAR_URL}/progress",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <tool_id> <percent> [detail] [eta]")
        sys.exit(1)

    tool_id = sys.argv[1]
    percent = int(sys.argv[2])
    detail = sys.argv[3] if len(sys.argv) > 3 else ""
    eta = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    message_id = get_active_message_id()
    if not message_id:
        print("ERROR: No active session found.")
        sys.exit(1)

    ok = send_progress(message_id, tool_id, percent, detail, eta)
    if ok:
        bar = "█" * (percent * 8 // 100) + "░" * (8 - percent * 8 // 100)
        eta_text = f" ETA {eta}s" if eta > 0 else ""
        print(f"✅ {bar} {percent}%{eta_text} ({detail})")
    else:
        print("❌ Failed")
        sys.exit(1)
