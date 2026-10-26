"""Executable contracts for the 79445a496c facade layout (fully offline)."""
import asyncio
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli, hook_runtime
from hermes_feishu_card.install import decomposed, decomposed_ownership, patcher, recovery
from hermes_feishu_card.install.detect import detect_hermes

FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_decomposed"


@pytest.fixture
def hermes(tmp_path):
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "VERSION").write_text("0.20.0\n")
    return root


def sources(root):
    return {name: (root / name).read_bytes() for name in decomposed.SOURCE_TARGETS
            if (root / name).exists()}


def test_decomposed_cli_install_doctor_repeat_remove_roundtrip(hermes, monkeypatch):
    # Runtime installation and SDK setup belong to deployment. This fixture
    # has no venv or adapter SDK; all patch/ownership/doctor calls remain real.
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda detection: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda detection: None)
    before = sources(hermes)
    detection = detect_hermes(hermes)
    assert detection.supported and detection.compatibility == "full"
    assert cli.main(["install", "--hermes-dir", str(hermes), "--yes"]) == 0
    installed = sources(hermes)
    assert {name for name in before if before[name] != installed[name]} == {name for name in patcher.DECOMPOSED_TARGETS if (FIXTURE / name).exists()}
    assert cli.main(["install", "--hermes-dir", str(hermes), "--yes"]) == 0
    assert installed == sources(hermes)
    detection = detect_hermes(hermes)
    assert cli._doctor_hermes_report(detection)["compatibility"] == "full"
    assert cli._diagnose_install_state(detection)["status"] == "installed"
    assert detection.capability_locations["answer_delta_callback"] == ("gateway/run_turn_runner.py",)
    assert detection.capability_locations["completion_return"] == ("gateway/run_turn.py",)
    assert "status_callback: found (gateway/run_turn_runner.py)" in cli._format_hermes_detection(detection)
    for name, raw in installed.items():
        compile(raw, name, "exec")
    assert cli.main(["uninstall", "--hermes-dir", str(hermes), "--yes"]) == 0
    assert sources(hermes) == before
    assert not list(hermes.rglob("*.hermes_feishu_card.bak"))
    assert not (hermes / decomposed.MANIFEST_NAME).exists()
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "clean"


def test_portable_restore_uses_bound_writer_and_exact_evidence(hermes, monkeypatch):
    from hermes_feishu_card.install import file_transaction

    detection = detect_hermes(hermes)
    before = sources(hermes)
    recovery.install_decomposed(detection)
    removed = []

    def remove(paths, *, expected_identities, expected_directories, recovery_contents):
        assert all(expected_identities[path] is not None for path in paths)
        assert set(recovery_contents) == set(paths)
        assert set(paths) == {
            hermes / (name + ".hermes_feishu_card.bak") for name in before
        } | {hermes / ".hermes_feishu_card_manifest"}
        file_transaction._assert_restore_evidence_set_unchanged(
            {path: expected_identities[path] for path in paths}
        )
        removed.extend(paths)
        for path in paths:
            path.unlink()

    monkeypatch.setattr(recovery, "_portable_restore_supported", lambda: True)
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(file_transaction, "_remove_targets_portably", remove)

    recovery.restore_decomposed(detect_hermes(hermes))

    assert removed
    assert sources(hermes) == before
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "clean"


