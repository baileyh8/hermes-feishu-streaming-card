import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


CONFIG_ENV_VARS = {
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
}


def run_cli(*args, env=None):
    process_env = {
        key: value for key, value in os.environ.items() if key not in CONFIG_ENV_VARS
    }
    if env:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env=process_env,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_config(tmp_path: Path, port: int) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"server:\n  host: 127.0.0.1\n  port: {port}\n",
        encoding="utf-8",
    )
    return path


def process_env(tmp_path: Path) -> dict[str, str]:
    state_dir = tmp_path / "state"
    return {"HERMES_FEISHU_CARD_STATE_DIR": str(state_dir)}


def pidfile_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "sidecar.pid"


def read_health(port: int) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/health",
        timeout=2,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def post_started_event(port: int) -> dict:
    payload = {
        "schema_version": "1",
        "event": "message.started",
        "conversation_id": "conversation-1",
        "message_id": "hermes-message-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "sequence": 0,
        "created_at": 1777017600.0,
        "data": {},
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def test_status_reports_stopped_when_sidecar_is_not_running(tmp_path):
    config = write_config(tmp_path, free_port())

    result = run_cli("status", "--config", str(config), env=process_env(tmp_path))

    assert result.returncode == 0
    assert "status: stopped" in result.stdout


def test_stop_rejects_pidfile_without_matching_health_token(tmp_path):
    config = write_config(tmp_path, free_port())
    env = process_env(tmp_path)
    pidfile_path(tmp_path).parent.mkdir()
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pidfile_path(tmp_path).write_text(
            json.dumps({"pid": sleeper.pid, "token": "not-sidecar"}) + "\n",
            encoding="utf-8",
        )

        result = run_cli("stop", "--config", str(config), env=env)

        assert result.returncode != 0
        assert "pidfile identity mismatch" in result.stderr
        assert sleeper.poll() is None
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_stop_rejects_matching_token_with_wrong_pid(tmp_path):
    port = free_port()
    config = write_config(tmp_path, port)
    env = process_env(tmp_path)

    start = run_cli("start", "--config", str(config), env=env)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert start.returncode == 0, start.stderr
        health = read_health(port)
        pidfile_path(tmp_path).write_text(
            json.dumps({"pid": sleeper.pid, "token": health["process_token"]}) + "\n",
            encoding="utf-8",
        )

        result = run_cli("stop", "--config", str(config), env=env)

        assert result.returncode != 0
        assert "pidfile identity mismatch" in result.stderr
        assert sleeper.poll() is None
        assert read_health(port)["status"] == "healthy"
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)
        run_cli("stop", "--config", str(config), env=env)


def test_start_status_and_stop_manage_sidecar_process(tmp_path):
    port = free_port()
    config = write_config(tmp_path, port)
    env = process_env(tmp_path)

    start = run_cli("start", "--config", str(config), env=env)
    try:
        assert start.returncode == 0, start.stderr
        assert "start ok" in start.stdout
        assert read_health(port)["status"] == "healthy"
        assert post_started_event(port) == {"ok": True, "applied": True}

        status = run_cli("status", "--config", str(config), env=env)

        assert status.returncode == 0
        assert "status: running" in status.stdout
        assert "active_sessions: 1" in status.stdout
        assert "events_received: 1" in status.stdout
        assert "events_applied: 1" in status.stdout
        assert "feishu_send_successes: 1" in status.stdout

        stop = run_cli("stop", "--config", str(config), env=env)

        assert stop.returncode == 0, stop.stderr
        assert "stop ok" in stop.stdout

        stopped = run_cli("status", "--config", str(config), env=env)
        assert stopped.returncode == 0
        assert "status: stopped" in stopped.stdout
    finally:
        run_cli("stop", "--config", str(config), env=env)
        time.sleep(0.1)
