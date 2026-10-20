"""Pinned native admission/waiter and rendered delivery; I/O/DB ports are fake.

A real Session Authority SQLite test is separate from this call-chain harness.
"""

import ast
import asyncio
from contextlib import nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
import gzip
import hashlib
import json
import logging
from pathlib import Path
import sys
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime as h
from hermes_feishu_card.install.decomposed_ownership import render


FIXTURE = Path(__file__).parents[1] / "fixtures/session_authority_5fc490e.json"


def load_sources():
    manifest = json.loads(FIXTURE.read_text())
    compressed = FIXTURE.with_suffix(".json.gz").read_bytes()
    assert hashlib.sha256(compressed).hexdigest() == manifest["archive_sha256"]
    files = json.loads(gzip.decompress(compressed))["files"]
    assert {
        k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()
    } == manifest["files"]
    return files


def functions(raw, names, namespace, filename):
    nodes = [
        n
        for n in ast.walk(ast.parse(raw))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    assert len(nodes) == len(names)
    compiled = compile(raw, filename, "exec", dont_inherit=True)

    def find(code, name):
        for value in code.co_consts:
            if isinstance(value, CodeType):
                if value.co_name == name:
                    return value
                found = find(value, name)
                if found is not None:
                    return found
        return None

    for node in nodes:
        defaults = tuple(
            eval(compile(ast.Expression(value), filename, "eval"), namespace)
            for value in node.args.defaults
        )
        function = FunctionType(
            find(compiled, node.name), namespace, node.name, defaults or None
        )
        function.__kwdefaults__ = {
            arg.arg: eval(compile(ast.Expression(value), filename, "eval"), namespace)
            for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
            if value is not None
        }
        namespace[node.name] = function


@dataclass
class Source:
    platform: str = "feishu"
    chat_id: str = "chat-a"
    thread_id: str = "omt_thread-a"
    user_id: str = "ou_sender-a"
    profile_id: str = "default"
    chat_type: str = "p2p"


@dataclass
class Event:
    source: Source = field(default_factory=Source)
    message_id: str = "ingress-a"
    text: str = "question"
    metadata: dict = field(default_factory=dict)
    internal: bool = False

    def get_command(self):
        return None


class RuntimeErrorPort(Exception):
    pass


async def noop(*args, **kwargs):
    pass


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from hermes_feishu_card import admission_completion as ac

    private_home = tmp_path / "private-home"
    private_home.mkdir()
    monkeypatch.setenv("HOME", str(private_home))
    monkeypatch.setenv("HERMES_HOME", str(private_home / ".hermes"))
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "sidecar-state"))
    files = load_sources()
    for path, raw in files.items():
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(raw)
    from hermes_feishu_card.install.source_layout import DECOMPOSED_SOURCE_TARGETS

    rendered = render({k: files[k].encode() for k in DECOMPOSED_SOURCE_TARGETS})
    result = SimpleNamespace(
        posts=[],
        native=[],
        ledger=[],
        attachments=[],
        staged=[],
        body="complete answer",
        media=[],
        images=[],
        files=[],
        before_extract=lambda: None,
        before_deliver=lambda: None,
        fail_attachments=False,
        tts=False,
        tts_calls=[],
        real_post=h._post_json_ordered_response,
    )
    h.reset_runtime_state()
    monkeypatch.setattr(
        h,
        "load_runtime_config",
        lambda: SimpleNamespace(
            enabled=True, event_url="http://127.0.0.1:1/events", timeout_seconds=0.2
        ),
    )
    monkeypatch.setattr(h, "_ensure_runtime_control_started", lambda *a: None)
    monkeypatch.setattr(h, "_flush_pending_deltas_for_local_vars", noop)

    async def gate(*a, **k):
        return h._PolicyGateResult(True, None)

    monkeypatch.setattr(h, "_policy_gate_async", gate)

    async def post(url, payload, timeout):
        result.posts.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(h, "_post_json_ordered_response", post)
    monkeypatch.setattr(h, "_native_handoff_plan_fingerprint", lambda _: "f" * 64)
    monkeypatch.setattr(h, "_native_handoff_runtime_wrappers_ready", lambda _: True)
    globs = dict(
        asyncio=asyncio,
        contextlib=__import__("contextlib"),
        os=__import__("os"),
        logger=logging.getLogger(__name__),
        diagnostic_wake_muted=lambda _: False,
        stop_reply_clock=lambda *a: None,
        _reply_anchor_for_event=lambda e: e.message_id,
        _thread_metadata_for_event=lambda e: {"thread_id": e.source.thread_id},
        _mark_notify_metadata=lambda d: {**d, "notify": True},
        _strip_media_directives=lambda s: s,
        _ExtractedResponse=SimpleNamespace,
        EphemeralReply=type("EphemeralReply", (str,), {}),
        ProcessingOutcome=SimpleNamespace(
            SUCCESS="success", FAILURE="failure", CANCELLED="cancelled"
        ),
    )
    base_names = {
        "_process_message_background",
        "_send_final_text",
        "send_final_ledgered",
        "_extract_response_content",
    }
    functions(
        rendered["gateway/platforms/base.py"].decode(),
        base_names,
        globs,
        str(tmp_path / "gateway/platforms/base.py"),
    )
    base_module = ModuleType("gateway.platforms.base")
    base_module.BasePlatformAdapter = type(
        "BasePlatformAdapter", (), {n: globs[n] for n in base_names}
    )
    base_module.SendResult = SimpleNamespace
    base_module._thread_metadata_for_event = globs["_thread_metadata_for_event"]
    base_module._mark_notify_metadata = globs["_mark_notify_metadata"]
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", base_module)
    functions(
        rendered["gateway/run_turn_persistence.py"].decode(),
        {"_hmwa_deliver_turn_response"},
        globs,
        str(tmp_path / "gateway/run_turn_persistence.py"),
    )

    class Adapter(base_module.BasePlatformAdapter):
        name = "feishu"
        _app_id = "synthetic-app"

        def __init__(self):
            self._active_sessions = {}
            self._pending_messages = {}
            self._expected_cancelled_tasks = set()
            self._streaming_tts_completed_turns = set()

        _start_typing_refresh = lambda *a: None
        _media_delivery_scope = lambda *a: nullcontext()
        _unwrap_ephemeral = lambda self, text: (text, 0)

        def extract_media(self, text):
            result.before_extract()
            return result.media, "" if result.media else text

        filter_media_delivery_paths = lambda self, media, **k: media
        extract_images = lambda self, text: (
            result.images,
            "" if result.images else text,
        )
        extract_local_files = lambda self, text: (
            result.files,
            "" if result.files else text,
        )
        filter_local_delivery_paths = lambda self, media, **k: media
        _wants_auto_tts = lambda *a: result.tts

        async def _synthesize_auto_tts(self, text):
            return [str(tmp_path / "owned-tts.wav")], None

        async def _play_tts_file(self, event, text, path, first, metadata, record):
            result.tts_calls.append(text)
            record(SimpleNamespace(success=True, message_id="tts-port"))
            return True

        pause_typing_for_chat = lambda *a: None
        _streaming_tts_turn_key = lambda *a, **k: ""
        _finish_session_task = lambda *a: None
        _final_delivery_adapter = lambda self, source: self
        _run_processing_hook = noop
        _release_turn_marker = noop
        _stop_typing_refresh = noop
        _fire_post_delivery_callback = noop
        _flush_text_debounce_now = noop
        _bounded_history_media_paths_for_session = noop

        async def _record_delivery_obligation(
            self, event, key, text, adapter, ephemeral
        ):
            result.ledger.append(("attempt", event.message_id, text))
            return "obligation-" + event.message_id

        async def _finalize_delivery_obligation(self, oid, sent, event, adapter):
            result.ledger.append(("final", event.message_id, sent.success))

        async def _send_with_retry(self, **kw):
            result.native.append(kw["content"])
            return SimpleNamespace(success=True, message_id="native")

        async def _deliver_attachments(self, event, extracted, metadata, **kw):
            if result.fail_attachments:
                raise RuntimeError("attachment I/O port failed")
            result.attachments.append(
                (
                    event.message_id,
                    extracted.images,
                    extracted.local_files,
                    extracted.media_files,
                )
            )

        async def _notify_turn_error(self, event, error):
            raise error

    adapter = Adapter()

    class Runner:
        _draining = False
        _delivery_adapter_for = lambda self, source: adapter
        _adapter_for_source = lambda self, source: adapter
        _should_send_voice_reply = lambda *a, **k: False
        _reply_anchor_for_event = lambda self, event: event.message_id
        _is_session_run_current = lambda self, key, generation: generation == 101
        _clear_durable_active_turn = noop

        async def _handle_message(self, event):
            reply = await globs["_hmwa_deliver_turn_response"](
                self,
                event,
                event.source,
                SimpleNamespace(session_id="physical-session"),
                "route-key",
                101,
                {
                    "final_response": result.body,
                    "completed": True,
                    "session_id": "physical-session",
                },
                [],
                result.body,
                "",
                False,
            )
            result.staged.append(
                h._exact_completion_stage_for_current_task() is not None
            )
            return reply

    runner = Runner()
    adapter.gateway_runner = runner
    monkeypatch.setattr(
        ac,
        "_authority_current",
        lambda offer, started: (
            h._is_lower_hex("f" * 64, 64)
            and offer.runner() is runner
            and offer.adapter() is adapter
            and adapter._app_id == offer.app_id
            and ac.monotonic() < offer.expires
        ),
    )
    ingress = ModuleType("gateway.session_ingress")
    ingress.__file__ = str(tmp_path / "gateway/session_ingress.py")
    ingress.__dict__.update(
        asyncio=asyncio,
        replace=replace,
        logger=logging.getLogger(__name__),
        MessageEvent=Event,
        RuntimeStoreError=RuntimeErrorPort,
        _MARKER_FIELDS=(
            "_gateway_active_turn_session_key",
            "_gateway_active_turn_token",
        ),
        admission_author=ContextVar("author", default=None),
        executing_admission=ContextVar("executing", default=False),
        viewer_turn=ContextVar("viewer", default=None),
        busy_acceptance=ContextVar("accepted", default=None),
        busy_commit=ContextVar("commit", default=None),
        row_turn_author=lambda *a: None,
    )
    functions(
        files["gateway/session_ingress.py"],
        {
            "dispatch_shared_busy",
            "admit_message",
            "execute_admission",
            "deliver_response",
            "deliver_settled",
            "_hand_over_turn_marker",
            "_marker_adopters",
            "_move_marker",
            "_held_markers",
            "_registry",
            "waiter_replies",
            "_release_turn_marker",
        },
        ingress.__dict__,
        ingress.__file__,
    )

    async def scope(*a):
        return nullcontext()

    ingress._execution_scope = scope
    monkeypatch.setitem(sys.modules, "gateway.session_ingress", ingress)
    for name, values in {
        "hermes_state_runtime": dict(
            RuntimeStoreError=RuntimeErrorPort,
            get_session_admission=lambda *a, **k: {"status": "started"},
        ),
        "gateway.session_completion_notes": dict(note_if_unviewed=noop),
        "gateway.session_policy": dict(policy_for_source=lambda *a: None),
        "gateway.session_managed_worker": dict(
            managed_policy=lambda *a: None, execute_managed=noop
        ),
        "gateway.config": dict(
            Platform=SimpleNamespace(API_SERVER="api", LOCAL="local")
        ),
        "gateway.session_api_turn": dict(
            api_execution=ContextVar("api", default=None),
            prepare_api_execution=lambda *a: None,
        ),
        "gateway.session_results": dict(
            execution_result=ContextVar("result", default=None)
        ),
        "gateway.session_operator": dict(is_viewer_input=lambda _: False),
        "gateway.session_runtime_workers": dict(persist_work_count=lambda *a: None),
    }.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    auth_module = ModuleType("gateway.session_authority")
    auth_module.__file__ = str(tmp_path / "gateway/session_authority.py")
    auth_module.__dict__.update(
        asyncio=asyncio,
        contextlib=__import__("contextlib"),
        _log_drain_failure=lambda t: None,
        messaging_admission=ContextVar("messaging", default=None),
    )
    functions(
        files["gateway/session_authority.py"],
        {
            "_schedule",
            "_drain",
            "_resolve_settled_waiter",
            "_execute_claimed",
            "_stamp_claim",
        },
        auth_module.__dict__,
        auth_module.__file__,
    )
    auth_module.SessionAuthority = type(
        "SessionAuthority",
        (),
        {
            name: auth_module.__dict__[name]
            for name in (
                "_schedule",
                "_drain",
                "_resolve_settled_waiter",
                "_execute_claimed",
                "_stamp_claim",
            )
        },
    )
    monkeypatch.setitem(sys.modules, "gateway.session_authority", auth_module)

    class Authority:
        _schedule = auth_module._schedule
        _resolve_settled_waiter = auth_module._resolve_settled_waiter

        def __init__(self, event):
            self.runner = runner
            self.profile_id = "default"
            self.epoch = "epoch"
            self.db = type("DBPort", (), {})()
            self.instance_id = "port-instance"
            self.retiring = False
            self.ref = SimpleNamespace(session_id="authority-session")
            self.sessions = {self.ref.session_id: type("LivePort", (), {})()}
            self.sessions[self.ref.session_id].source = event.source
            self.sessions[self.ref.session_id].route = "route-key"
            self.sessions[self.ref.session_id].task = None
            self.waiters = {}
            self.native_waiters = set()
            self.pending_results = {}
            self.pending_deliveries = {}
            self.executing = {}
            self.event = event
            self.row = {
                "admission_id": "admission-" + event.message_id,
                "generation": 7,
                "payload": {
                    "native_text_v1": {
                        "source": {
                            "platform": "feishu",
                            "chat_id": event.source.chat_id,
                            "thread_id": event.source.thread_id,
                            "user_id": event.source.user_id,
                            "profile": event.source.profile_id,
                        },
                        "event": {"message_id": event.message_id},
                        "route": "route-key",
                    },
                    "text": event.text,
                },
            }
            ingress.restore_native = lambda *a: replace(
                self.event, source=replace(self.event.source)
            )

        async def admit_native(self, event):
            self._schedule(self.ref)
            return SimpleNamespace(
                status="queued", admission_id=self.row["admission_id"], ref=self.ref
            )

        async def _drain(self, ref):
            self.executing[ref.session_id] = 7
            try:
                reply = await ingress.execute_admission(self, ref, self.row)
            except BaseException as error:
                future = self.waiters.get(self.row["admission_id"])
                if future is not None and not future.done():
                    future.set_exception(error)
                raise
            finally:
                self.executing.pop(ref.session_id, None)
            result.before_deliver()
            await ingress.deliver_settled(self, self.row["admission_id"])
            await self._resolve_settled_waiter(self.row["admission_id"], reply)

    result.adapter = adapter
    result.runner = runner
    result.ingress = ingress
    result.Authority = Authority
    yield result
    h.reset_runtime_state()