def test_unsupported_restore_refuses_before_lock_or_state_read(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(recovery, "_portable_restore_supported", lambda: False)
    monkeypatch.setattr(
        recovery,
        "_root_lock",
        lambda _root: pytest.fail("unsupported restore must not acquire a lock"),
    )
    with pytest.raises(ValueError, match="directory-relative"):
        recovery.restore_decomposed(SimpleNamespace(root=tmp_path))


def test_portable_restore_deletion_failure_rolls_back_source_family(hermes, monkeypatch):
    from hermes_feishu_card.install import file_transaction

    detection = detect_hermes(hermes)
    before = sources(hermes)
    recovery.install_decomposed(detection)
    installed = sources(hermes)
    backups = {
        path: path.read_bytes()
        for path in hermes.rglob("*.hermes_feishu_card.bak")
    }
    manifest = (hermes / ".hermes_feishu_card_manifest").read_bytes()

    def fail_last_delete(paths, **kwargs):
        raise OSError("injected last evidence deletion failure")

    monkeypatch.setattr(recovery, "_portable_restore_supported", lambda: True)
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(file_transaction, "_remove_targets_portably", fail_last_delete)

    with pytest.raises(OSError, match="last evidence deletion failure"):
        recovery.restore_decomposed(detect_hermes(hermes))

    assert sources(hermes) == installed
    assert {path: path.read_bytes() for path in backups} == backups
    assert (hermes / ".hermes_feishu_card_manifest").read_bytes() == manifest
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "installed"


def test_portable_restore_rollback_failure_preserves_complete_evidence(hermes, monkeypatch):
    from hermes_feishu_card.install import file_transaction

    detection = detect_hermes(hermes)
    recovery.install_decomposed(detection)
    backups = {
        path: path.read_bytes()
        for path in hermes.rglob("*.hermes_feishu_card.bak")
    }
    manifest = (hermes / ".hermes_feishu_card_manifest").read_bytes()
    real_write = file_transaction._write_targets_portably
    writes = 0

    def fail_rollback(changes, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected source rollback failure")
        return real_write(changes, **kwargs)

    monkeypatch.setattr(recovery, "_portable_restore_supported", lambda: True)
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(file_transaction, "_remove_targets_portably",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("delete failed")))
    monkeypatch.setattr(file_transaction, "_write_targets_portably", fail_rollback)

    with pytest.raises(ValueError, match="rollback failed; manual review required"):
        recovery.restore_decomposed(detect_hermes(hermes))

    assert writes == 2
    assert {path: path.read_bytes() for path in backups} == backups
    assert (hermes / ".hermes_feishu_card_manifest").read_bytes() == manifest
    assert recovery.plan_recovery(detect_hermes(hermes)).state in {
        "installed", "owned_incomplete"
    }


@pytest.mark.parametrize("edit_kind", ["bytes", "inode", "directory"])
def test_portable_cleanup_failure_never_claims_a_concurrent_source_edit(
    hermes, tmp_path, monkeypatch, edit_kind
):
    from hermes_feishu_card.install import file_transaction

    detection = detect_hermes(hermes)
    recovery.install_decomposed(detection)
    target = next(
        hermes / name for name in decomposed.SOURCE_TARGETS
        if (hermes / name).exists()
    )
    user_edit = target.read_bytes() + b"\n# concurrent user edit during cleanup\n"
    backups = {
        path: path.read_bytes()
        for path in hermes.rglob("*.hermes_feishu_card.bak")
    }
    manifest = (hermes / ".hermes_feishu_card_manifest").read_bytes()

    def fail_after_edit(paths, **kwargs):
        if edit_kind == "bytes":
            target.write_bytes(user_edit)
        elif edit_kind == "inode":
            replacement = tmp_path / "replacement-source"
            replacement.write_bytes(user_edit)
            os.replace(replacement, target)
        else:
            moved = target.parent.with_name(target.parent.name + "-moved")
            target.parent.rename(moved)
            target.parent.mkdir()
            target.write_bytes(user_edit)
        raise OSError("injected cleanup failure after user edit")

    monkeypatch.setattr(recovery, "_portable_restore_supported", lambda: True)
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(file_transaction, "_remove_targets_portably", fail_after_edit)

    with pytest.raises((OSError, ValueError)):
        recovery.restore_decomposed(detect_hermes(hermes))

    assert target.read_bytes() == user_edit
    if edit_kind == "directory":
        moved_parent = target.parent.with_name(target.parent.name + "-moved")
        moved_backups = {
            moved_parent / path.relative_to(target.parent): value
            for path, value in backups.items() if target.parent in path.parents
        }
        assert {path: path.read_bytes() for path in moved_backups} == moved_backups
        unchanged = {
            path: value for path, value in backups.items()
            if target.parent not in path.parents
        }
        assert {path: path.read_bytes() for path in unchanged} == unchanged
    else:
        assert {path: path.read_bytes() for path in backups} == backups
    assert (hermes / ".hermes_feishu_card_manifest").read_bytes() == manifest


def test_portable_initial_write_failure_does_not_attempt_a_second_claim(hermes, monkeypatch):
    from hermes_feishu_card.install import file_transaction

    detection = detect_hermes(hermes)
    recovery.install_decomposed(detection)
    target = next(
        hermes / name for name in decomposed.SOURCE_TARGETS
        if (hermes / name).exists()
    )
    user_edit = target.read_bytes() + b"\n# concurrent edit before initial write failure\n"
    calls = []

    def fail_initial(changes, **kwargs):
        calls.append(changes)
        target.write_bytes(user_edit)
        raise OSError("injected initial portable write failure")

    monkeypatch.setattr(recovery, "_portable_restore_supported", lambda: True)
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    monkeypatch.setattr(file_transaction, "_write_targets_portably", fail_initial)

    with pytest.raises(OSError, match="initial portable write failure"):
        recovery.restore_decomposed(detect_hermes(hermes))

    assert len(calls) == 1
    assert target.read_bytes() == user_edit
    assert list(hermes.rglob("*.hermes_feishu_card.bak"))
    assert (hermes / ".hermes_feishu_card_manifest").is_file()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows handles")
def test_windows_restore_partial_delete_failure_restores_installed_family(hermes, monkeypatch):
    from hermes_feishu_card.install import file_transaction

    detection = detect_hermes(hermes)
    recovery.install_decomposed(detection)
    installed = sources(hermes)
    backups = {
        path: path.read_bytes()
        for path in hermes.rglob("*.hermes_feishu_card.bak")
    }
    manifest = (hermes / ".hermes_feishu_card_manifest").read_bytes()
    original_open = file_transaction._open_portable_delete_fd
    original_close = file_transaction.os.close
    delete_fds = []
    failed = False

    def open_delete(path):
        descriptor = original_open(path)
        delete_fds.append(descriptor)
        return descriptor

    def fail_last_delete_close(descriptor):
        nonlocal failed
        if descriptor in delete_fds and descriptor == delete_fds[-1] and not failed:
            failed = True
            raise OSError("injected last evidence close failure")
        return original_close(descriptor)

    monkeypatch.setattr(file_transaction, "_open_portable_delete_fd", open_delete)
    monkeypatch.setattr(file_transaction.os, "close", fail_last_delete_close)

    with pytest.raises(ValueError, match="original evidence restored"):
        recovery.restore_decomposed(detect_hermes(hermes))

    assert failed
    assert sources(hermes) == installed
    assert {path: path.read_bytes() for path in backups} == backups
    assert (hermes / ".hermes_feishu_card_manifest").read_bytes() == manifest
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "installed"


@pytest.mark.parametrize("target,old,new", [
    ("gateway/run_turn.py", 'await self.hooks.emit("agent:end",', 'await self.hooks.emit("agent:other",'),
    ("gateway/run_turn.py", 'agent_result, agent_messages, response, _footer_line, _intentional_silence,\n        )', 'agent_result, agent_messages, "wrong", _footer_line, _intentional_silence,\n        )'),
    ("gateway/run_turn.py", "async def _hmwa_post_turn_hooks(self, hook_ctx, agent_result, response):", "async def _hmwa_post_turn_hooks(self, hook_ctx, response, agent_result):"),
    ("gateway/run_turn.py", "agent_result, agent_messages, response, _footer_line, _intentional_silence,\n    ):", "response, agent_messages, agent_result, _footer_line, _intentional_silence,\n    ):"),
    ("gateway/run_turn_runner.py", "ctx = self._ctx", "ctx = self._other"),
    ("gateway/run_turn_runner.py", "def stream_delta_cb(text):", "def stream_delta_cb(wrong):"),
    ("gateway/run_busy.py", "_slash_confirm_mod.register(session_key, confirm_id, command, handler)", "_slash_confirm_mod.register(session_key, confirm_id, command, wrong)"),
    ("gateway/platforms/base.py", "session_key=session_key)", "session_key=wrong)"),
    ("gateway/platforms/base.py", "await asyncio.to_thread(mark_attempting, obligation_id)", "asyncio.to_thread(mark_attempting, obligation_id)"),
    ("gateway/platforms/base.py", "record_delivery(result)", "record_delivery(wrong)"),
    ("gateway/platforms/base.py", "reply_to=_reply_anchor_for_event(event)", "reply_to=None"),
    ("gateway/platforms/base.py", "self, event: MessageEvent, session_key: str, text_content: str, metadata:", "self, event: MessageEvent, text_content: str, session_key: str, metadata:"),
    ("gateway/platforms/base.py", "source = event.source", "source = self.other_source"),
    ("gateway/platforms/base.py", "_final_thread_metadata = _mark_notify_metadata(_thread_metadata)", "_final_thread_metadata = {}"),
])
def test_decomposed_contract_drift_fails_closed(hermes, target, old, new):
    path = hermes / target
    content = path.read_text()
    assert old in content
    path.write_text(content.replace(old, new))
    assert not detect_hermes(hermes).supported


def test_decomposed_accepts_record_delivery_kwarg_end_to_end(hermes):
    """A build that passes ``record_delivery=...`` to ``_deliver_attachments`` stays installable.

    Hermes adds that keyword to the exact final-delivery pipeline; it is orthogonal
    to the delivery contract, so detection must stay supported and the patched tree
    must round-trip byte-exact.
    """
    path = hermes / "gateway/platforms/base.py"
    old = "anything_sent=delivery_attempted or _tts_caption_delivered)"
    new = ("anything_sent=delivery_attempted or _tts_caption_delivered, "
           "record_delivery=_record_delivery)")
    content = path.read_text()
    assert content.count(old) == 1
    path.write_text(content.replace(old, new))
    detection = detect_hermes(hermes)
    assert detection.supported, detection.reason
    before = sources(hermes)
    decomposed.install(detection)
    assert sources(hermes) != before
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == before


@pytest.mark.parametrize("target", [name for name in patcher.DECOMPOSED_TARGETS if (FIXTURE / name).exists()])
def test_each_owned_file_is_required_for_restore(hermes, target):
    detection = detect_hermes(hermes)
    decomposed.install(detection)
    path = hermes / target
    path.write_bytes(path.read_bytes() + b"\n# user edit\n")
    edited = sources(hermes)
    assert recovery.plan_recovery(detection).state == "refused"
    with pytest.raises(ValueError, match="source drift"):
        decomposed.restore(detection)
    assert sources(hermes) == edited


def test_multifile_transaction_rolls_back_on_late_write_failure(hermes, monkeypatch):
    before = sources(hermes)
    replace = recovery._atomic_replace
    count = 0
    def fail_once(staged, target):
        nonlocal count
        count += 1
        if count == 5:
            raise OSError("injected write failure")
        return replace(staged, target)
    monkeypatch.setattr(recovery, "_atomic_replace", fail_once)
    with pytest.raises(OSError, match="injected"):
        decomposed.install(detect_hermes(hermes))
    assert sources(hermes) == before
    assert not list(hermes.rglob("*.hermes_feishu_card.bak"))
    assert not (hermes / decomposed.MANIFEST_NAME).exists()


def test_owned_missing_hooks_repair_and_no_repair(hermes):
    detection = detect_hermes(hermes)
    decomposed.install(detection)
    target = "gateway/run_turn_runner.py"
    installed = (hermes / target).read_bytes()
    (hermes / target).write_bytes((hermes / (target + decomposed.BACKUP_SUFFIX)).read_bytes())
    plan = recovery.plan_recovery(detection)
    assert plan.state == "owned_incomplete" and plan.executable
    with pytest.raises(ValueError, match="no-repair"):
        decomposed.install(detection, no_repair=True)
    recovery.execute_recovery(detection, expected_fingerprint=plan.fingerprint)
    assert (hermes / target).read_bytes() == installed


def test_manifest_path_escape_and_symlink_refused(hermes, tmp_path):
    detection = detect_hermes(hermes)
    decomposed.install(detection)
    manifest_path = hermes / decomposed.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["targets"]["gateway/run.py"]["backup"] = "../outside"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="ownership"):
        decomposed.restore(detection)
    target = hermes / "gateway/run_busy.py"
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    assert not detect_hermes(hermes).supported


