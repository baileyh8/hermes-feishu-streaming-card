from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import subprocess
from typing import Any


_FETCH_SCRIPT = """
import json
from agent.account_usage import fetch_account_usage

snapshot = fetch_account_usage("openai-codex")
windows = []
if snapshot is not None and getattr(snapshot, "available", False):
    for window in getattr(snapshot, "windows", ()):
        windows.append({
            "label": getattr(window, "label", ""),
            "used_percent": getattr(window, "used_percent", None),
        })
print(json.dumps({"windows": windows}, separators=(",", ":")))
""".strip()

_WINDOW_LABELS = {
    "session": "5h",
    "primary": "5h",
    "weekly": "weekly",
    "week": "weekly",
    "secondary": "weekly",
}


async def fetch_codex_subscription_usage(
    hermes_root: str | Path, *, timeout_seconds: float = 5.0
) -> str:
    return await asyncio.to_thread(
        _fetch_sync,
        Path(hermes_root).expanduser(),
        timeout_seconds=timeout_seconds,
    )


def _fetch_sync(hermes_root: Path, *, timeout_seconds: float) -> str:
    runtime_python = _runtime_python(hermes_root)
    if runtime_python is None:
        return ""
    try:
        result = subprocess.run(
            [str(runtime_python), "-c", _FETCH_SCRIPT],
            cwd=hermes_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    try:
        payload = json.loads(lines[-1])
    except (TypeError, json.JSONDecodeError):
        return ""
    return format_subscription_usage(payload)


def format_subscription_usage(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return ""
    candidates: list[tuple[str, float]] = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        raw_label = str(window.get("label") or "").strip().lower()
        label = _WINDOW_LABELS.get(raw_label)
        used = window.get("used_percent")
        if not label or isinstance(used, bool):
            continue
        try:
            used_percent = float(used)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(used_percent):
            continue
        candidates.append((label, used_percent))

    values: list[str] = []
    seen: set[str] = set()
    for label, used_percent in candidates:
        if label in seen:
            continue
        if len(candidates) == 1 and label == "5h":
            label = "limit"
        remaining = max(0, min(100, round(100 - used_percent)))
        values.append(f"{label} {remaining}%")
        seen.add(label)
    return " · ".join(values)


def _runtime_python(hermes_root: Path) -> Path | None:
    for candidate in (
        hermes_root / "venv" / "bin" / "python",
        hermes_root / "venv" / "bin" / "python3",
        hermes_root / ".venv" / "bin" / "python",
        hermes_root / ".venv" / "bin" / "python3",
        hermes_root / "venv" / "Scripts" / "python.exe",
        hermes_root / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return candidate
    return None