@pytest.mark.parametrize("waiter", [True, False])
async def test_native_admission_waiter_and_drain_owned_finish_exact_card(
    harness, waiter
):
    event = Event()
    authority = harness.Authority(event)
    if waiter:
        harness.adapter._message_handler = lambda e: harness.ingress.admit_message(
            authority, e
        )
        await asyncio.wait_for(
            harness.adapter._process_message_background(event, "route-key"), 3
        )
        await authority.sessions["authority-session"].task
    else:
        await authority._drain(authority.ref)
    assert harness.staged == [True]
    assert len(harness.posts) == 1
    assert harness.posts[0]["event"] == "message.completed"
    assert harness.posts[0]["data"]["answer"] == "complete answer"
    assert harness.native == []
    assert harness.ledger == [
        ("attempt", "ingress-a", "complete answer"),
        ("final", "ingress-a", True),
    ]


async def deliver(harness, *, waiter=False, event=None):
    event = event or Event()
    authority = harness.Authority(event)
    if waiter:
        harness.adapter._message_handler = lambda e: harness.ingress.admit_message(
            authority, e
        )
        await asyncio.wait_for(
            harness.adapter._process_message_background(event, "route-key"), 3
        )
        await authority.sessions["authority-session"].task
    else:
        await authority._drain(authority.ref)
    return event, authority