def test_optional_status_stays_partial(hermes):
    path = hermes / "gateway/run_turn_runner.py"
    path.write_text(path.read_text().replace("def _status_callback_sync(", "def other_status("))
    detection = detect_hermes(hermes)
    assert detection.supported and detection.compatibility == "partial"
    assert not detection.capabilities["status_callback"]


def test_decomposed_callbacks_execute_with_real_context_mapping(monkeypatch):
    source = (FIXTURE / "gateway/run_turn_runner.py").read_text()
    namespace = {}
    exec(patcher.apply_gateway_fragment(source, "gateway/run_turn_runner.py"), namespace)
    emitted, native = [], []
    def emit(payload, *, event_name):
        emitted.append((event_name, payload))
        return True
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_threadsafe", emit)
    monkeypatch.setattr(hook_runtime, "request_clarify_response_from_hermes_locals", lambda *a, **kw: "chosen")
    monkeypatch.setattr(hook_runtime, "request_approval_choice_from_hermes_locals", lambda *a, **kw: "")
    ctx = SimpleNamespace(source=SimpleNamespace(platform="feishu"), event_message_id="fixture-message",
                          _loop_for_step=None, _run_still_current=lambda: True,
                          _status_chat_id="fixture-chat", session_key="fixture-session",
                          status_queue=SimpleNamespace(put=native.append),
                          progress_callback=lambda *a, **kw: native.append(a),
                          native_tool_start_callback=None, native_tool_complete_callback=None,
                          wait_for_clarify=lambda *a: "native",
                          send_approval=lambda *a: native.append(a))
    consumer = SimpleNamespace(on_delta=lambda text: native.append(text),
                               on_commentary=lambda text: native.append(text), on_segment_break=lambda: None)
    runner = namespace["TurnRunner"](SimpleNamespace(stream_consumer=consumer), ctx)
    _, answer, thinking, _ = runner._setup_stream_consumer("feishu")
    answer("answer")
    thinking("thinking")
    runner._status_callback_sync("status", "Compacting context")
    assert runner._clarify_callback_sync("question", ["a", "b"]) == "chosen"
    runner._approval_notify_sync({"command": "echo fixture"})
    agent = SimpleNamespace()
    runner._wire_turn_agent_callbacks(agent, None, None, answer, thinking, True)
    agent.tool_start_callback("fixture-call", "read_file", {})
    agent.tool_complete_callback("fixture-call", "read_file", {}, "ok")
    assert {name for name, _ in emitted} >= {"answer.delta", "thinking.delta", "tool.updated"}
    for _, payload in emitted:
        assert payload["source"] is ctx.source
        assert payload["message_id"] == ctx.event_message_id
    assert "answer" not in native and "thinking" not in native
    assert ("fixture-session", {"command": "echo fixture"}) in native


