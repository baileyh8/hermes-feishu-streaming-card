"""Read loaded defaults through the real runner factory and loopback control path."""
import asyncio
import copy
from hashlib import sha256
import json
import os
import sys

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
import pytest
import yaml

from hermes_feishu_card import cli, process, runner, server
from hermes_feishu_card.config import load_config
from hermes_feishu_card.config_snapshot import (
    CONTROL_CARD_CONFIG_PATH, SNAPSHOT_SCHEMA, config_path_identity, scope_identity,
)
from hermes_feishu_card.reading import explain_reading_config


TOKEN = "PRIVATE_RUNNING_TOKEN_FIXTURE"
PROFILE = "private-profile-fixture"
BOT = "private-bot-fixture"
CREDENTIALS = {"app_id": "private-app-fixture", "app_secret": "private-secret-fixture"}


@pytest.fixture
def private_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(state))
    process.write_pid_record(os.getpid(), TOKEN)
    return state


def write_config(path, raw):
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def build_app(path):
    config = load_config(path)
    boundary = runner.build_feishu_boundary(config)
    app = server.create_app(
        boundary.client, card_config=runner._card_config_for_server(config),
        bot_router=boundary.router, process_token=TOKEN, operations_config_path=path,
        session_store_directory=path.parent / "sessions",
    )
    factories = boundary.client.values() if isinstance(boundary.client, dict) else [boundary.client]
    for factory in factories:
        factory._client_builder = lambda _: pytest.fail("config read created a Feishu client")
    return config, boundary, app


def assert_private(text, path):
    for secret in (TOKEN, PROFILE, BOT, *CREDENTIALS.values(), str(path), "_selected_scope"):
        assert secret not in text


@pytest.mark.parametrize('chat_type,chat_id,expected', [
    ('group', None, 'hidden'), ('p2p', None, 'summary'),
    ('group', 'oc_example', 'full'), (None, 'oc_example', 'full'),
])
async def test_running_chat_selectors_apply_loaded_overlays(private_state, tmp_path, capsys, chat_type, chat_id, expected):
    path = tmp_path / 'config.yaml'
    raw = {'feishu': CREDENTIALS, 'card': {'chat_overrides': {
        'group': {'live_tool_activity': 'summary'}, 'p2p': {'live_tool_activity': 'summary'},
        'chats': {'oc_example': {'live_tool_activity': 'full'}}}}}
    raw['profiles'] = {'default': {'feishu': CREDENTIALS,
        'card': {'chat_overrides': {'group': {'live_tool_activity': 'full', 'show_reasoning': False}}},
        'bots': {'items': {'default': {**CREDENTIALS,
            'card': {'chat_overrides': {'group': {'live_tool_activity': 'hidden'}}}}}}}}
    write_config(path, raw)
    _, _, app = build_app(path)
    args = []
    selectors = {}
    for key, value in (('chat_type', chat_type), ('chat_id', chat_id)):
        if value is not None:
            args += ['--' + key.replace('_', '-'), value]
            selectors[key] = value
    async with TestClient(TestServer(app)) as http:
        raw['server'] = {'host': '127.0.0.1', 'port': http.server.port}
        write_config(path, raw)
        assert await asyncio.to_thread(cli.main, ['card-config', '--config', str(path), '--running', '--json', *args]) == 0
        output = capsys.readouterr()
        report = json.loads(output.out)
        assert report['running_config'] == 'verified'
        assert report['values']['live_tool_activity'] == expected
        assert report['running']['values']['live_tool_activity'] == expected
        assert report['running']['values']['show_reasoning'] is (chat_type != 'group')
        assert not report['differences']
        assert 'oc_example' not in output.out + output.err
        response = await http.post(CONTROL_CARD_CONFIG_PATH, json={
            'profile_id': 'default', 'bot_id': 'default', 'config_path_hash': config_path_identity(path), **selectors,
        }, headers={process.CONTROL_TOKEN_HEADER: TOKEN})
        assert response.status == 200
        payload = await response.json()
        assert payload['scope_hash'] == scope_identity('default', 'default', **selectors)
        assert 'oc_example' not in json.dumps(payload)