@pytest.mark.parametrize("waiter", [True, False])
@pytest.mark.parametrize("kind", ["media", "images", "files"])
async def test_native_actual_extraction_attachment_only_completes_without_text_ack(
    harness, waiter, kind
):
    setattr(harness, kind, ["/tmp/fixture.png"])
    await deliver(harness, waiter=waiter)
    assert len(harness.posts) == 1
    assert harness.posts[0]["data"]["answer"] == ""
    assert harness.posts[0]["data"]["native_delivery"] == "required"
    assert "obligation_key" not in harness.posts[0]["data"]["native_handoff"]
    assert harness.native == []
    assert harness.attachments


@pytest.mark.parametrize("waiter", [True, False])
async def test_sidecar_post_failure_preserves_complete_native_and_ledger(
    harness, monkeypatch, waiter
):
    async def failed(*a, **k):
        raise OSError("loopback transport failed")

    monkeypatch.setattr(h, "_post_json_ordered_response", failed)
    await deliver(harness, waiter=waiter)
    assert harness.native == ["complete answer"]
    assert harness.ledger[-1] == ("final", "ingress-a", True)


@pytest.mark.parametrize(
    "change", ["bot", "sender", "thread", "profile", "chat", "ttl", "source"]
)
async def test_changed_handoff_identity_or_expiry_never_suppresses_native(
    harness, change, monkeypatch
):
    from hermes_feishu_card import admission_completion as ac

    if change == "bot":
        harness.before_extract = lambda: setattr(
            harness.adapter, "_app_id", "other-app"
        )
    elif change == "ttl":
        harness.before_extract = lambda: monkeypatch.setattr(
            ac, "monotonic", lambda: 10**15
        )
    elif change == "source":
        harness.before_extract = lambda: Path(harness.ingress.__file__).write_text(
            "# drift"
        )
    else:
        original = ac.consume_extracted

        def consume(values, profile):
            event = values["event"]
            attribute = {
                "sender": "user_id",
                "thread": "thread_id",
                "profile": "profile_id",
                "chat": "chat_id",
            }[change]
            setattr(event.source, attribute, "other")
            return original(values, profile)

        monkeypatch.setattr(ac, "consume_extracted", consume)
    await deliver(harness)
    assert harness.posts == []
    assert harness.native == ["complete answer"]