def test_facade_comments_are_not_capability_evidence(hermes):
    facade = hermes / "gateway/run.py"
    facade.write_text('# reply_to_message_id _reply_anchor_for_event extract_media _deliver_media_from_response\n')
    detection = detect_hermes(hermes)
    assert detection.supported
    assert "gateway/run.py" not in detection.capability_locations["reply_context"]
    assert "gateway/run.py" not in detection.capability_locations["attachment_delivery"]


def test_cron_routing_uses_definition_not_facade_filename(hermes):
    scheduler = hermes / "cron/scheduler.py"
    delivery = hermes / "cron/scheduler_delivery.py"
    scheduler.write_bytes(delivery.read_bytes())
    delivery.write_text('from .scheduler import _deliver_result\n')
    detection = detect_hermes(hermes)
    assert detection.supported and detection.compatibility == "full"
    assert detection.capability_locations["cron_delivery"] == ("cron/scheduler.py",)
    assert detection.cron_py == scheduler
    before = sources(hermes)
    decomposed.install(detection)
    assert patcher.CRON_PATCH_BEGIN in scheduler.read_text()
    assert delivery.read_bytes() == before["cron/scheduler_delivery.py"]
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == before
    scheduler.write_text("# facade without a delivery definition\n")
    assert not detect_hermes(hermes).supported


@pytest.mark.asyncio
@pytest.mark.parametrize("attachment", [None, "images", "local_files", "media_files", "media_only"])
@pytest.mark.parametrize("outcome", ["applied", "native", "error"])
async def test_generated_base_pipeline_preserves_ledger_media_and_fallback(monkeypatch, attachment, outcome):
    events, posted = [], []
    async def no_pending(_locals):
        pass
    async def policy(*args, **kwargs):
        return hook_runtime._PolicyGateResult(True, None)
    async def post(_url, payload, _timeout):
        events.append("terminal")
        posted.append(payload)
        if outcome == "error":
            raise OSError("offline simulated response loss")
        return {"ok": True, "applied": outcome == "applied"}
    async def absent(*args):
        return {"ok": True, "found": False}
    monkeypatch.setattr(hook_runtime, "_policy_gate_async", policy)
    monkeypatch.setattr(hook_runtime, "_flush_pending_deltas_for_local_vars", no_pending)
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", post)
    monkeypatch.setattr(hook_runtime, "_post_json_response", absent)
    monkeypatch.setattr(hook_runtime, "_native_handoff_plan_fingerprint", lambda adapter: "f" * 64)
    monkeypatch.setattr(hook_runtime, "_native_handoff_runtime_wrappers_ready", lambda adapter: True)
    ledger = SimpleNamespace(
        ledger_enabled=lambda: True,
        compute_obligation_id=lambda *args: "fixture-obligation",
        record_obligation=lambda **kwargs: events.append(("ledger", kwargs["content"])),
        mark_attempting=lambda obligation: events.append("attempting"),
        mark_delivered=lambda obligation: events.append("delivered"),
        mark_failed=lambda *args: events.append("failed"),
    )
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger)
    namespace = {"asyncio": asyncio, "logger": logging.getLogger(__name__),
                 "_ExtractedResponse": SimpleNamespace,
                 "_strip_media_directives": lambda text: text,
                 "_mark_notify_metadata": lambda metadata: {**metadata, "notify": True},
                 "_reply_anchor_for_event": lambda event: event.message_id}
    original = (FIXTURE / "gateway/platforms/base.py").read_text()
    exec(patcher.apply_base_patch(original), namespace)
    adapter = namespace["BasePlatformAdapter"]()
    adapter.name, adapter.typed_command_prefix = "feishu", "!"
    media = [("/tmp/fixture-video.mp4", False)] if attachment in {"media_files", "media_only"} else []
    images = [("https://example.invalid/fixture.png", "fixture")] if attachment == "images" else []
    local = ["/tmp/fixture-document.pdf"] if attachment == "local_files" else []
    text = "" if attachment == "media_only" else "exact text"
    adapter.extract_media = lambda response: (media, text)
    adapter.filter_media_delivery_paths = lambda paths, **kwargs: paths
    adapter.extract_images = lambda response: (images, response)
    adapter.extract_local_files = lambda response: (local, response)
    adapter.filter_local_delivery_paths = lambda paths, **kwargs: paths
    adapter._bounded_history_media_paths_for_session = no_pending
    adapter._final_delivery_adapter = lambda source: adapter
    adapter.record_delivery = lambda result: events.append(("record_delivery", result.success))
    async def native_send(**kwargs):
        events.append(("native", kwargs))
        return SimpleNamespace(success=True, message_id="fixture-native")
    async def attachments(event, extracted, metadata, **kwargs):
        events.append(("attachments", extracted.images, extracted.local_files, extracted.media_files))
    async def handler(event):
        assert await hook_runtime.stage_message_completed_from_hermes_locals_async({
            "platform": "feishu", "chat_id": "fixture-chat", "conversation_id": "fixture-session",
            "message_id": "fixture-message", "answer": "raw answer", "created_at": 1777017600.0,
        })
        return "raw answer"
    adapter._send_with_retry = native_send
    adapter._deliver_attachments = attachments
    adapter._message_handler = handler
    event = SimpleNamespace(text="question", message_id="fixture-message",
                            source=SimpleNamespace(platform="feishu", chat_id="fixture-chat"))
    await adapter._process_message_background(event, "fixture-session")
    assert len(posted) == 1
    assert events[-1] == ("attachments", images, local, media)
    assert hook_runtime._HFC_EXACT_COMPLETION_STAGE.get() is None
    data = posted[0]["data"]
    if attachment is not None:
        assert data["native_delivery"] == "required"
        assert "capabilities" not in data.get("native_handoff", {})
        assert data["attachments"]
    else:
        assert "native-ack-v2" in data["native_handoff"]["capabilities"]
    sends = [row for row in events if isinstance(row, tuple) and row[0] == "native"]
    if text:
        assert events[:3] == [("ledger", text), "attempting", "terminal"]
        assert events[-3:-1] == [("record_delivery", True), "delivered"]
        assert data["answer"] == text
        assert len(sends) == (0 if outcome == "applied" else 1)
        if sends:
            assert sends[0][1] == {"chat_id": "fixture-chat", "content": text,
                                   "reply_to": "fixture-message", "metadata": {"notify": True}}
    else:
        assert events == ["terminal", ("attachments", images, local, media)]


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_generated_turn_completion_keeps_timing_and_native_fallback(monkeypatch, accepted):
    events = []
    async def post_turn(*args):
        events.append("agent:end")
    async def completed(payload, *, event_name):
        events.append((event_name, payload["answer"], payload["duration"]))
        return accepted
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals", lambda *args, **kwargs: False)
    monkeypatch.setattr(hook_runtime, "can_stage_exact_base_completion", lambda payload: False)
    monkeypatch.setattr(hook_runtime, "build_event", lambda *args, **kwargs: {"data": {}})
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", completed)
    namespace = {}
    source = (FIXTURE / "gateway/run_turn.py").read_text()
    exec(patcher.apply_gateway_fragment(source, "gateway/run_turn.py"), namespace)
    runner = namespace["GatewayTurnMixin"]()
    runner.hooks = SimpleNamespace(emit=post_turn)
    source = SimpleNamespace(platform="feishu")
    event = SimpleNamespace(source=source, message_id="fixture-message", reply_to_message_id="fixture-parent")
    result = await runner._handle_message_with_agent(event, source, "fixture-session", 1)
    assert result == (None if accepted else "answer")
    assert events == ["agent:end", ("message.completed", "answer", 1.25)]


