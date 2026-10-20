"""Pinned Gateway egress -> actual HFC send wrapper -> loopback recall owner.

Native Feishu I/O is synthetic. This does not execute a model or live Gateway.
"""
import asyncio
from contextlib import nullcontext, suppress
import hashlib
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime as runtime, server
from test_server_notice_lifecycle import app_with_clients, drain


FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/compression_status_e06df9c.json').read_text())


def upstream():
    namespace = {'Any': object, 'Optional': object, '_swallow': lambda *a, **k: nullcontext(),
                 'asyncio': asyncio, 'suppress': suppress, 'logger': logging.getLogger(__name__)}
    for file in FIXTURE['files'].values():
        for part in file.get('fragments', []):
            assert hashlib.sha256(part['source'].encode()).hexdigest() == part['sha256']
            exec('from __future__ import annotations\n' + part['source'], namespace)
    for path in ('gateway/platforms/base.py', 'plugins/platforms/feishu/adapter.py'):
        assert 'send_or_update_status' not in FIXTURE['files'][path]['methods']
    return namespace


class Adapter:
    def __init__(self, result=None):
        self.result = result or SimpleNamespace(success=True, message_id='om_compression')
        self.sent = []
        self.edited = []

    async def _hfc_original_send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, reply_to, metadata))
        return self.result

    async def send(self, *args, **kwargs):
        return await runtime._hfc_send_with_native_command_result_card(self, *args, **kwargs)

    async def edit_message(self, *args, **kwargs):
        self.edited.append((args, kwargs))
        raise AssertionError('Feishu compression status has no editable bubble')


@pytest.fixture
def egress(monkeypatch):
    runtime.reset_runtime_state()
    monkeypatch.delenv('HERMES_FEISHU_CARD_PROFILE_ID', raising=False)
    async def allowed(*a):
        return True
    monkeypatch.setattr(runtime, '_hfc_direct_card_allowed_async', allowed)
    token = runtime._HFC_FEISHU_NOTICE_CONTEXT.set(dict(
        profile_id='work', chat_id='oc_fixture', thread_id='topic_fixture'))
    yield upstream()
    runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)
    runtime.reset_runtime_state()


def emitted(egress, kind):
    callbacks = []
    agent = SimpleNamespace(status_callback=lambda *args: callbacks.append(args),
                            _emit_status_kind=lambda *args, **kw: callbacks.append(args))
    if kind == 'preflight':
        text = egress['PREFLIGHT_COMPRESSION_STATUS_TEMPLATE'].format(tokens=120000, threshold=100000)
        egress['_emit_status'](agent, text)
    else:
        egress['_emit_compaction_done'](agent)
    return callbacks[0]


@pytest.mark.parametrize('kind', ['preflight', 'done'])
async def test_real_egress_http_recall_keeps_native_result_route_and_pending_dedup(egress, monkeypatch, kind):
    app, clients = app_with_clients()
    selected = clients['work', 'alpha']
    adapter = Adapter()
    event_type, text = emitted(egress, kind)
    async with TestClient(TestServer(app)) as http:
        monkeypatch.setattr(runtime, 'load_runtime_config', lambda: SimpleNamespace(
            enabled=True, event_url=str(http.make_url('/events')), timeout_seconds=2.0))
        posts = []
        original_post = runtime._post_json_ordered_response
        async def post(url, payload, timeout):
            posts.append(dict(payload))
            return await original_post(url, payload, timeout)
        monkeypatch.setattr(runtime, '_post_json_ordered_response', post)
        result = await egress['_send_or_update_status_coro'](
            adapter, 'oc_fixture', event_type, text, {'thread_id': 'topic_fixture'})
        assert result is adapter.result
        assert adapter.sent == [('oc_fixture', text, None, {'thread_id': 'topic_fixture'})]
        assert posts == [{'message_id': 'om_compression', 'delay_seconds': 15.0,
                          'route': {'profile_id': 'work', 'chat_id': 'oc_fixture',
                                    'conversation_id': 'topic_fixture'}}]
        source = SimpleNamespace(platform='feishu', profile_id='work', chat_id='oc_fixture', thread_id='topic_fixture')
        assert await runtime.recall_transient_thread_notice_async(source, text, result)
        assert app[server.METRICS_KEY].ephemeral_recalls_scheduled == 1
        assert len(app[server.EPHEMERAL_RECALL_TASKS_KEY]) == 1
        next(iter(app[server.EPHEMERAL_RECALL_WAKE_KEY].values()))[1].set()
        for _ in range(80):
            if selected.deleted:
                break
            await asyncio.sleep(.01)
        assert selected.deleted == ['om_compression']
        assert all(not c.deleted for c in clients.values() if c is not selected)
        assert not adapter.edited
        # Upstream only remembers a deletion ID; it does not use it as an edit
        # target. Late terminal cleanup can DELETE the same id without editing.
        ctx = SimpleNamespace(_cleanup_progress=True, _cleanup_msg_ids=[])
        egress['_track_progress_result'](SimpleNamespace(_ctx=ctx), result)
        assert ctx._cleanup_msg_ids == ['om_compression']
        callbacks, cleanup_attempts, tasks = [], [], []
        def schedule(coro, loop, **kwargs):
            tasks.append(loop.create_task(coro))
        monkeypatch.setitem(sys.modules, 'gateway.run', SimpleNamespace(safe_schedule_threadsafe=schedule))
        async def cleanup_delete(chat, mid):
            cleanup_attempts.append((chat, mid))
            return False  # Already deleted by HFC; upstream cleanup swallows it.
        native_cleanup = SimpleNamespace(
            delete_message=cleanup_delete,
            register_post_delivery_callback=lambda key, cb, **kw: callbacks.append(cb))
        ctx.session_key = 'synthetic-session'
        ctx.source = source
        ctx.run_generation = 'synthetic-generation'
        egress['_run_agent_schedule_bubble_cleanup'](None, {'failed': False}, native_cleanup, ctx)
        assert len(callbacks) == 1
        callbacks[0]()
        await asyncio.gather(*tasks)
        assert cleanup_attempts == [('oc_fixture', 'om_compression')]
        assert not adapter.edited
        assert app[server.METRICS_KEY].ephemeral_recalls_completed == 1
        # Later compression notices are fresh sends, never edits of the recalled
        # id. Do not invent a cross-turn recall registry to change that behavior.
        adapter.result = SimpleNamespace(success=True, message_id='om_compression_next')
        assert await egress['_send_or_update_status_coro'](
            adapter, 'oc_fixture', event_type, text, {'thread_id': 'topic_fixture'}) is adapter.result
        assert len(adapter.sent) == 2 and not adapter.edited