async def test_attachment_failure_cleans_carrier_and_next_turn_is_not_starved(harness):
    harness.media = ["/tmp/fixture.png"]
    harness.fail_attachments = True
    await deliver(harness)
    assert harness.posts == []
    assert h._exact_completion_stage_for_current_task() is None
    harness.media = []
    harness.fail_attachments = False
    await deliver(harness, event=Event(message_id="ingress-b"))
    assert len(harness.posts) == 1 and harness.posts[0]["turn_id"] == "ingress-b"
    assert harness.native == []


async def test_duplicate_and_late_original_delivery_do_not_replay_card(harness):
    _, authority = await deliver(harness)
    await harness.ingress.deliver_settled(authority, authority.row["admission_id"])
    assert len(harness.posts) == 1
    assert h._exact_completion_stage_for_current_task() is None


@pytest.mark.parametrize(
    "module,function",
    [
        ("gateway.session_ingress", "deliver_settled"),
        ("gateway.session_ingress", "execute_admission"),
        ("gateway.session_authority", "_drain"),
    ],
)
def test_same_file_loaded_topology_drift_is_rejected(harness, module, function):
    from hermes_feishu_card import admission_completion as ac

    assert ac._verified() is not None
    owner = sys.modules[module]
    if module.endswith("authority"):
        owner = owner.SessionAuthority
    original = getattr(owner, function)

    async def replacement(*a, **k):
        return None

    setattr(owner, function, replacement)
    try:
        assert ac._verified() is None
    finally:
        setattr(owner, function, original)