@pytest.mark.parametrize("newline", [b"\r\n", b"\n"])
def test_byte_exact_reinstall_after_uninstall(hermes, newline):
    for name, raw in sources(hermes).items():
        (hermes / name).write_bytes(raw.rstrip(b"\n").replace(b"\n", newline))
    before = sources(hermes)
    for _ in range(2):
        detection = detect_hermes(hermes)
        assert detection.supported
        assert decomposed.install(detection)
        installed = sources(hermes)
        assert not decomposed.install(detect_hermes(hermes))
        assert sources(hermes) == installed
        decomposed.restore(detect_hermes(hermes))
        assert sources(hermes) == before


def test_upgrade_requires_explicit_acceptance_and_binds_all_evidence(hermes):
    detection = detect_hermes(hermes)
    decomposed.install(detection)
    target = "gateway/run_turn_runner.py"
    original = (hermes / (target + decomposed.BACKUP_SUFFIX)).read_bytes()
    (hermes / target).write_bytes(original + b"\n# simulated source upgrade\n")
    detection = detect_hermes(hermes)
    plan = recovery.plan_recovery(detection)
    assert plan.state == "stale_unpatched" and not plan.executable
    with pytest.raises(ValueError, match="accept-hermes-upgrade"):
        decomposed.install(detection)
    approved = recovery.plan_recovery(detection, accept_hermes_upgrade=True)
    assert approved.executable
    # Even an otherwise unpatched facade participates in the fingerprint.
    facade = hermes / "gateway/run.py"
    facade.write_bytes(facade.read_bytes() + b"\n# concurrent edit\n")
    with pytest.raises(ValueError, match="evidence changed"):
        recovery.execute_recovery(detection, expected_fingerprint=approved.fingerprint,
                                  accept_hermes_upgrade=True)
    fresh = recovery.plan_recovery(detect_hermes(hermes), accept_hermes_upgrade=True)
    recovery.execute_recovery(detect_hermes(hermes), expected_fingerprint=fresh.fingerprint,
                              accept_hermes_upgrade=True)
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "installed"
    decomposed.restore(detect_hermes(hermes))
    assert (hermes / target).read_bytes() == original + b"\n# simulated source upgrade\n"
    assert facade.read_bytes().endswith(b"# concurrent edit\n")


@pytest.mark.parametrize("damage", ["missing_backup", "orphan_backup", "marker_body", "missing_source"])
def test_incomplete_ownership_never_overwrites_sources(hermes, damage):
    detection = detect_hermes(hermes)
    decomposed.install(detection)
    target = hermes / "gateway/run_turn_runner.py"
    if damage == "missing_backup":
        target.with_name(target.name + decomposed.BACKUP_SUFFIX).unlink()
    elif damage == "orphan_backup":
        (hermes / decomposed.MANIFEST_NAME).unlink()
    elif damage == "marker_body":
        target.write_text(target.read_text().replace("_hfc_turn_ctx = ctx", "_hfc_turn_ctx = None"))
    else:
        target.unlink()
    before = sources(hermes)
    assert recovery.plan_recovery(detection).state == "refused"
    with pytest.raises(ValueError):
        decomposed.install(detection)
    with pytest.raises(ValueError):
        decomposed.restore(detection)
    assert sources(hermes) == before


