#!/usr/bin/env python3
"""
Auto progress watchdog — runs a command, monitors its output for progress
indicators, and pushes updates to the Feishu streaming card.

Usage:
  python3 auto_progress.py --tool download -- wget -c <url> -O file
  python3 auto_progress.py --tool compile --interval 10 -- make -j16

Prerequisites:
  - Hermes Agent with hermes-feishu-streaming-card plugin (v3.4.2+)
  - Sidecar running on http://127.0.0.1:8765 with /progress endpoint

Recognized progress patterns:
  - "45% [========>"          (wget/curl progress bar)
  - "[ 45%] Building CXX"     (CMake/Make)
  - "45% |████████"           (pip)
  - "### 45.0%"               (curl)
  - "45%" / "已下载 65%"      (generic)
"""
import os
import re
import sys
import time
import json
import argparse
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path

SIDECAR_URL = "http://127.0.0.1:8765"
PROGRESS_PATTERNS = [
    re.compile(r'(\d{1,3})%\s*\['),               # wget: "45% ["
    re.compile(r'#+\s+(\d{1,3}(?:\.\d)?)%'),       # curl: "### 45.0%"
    re.compile(r'\[\s*(\d{1,3})%\s*\]'),            # CMake/Make: "[ 45%]"
    re.compile(r'^[\s]*(\d{1,3})%\s*[|━]'),         # pip: "45%|████"
    re.compile(r'(?:^|\s)(\d{1,3})%(?:\s|$|\.)'),  # Generic catch-all
]


def get_active_message_id() -> str | None:
    """Find the most recent active session from sidecar health endpoint."""
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
    except Exception:
        return None


def send_progress(message_id: str, tool_id: str, percent: int, detail: str = "", eta: int = 0) -> bool:
    """Send progress update to the sidecar /progress endpoint."""
    payload = json.dumps({
        "message_id": message_id,
        "tool_id": tool_id,
        "percent": min(100, max(0, percent)),
        "detail": detail[:200],
        "eta": max(0, eta),
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{SIDECAR_URL}/progress",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def parse_progress(line: str) -> int | None:
    """Parse a percentage from a line of output."""
    for pattern in PROGRESS_PATTERNS:
        match = pattern.search(line)
        if match:
            try:
                pct = int(float(match.group(1)))
                if 0 <= pct <= 100:
                    return pct
            except (ValueError, IndexError):
                continue
    return None


def format_detail(line: str, max_len: int = 60) -> str:
    """Extract a readable detail string from a progress line."""
    cleaned = line.strip()
    cleaned = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', cleaned)
    cleaned = re.sub(r'\r', '', cleaned)
    if len(cleaned) > max_len:
        truncated = cleaned[:max_len]
        last_space = truncated.rfind(' ')
        if last_space > max_len // 2:
            truncated = truncated[:last_space]
        cleaned = truncated + '...'
    return cleaned


def estimate_eta(percent: int, elapsed: float) -> int:
    """Estimate remaining seconds based on current progress."""
    if percent <= 0:
        return 0
    total_estimate = elapsed / (percent / 100.0)
    remaining = total_estimate - elapsed
    return max(0, int(remaining))


def watch_process(tool_id: str, process: subprocess.Popen, log_path: str | None = None, interval: float = 5.0):
    """Monitor a running process and send progress updates every `interval` seconds."""
    message_id = None
    start_time = time.time()
    last_percent = -1
    last_send = 0.0
    seen_lines = set()

    def get_new_lines() -> list[str]:
        if not log_path or not os.path.exists(log_path):
            return []
        try:
            with open(log_path, 'r', errors='replace') as f:
                content = f.read()
        except (IOError, PermissionError):
            return []
        lines = content.split('\n')
        new = []
        for line in lines:
            key = line.strip()[:40]
            if key and key not in seen_lines:
                seen_lines.add(key)
                new.append(line)
        return new

    while process.poll() is None:
        if message_id is None:
            message_id = get_active_message_id()

        new_lines = get_new_lines()
        best_percent = None
        best_detail = ""

        for line in new_lines:
            pct = parse_progress(line)
            if pct is not None:
                if best_percent is None or pct > best_percent:
                    best_percent = pct
                    best_detail = format_detail(line)

        if best_percent is not None and message_id:
            elapsed = time.time() - start_time
            eta = estimate_eta(best_percent, elapsed)
            now = time.time()
            if best_percent != last_percent or (now - last_send) > 30:
                send_progress(message_id, tool_id, best_percent, best_detail, eta)
                last_percent = best_percent
                last_send = now

        time.sleep(interval)

    if message_id:
        send_progress(message_id, tool_id, 100, "完成 ✅", 0)


def main():
    parser = argparse.ArgumentParser(description="Auto progress watchdog for long-running commands")
    parser.add_argument("--tool", "-t", default="task", help="Tool name for the card display")
    parser.add_argument("--interval", "-i", type=float, default=5.0, help="Check interval in seconds")
    parser.add_argument("--log", "-l", help="Log file path (if not stdout)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run")

    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("Usage: auto_progress.py --tool download -- wget <url>")
        sys.exit(1)

    tool_id = args.tool
    interval = max(1.0, min(60.0, args.interval))

    if args.log:
        print(f"🔄 [{tool_id}] Running: {' '.join(command)}")
        print(f"📝 Log: {args.log}")
        with open(args.log, 'w') as lf:
            process = subprocess.Popen(command, stdout=lf, stderr=subprocess.STDOUT, text=True)
        watch_process(tool_id, process, log_path=args.log, interval=interval)
    else:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as tmp:
            log_path = tmp.name
        print(f"🔄 [{tool_id}] Running: {' '.join(command)}")
        with open(log_path, 'w') as lf:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def reader_thread(proc, log_file):
            for line in iter(proc.stdout.readline, ''):
                print(line, end='', flush=True)
                log_file.write(line)
                log_file.flush()
            proc.stdout.close()

        with open(log_path, 'w') as lf:
            t = threading.Thread(target=reader_thread, args=(process, lf), daemon=True)
            t.start()
            watch_process(tool_id, process, log_path=log_path, interval=interval)
            t.join(timeout=5)

        try:
            os.unlink(log_path)
        except OSError:
            pass

    exit_code = process.returncode
    if exit_code == 0:
        print(f"✅ [{tool_id}] Completed (exit={exit_code})")
    else:
        print(f"❌ [{tool_id}] Failed (exit={exit_code})")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