def test_scope_identity_without_chat_selectors_is_byte_compatible():
    material = json.dumps(['default', 'default'], separators=(',', ':'))
    assert scope_identity('default', 'default') == sha256(material.encode()).hexdigest()
    assert scope_identity('default', 'default', chat_type='group') != scope_identity('default', 'default')


@pytest.mark.parametrize('selector', [
    {'chat_type': 'dm'}, {'chat_type': None}, {'chat_type': []}, {'chat_id': '*'},
    {'chat_id': 're:.*'}, {'chat_id': 1}, {'chat_id': None}, {'chat_id': 'x' * 513},
    {'chat_id': 'oc_\x01example'}, {'extra': True},
])
async def test_control_rejects_invalid_chat_selectors(private_state, tmp_path, selector):
    path = tmp_path / 'config.yaml'
    write_config(path, {'feishu': CREDENTIALS})
    _, _, app = build_app(path)
    async with TestClient(TestServer(app)) as http:
        response = await http.post(CONTROL_CARD_CONFIG_PATH, json={
            'profile_id': 'default', 'bot_id': 'default', 'config_path_hash': config_path_identity(path), **selector,
        }, headers={process.CONTROL_TOKEN_HEADER: TOKEN})
        assert response.status == 400
        assert await response.json() == {'ok': False, 'error': 'invalid_request'}


@pytest.mark.parametrize("scope", ["global", "profile", "bot", "alternate_bot", "alternate_profile", "profile_separate_bots"])
async def test_running_cli_matches_real_runner_scope_merge(private_state, tmp_path, capsys, scope):
    path = tmp_path / "private-config-fixture.yaml"
    raw = {"card": {"reading_preset": "task", "interaction_mode": "auto", "timeline_expanded": False}, "feishu": CREDENTIALS}
    args = []
    expected_preset, expected_expanded, expected_reasoning = "task", False, True
    if scope != "global":
        profile = {"card": {"reading_preset": "detailed", "show_reasoning": False}, "feishu": CREDENTIALS}
        raw["profiles"] = {PROFILE: profile}
        expected_preset, expected_expanded, expected_reasoning = "detailed", True, False
        if scope in {"bot", "alternate_bot", "profile_separate_bots"}:
            bots = {"default": BOT, "items": {
                BOT: {**CREDENTIALS, "card": {"reading_preset": "task", "timeline_expanded": False}},
                "alternate": {**CREDENTIALS, "card": {"reading_preset": "focused", "show_reasoning": False}},
            }}
            if scope == "profile_separate_bots":
                raw["bots"] = bots
            else:
                profile["bots"] = bots
                expected_preset, expected_expanded, expected_reasoning = "task", False, True
            if scope == "alternate_bot":
                args += ["--bot-id", "alternate"]
                expected_preset, expected_reasoning = "focused", False
        if scope == "alternate_profile":
            raw["profiles"]["alternate"] = {"feishu": CREDENTIALS, "card": {"reading_preset": "classic"}}
            args += ["--profile-id", "alternate"]
            expected_preset, expected_expanded, expected_reasoning = "classic", False, True
    write_config(path, raw)
    config, boundary, app = build_app(path)
    before_config = copy.deepcopy(config)
    async with TestClient(TestServer(app)) as http:
        raw["server"] = {"host": "127.0.0.1", "port": http.server.port}
        write_config(path, raw)
        assert await asyncio.to_thread(cli.main, ["card-config", "--config", str(path), "--running", "--json", *args]) == 0
        output = capsys.readouterr()
        report = json.loads(output.out)
        assert report["running_config"] == "verified", report
        assert report["differences"] == []
        for values in (report["values"], report["running"]["values"]):
            assert values["reading_preset"] == expected_preset
            assert values["timeline_expanded"] is expected_expanded
            assert values["show_reasoning"] is expected_reasoning
        assert_private(output.out + output.err, path)
    assert config == before_config
    factories = boundary.client.values() if isinstance(boundary.client, dict) else [boundary.client]
    assert all(factory._clients == {} for factory in factories)