@pytest.mark.asyncio
async def test_missing_base_context_falls_back_and_clears_terminal_stage():
    token = hook_runtime._HFC_EXACT_COMPLETION_STAGE.set({"payload": {}, "task_id": id(asyncio.current_task())})
    adapter = object()
    try:
        result = await hook_runtime.prepare_decomposed_base_final_delivery({
            "delivery_adapter": adapter, "content": "native answer", "reply_to": "fixture-parent", "metadata": {},
        })
        assert result == (adapter, "native answer", "fixture-parent", {})
        assert hook_runtime._HFC_EXACT_COMPLETION_STAGE.get() is None
    finally:
        hook_runtime._HFC_EXACT_COMPLETION_STAGE.reset(token)


def test_install_rechecks_version_gate_and_recovery_binds_version(hermes):
    detection = detect_hermes(hermes)
    before = sources(hermes)
    old_plan = recovery.plan_recovery(detection)
    (hermes / "VERSION").write_text("0.1.0\n")
    assert recovery.plan_recovery(detection).fingerprint != old_plan.fingerprint
    with pytest.raises(ValueError, match="unsupported"):
        decomposed.install(detection)
    assert sources(hermes) == before
    assert not (hermes / decomposed.MANIFEST_NAME).exists()


def _write_legacy_upgrade_evidence(hermes, version=2):
    fixture_root = Path(__file__).parents[1] / "fixtures"
    old_targets = {
        "gateway/run.py": (fixture_root / "hermes_v2026_4_23/gateway/run.py", "run_py", ""),
        "cron/scheduler.py": (fixture_root / "hermes_cron/scheduler.py", "cron_py", "cron_"),
        "gateway/platforms/base.py": (fixture_root / "hermes_exact_base.py", "base_py", "base_"),
    }
    manifest = {"manifest_version": version}
    for target, (fixture, path_key, prefix) in old_targets.items():
        raw = fixture.read_bytes()
        text = raw.decode()
        patched = (patcher.apply_patch(text) if target == "gateway/run.py" else
                   patcher.apply_cron_patch(text) if target.startswith("cron/") else
                   patcher.apply_base_patch(text)).encode()
        (hermes / (target + decomposed.BACKUP_SUFFIX)).write_bytes(raw)
        manifest.update({path_key: target, prefix + "backup": target + decomposed.BACKUP_SUFFIX,
                         prefix + "backup_sha256": sha256(raw).hexdigest(),
                         prefix + "patched_sha256": sha256(patched).hexdigest()})
    (hermes / decomposed.MANIFEST_NAME).write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_updater_layout_migration_requires_acceptance_and_restores_new_source(hermes, version):
    original = sources(hermes)
    _write_legacy_upgrade_evidence(hermes, version)
    detection = detect_hermes(hermes)
    plan = recovery.plan_recovery(detection)
    assert plan.state == "stale_unpatched" and not plan.executable
    report = cli._diagnose_install_state(detection)
    assert "autostash" in report["message"]
    assert "--accept-hermes-upgrade" in report["repair_command"]
    with pytest.raises(ValueError, match="accept-hermes-upgrade"):
        decomposed.install(detection)
    with pytest.raises(ValueError, match="no-repair"):
        decomposed.install(detection, no_repair=True, accept_hermes_upgrade=True)
    assert sources(hermes) == original
    approved = recovery.plan_recovery(detection, accept_hermes_upgrade=True)
    assert approved.executable
    recovery.execute_recovery(detection, expected_fingerprint=approved.fingerprint,
                              accept_hermes_upgrade=True)
    assert json.loads((hermes / decomposed.MANIFEST_NAME).read_text())["manifest_version"] == 4
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "installed"
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == original


@pytest.mark.parametrize("damage", ["backup_hash", "backup_path", "patched_user_edit", "orphan_backup"])
def test_legacy_layout_migration_rejects_unverified_ownership(hermes, damage):
    manifest = _write_legacy_upgrade_evidence(hermes)
    if damage == "backup_hash":
        manifest["backup_sha256"] = "0" * 64
    elif damage == "backup_path":
        manifest["backup"] = "../run.py.hermes_feishu_card.bak"
    elif damage == "patched_user_edit":
        target = hermes / "gateway/run.py"
        target.write_bytes(target.read_bytes() + b"\n# HERMES_FEISHU_CARD_USER_EDIT\n")
    else:
        (hermes / ("gateway/run_busy.py" + decomposed.BACKUP_SUFFIX)).write_bytes(b"unowned\n")
    (hermes / decomposed.MANIFEST_NAME).write_text(json.dumps(manifest))
    before = sources(hermes)
    assert recovery.plan_recovery(detect_hermes(hermes), accept_hermes_upgrade=True).state == "refused"
    with pytest.raises(ValueError):
        decomposed.install(detect_hermes(hermes), accept_hermes_upgrade=True)
    assert sources(hermes) == before


def test_owned_restore_does_not_depend_on_current_renderer(hermes, monkeypatch):
    before = sources(hermes)
    decomposed.install(detect_hermes(hermes))
    monkeypatch.setattr(decomposed_ownership, "render", lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("future renderer")))
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == before


def test_renderer_upgrade_refreshes_manifest_without_losing_originals(hermes):
    before = sources(hermes)
    decomposed.install(detect_hermes(hermes))
    # PR #257's original approval block lacked this session binding. Its old
    # marker form is understood by the remover, but its full-file hash differs
    # from the current renderer. Recreate that real historical owned state.
    target = "gateway/run_turn_runner.py"
    path = hermes / target
    raw = path.read_bytes()
    previous = b"".join(line for line in raw.splitlines(keepends=True)
                        if not line.strip().startswith(b"_approval_session_key = ctx.session_key"))
    assert previous != raw
    path.write_bytes(previous)
    manifest_path = hermes / decomposed.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["targets"][target]["patched_sha256"] = sha256(previous).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "owned_incomplete"
    assert decomposed.install(detect_hermes(hermes))
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "installed"
    assert not decomposed.install(detect_hermes(hermes))
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == before