def test_full_official_family_idempotence_and_original_bytes_restore():
    from hermes_feishu_card.install import patcher

    files = load_sources()
    raw = files["gateway/platforms/base.py"]
    patched = patcher.apply_base_patch(raw)
    assert patched.count("capture_admission_base_extraction(locals())") == 1
    assert patcher.apply_base_patch(patched) == patched
    assert patcher.remove_base_patch(patched) == raw
    assert patcher.remove_base_patch_lenient(patched) == raw
    drift = raw.replace(
        'force_document = "[[as_document]]" in response', "force_document = False"
    )
    assert (
        "capture_admission_base_extraction(locals())"
        not in patcher.apply_base_patch(drift)
    )


async def test_real_busy_observer_copies_carrier_with_original_marker_move(harness):
    event = Event()
    authority = harness.Authority(event)
    harness.adapter._background_tasks = set()
    harness.adapter._message_handler = lambda e: harness.ingress.admit_message(
        authority, e
    )
    await asyncio.wait_for(
        harness.ingress.dispatch_shared_busy(harness.adapter, event, "route-key"), 4
    )
    tasks = list(harness.adapter._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)
    await authority.sessions["authority-session"].task
    assert len(harness.posts) == 1
    assert harness.native == []
    assert harness.ledger[-1] == ("final", "ingress-a", True)


