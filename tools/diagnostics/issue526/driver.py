#!/usr/bin/env python3
"""stage2 #526: full real Gateway path DM rig (two DM turns).

Real: GatewayRunner + initialize_gateway_runtime SessionAuthority + FeishuAdapter
      inbound handler _handle_message_event_data + patched managed gateway code.
Local stubs (labelled): model HTTP stub (openai-compat), sidecar HTTP stub
      (/events + /delivery/policy). Transport injection point =
      FeishuAdapter._handle_message_event_data, the shared entry of BOTH websocket
      dispatcher and webhook route; no Feishu cloud API is contacted.
Observation only: hook_runtime emit wrappers record locals keys/identity/claim and
      pass through to the real function. No gate is relaxed.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(os.environ["HFC526_SOURCE_OR_EMPTY"])
MANAGED = Path(os.environ["HFC526_MANAGED_ROOT"])
TESTLIBS = Path(os.environ["HFC526_EMPTY_TESTLIBS"])
HERE = Path(os.environ["HFC526_OUTPUT_ROOT"])
CHILD = HERE / "child"
HOME = CHILD / "home"

RECEIPT: dict = {"stages": [], "hook_calls": [], "policy_queries": [], "events": [],
                 "model_requests": [], "errors": []}


def stage(name, ok, detail=""):
    RECEIPT["stages"].append({"stage": name, "ok": bool(ok), "detail": str(detail)[:500],
                              "t": datetime.now().isoformat(timespec="seconds")})
    marker = "OK" if ok else "FAIL"
    print(f"[stage] {name}: {marker} {detail}", flush=True)

class ModelStub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        RECEIPT["model_requests"].append({"path": self.path, "len": len(body),
                                          "head": body[:200]})
        try:
            streamed = bool(json.loads(body or "{}").get("stream"))
        except Exception:
            streamed = False
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        if streamed:
            chunks = [
                {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": "fixture reply"},
                              "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": usage},
            ]
            data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
            raw = data.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            payload = {
                "id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture reply"},
                             "finish_reason": "stop"}],
                "usage": usage,
            }
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

class SidecarStub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"_raw": body[:200]}
        layer = "policy-query" if self.path.endswith("/delivery/policy") else ("event-post" if self.path.endswith("/events") else "other-post")
        sig_headers = {k: v for k, v in self.headers.items() if k.lower().startswith("x-hfc-")}
        print("[stub " + layer + "] path=" + self.path + " sig_keys=" + str(sorted(sig_headers.keys())), flush=True)
        if self.path.endswith("/delivery/policy"):
            RECEIPT["policy_queries"].append({"layer": "policy-query", "body": parsed, "sig": bool(sig_headers)})
            self._reply({"ok": True, "disposition": "card", "ttl_ms": 500})
        elif self.path.endswith("/events"):
            RECEIPT["events"].append({"layer": "event-post", "sig": bool(sig_headers), "event": parsed.get("event"), "message_id": parsed.get("message_id"),
                                      "chat_id": parsed.get("chat_id"), "sequence": parsed.get("sequence"),
                                      "created_at": parsed.get("created_at"),
                                      "data_keys": sorted(parsed.get("data", {}).keys()) if isinstance(parsed.get("data"), dict) else None})
            self._reply({"ok": True, "applied": True})
        elif "native-handoff" in self.path:
            self._reply({"status": "unknown"})
        else:
            RECEIPT["events"].append({"path": self.path, "unrouted": True})
            self._reply({"ok": True, "applied": True})

    def do_GET(self):
        if "native-handoff" in self.path:
            self._reply({"status": "unknown"})
        else:
            self._reply({"ok": True})

def start_stub(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def install_socket_guard():
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def guarded(sock, address):
        if isinstance(address, tuple) and str(address[0]) not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("stage2 rig: external network blocked (socket.connect)")
        return original(sock, address)

    def guarded_ex(sock, address):
        if isinstance(address, tuple) and str(address[0]) not in ("127.0.0.1", "::1", "localhost"):
            return errno.ENETUNREACH
        return original_ex(sock, address)

    socket.socket.connect = guarded
    socket.socket.connect_ex = guarded_ex


def env_proof():
    proxy = {k: v for k, v in os.environ.items()
             if k.lower().endswith("_proxy") or k.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    credish = sorted(k for k in os.environ
                     if any(tag in k.upper() for tag in ("KEY", "TOKEN", "SECRET", "CREDENTIAL", "PASSWORD")))
    return {
        "proxy_vars": proxy,
        "credential_like_vars": [k + "=<redacted>" for k in credish],
        "no_proxy": os.environ.get("NO_PROXY"),
        "hermes_home": os.environ.get("HERMES_HOME"),
        "home": os.environ.get("HOME"),
    }


async def wait_until(predicate, timeout, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def make_feishu_event(message_id: str, text: str):
    """lark-shaped SDK object, mirroring upstream tests/gateway/feishu_helpers.py."""
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(message_id=message_id, chat_type="p2p", chat_id="oc_fixture_dm_1",
                                    mentions=None, content=json.dumps({"text": text}), message_type="text"),
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="ou_human", user_id=None, union_id=None),
            ),
        ),
    )

def observe_locals(local_vars, event_name, via):
    snap = {
        "via": via,
        "event_name": event_name,
        "locals_keys": sorted(local_vars.keys()),
        "t": datetime.now().isoformat(timespec="milliseconds"),
    }
    try:
        from hermes_feishu_card import admission_completion as ac
        snap["admission_mode"] = ac.mode(local_vars)
    except Exception as exc:
        snap["admission_mode_error"] = repr(exc)[:120]
    try:
        event = local_vars.get("event")
        runner = local_vars.get("self")
        source = local_vars.get("source")
        claim = getattr(event, "_claimed_owner", None)
        identity = {
            "claim": repr(claim),
            "event_type": type(event).__name__,
            "runner_type": type(runner).__name__ if runner is not None else None,
            "source_platform": str(getattr(getattr(source, "platform", None), "value", None)),
            "source_message_id": getattr(source, "message_id", None),
            "event_message_id": getattr(event, "message_id", None),
        }
        try:
            from gateway.session_authorities import active_authority
            active = active_authority(runner) if runner is not None else None
            identity["active_authority_profile"] = getattr(active, "profile_id", None)
            identity["active_authority_db_path"] = str(getattr(getattr(active, "db", None), "db_path", None))
            identity["owner_session_id"] = claim[0] if isinstance(claim, tuple) else None
            identity["owner_generation"] = claim[1] if isinstance(claim, tuple) and len(claim) > 1 else None
            if active is not None and isinstance(claim, tuple):
                live = active.sessions.get(claim[0])
                identity["live_entry_present"] = live is not None
                identity["live_task_running"] = bool(live is not None and live.task is not None and not live.task.done())
                identity["executing_generation"] = active.executing.get(claim[0])
                identity["authority_runner_is_runner"] = active.runner is runner
        except Exception as exc:
            identity["authority_error"] = repr(exc)[:160]
        snap["identity"] = identity
    except Exception as exc:
        snap["identity_error"] = repr(exc)[:200]
    snap["run_generation"] = local_vars.get("run_generation")
    snap["quick_key"] = local_vars.get("_quick_key")
    snap["message_text"] = str(local_vars.get("message_text") or local_vars.get("message") or "")[:80]
    return snap


def wrap_emit(hook_runtime):
    """Record-then-passthrough wrappers over the real emit functions."""
    targets = [
        ("emit_from_hermes_locals", "sync"),
        ("emit_from_hermes_locals_async", "async"),
        ("emit_from_hermes_locals_threadsafe", "threadsafe"),
    ]
    for attr, kind in targets:
        original = getattr(hook_runtime, attr)

        if kind == "async":
            async def observed_async(local_vars, event_name="message.started", require_ack=False,
                                     _orig=original, _attr=attr):
                snap = observe_locals(local_vars, event_name, _attr)
                result = await _orig(local_vars, event_name, require_ack=require_ack)
                snap["result"] = bool(result)
                RECEIPT["hook_calls"].append(snap)
                return result
            setattr(hook_runtime, attr, observed_async)
        else:
            def observed(local_vars, event_name="message.started", _orig=original, _attr=attr):
                snap = observe_locals(local_vars, event_name, _attr)
                result = _orig(local_vars, event_name)
                snap["result"] = bool(result)
                RECEIPT["hook_calls"].append(snap)
                return result
            setattr(hook_runtime, attr, observed)

# ---- stage2 instrumentation (appended): inbound layer trace ----
def _install_layer_trace(adapter, runner):
    _orig_dup = adapter._is_duplicate
    async def dup_receipt(message_id):
        result = await _orig_dup(message_id)
        RECEIPT.setdefault("dedup_receipts", []).append({"message_id": message_id, "duplicate": bool(result)})
        log.info("DEDUP %s duplicate=%s", message_id, result)
        return result
    adapter._is_duplicate = dup_receipt
    import logging
    import functools
    log = logging.getLogger("stage2.trace")

    def snap(name, fn_name, fn):
        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            log.info("ENTER %s.%s args=%d", name, fn_name, len(args))
            try:
                result = await fn(*args, **kwargs)
                log.info("LAYER %s.%s -> %r", name, fn_name, str(result)[:120])
                return result
            except BaseException as exc:
                log.exception("LAYER %s.%s raised", name, fn_name)
                raise
        return wrapped

    adapter._dispatch_inbound_event = snap("adapter", "_dispatch_inbound_event", adapter._dispatch_inbound_event)
    adapter._handle_message_with_guards = snap("adapter", "_handle_message_with_guards", adapter._handle_message_with_guards)
    adapter._admit = (lambda orig: (lambda sender, message: (_r := orig(sender, message), log.info("ADMIT -> %r", _r), _r)[2]))(adapter._admit)
    runner._handle_message = snap("gateway", "_handle_message", runner._handle_message)
    runner._hm_admit_event = snap("gateway", "_hm_admit_event", runner._hm_admit_event)
    runner._handle_message_with_agent = snap("gateway", "_handle_message_with_agent", runner._handle_message_with_agent)
    log.info("layer trace installed")


RUN_NONCE = "run" + str(int(time.time() * 1000))

async def async_main():
    env_ok = not any(k.lower().endswith("_proxy") and k.lower() != "no_proxy" for k in os.environ)
    stage("env_clean", env_ok, json.dumps(env_proof()))
    HOME.mkdir(parents=True, exist_ok=True)
    (CHILD / "tmp").mkdir(exist_ok=True)
    sys.path[:0] = [str(WORKTREE), str(MANAGED), str(TESTLIBS)]
    install_socket_guard()
    model_srv = start_stub(ModelStub)
    sidecar_srv = start_stub(SidecarStub)
    model_base = "http://127.0.0.1:" + str(model_srv.server_port) + "/v1"
    event_url = "http://127.0.0.1:" + str(sidecar_srv.server_port) + "/events"
    stage("stubs", True, "model=" + model_base + " sidecar=" + event_url + " [local stub, labelled]")
    # Private hook-runtime env (env change, not code change): fixed fake token, dynamic local event URL.
    os.environ["HERMES_FEISHU_CARD_ENABLED"] = "1"
    os.environ["HERMES_FEISHU_CARD_EVENT_URL"] = event_url
    os.environ["HERMES_FEISHU_CARD_TIMEOUT_MS"] = "800"
    os.environ["HERMES_FEISHU_CARD_TOKEN"] = "fixture-hook-token"
    # Official allowlist envs (read by gateway authz): authorize the fixture sender only.
    os.environ["FEISHU_ALLOWED_USERS"] = "ou_human"
    os.environ["GATEWAY_ALLOWED_USERS"] = "ou_human"
    config_text = (
        "model:\n"
        "  provider: custom\n"
        "  base_url: " + model_base + "\n"
        "  api_key: fixture-only\n"
        "  default: fixture-model\n"
        "  context_length: 131072\n"
        "  api_mode: chat_completions\n"
        "agent:\n  max_iterations: 2\n"
        "compression:\n  enabled: false\n"
        "database:\n  journal_mode: delete\n"
    )
    (HOME / "config.yaml").write_text(config_text)
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    import gateway.run as gateway_run
    import gateway.session_ingress
    import gateway.session_authority
    stage("imports", True, json.dumps({"gateway_run": gateway_run.__file__, "session_ingress": gateway.session_ingress.__file__, "session_authority": gateway.session_authority.__file__, "python": sys.version.split()[0]}))
    # Transport root secret exactly like a real install (0600 file in private state dir).
    os.environ["HERMES_FEISHU_CARD_STATE_DIR"] = str(CHILD / "hfc-state")
    (CHILD / "hfc-state").mkdir(parents=True, exist_ok=True)
    os.chmod(CHILD / "hfc-state", 0o700)
    from hermes_feishu_card.operations_transport import ensure_transport_root_secret
    root_secret = ensure_transport_root_secret()
    stage("transport_secret", root_secret is not None, "bytes=" + str(len(root_secret or b'')))
    import hermes_feishu_card.hook_runtime as hook_runtime
    from hermes_feishu_card import admission_completion as ac
    stage("hfc_import", True, json.dumps({"hook_runtime": hook_runtime.__file__, "admission_completion": ac.__file__, "verified_topology": ac._verified() is not None}))
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    cfg = GatewayConfig(multiplex_profiles=False, loop_watchdog=False)
    runner = gateway_run.GatewayRunner(config=cfg)
    stage("runner_init", True, json.dumps({"session_store": type(runner.session_store).__name__, "db_init_error": getattr(runner, "_session_db_init_error", None)}))
    ac_checkpoint("runner_init")
    generation_calls = []
    original_begin_gen = runner._begin_session_run_generation
    def recorded_begin_gen(session_key):
        value = original_begin_gen(session_key)
        generation_calls.append({"session_key": session_key, "generation": value, "origin": "GatewayRunner._begin_session_run_generation"})
        return value
    runner._begin_session_run_generation = recorded_begin_gen
    from gateway.runtime_ownership import process_ownership
    process_ownership.reserve([HOME])
    from gateway.run_runtime import initialize_gateway_runtime
    await initialize_gateway_runtime(runner)
    authority = runner.session_authority
    stage("authority_init", True, json.dumps({"profile_id": authority.profile_id, "epoch": str(authority.epoch), "instance_id": authority.instance_id, "db": str(authority.db.db_path)}))
    ac_checkpoint("authority_init")
    # Adapter via the REAL loader namespace (registry entry), not a direct import.
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
    from gateway.platform_registry import platform_registry
    _entry = platform_registry.get("feishu")
    assert _entry is not None, "feishu plugin not registered by the real loader"
    FeishuAdapter = _entry.adapter_factory
    _direct = __import__("plugins.platforms.feishu.adapter", fromlist=["x"])
    adapter = FeishuAdapter(PlatformConfig(enabled=True, extra={"app_id": "fixture-app", "app_secret": "fixture-secret"}))
    adapter.gateway_runner = runner
    runner.adapters[Platform.FEISHU] = adapter
    runner._wire_adapter_handlers(adapter)
    stage("adapter", True, json.dumps({"adapter": FeishuAdapter.__module__ + "." + FeishuAdapter.__name__, "adapter_file": sys.modules["plugins.platforms.feishu.adapter"].__file__}))
    ac_checkpoint("adapter")
    wrap_emit(hook_runtime)
    _install_layer_trace(adapter, runner)
    stage("emit_wrapped", True, "record-then-passthrough; no gate changed")
    ac_checkpoint("emit_wrapped")
    async def inject(message_id, text):
        await adapter._handle_message_event_data(make_feishu_event(message_id, text))
    await inject(RUN_NONCE + "-dm-001", "stage2 first dm")
    started = await wait_until(lambda: any((e.get("event") == "message.started") for e in RECEIPT["events"]), 20)
    await asyncio.sleep(2)
    await inject(RUN_NONCE + "-dm-002", "stage2 second dm")
    completed2 = await wait_until(lambda: len([e for e in RECEIPT["events"] if e.get("event") == "message.completed"]) >= 2, 20)
    await asyncio.sleep(2)
    stage("turns", started and completed2, "hook_calls=" + str(len(RECEIPT["hook_calls"])) + " events=" + str(len(RECEIPT["events"])) + " policy=" + str(len(RECEIPT["policy_queries"])))
    ac_checkpoint("turns")
    try:
        rows = [dict(r) for r in authority.db._read_all("SELECT seq, admission_id, target_session_id, status, outcome, generation, request_id FROM session_admissions")]
        RECEIPT["admissions"] = rows
    except Exception as exc:
        RECEIPT["errors"].append("admission read: " + repr(exc)[:200])
        RECEIPT["admissions"] = []
    RECEIPT["adapter_diag"] = {"pending_text_batches": {k: len(v.text or "") for k, v in adapter._pending_text_batches.items()}, "chat_locks": len(getattr(adapter, "_chat_locks", {})), "seen_ids": len(getattr(adapter, "_seen_message_ids", {}))}
    RECEIPT["generation_calls"] = generation_calls
    RECEIPT["hook_call_count"] = len(RECEIPT["hook_calls"])
    RECEIPT["handoff_modes"] = [c.get("admission_mode") for c in RECEIPT["hook_calls"]]
    RECEIPT["env_proof"] = env_proof()
    _loader_mod = sys.modules.get("hermes_plugins.platforms__feishu.adapter")
    RECEIPT["files"] = {
        "gateway_run": gateway_run.__file__,
        "hook_runtime": hook_runtime.__file__,
        "feishu_adapter_loader": _loader_mod.__file__ if _loader_mod else None,
        "feishu_adapter_loader_module": _loader_mod.__name__ if _loader_mod else None,
        "feishu_adapter_direct": sys.modules["plugins.platforms.feishu.adapter"].__file__,
        "hermes_feishu_card_init": sys.modules["hermes_feishu_card"].__file__,
    }
    out = HERE / (os.environ.get("HFC_RUN_RESULT", "RUN2-RESULT.json"))
    out.write_text(json.dumps(RECEIPT, indent=2, default=str) + chr(10))
    print(json.dumps({"written": str(out), "hook_calls": RECEIPT["hook_call_count"], "events": len(RECEIPT["events"]), "policy_queries": len(RECEIPT["policy_queries"]), "handoff_modes": RECEIPT["handoff_modes"], "errors": RECEIPT["errors"]}, indent=2))
    try:
        process_ownership.close()
    except Exception:
        pass
    model_srv.shutdown()
    sidecar_srv.shutdown()
    passed = started and completed2 and bool(RECEIPT["handoff_modes"]) and all(m == "handoff" for m in RECEIPT["handoff_modes"])
    return 0 if passed else 1


def ac_checkpoint(name):
    try:
        from hermes_feishu_card import admission_completion as _ac
        v = _ac._verified() is not None
        err = None
    except Exception as exc:
        v = None
        err = repr(exc)[:120]
    RECEIPT.setdefault("ac_checkpoints", []).append({"stage": name, "verified": v, "error": err})
    print("[ac-checkpoint] " + name + ": verified=" + repr(v), flush=True)

def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