def test_explicit_install_uses_verified_portable_writer_without_dirfd(hermes, monkeypatch):
    monkeypatch.setattr(recovery, "_secure_dirfd_transactions_supported", lambda: False)
    assert decomposed.install(detect_hermes(hermes))
    assert recovery.plan_recovery(detect_hermes(hermes)).state == "installed"
    assert not decomposed.install(detect_hermes(hermes))
    with pytest.raises(ValueError, match="directory-relative"):
        decomposed.restore(detect_hermes(hermes))


def test_old_owned_template_is_detectable_and_repairable(hermes):
    before = sources(hermes)
    decomposed.install(detect_hermes(hermes))
    target = "gateway/run_turn.py"
    path = hermes / target
    # A previous owned hook body need not be recognized by today's remover.
    raw = path.read_bytes().replace(b"_hfc_exact_staged = False", b"_hfc_exact_staged = bool(0)")
    path.write_bytes(raw)
    manifest_path = hermes / decomposed.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["targets"][target]["patched_sha256"] = sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="corrupt"):
        patcher.remove_patch(raw.decode())
    detection = detect_hermes(hermes)
    assert detection.supported
    assert recovery.plan_recovery(detection).state == "owned_incomplete"
    assert decomposed.install(detection)
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == before


def test_no_git_install_has_verified_snapshot_without_upgrade_authority(hermes):
    from hermes_feishu_card.install.integrity import plan_integrity_repair
    assert not (hermes / ".git").exists()
    decomposed.install(detect_hermes(hermes))
    manifest = json.loads((hermes / decomposed.MANIFEST_NAME).read_text())
    assert manifest["integrity"]["kind"] == "verified_owned_snapshot"
    assert "git_head" not in manifest["integrity"]
    assert set(manifest["integrity"]["targets"]) == set(sources(hermes))
    plan = plan_integrity_repair(detect_hermes(hermes))
    assert plan.reason == "recovery_not_required" and not plan.executable
    target = hermes / "gateway/run_turn.py"
    backup = target.with_name(target.name + decomposed.BACKUP_SUFFIX)
    target.write_bytes(backup.read_bytes() + b"\n# upstream update\n")
    plan = plan_integrity_repair(detect_hermes(hermes))
    assert not plan.executable
    assert plan.reason == "decomposed_upgrade_requires_explicit_install"


def test_decomposed_migration_all_targets_permits_fence_acknowledgement(hermes):
    from hermes_feishu_card.install.integrity import (
        migrate_integrity_manifest, plan_integrity_repair, integrity_acknowledgement_eligible,
    )
    decomposed.install(detect_hermes(hermes))
    installed = sources(hermes)
    manifest_path = hermes / decomposed.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    assert manifest["targets"]["gateway/run.py"]["original_sha256"] == manifest["targets"]["gateway/run.py"]["patched_sha256"]
    manifest.pop("integrity")
    manifest_path.write_text(json.dumps(manifest))
    detection = detect_hermes(hermes)
    assert plan_integrity_repair(detection).reason == "integrity_migration_required"
    evidence = migrate_integrity_manifest(detection)
    assert set(evidence["targets"]) == set(installed)
    assert sources(hermes) == installed
    plan = plan_integrity_repair(detection)
    assert integrity_acknowledgement_eligible(detection, recovery.plan_recovery(detection), plan)


@pytest.mark.parametrize("kind", ["source", "backup", "missing", "symlink"])
def test_decomposed_migration_refuses_sibling_drift(hermes, kind):
    from hermes_feishu_card.install.integrity import migrate_integrity_manifest, IntegrityRepairRefused
    decomposed.install(detect_hermes(hermes))
    target = hermes / "gateway/run_turn.py"
    if kind == "backup":
        target = target.with_name(target.name + decomposed.BACKUP_SUFFIX)
    if kind == "missing":
        target.unlink()
    elif kind == "symlink":
        target.unlink()
        target.symlink_to(hermes / "gateway/run.py")
    else:
        target.write_bytes(target.read_bytes() + b"\n# user edit\n")
    manifest = (hermes / decomposed.MANIFEST_NAME).read_bytes()
    with pytest.raises(IntegrityRepairRefused):
        migrate_integrity_manifest(detect_hermes(hermes))
    assert (hermes / decomposed.MANIFEST_NAME).read_bytes() == manifest


def test_decomposed_migration_rechecks_sibling_before_manifest_commit(hermes, monkeypatch):
    from hermes_feishu_card.install import integrity
    decomposed.install(detect_hermes(hermes))
    manifest_path = hermes / decomposed.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("integrity")
    manifest_path.write_text(json.dumps(manifest))
    before = manifest_path.read_bytes()
    stage = integrity._stage_text
    changed = False

    def edit_sibling_after_render(*args, **kwargs):
        nonlocal changed
        result = stage(*args, **kwargs)
        if not changed:
            changed = True
            target = hermes / "gateway/run_turn.py"
            target.write_bytes(target.read_bytes() + b"\n# concurrent user edit\n")
        return result

    monkeypatch.setattr(integrity, "_stage_text", edit_sibling_after_render)
    with pytest.raises(integrity.IntegrityRepairRefused):
        integrity.migrate_integrity_manifest(detect_hermes(hermes))
    assert manifest_path.read_bytes() == before
    assert (hermes / "gateway/run_turn.py").read_bytes().endswith(b"# concurrent user edit\n")


def extract_persistence(hermes):
    """The exact upstream extraction: inherited persistence, handler stays put."""
    path = hermes / 'gateway/run_turn.py'
    text = path.read_text()
    start = text.index('    async def _hmwa_post_turn_hooks')
    end = text.index('    async def _run_agent', start)
    methods = text[start:end]
    path.write_text('from gateway.run_turn_persistence import GatewayTurnPersistenceMixin\n\n'
                    + text[:start].replace('class GatewayTurnMixin:',
                        'class GatewayTurnMixin(GatewayTurnPersistenceMixin):') + text[end:])
    (hermes / 'gateway/run_turn_persistence.py').write_text(
        'class GatewayTurnPersistenceMixin:\n' + methods)
    return hermes