@pytest.mark.parametrize('fault', ['send-failed', 'no-id', 'foreign-chat', 'invalid-profile', 'unknown-profile', 'http-failed'])
async def test_unschedulable_send_or_route_never_changes_native_result(egress, monkeypatch, fault):
    app, _ = app_with_clients()
    result = SimpleNamespace(success=fault != 'send-failed', message_id='' if fault == 'no-id' else 'om_fault')
    adapter = Adapter(result)
    event_type, text = emitted(egress, 'preflight')
    async with TestClient(TestServer(app)) as http:
        monkeypatch.setattr(runtime, 'load_runtime_config', lambda: SimpleNamespace(
            enabled=True, event_url=str(http.make_url('/events')), timeout_seconds=2.0))
        context = dict(profile_id='work', chat_id='oc_fixture', thread_id='topic_fixture')
        if fault == 'foreign-chat':
            context['chat_id'] = 'other-chat'
        if fault == 'invalid-profile':
            context['profile_invalid'] = True
        if fault == 'unknown-profile':
            context['profile_id'] = 'unknown'
        if fault == 'http-failed':
            async def fail_post(*a):
                raise OSError('synthetic HTTP failure')
            monkeypatch.setattr(runtime, '_post_json_ordered_response', fail_post)
        token = runtime._HFC_FEISHU_NOTICE_CONTEXT.set(context)
        try:
            returned = await egress['_send_or_update_status_coro'](
                adapter, 'oc_fixture', event_type, text, {'thread_id': 'topic_fixture'})
        finally:
            runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)
        assert returned is result
        assert adapter.sent[0][1] == text
        assert app[server.METRICS_KEY].ephemeral_recalls_scheduled == 0
        assert not app[server.EPHEMERAL_RECALL_TASKS_KEY]


async def test_foreign_platform_retains_notice_and_http_owner_protects_late_answer(egress, monkeypatch):
    app, clients = app_with_clients()
    _, text = emitted(egress, 'done')
    result = SimpleNamespace(success=True, message_id='om_late')
    async with TestClient(TestServer(app)) as http:
        monkeypatch.setattr(runtime, 'load_runtime_config', lambda: SimpleNamespace(
            enabled=True, event_url=str(http.make_url('/events')), timeout_seconds=2.0))
        source = SimpleNamespace(platform='telegram', profile_id='work', chat_id='oc_fixture', thread_id='topic_fixture')
        assert not await runtime.recall_transient_thread_notice_async(source, text, result)
        assert app[server.METRICS_KEY].ephemeral_recalls_scheduled == 0
        source.platform = 'feishu'
        assert await runtime.recall_transient_thread_notice_async(source, text, result)
        app[server.FEISHU_MESSAGE_IDS_KEY]['synthetic-owner'] = 'om_late'
        task = next(iter(app[server.EPHEMERAL_RECALL_TASKS_KEY].values()))
        next(iter(app[server.EPHEMERAL_RECALL_WAKE_KEY].values()))[1].set()
        await task
        await drain()
        assert all(not c.deleted for c in clients.values())
        assert not await runtime.recall_transient_thread_notice_async(source, text, result)
        assert app[server.METRICS_KEY].ephemeral_recalls_scheduled == 1
        assert not app[server.EPHEMERAL_RECALL_TASKS_KEY]


async def test_delete_failure_keeps_successful_native_send_and_does_not_loop(egress, monkeypatch):
    app, clients = app_with_clients()
    clients['work', 'alpha'].fail_delete = True
    adapter = Adapter()
    event_type, text = emitted(egress, 'done')
    async with TestClient(TestServer(app)) as http:
        monkeypatch.setattr(runtime, 'load_runtime_config', lambda: SimpleNamespace(
            enabled=True, event_url=str(http.make_url('/events')), timeout_seconds=2.0))
        assert await egress['_send_or_update_status_coro'](
            adapter, 'oc_fixture', event_type, text, {'thread_id': 'topic_fixture'}) is adapter.result
        task = next(iter(app[server.EPHEMERAL_RECALL_TASKS_KEY].values()))
        next(iter(app[server.EPHEMERAL_RECALL_WAKE_KEY].values()))[1].set()
        await task
        await drain()
        assert adapter.result.success is True
        assert app[server.METRICS_KEY].ephemeral_recall_failures == 1
        assert app[server.METRICS_KEY].ephemeral_recalls_completed == 0
        assert not app[server.EPHEMERAL_RECALL_TASKS_KEY]