@pytest.mark.parametrize("output_mode", ["json", "text", "preview"])
async def test_yaml_edits_report_next_load_without_changing_loaded_defaults(private_state, tmp_path, capsys, output_mode):
    path = tmp_path / "private-config-fixture.yaml"
    raw = {"card": {"reading_preset": "task", "timeline_expanded": False}, "feishu": CREDENTIALS}
    write_config(path, raw)
    _, _, app = build_app(path)
    pid_before = process.pid_path().read_bytes()
    async with TestClient(TestServer(app)) as http:
        raw["server"] = {"host": "127.0.0.1", "port": http.server.port}
        raw["card"].update({"show_reasoning": False, "timeline_expanded": True})
        write_config(path, raw)
        disk_before = path.read_bytes()
        args = ["card-config", "--config", str(path), "--running"]
        if output_mode == "json":
            args += ["--json"]
        elif output_mode == "preview":
            args += ["--preview", str(tmp_path / "preview")]
        assert await asyncio.to_thread(cli.main, args) == 0
        output = capsys.readouterr()
        assert_private(output.out + output.err, path)
        loaded = await asyncio.to_thread(process.fetch_loaded_card_config, path, load_config(path), profile_id="default", bot_id="default")
        assert loaded["values"]["show_reasoning"] is True
        assert loaded["values"]["timeline_expanded"] is False
        if output_mode == "json":
            report = json.loads(output.out)
            assert report["running_config"] == "verified"
            assert {item["field"] for item in report["differences"]} == {"show_reasoning", "timeline_expanded"}
            assert report["values"]["show_reasoning"] is False
            assert report["values"]["timeline_expanded"] is True
            child = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "hermes_feishu_card.cli", *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            child_out, child_err = await child.communicate()
            assert child.returncode == 0, child_err.decode()
            assert json.loads(child_out) == report
            assert_private(child_out.decode() + child_err.decode(), path)
        else:
            assert "verified" in output.out
        if output_mode == "preview":
            files = list((tmp_path / "preview").iterdir())
            assert files
            for artifact in files:
                assert_private(artifact.read_text(), path)
        assert path.read_bytes() == disk_before
        assert process.pid_path().read_bytes() == pid_before


@pytest.mark.parametrize("failure", ["token", "path", "profile", "bot", "request_size", "extra_field"])
async def test_control_endpoint_rejects_wrong_identity_or_selectors(private_state, tmp_path, failure):
    path = tmp_path / "config.yaml"
    write_config(path, {"feishu": CREDENTIALS})
    _, _, app = build_app(path)
    selectors = {"profile_id": "default", "bot_id": "default", "config_path_hash": config_path_identity(path)}
    headers = {process.CONTROL_TOKEN_HEADER: TOKEN}
    expected = 404
    if failure == "token":
        headers[process.CONTROL_TOKEN_HEADER] = "other-private-token"
        expected = 403
    elif failure == "path":
        selectors["config_path_hash"] = config_path_identity(tmp_path / "other.yaml")
        expected = 409
    elif failure in {"profile", "bot"}:
        selectors[failure + "_id"] = "missing-private-scope"
    elif failure == "request_size":
        selectors["profile_id"] = "x" * 5000
        expected = 400
    else:
        selectors["secret"] = "private-extra-field"
        expected = 400
    async with TestClient(TestServer(app)) as http:
        response = await http.post(CONTROL_CARD_CONFIG_PATH, json=selectors, headers=headers)
        assert response.status == expected
        assert_private(await response.text(), path)
        assert not app[server.SESSIONS_KEY]