@pytest.fixture
def persistence_hermes(hermes):
    return extract_persistence(hermes)


def test_inherited_persistence_install_restore(persistence_hermes):
    root = persistence_hermes
    detection = detect_hermes(root)
    assert detection.supported, detection.reason
    assert detection.capability_locations['completion_return'] == ('gateway/run_turn_persistence.py',)
    before = sources(root)
    decomposed.install(detection)
    installed = sources(root)
    assert patcher.TURN_TIMING_PATCH_BEGIN.encode() in installed['gateway/run_turn.py']
    assert patcher.COMPLETE_PATCH_BEGIN.encode() in installed['gateway/run_turn_persistence.py']
    decomposed.install(detect_hermes(root))
    assert sources(root) == installed
    decomposed.restore(detect_hermes(root))
    assert sources(root) == before


@pytest.mark.parametrize('target,old,new', [
    ('gateway/run_turn.py', 'GatewayTurnMixin(GatewayTurnPersistenceMixin)', 'GatewayTurnMixin(object)'),
    ('gateway/run_turn.py', 'from gateway.run_turn_persistence import', 'from gateway.other import'),
    ('gateway/run_turn.py', 'await self._hmwa_post_turn_hooks(hook_ctx, agent_result, response)',
        'await self._hmwa_post_turn_hooks(hook_ctx, agent_result, "wrong")'),
    ('gateway/run_turn_persistence.py', '"agent:end"', '"agent:other"'),
    ('gateway/run_turn_persistence.py', 'self, event, source, session_entry', 'self, event, wrong, session_entry'),
])
def test_inherited_persistence_drift_refused(persistence_hermes, target, old, new):
    path = persistence_hermes / target
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
    assert not detect_hermes(persistence_hermes).supported


@pytest.mark.parametrize('extra', [
    'GatewayTurnPersistenceMixin = object\n',
    'from other import GatewayTurnPersistenceMixin\n',
    'GatewayTurnPersistenceMixin._hmwa_deliver_turn_response = other\n',
])
def test_inherited_persistence_rebinding_refused(persistence_hermes, extra):
    path = persistence_hermes / 'gateway/run_turn.py'
    path.write_text(path.read_text() + '\n' + extra)
    assert not detect_hermes(persistence_hermes).supported


def test_inherited_persistence_earlier_mro_shadow_refused(persistence_hermes):
    root = persistence_hermes
    path = root / 'gateway/run_turn.py'
    path.write_text('from gateway.run_turn_prepare import GatewayTurnPrepareMixin\n'
                    'from gateway.run_turn_hygiene import GatewayTurnHygieneMixin\n'
                    + path.read_text().replace('GatewayTurnMixin(GatewayTurnPersistenceMixin)',
                      'GatewayTurnMixin(GatewayTurnPrepareMixin, GatewayTurnHygieneMixin, GatewayTurnPersistenceMixin)'))
    prepare = root / 'gateway/run_turn_prepare.py'
    prepare.write_text('class GatewayTurnPrepareMixin:\n    pass\n')
    (root / 'gateway/run_turn_hygiene.py').write_text('class GatewayTurnHygieneMixin:\n    pass\n')
    assert detect_hermes(root).supported
    prepare.write_text('class GatewayTurnPrepareMixin:\n    async def _hmwa_deliver_turn_response(self):\n        pass\n')
    assert not detect_hermes(root).supported


def test_v4_owned_target_extension_requires_explicit_upgrade_and_restores(hermes):
    decomposed.install(detect_hermes(hermes))
    target = hermes / 'gateway/run_turn.py'
    target.write_text(patcher.remove_patch(target.read_text()))
    extract_persistence(hermes)
    expected = {name: decomposed_ownership._remove_target(name, raw) for name, raw in sources(hermes).items()}
    assert recovery.plan_recovery(detect_hermes(hermes)).state == 'stale_unpatched'
    with pytest.raises(ValueError, match='accept-hermes-upgrade'):
        decomposed.install(detect_hermes(hermes))
    decomposed.install(detect_hermes(hermes), accept_hermes_upgrade=True)
    assert recovery.plan_recovery(detect_hermes(hermes)).state == 'installed'
    decomposed.restore(detect_hermes(hermes))
    assert sources(hermes) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', [True, False, 'error'])
async def test_inherited_completion_executes_after_post_turn_with_original_timing(persistence_hermes, monkeypatch, accepted):
    import ast
    events = []
    async def post_turn(*args):
        events.append('agent:end')
    async def completed(payload, *, event_name):
        events.append((event_name, payload['answer'], payload['duration']))
        if accepted == 'error':
            raise OSError('fixture sidecar unavailable')
        return accepted
    monkeypatch.setattr(hook_runtime, 'emit_from_hermes_locals', lambda *a, **k: False)
    monkeypatch.setattr(hook_runtime, 'can_stage_exact_base_completion', lambda payload: False)
    monkeypatch.setattr(hook_runtime, 'build_event', lambda *a, **k: {'data': {}})
    monkeypatch.setattr(hook_runtime, 'emit_from_hermes_locals_async', completed)
    original = sources(persistence_hermes)
    rendered = decomposed_ownership.render(original)
    namespace = {}
    exec(rendered['gateway/run_turn_persistence.py'], namespace)
    tree = ast.parse(rendered['gateway/run_turn.py'])
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
    exec(compile(tree, 'inherited-handler', 'exec'), namespace)
    runner = namespace['GatewayTurnMixin']()
    runner.hooks = SimpleNamespace(emit=post_turn)
    source = SimpleNamespace(platform='feishu')
    event = SimpleNamespace(source=source, message_id='fixture-message', reply_to_message_id='fixture-parent')
    assert await runner._handle_message_with_agent(event, source, 'fixture-session', 1) == (None if accepted is True else 'answer')
    assert events == ['agent:end', ('message.completed', 'answer', 1.25)]