async def test_real_late_waiter_registration_moves_held_carrier_to_adopter(harness):
    original = Event()
    adopter = Event()
    authority = harness.Authority(original)

    def register():
        authority.native_waiters.add(authority.row["admission_id"])
        authority.marker_adopters = {authority.row["admission_id"]: adopter}
        authority.waiters[authority.row["admission_id"]] = (
            asyncio.get_running_loop().create_future()
        )

    harness.before_deliver = register
    await authority._drain(authority.ref)
    assert harness.posts == []
    assert authority.pending_deliveries == {}
    await harness.ingress.deliver_response(
        harness.adapter, adopter, "route-key", "complete answer"
    )
    assert len(harness.posts) == 1 and harness.native == []


@pytest.mark.parametrize("change", ["drop-hook", "same-token"])
async def test_loaded_extractor_drift_with_valid_source_never_uses_direct_suppression(
    harness, change
):
    original = harness.adapter._extract_response_content

    async def altered(self, *a, **k):
        if change == "same-token":
            capture_admission_base_extraction = None
            assert capture_admission_base_extraction is None
        return await original(*a, **k)

    from types import MethodType

    harness.adapter._extract_response_content = MethodType(altered, harness.adapter)
    await deliver(harness)
    assert harness.posts == []
    assert harness.native == ["complete answer"]


@pytest.mark.parametrize("failed", [True, False])
async def test_extractor_exception_never_leaves_previous_stage_for_next_admission(
    harness, failed
):
    if failed:

        def error():
            raise RuntimeError("extractor failed")

        harness.before_extract = error
        await deliver(harness)
        assert harness.posts == [] and harness.native == []
        assert h._exact_completion_stage_for_current_task() is None
        harness.before_extract = lambda: None
    await deliver(harness, event=Event(message_id="ingress-next"))
    assert len(harness.posts) == 1
    assert harness.posts[0]["turn_id"] == "ingress-next"


async def test_real_base_tts_caption_skips_text_and_still_finishes_card(harness):
    harness.tts = True
    await deliver(harness, waiter=True)
    assert harness.tts_calls == ["complete answer"]
    assert len(harness.posts) == 1
    assert "obligation_key" not in harness.posts[0]["data"]["native_handoff"]
    assert harness.native == []
    assert h._exact_completion_stage_for_current_task() is None


