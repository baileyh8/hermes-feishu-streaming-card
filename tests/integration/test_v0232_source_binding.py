"""Hermes v0.23.1 / v0.23.2 source-binding and approval notify contracts (#532).

Exact upstream checkouts may be supplied with HFC_UPSTREAM_0231_ROOT and
HFC_UPSTREAM_0232_ROOT. Negative controls use a private copy of the relevant
sources and never mutate the pinned checkout.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.patcher import _approval_callback_name, apply_gateway_fragment
from hermes_feishu_card.install.source_layout import read_peer_contract_sources

ENV_0231 = "HFC_UPSTREAM_0231_ROOT"
ENV_0232 = "HFC_UPSTREAM_0232_ROOT"
REQUIRE_ENV = "HFC_REQUIRE_0232_PIN"
COMMIT_0231 = "3b421578588243f920886e6fe79a6faa87ea026e"
COMMIT_0232 = "ae2a81ccc00e9b7c06301523e5bb09608397149c"
ANCHORS = (
    "message_handler",
    "tool_callback",
    "answer_delta_callback",
    "thinking_delta_callback",
    "clarify_callback",
    "approval_callback",
)
FILE_HASHES = {
    "v0.23.1": {
        "gateway/run_turn.py": "32de792e49dbe085fca9ce17cf75e522a7ef67dcc375bd465e274127f089e333",
        "gateway/run_turn_runner.py": "76a97f48cc5f769c84991644957e0e9d64c4be60dbd0774ec7457e740da253fb",
    },
    "v0.23.2": {
        "gateway/run_turn.py": "834c924b1b5b83816ba228800f4b2daff39d10e9ef046ba32f28f2133752284b",
        "gateway/run_turn_runner.py": "c7d0ffc2bc87d128a854b29844397006bf1fef990454fae7c325de22c9e4b1b6",
    },
}


def _pinned_root(env, commit, digest_key):
    configured = os.environ.get(env)
    if not configured:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"required {digest_key} pinned source missing")
        pytest.skip(f"{digest_key} pinned source not supplied")
    root = Path(configured)
    found = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    assert found == commit, (found, commit)
    assert subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True) == ""
    for name, digest in FILE_HASHES[digest_key].items():
        path = root / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    return root


def test_v0231_tag_stays_full_after_v0232_contract():
    root = _pinned_root(ENV_0231, COMMIT_0231, "v0.23.1")
    detection = detect_hermes(root)
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.reason == "supported"
    for name in ANCHORS:
        assert detection.capabilities[name] is True, name


def test_v0232_tag_finds_documented_anchors_and_is_not_unsupported():
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    detection = detect_hermes(root)
    assert "stale result pinned source binding changed" not in detection.reason
    assert "extracted approval generation/notify contract changed" not in detection.reason
    assert "missing decomposed anchors:" not in detection.reason
    for name in ANCHORS:
        assert detection.capabilities[name] is True, name
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.reason == "supported"
    assert detection.capabilities["admission_completion"] is True
    assert detection.capabilities["exact_base_delivery"] is True


def test_v0232_legacy_observer_preserves_auxiliary_request_boundary(tmp_path):
    from hermes_feishu_card.install import legacy_observer

    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    assert legacy_observer.available(root)
    name = "agent/turn_response_intake.py"
    target = tmp_path / name
    target.parent.mkdir()
    source = (root / name).read_bytes()
    needle = b'getattr(agent, "_loop_aux_task", None)'
    assert needle in source
    target.write_bytes(source.replace(needle, b"False", 1))
    assert not legacy_observer.available(tmp_path)


def test_v0232_runner_and_handler_apply_with_peer_contracts():
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    peers = read_peer_contract_sources(root)
    sources = {
        "gateway/run_turn.py": (root / "gateway/run_turn.py").read_text(encoding="utf-8"),
        "gateway/run_turn_runner.py": (root / "gateway/run_turn_runner.py").read_text(encoding="utf-8"),
    }
    patched_turn = apply_gateway_fragment(
        sources["gateway/run_turn.py"], "gateway/run_turn.py",
        completion_sources=sources, peer_sources=peers,
    )
    patched_runner = apply_gateway_fragment(
        sources["gateway/run_turn_runner.py"], "gateway/run_turn_runner.py",
        completion_sources=sources, peer_sources=peers,
    )
    assert "HERMES_FEISHU_CARD_" in patched_turn
    assert "HERMES_FEISHU_CARD_APPROVAL_PATCH_BEGIN" in patched_runner
    assert _approval_callback_name(sources["gateway/run_turn_runner.py"], peer_sources=peers) == "_render_approval_sync"


@pytest.mark.parametrize("mutation", ["foreign-source", "foreign-transport"])
def test_v0232_private_copy_rejects_binding_drift(mutation, tmp_path):
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "mutated"
    # Copy only the files detect reads plus peers, not the full checkout.
    from hermes_feishu_card.install.source_layout import DECOMPOSED_SOURCE_TARGETS, DECOMPOSED_PEER_SOURCES
    names = (
        *DECOMPOSED_SOURCE_TARGETS,
        *DECOMPOSED_PEER_SOURCES,
        "VERSION",
        "hermes_cli/__init__.py",
        "gateway/session_ingress.py",
        "gateway/session_authority.py",
    )
    for name in names:
        src = root / name
        if src.is_file():
            dest = copy / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    if mutation == "foreign-source":
        path = copy / "gateway/run_turn.py"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(
            "_turn_channel_prompt, _turn_source = channel",
            "_turn_channel_prompt, _turn_source = other_channel",
        ), encoding="utf-8")
    else:
        path = copy / "gateway/run_turn_runner.py"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(
            "register_gateway_notify(session_key, approval_transport(self))",
            "register_gateway_notify(session_key, approval_transport(other))",
        ), encoding="utf-8")
    detection = detect_hermes(copy)
    assert detection.supported is False
    if mutation == "foreign-source":
        assert detection.compatibility == "unsupported"
        assert "stale result pinned source binding changed" in detection.reason
        assert detection.capabilities["message_handler"] is False
    else:
        # Core message/completion anchors can remain; only the approval contract fails.
        assert detection.compatibility != "full"
        assert "extracted approval generation/notify contract changed" in detection.reason
        assert detection.capabilities["approval_callback"] is False


def _copy_detect_tree(root: Path, dest: Path) -> None:
    from hermes_feishu_card.admission_topology_0226 import SOURCE_HASHES_0232
    from hermes_feishu_card.install.source_layout import (
        DECOMPOSED_PEER_SOURCES,
        DECOMPOSED_SOURCE_TARGETS,
    )
    names = {
        *DECOMPOSED_SOURCE_TARGETS,
        *DECOMPOSED_PEER_SOURCES,
        "VERSION",
        "hermes_cli/__init__.py",
        *(name.replace(".", "/") + ".py" for name in SOURCE_HASHES_0232),
    }
    for name in names:
        src = root / name
        if src.is_file():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def test_v0232_private_copy_stays_full(tmp_path):
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "clean"
    _copy_detect_tree(root, copy)
    detection = detect_hermes(copy)
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason


@pytest.mark.parametrize(
    "mutation,target,old,new",
    [
        (
            "foreign-ledger-session",
            "gateway/platforms/base_delivery_ledger.py",
            "compute_obligation_id(\n            session_key, str(_ledger_id or \"\"), text_content)",
            "compute_obligation_id(\n            other_session, str(_ledger_id or \"\"), text_content)",
        ),
        (
            "unconditional-stop-cancel",
            "gateway/platforms/base_session_command.py",
            "if adapter._authorization_check is None or getattr(event, \"_interrupt_accepted\", False) is True:",
            "if True:",
        ),
        (
            "footer-return-other-adapter",
            "gateway/platforms/base.py",
            "return footer_onto_photo_caption(self, _ExtractedResponse(text_content=text_content, images=images, media_files=media_files,",
            "return footer_onto_photo_caption(other, _ExtractedResponse(text_content=text_content, images=images, media_files=media_files,",
        ),
    ],
)
def test_v0232_private_copy_rejects_delivery_and_stop_drift(mutation, target, old, new, tmp_path):
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / mutation
    _copy_detect_tree(root, copy)
    path = copy / target
    text = path.read_text(encoding="utf-8")
    assert old in text, mutation
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    detection = detect_hermes(copy)
    assert detection.supported is False
    assert detection.compatibility != "full", detection.reason
    assert detection.capabilities["exact_base_delivery"] is False


def test_v0232_missing_ledger_peer_is_rejected(tmp_path):
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "no-ledger"
    _copy_detect_tree(root, copy)
    (copy / "gateway/platforms/base_delivery_ledger.py").unlink()
    detection = detect_hermes(copy)
    assert detection.supported is False
    assert detection.capabilities["exact_base_delivery"] is False


def test_v0232_admission_rejects_per_file_hash_drift(tmp_path):
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "ingress-drift"
    _copy_detect_tree(root, copy)
    path = copy / "gateway/session_ingress.py"
    path.write_bytes(path.read_bytes() + b"\n")
    detection = detect_hermes(copy)
    assert detection.capabilities["admission_completion"] is False
    assert detection.compatibility == "partial", detection.reason
    assert "admission completion unavailable: unsupported-source" in detection.reason



def test_v0232_missing_dispatch_peer_is_rejected(tmp_path):
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "no-dispatch"
    _copy_detect_tree(root, copy)
    (copy / "gateway/platforms/base_session_command.py").unlink()
    detection = detect_hermes(copy)
    assert detection.supported is False
    assert detection.capabilities["exact_base_delivery"] is False


def test_v0232_install_plan_refuses_peer_drift_before_writing(tmp_path):
    from hermes_feishu_card.install import recovery

    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "peer-drift"
    _copy_detect_tree(root, copy)
    detection = detect_hermes(copy)
    plan = recovery.plan_recovery(detection)
    assert plan.state == "clean"
    peer = copy / "gateway/run_turn_setup_guard.py"
    peer.write_bytes(peer.read_bytes() + b"\n# changed since diagnosis\n")
    with pytest.raises(ValueError, match="recovery evidence changed"):
        recovery.install_decomposed(detection, expected_fingerprint=plan.fingerprint)
    assert not (copy / ".hermes_feishu_card_manifest").exists()
    assert not list(copy.rglob("*.hermes_feishu_card.bak"))


def test_v0232_loaded_admission_verifies_native_contextmanager(tmp_path, monkeypatch):
    from hermes_feishu_card import admission_completion, admission_topology_0226 as topology
    from hermes_feishu_card.install import decomposed_ownership
    from hermes_feishu_card.install.source_layout import DECOMPOSED_SOURCE_TARGETS
    from test_v0230_admission_sources import _build_modules

    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "installed"
    _copy_detect_tree(root, copy)
    originals = {name: (copy / name).read_bytes() for name in DECOMPOSED_SOURCE_TARGETS}
    for name, rendered in decomposed_ownership.render(originals, peer_sources=read_peer_contract_sources(copy)).items():
        (copy / name).write_bytes(rendered)
    pair = topology.source_bytes_0232(copy)
    assert pair is not None
    modules = _build_modules(
        copy, pair[1], functions=topology.FUNCTIONS_0232,
        decorated=topology.DECORATED_0232,
        sync_decorated=topology.SYNC_DECORATED_0231 | {("agent.runtime_scope", "bind_turn")},
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    assert admission_completion._verified() is not None
    runtime_scope = modules["agent.runtime_scope"]
    runtime_scope.bind_turn = runtime_scope.bind_turn.__wrapped__
    assert admission_completion._verified() is None


def test_v0232_base_patch_roundtrip_requires_peers_and_restores(tmp_path):
    from hermes_feishu_card.install import decomposed_ownership, patcher, recovery

    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    copy = tmp_path / "roundtrip"
    _copy_detect_tree(root, copy)
    peers = read_peer_contract_sources(copy)
    original = (copy / "gateway/platforms/base.py").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        patcher.apply_base_patch(original)
    with pytest.raises(ValueError):
        patcher.remove_base_patch_lenient(
            patcher.apply_base_patch(original, peer_sources=peers)
        )
    patched = patcher.apply_base_patch(original, peer_sources=peers)
    assert patched != original
    assert patcher.apply_base_patch(patched, peer_sources=peers) == patched
    assert patcher.remove_base_patch(patched, peer_sources=peers) == original
    assert patcher.remove_base_patch_lenient(patched, peer_sources=peers) == original
    compile(patched, "base.py", "exec")

    from hermes_feishu_card.install.source_layout import DECOMPOSED_SOURCE_TARGETS
    originals = {
        name: (copy / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (copy / name).is_file()
    }
    rendered = decomposed_ownership.render(originals, peer_sources=peers)
    assert rendered["gateway/platforms/base.py"].decode("utf-8") == patched
    detection = detect_hermes(copy)
    assert detection.supported is True
    assert recovery._validate_base_reapplication(original, peer_sources=peers) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_fails", [False, True])
async def test_v0232_live_ledger_callback_keeps_original_closure_and_order(
    monkeypatch, hook_fails,
):
    from logging import getLogger
    from types import FunctionType, ModuleType, SimpleNamespace

    from hermes_feishu_card import hook_runtime
    from hermes_feishu_card.admission_completion import _compiled_code
    from hermes_feishu_card.install.patcher import apply_base_patch

    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    base_path = root / "gateway/platforms/base.py"
    compiled = compile(
        apply_base_patch(base_path.read_text(), peer_sources=read_peer_contract_sources(root)),
        str(base_path), "exec",
    )
    peer = ModuleType("gateway.platforms.base_delivery_ledger")
    peer_path = root / "gateway/platforms/base_delivery_ledger.py"
    exec(compile(peer_path.read_bytes(), str(peer_path), "exec"), vars(peer))
    monkeypatch.setitem(sys.modules, peer.__name__, peer)
    base = ModuleType("gateway.platforms.base")
    monkeypatch.setitem(sys.modules, base.__name__, base)
    trace = []
    base.ledger_bracket = peer.ledger_bracket
    base.logger = getLogger(__name__)
    base.stop_reply_clock = lambda *args: None

    class BasePlatformAdapter:
        name = "test"

        def _final_delivery_adapter(self, source):
            return self

        async def _record_delivery_obligation(self, event, session_key, content, adapter, ephemeral):
            trace.append(("record", content))
            return "obligation"

        async def _release_turn_marker(self, event):
            trace.append(("release",))

        async def _send_with_retry(self, **kwargs):
            trace.append(("send", kwargs["content"], kwargs["reply_to"], kwargs["metadata"]))
            return "result"

        async def _finalize_delivery_obligation(self, obligation_id, result, event, adapter):
            trace.append(("finalize", obligation_id, result))

    base.BasePlatformAdapter = BasePlatformAdapter
    for name in ("_process_message_background", "_send_final_text", "send_final_ledgered"):
        code = _compiled_code(compiled, name, "BasePlatformAdapter")
        setattr(BasePlatformAdapter, name, FunctionType(code, vars(base), name))
    assert hook_runtime._exact_base_delivery_hook_available()

    async def prepare(values):
        trace.append(("hook", values["content"], values["obligation_id"]))
        if hook_fails:
            raise ValueError("sidecar unavailable")
        return values["delivery_adapter"], "handoff", "new-reply", {"new": True}

    monkeypatch.setattr(hook_runtime, "prepare_decomposed_base_final_delivery", prepare)
    adapter = BasePlatformAdapter()
    event = SimpleNamespace(source=SimpleNamespace(chat_id="test-chat"))
    assert await adapter.send_final_ledgered(
        event, "session", "answer", {}, reply_to="reply", is_ephemeral_response=False,
    ) == ("result", adapter)
    send = ("answer", "reply", {}) if hook_fails else ("handoff", "new-reply", {"new": True})
    assert trace == [
        ("record", "answer"), ("release",), ("hook", "answer", "obligation"),
        ("send", *send), ("finalize", "obligation", "result"),
    ]
    base.ledger_bracket = object()
    assert not hook_runtime._exact_base_delivery_hook_available()
