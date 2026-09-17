"""Install real, hash-bound Hermes sources, without importing or starting Hermes.

CI supplies exact upstream checkouts. Local runs may opt in with
HFC_UPSTREAM_STABLE_ROOT / HFC_UPSTREAM_MAIN_ROOT /
HFC_UPSTREAM_PRODUCTION_ROOT; no network runs inside pytest.
"""
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.integrity import plan_integrity_repair


SOURCES = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_upstream_sources.json").read_text()
)


@pytest.mark.parametrize("baseline", ["stable", "main", "production", "historical", "image", "extracted"])
def test_pinned_upstream_install_repeat_doctor_restore(baseline, tmp_path, monkeypatch):
    configured = os.environ.get(f"HFC_UPSTREAM_{baseline.upper()}_ROOT")
    if not configured:
        pytest.skip(f"exact {baseline} Hermes source checkout not supplied")
    source = Path(configured)
    target = tmp_path / "hermes"
    target.mkdir()
    expected = SOURCES[baseline]
    originals = {}
    for name, digest in expected["sha256"].items():
        raw = (source / name).read_bytes()
        assert sha256(raw).hexdigest() == digest, (
            f"{baseline} {name} does not match upstream {expected['commit']}"
        )
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        originals[name] = raw

    # Deliberately source-only: official Docker images need not contain .git.
    # Only package/SDK installation is skipped; detection, patching, ownership,
    # CLI dispatch, repeat installation and restore execute unchanged.
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda detection: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda detection: None)
    detection = detect_hermes(target)
    assert detection.supported, detection.reason
    assert detection.version == expected.get("version", "0.21.0")
    assert detection.decomposed == (baseline in {"main", "extracted"})
    assert cli.main(["install", "--hermes-dir", str(target), "--yes"]) == 0
    installed = {name: (target / name).read_bytes() for name in originals}
    assert installed != originals
    for name, raw in installed.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    assert cli.main(["install", "--hermes-dir", str(target), "--yes"]) == 0
    assert installed == {name: (target / name).read_bytes() for name in originals}
    detection = detect_hermes(target)
    assert detection.supported, detection.reason
    assert cli._diagnose_install_state(detection)["status"] == "installed"
    integrity = plan_integrity_repair(detection)
    assert integrity.reason == "recovery_not_required", integrity
    assert not integrity.executable
    # Reproduce the pre-integrity manifest upgrade on the exact source, then
    # prove every owned byte survives migration and the stale fence is eligible.
    from hermes_feishu_card.install.integrity import migrate_integrity_manifest, integrity_acknowledgement_eligible
    from hermes_feishu_card.install.recovery import plan_recovery
    manifest_path = target / ".hermes_feishu_card_manifest"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("integrity", None)
    manifest_path.write_text(json.dumps(manifest))
    assert plan_integrity_repair(detection).reason == "integrity_migration_required"
    migrate_integrity_manifest(detection)
    assert installed == {name: (target / name).read_bytes() for name in originals}
    assert integrity_acknowledgement_eligible(detection, plan_recovery(detection), plan_integrity_repair(detection))
    assert cli.main(["uninstall", "--hermes-dir", str(target), "--yes"]) == 0
    assert originals == {name: (target / name).read_bytes() for name in originals}
    assert not list(target.rglob("*.hermes_feishu_card.bak"))
    assert not (target / ".hermes_feishu_card_manifest").exists()