@pytest.mark.parametrize("waiter", [True, False])
@pytest.mark.parametrize(
    "response",
    [{"ok": True, "applied": True}, {"ok": True}, {"ok": False, "applied": False}],
)
async def test_carrier_final_crosses_real_loopback_http_without_broadening_ack(
    harness, monkeypatch, waiter, response
):
    from aiohttp import web

    seen = []

    async def endpoint(request):
        seen.append(await request.json())
        return web.json_response(response)

    app = web.Application()
    app.router.add_post("/events", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(
            h,
            "load_runtime_config",
            lambda: SimpleNamespace(
                enabled=True,
                event_url=f"http://127.0.0.1:{port}/events",
                timeout_seconds=1,
            ),
        )
        monkeypatch.setattr(h, "_post_json_ordered_response", harness.real_post)
        await deliver(harness, waiter=waiter)
        assert len(seen) == 1
        assert seen[0]["data"]["answer"] == "complete answer"
        assert seen[0]["data"]["native_handoff"]["obligation_key"]
        assert harness.native == (
            [] if response.get("applied") is True else ["complete answer"]
        )
        assert harness.ledger[-1] == ("final", "ingress-a", True)
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("waiter", [True, False])
async def test_real_sidecar_events_handler_terminalizes_existing_owner(
    harness, monkeypatch, tmp_path, waiter
):
    from aiohttp.test_utils import TestServer, TestClient
    from hermes_feishu_card.server import create_app, SESSIONS_KEY
    from hermes_feishu_card.native_handoff import NativeHandoffStore

    class FeishuPort:
        async def send_card(self, *a, **k):
            return "om_owned-card"

        async def update_card_message(self, *a, **k):
            pass

        async def send_text_message(self, *a, **k):
            raise AssertionError("unexpected sidecar text")

    app = create_app(
        FeishuPort(),
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff"),
        operations_transport_root_secret=b"x" * 32,
    )
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    try:
        url = str(client.make_url("/events"))
        monkeypatch.setattr(
            h,
            "load_runtime_config",
            lambda: SimpleNamespace(enabled=True, event_url=url, timeout_seconds=1),
        )
        monkeypatch.setattr(h, "_post_json_ordered_response", harness.real_post)
        event = Event()
        started = h.build_event(
            "message.started",
            {
                "event": event,
                "source": event.source,
                "message_id": event.message_id,
                "answer": "",
            },
        )
        response = await client.post("/events", json=started)
        assert response.status == 200 and (await response.json())["applied"] is True
        await deliver(harness, waiter=waiter, event=event)
        sessions = list(app[SESSIONS_KEY].values())
        assert len(sessions) == 1
        assert sessions[0].status == "completed"
        assert sessions[0].answer_text == "complete answer"
        assert harness.native == []
    finally:
        await client.close()


@pytest.mark.parametrize("waiter", [True, False])
async def test_real_sidecar_authenticated_admission_rejection_keeps_native(
    harness, monkeypatch, tmp_path, waiter
):
    from aiohttp.test_utils import TestServer, TestClient
    from hermes_feishu_card.server import create_app, SESSIONS_KEY
    from hermes_feishu_card.native_handoff import NativeHandoffStore

    class NoFeishuExpected:
        async def send_card(self, *a, **k):
            raise AssertionError("unauthenticated event created card")

    app = create_app(
        NoFeishuExpected(),
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff"),
        operations_transport_root_secret=b"x" * 32,
        event_auth_required=True,
    )
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    try:
        url = str(client.make_url("/events"))
        monkeypatch.setattr(
            h,
            "load_runtime_config",
            lambda: SimpleNamespace(enabled=True, event_url=url, timeout_seconds=1),
        )
        monkeypatch.setattr(h, "_post_json_ordered_response", harness.real_post)
        await deliver(harness, waiter=waiter)
        assert not app[SESSIONS_KEY]
        assert harness.native == ["complete answer"]
        assert harness.ledger[-1] == ("final", "ingress-a", True)
    finally:
        await client.close()


async def test_owner_changes_during_terminal_http_ack_preserve_native(
    harness, monkeypatch
):
    async def post(*a, **k):
        harness.adapter._app_id = "new-app"
        return {"ok": True, "applied": True}

    monkeypatch.setattr(h, "_post_json_ordered_response", post)
    await deliver(harness)
    assert harness.native == ["complete answer"]
    assert harness.ledger[-1] == ("final", "ingress-a", True)