async def test_control_endpoint_requires_loopback_even_with_correct_token(private_state, tmp_path):
    path = tmp_path / "config.yaml"
    write_config(path, {"feishu": CREDENTIALS})
    _, _, app = build_app(path)
    request = make_mocked_request("POST", CONTROL_CARD_CONFIG_PATH, app=app, headers={process.CONTROL_TOKEN_HEADER: TOKEN})
    request._transport_peername = ("203.0.113.7", 50000)
    response = await server._control_card_config(request)
    assert response.status == 403
    assert_private(response.text, path)


@pytest.mark.parametrize("failure", ["pid", "token_hash", "path_hash", "scope_hash", "schema", "values", "old_values", "oversize", "redirect", "old_endpoint", "pidfile_changed"])
async def test_fetch_refuses_unverified_local_control_response(private_state, tmp_path, failure):
    path = tmp_path / "config.yaml"
    selectors = {"profile_id": "default", "bot_id": "default"}
    response = {
        "ok": True, "schema_version": SNAPSHOT_SCHEMA,
        "config_path_hash": config_path_identity(path), "scope_hash": scope_identity(**selectors),
        "process_pid": os.getpid(), "process_token_hash": sha256(TOKEN.encode()).hexdigest(),
        "values": explain_reading_config({})["values"],
    }
    if failure == "pid":
        response["process_pid"] += 1
    elif failure in {"token_hash", "path_hash", "scope_hash"}:
        response[{"token_hash": "process_token_hash", "path_hash": "config_path_hash", "scope_hash": "scope_hash"}[failure]] = "wrong"
    elif failure == "schema":
        response["schema_version"] = "unknown"
    elif failure == "values":
        response["values"]["token"] = TOKEN
    elif failure == "old_values":
        del response["values"]["live_tool_activity"]
    elif failure == "oversize":
        response["padding"] = "x" * 65536
    requests, leaks = [], []
    async def handler(request):
        requests.append(await request.json())
        assert request.headers[process.CONTROL_TOKEN_HEADER] == TOKEN
        if failure == "old_endpoint":
            raise web.HTTPNotFound()
        if failure == "redirect":
            raise web.HTTPFound("/leak")
        if failure == "pidfile_changed":
            process.write_pid_record(os.getpid(), "different-private-token")
        return web.json_response(response)
    async def leak(request):
        leaks.append(dict(request.headers))
        return web.json_response(response)
    app = web.Application()
    app.router.add_post(CONTROL_CARD_CONFIG_PATH, handler)
    app.router.add_route("*", "/leak", leak)
    async with TestServer(app) as http:
        config = {"server": {"host": "127.0.0.1", "port": http.port}}
        result = await asyncio.to_thread(process.fetch_loaded_card_config, path, config, **selectors)
    assert result["status"] == "unavailable"
    assert set(result) == {"status", "reason"}
    assert requests and not leaks
    assert_private(json.dumps(result), path)


@pytest.mark.parametrize("failure", ["missing_pidfile", "insecure_pidfile", "wrong_owner", "insecure_state"])
def test_fetch_requires_private_managed_pidfile_before_network(private_state, tmp_path, monkeypatch, failure):
    if failure == "missing_pidfile":
        process.pid_path().unlink()
    elif failure == "insecure_pidfile":
        if os.name == "nt":
            pytest.skip("POSIX mode check")
        process.pid_path().chmod(0o644)
    elif failure == "wrong_owner":
        if not hasattr(os, "getuid"):
            pytest.skip("POSIX owner check")
        actual_uid = os.getuid()
        monkeypatch.setattr(process.os, "getuid", lambda: actual_uid + 1)
    else:
        if os.name == "nt":
            pytest.skip("POSIX mode check")
        private_state.chmod(0o755)
    monkeypatch.setattr(process.urllib.request, "build_opener", lambda *args: pytest.fail("unverified owner reached network"))
    result = process.fetch_loaded_card_config(tmp_path / "config.yaml", {"server": {"host": "127.0.0.1", "port": 1}}, profile_id="default", bot_id="default")
    assert result["status"] == "unavailable"