@pytest.mark.asyncio
async def test_actual_upstream_redirect_keeps_original_callbacks_on_new_card(tmp_path, monkeypatch):
    import ast
    import asyncio
    import sys
    from types import ModuleType, SimpleNamespace
    from aiohttp.test_utils import TestClient, TestServer
    from hermes_feishu_card import hook_runtime, server
    from hermes_feishu_card.install import patcher
    from hermes_feishu_card.native_handoff import NativeHandoffStore

    configured = os.environ.get('HFC_UPSTREAM_MAIN_ROOT')
    if not configured:
        pytest.skip('exact upstream main source not supplied')
    raw = (Path(configured)/'gateway/run_busy.py').read_bytes()
    assert sha256(raw).hexdigest() == SOURCES['main']['sha256']['gateway/run_busy.py']
    patched = patcher._apply_redirect_patch(raw.decode())
    assert patcher.REDIRECT_PATCH_BEGIN in patched
    function = next(node for node in ast.walk(ast.parse(patched))
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == '_resolve_busy_steer_or_redirect')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function], type_ignores=[])
    namespace = {'MessageType': SimpleNamespace(TEXT='text')}
    exec(compile(ast.fix_missing_locations(module), 'verified-upstream-run-busy', 'exec'), namespace)
    gateway_module = ModuleType('gateway.run')
    gateway_module._AGENT_PENDING_SENTINEL = object()
    monkeypatch.setitem(sys.modules, 'gateway.run', gateway_module)
    hook_runtime.reset_runtime_state()

    class Client:
        def __init__(self): self.sent=[]; self.updated=[]
        async def send_card(self, chat_id, card, **kwargs):
            self.sent.append((chat_id, card, kwargs)); return 'om_card_' + str(len(self.sent))
        async def update_card_message(self, message_id, card): self.updated.append((message_id, card))
    feishu = Client()
    app = server.create_app(feishu, card_config={'flush_interval_ms': 0},
                            native_handoff_store=NativeHandoffStore(tmp_path/'handoff'))
    original_source = SimpleNamespace(platform='feishu', profile='default', chat_id='oc_topic', thread_id='omt_topic')
    original_event = SimpleNamespace(message_id='om_old')
    original_locals = {'source': original_source, 'event': original_event, 'message_id': 'om_old', 'conversation_id': 'oc_topic'}
    async with TestClient(TestServer(app)) as http:
        started = hook_runtime.build_event('message.started', original_locals)
        assert (await http.post('/events', json=started)).status == 200
        agent = SimpleNamespace(_supports_active_turn_redirect=True, redirect=lambda text: True)
        assert hook_runtime.bind_agent_turn_identity(agent, original_source)
        incoming_source = SimpleNamespace(platform='feishu', profile='default', chat_id='oc_topic', thread_id='omt_topic')
        incoming = SimpleNamespace(source=incoming_source, message_id='om_new', message_type='text',
                                   media_urls=[], media_types=[], text='redirect fixture')
        async def no_compression(session_key): return False
        runner = SimpleNamespace(_agent_has_active_subagents=lambda agent: False,
                                 _session_has_compression_in_flight=no_compression,
                                 _try_agent_verb=lambda agent, verb, text, session_key, **kwargs: agent.redirect(text),
                                 _BusySteerOutcome=SimpleNamespace)
        async def emit(local_vars, *, event_name):
            payload = hook_runtime.build_event(event_name, local_vars)
            response = await http.post('/events', json=payload)
            assert response.status == 200
            return True
        monkeypatch.setattr(hook_runtime, 'emit_from_hermes_locals_async', emit)
        outcome = await namespace['_resolve_busy_steer_or_redirect'](runner, incoming, 'session', 'interrupt', agent)
        assert outcome.redirected
        delta = hook_runtime.build_event('answer.delta', {**original_locals, 'text': 'continued from original callback'})
        assert (await http.post('/events', json=delta)).status == 200
        terminal = hook_runtime.build_event('message.completed', {**original_locals, 'answer': 'complete redirected answer'})
        assert (await http.post('/events', json=terminal)).status == 200
        for _ in range(100):
            if any(mid=='om_card_2' and 'complete redirected answer' in str(card) for mid,card in feishu.updated): break
            await asyncio.sleep(0.01)
        assert len(feishu.sent) == 2
        assert any(mid=='om_card_2' and 'complete redirected answer' in str(card) for mid,card in feishu.updated), ([(k, v.status, v.last_sequence, v.answer_text) for k,v in app[server.SESSIONS_KEY].items()], app[server.REDIRECT_SESSION_ALIASES_KEY], delta, terminal, feishu.updated)
        assert original_source._hfc_turn_id == 'om_old'
        assert not hasattr(incoming_source, '_hfc_turn_id')
