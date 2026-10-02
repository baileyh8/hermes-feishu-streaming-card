import json
import hashlib
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


CONFIG_ENV_VARS = {
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK",
    "HERMES_FEISHU_CARD_SERVICE_MANAGER",
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
    return {
        "HERMES_FEISHU_CARD_STATE_DIR": str(state_dir),
        "HERMES_FEISHU_CARD_SERVICE_MANAGER": "detached",
    }


def pidfile_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "sidecar.pid"


def read_health(port: int) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/health",
        timeout=2,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def post_started_event(port: int) -> tuple[int, dict]:
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
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_sidecar_process_rejects_non_loopback_listener_without_opt_in(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  host: 0.0.0.0\n  port: 8765\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_feishu_card.runner",
            "--config",
            str(config),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **process_env(tmp_path)},
        # This test verifies fail-closed listener validation, not import
        # latency.  Keep the same bounded 30-second cold-import allowance used
        # by installer/runtime probes so synced or cold filesystems do not turn
        # a correct rejection into an unrelated TimeoutExpired failure.
        timeout=30,
    )

    assert result.returncode != 0
    assert "allow_non_loopback" in result.stderr


def test_status_reports_stopped_when_sidecar_is_not_running(tmp_path):
    config = write_config(tmp_path, free_port())

    result = run_cli("status", "--config", str(config), env=process_env(tmp_path))

    assert result.returncode == 0
    assert "status: stopped" in result.stdout


def test_stop_recovers_legacy_pidfile_reused_by_non_hfc_process(tmp_path):
    config = write_config(tmp_path, free_port())
    env = process_env(tmp_path)
    pidfile_path(tmp_path).parent.mkdir(mode=0o700)
    pidfile_path(tmp_path).parent.chmod(0o700)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pidfile_path(tmp_path).write_text(
            json.dumps({"pid": sleeper.pid, "token": "not-sidecar"}) + "\n",
            encoding="utf-8",
        )
        pidfile_path(tmp_path).chmod(0o600)

        result = run_cli("stop", "--config", str(config), env=env)

        if sys.platform.startswith("linux"):
            assert result.returncode == 0
            assert "stop: not running" in result.stdout
            assert not pidfile_path(tmp_path).exists()
        else:
            assert result.returncode != 0
            assert "pidfile identity mismatch" in result.stderr
            assert pidfile_path(tmp_path).exists()
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
    original_pidfile = None
    try:
        assert start.returncode == 0, start.stderr
        health = read_health(port)
        assert "process_token" not in health
        original_pidfile = pidfile_path(tmp_path).read_bytes()
        pidfile_path(tmp_path).write_text(
            json.dumps({"pid": sleeper.pid, "token": "not-the-sidecar-token"}) + "\n",
            encoding="utf-8",
        )

        result = run_cli("stop", "--config", str(config), env=env)

        assert result.returncode != 0
        assert "pidfile identity mismatch" in result.stderr
        assert sleeper.poll() is None
        assert read_health(port)["status"] == "degraded"
    finally:
        if original_pidfile is not None:
            pidfile_path(tmp_path).write_bytes(original_pidfile)
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
        health = read_health(port)
        assert health["status"] == "degraded"
        assert health["noop_mode"] is True
        assert health["delivery"] == {"mode": "noop"}
        record = json.loads(pidfile_path(tmp_path).read_text())
        token_file = pidfile_path(tmp_path).parent / "sidecar-control.token"
        assert token_file.read_text().strip() == record["token"]
        assert health["process_token_hash"] == hashlib.sha256(record["token"].encode()).hexdigest()
        assert "process_token" not in health
        if sys.platform.startswith("linux"):
            command_line = Path(f"/proc/{health['process_pid']}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        elif sys.platform == "darwin":
            command_line = subprocess.check_output(["ps", "-p", str(health["process_pid"]), "-o", "command="], text=True)
        else:
            command_line = None
        if command_line is not None:
            assert "--token-file" in command_line
            assert record["token"] not in command_line
            assert "--token " not in command_line
        request = urllib.request.Request(f"http://127.0.0.1:{port}/control/shutdown", data=b"{}", headers={"X-HFC-Process-Token": "wrong-token"}, method="POST")
        try:
            urllib.request.urlopen(request, timeout=2)
            assert False, "wrong control token accepted"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert post_started_event(port) == (
            502,
            {
                "ok": False,
                "error": "feishu send failed",
                "delivery": {"outcome": "not_sent"},
            },
        )

        status = run_cli("status", "--config", str(config), env=env)

        assert status.returncode == 0
        assert "status: running" in status.stdout
        assert "manager: detached" in status.stdout
        assert "health: degraded" in status.stdout
        assert "delivery.mode: noop" in status.stdout
        assert "readiness: starting" in status.stdout
        assert "integrity.mode: notify" in status.stdout
        assert "active_sessions: 0" in status.stdout
        assert "events_received: 1" in status.stdout
        assert "events_applied: 0" in status.stdout
        assert "feishu_noop_attempts: 1" in status.stdout
        assert "feishu_send_successes: 0" in status.stdout
        assert "feishu_send_failures: 1" in status.stdout

        stop = run_cli("stop", "--config", str(config), env=env)

        assert stop.returncode == 0, stop.stderr
        assert "stop ok" in stop.stdout

        stopped = run_cli("status", "--config", str(config), env=env)
        assert stopped.returncode == 0
        assert "status: stopped" in stopped.stdout
    finally:
        run_cli("stop", "--config", str(config), env=env)
        time.sleep(0.1)


def test_start_safely_migrates_verified_v40_pidfile_in_private_state_dir(tmp_path):
    port = free_port()
    config = write_config(tmp_path, port)
    env = process_env(tmp_path)
    start = run_cli("start", "--config", str(config), env=env)
    try:
        assert start.returncode == 0, start.stderr
        record_path = pidfile_path(tmp_path)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record_path.write_text(
            json.dumps({"pid": record["pid"], "token": record["token"]}) + "\n",
            encoding="utf-8",
        )
        record_path.chmod(0o644)
        record_path.parent.chmod(0o700)
        legacy_stat = record_path.stat()

        adopted = run_cli("start", "--config", str(config), env=env)

        assert adopted.returncode == 0, adopted.stderr
        assert "start: already running" in adopted.stdout
        migrated = json.loads(record_path.read_text(encoding="utf-8"))
        assert migrated == {
            "pid": record["pid"],
            "token": record["token"],
        }
        migrated_stat = record_path.stat()
        assert (migrated_stat.st_dev, migrated_stat.st_ino) == (
            legacy_stat.st_dev,
            legacy_stat.st_ino,
        )
        assert migrated_stat.st_mode & 0o777 == 0o600
        assert record_path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        run_cli("stop", "--config", str(config), env=env)
        time.sleep(0.1)


def test_stop_refuses_pidless_v40_sidecar_without_adoption_or_automatic_kill(tmp_path):
    port = free_port()
    config = write_config(tmp_path, port)
    env = process_env(tmp_path)
    start = run_cli("start", "--config", str(config), env=env)
    original_pidfile = None
    try:
        assert start.returncode == 0, start.stderr
        original_pidfile = pidfile_path(tmp_path).read_bytes()
        pidfile_path(tmp_path).unlink()

        refused = run_cli("stop", "--config", str(config), env=env)

        assert refused.returncode != 0
        assert "running sidecar has no pidfile; stop refused" in refused.stderr
        assert read_health(port)["process_pid"] > 0

        help_result = run_cli("stop", "--help", env=env)
        assert help_result.returncode == 0
        assert "--adopt-legacy" not in help_result.stdout
    finally:
        if original_pidfile is not None:
            pidfile_path(tmp_path).write_bytes(original_pidfile)
            pidfile_path(tmp_path).chmod(0o600)
        run_cli("stop", "--config", str(config), env=env)
        time.sleep(0.1)


def test_start_loads_credentials_from_selected_env_file(tmp_path):
    port = free_port()
    config = write_config(tmp_path, port)
    selected_env = tmp_path / "selected.env"
    selected_env.write_text(
        "FEISHU_APP_ID=selected-app\nFEISHU_APP_SECRET=selected-secret\n",
        encoding="utf-8",
    )
    env = process_env(tmp_path)

    start = run_cli(
        "start",
        "--config",
        str(config),
        "--env-file",
        str(selected_env),
        env=env,
    )
    try:
        assert start.returncode == 0, start.stderr
        health = read_health(port)
        assert health["status"] == "healthy"
        assert health["noop_mode"] is False
        assert health["delivery"] == {"mode": "live"}
    finally:
        run_cli("stop", "--config", str(config), env=env)
        time.sleep(0.1)
