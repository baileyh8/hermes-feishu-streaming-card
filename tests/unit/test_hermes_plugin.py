import importlib
import json
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from hermes_feishu_card import hermes_plugin
from hermes_feishu_card import hermes_plugin_runtime as plugin_runtime
from hermes_feishu_card.event_auth import EventProofVerifier
from tests.fixtures.hermes_v020_plugin_api import PluginContext


EXPECTED_HOOKS = {
    "pre_llm_call", "post_llm_call", "on_session_end",
    "on_session_reset", "on_session_finalize", "pre_tool_call",
    "post_tool_call", "pre_approval_request", "post_approval_response",
    "subagent_start", "subagent_stop",
}


def test_plugin_import_does_not_import_hermes_or_runtime_bridge():
    sys.modules.pop("hermes_feishu_card.hermes_plugin", None)
    before = set(sys.modules)
    module = importlib.import_module("hermes_feishu_card.hermes_plugin")
    imported = set(sys.modules) - before
    assert callable(module.register)
    assert not any(
        name == "hermes_cli" or name.startswith("hermes_cli.")
        for name in imported
    )
    assert "hermes_feishu_card.hermes_plugin_runtime" not in imported


def test_register_is_fail_open_until_runtime_bridge_is_available(monkeypatch):
    module = importlib.import_module("hermes_feishu_card.hermes_plugin")
    real_import = importlib.import_module

    def unavailable(name, package=None):
        if name == ".hermes_plugin_runtime":
            raise ImportError("runtime unavailable")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", unavailable)
    assert module.register(object()) is None


def test_register_lazily_delegates_to_runtime_bridge(monkeypatch):
    module = importlib.import_module("hermes_feishu_card.hermes_plugin")
    requested = []
    received = []
    ctx = object()

    class Runtime:
        @staticmethod
        def bootstrap_plugin_runtime(callback_ctx):
            received.append(callback_ctx)

    def runtime_import(name, package=None):
        requested.append((name, package))
        assert name == ".hermes_plugin_runtime"
        assert package == "hermes_feishu_card"
        return Runtime

    monkeypatch.setattr(importlib, "import_module", runtime_import)

    assert module.register(ctx) is None
    assert received == [ctx]
    assert requested == [(".hermes_plugin_runtime", "hermes_feishu_card")]


def test_register_registers_exactly_the_verified_v020_official_hook_names():
    context = PluginContext()
    assert hermes_plugin.register(context) is None
    assert set(context.registered) == EXPECTED_HOOKS


def test_register_context_matches_v020_and_has_no_valid_hooks_attribute():
    context = PluginContext()
    assert hermes_plugin.register(context) is None
    assert not hasattr(context, "VALID_HOOKS")


def test_one_host_registration_error_does_not_abort_later_hooks():
    context = PluginContext(reject_hooks={"post_llm_call"})
    assert hermes_plugin.register(context) is None
    assert "post_llm_call" not in context.registered
    assert set(context.registered) == EXPECTED_HOOKS - {"post_llm_call"}


def test_registered_callback_returns_none_when_runtime_callback_raises(monkeypatch):
    context = PluginContext()
    hermes_plugin.register(context)
    monkeypatch.setattr(
        "hermes_feishu_card.hermes_plugin_runtime.handle_pre_llm_call",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bridge failure")),
    )
    assert context.registered["pre_llm_call"](turn_id="turn-1") is None


class CountingPluginContext(PluginContext):
    def __init__(self, reject_hooks=()):
        super().__init__(reject_hooks=reject_hooks)
        self.register_calls = []

    def register_hook(self, name, callback):
        self.register_calls.append(name)
        return super().register_hook(name, callback)


@pytest.fixture(autouse=True)
def reset_production_plugin_runtime(monkeypatch):
    plugin_runtime.reset_production_plugin_runtime_for_tests()
    plugin_runtime.reset_plugin_runtime_state()
    monkeypatch.delenv("HERMES_FEISHU_CARD_ENABLED", raising=False)
    monkeypatch.delenv("HERMES_FEISHU_CARD_EVENT_URL", raising=False)
    monkeypatch.delenv("HERMES_FEISHU_CARD_TIMEOUT_MS", raising=False)
    monkeypatch.setattr(plugin_runtime, "read_transport_root_secret", lambda: None)
    yield
    plugin_runtime.reset_production_plugin_runtime_for_tests()
    plugin_runtime.reset_plugin_runtime_state()


def test_real_entry_register_bootstraps_before_callbacks_and_is_process_idempotent(
    monkeypatch,
):
    acquired = []
    atexit_handlers = []

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    events = []
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )

    def acquire(**kwargs):
        events.append("acquired")
        acquired.append(kwargs)
        return lease

    monkeypatch.setattr(
        plugin_runtime,
        "acquire_runtime_control",
        acquire,
    )
    monkeypatch.setattr(
        plugin_runtime.atexit, "register", lambda callback: atexit_handlers.append(callback)
    )
    context = CountingPluginContext()
    real_register = context.register_hook

    def checked_register(name, callback):
        events.append(f"registered:{name}")
        return real_register(name, callback)

    monkeypatch.setattr(context, "register_hook", checked_register)

    assert hermes_plugin.register(context) is None
    assert plugin_runtime.active_plugin_runtime() is not None
    assert events[0] == "acquired"
    assert set(context.registered) == EXPECTED_HOOKS
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert len(acquired) == 1
    assert len(atexit_handlers) == 1

    assert hermes_plugin.register(context) is None
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert len(acquired) == 1
    assert len(atexit_handlers) == 1
    assert lease.close_calls == 0


@pytest.mark.parametrize("disabled", [False, True])
def test_missing_secret_or_disabled_registers_exact_inert_callbacks_without_lease(
    monkeypatch, disabled
):
    acquired = []
    if disabled:
        monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "0")
        monkeypatch.setattr(
            plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
        )
    monkeypatch.setattr(
        plugin_runtime,
        "acquire_runtime_control",
        lambda **kwargs: acquired.append(kwargs),
    )
    context = CountingPluginContext()

    assert hermes_plugin.register(context) is None
    assert set(context.registered) == EXPECTED_HOOKS
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert plugin_runtime._ACTIVE_RUNTIME is None
    assert acquired == []
    for callback in context.registered.values():
        assert callback(turn_id="turn-1", platform="feishu") is None

    assert hermes_plugin.register(context) is None
    assert len(context.register_calls) == len(EXPECTED_HOOKS)


def test_native_bootstrap_shares_gateway_drain_proof_without_masking_busy_work(monkeypatch):
    from hermes_feishu_card import runtime_control

    captured = {}

    class Emitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    runtime_control.reset_runtime_control_for_tests()
    monkeypatch.setattr(runtime_control, 'RuntimeControlEmitter', Emitter)
    monkeypatch.setenv('HERMES_FEISHU_CARD_EVENT_URL', 'http://127.0.0.1:18765/events')
    monkeypatch.setattr(plugin_runtime, 'read_transport_root_secret', lambda: b'r' * 32)
    try:
        hermes_plugin.register(CountingPluginContext())
        native = plugin_runtime.active_plugin_runtime()
        assert native is not None
        snapshot = captured['runtime_snapshot_provider']
        assert snapshot() == (0, False, True, False)
        draining = [False]
        home = [True]
        gateway = runtime_control.acquire_runtime_control(
            event_url='http://127.0.0.1:18765/events',
            package_version=plugin_runtime.__version__,
            active_work_snapshot_provider=lambda: (0, True),
            admission_draining_provider=lambda: draining[0],
            drain_home_verified_provider=lambda: home[0],
        )
        assert gateway is not None
        assert snapshot() == (0, True, False, True)
        monkeypatch.setattr(native, 'runtime_activity_snapshot', lambda: (2, True))
        draining[0] = True
        assert snapshot() == (2, True, True, True)
        monkeypatch.setattr(native, 'runtime_activity_snapshot', lambda: (0, False))
        assert snapshot() == (0, False, True, True)
        monkeypatch.setattr(native, 'runtime_activity_snapshot', lambda: (0, True))
        home[0] = False
        assert snapshot() == (0, True, True, False)
        gateway.close()
        assert snapshot() == (0, False, True, False)
    finally:
        plugin_runtime.reset_production_plugin_runtime_for_tests()
        runtime_control.reset_runtime_control_for_tests()


def test_partial_bootstrap_restores_inert_callbacks_and_releases_only_its_lease(
    monkeypatch,
):
    closed = []

    class Lease:
        def close(self):
            closed.append("lease")

    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: Lease()
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)
    context = CountingPluginContext(reject_hooks={"post_llm_call"})

    assert hermes_plugin.register(context) is None
    assert plugin_runtime._ACTIVE_RUNTIME is None
    assert closed == ["lease"]
    assert context.registered["pre_llm_call"](
        session_id="session-1", turn_id="turn-1", platform="feishu"
    ) is None


def test_atexit_registration_failure_rolls_back_runtime_and_heartbeat(monkeypatch):
    closed = []

    class Lease:
        def close(self):
            closed.append("lease")

    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: Lease()
    )
    monkeypatch.setattr(
        plugin_runtime.atexit,
        "register",
        lambda _callback: (_ for _ in ()).throw(RuntimeError("atexit unavailable")),
    )

    context = CountingPluginContext()
    assert hermes_plugin.register(context) is None
    assert plugin_runtime._ACTIVE_RUNTIME is None
    assert plugin_runtime._PRODUCTION_BOOTSTRAP is None
    assert closed == ["lease"]
    assert set(context.registered) == EXPECTED_HOOKS
    assert len(context.register_calls) == len(EXPECTED_HOOKS)


def test_replacement_and_process_close_each_runtime_and_lease_once(monkeypatch):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)
    leases = []

    class Lease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    def acquire(**_kwargs):
        lease = Lease()
        leases.append(lease)
        return lease

    monkeypatch.setattr(plugin_runtime, "acquire_runtime_control", acquire)
    first_context = CountingPluginContext()
    second_context = CountingPluginContext()
    assert hermes_plugin.register(first_context) is None
    first_runtime = plugin_runtime._ACTIVE_RUNTIME
    first_close_calls = []
    real_first_close = first_runtime.close

    def close_first():
        first_close_calls.append(True)
        return real_first_close()

    monkeypatch.setattr(first_runtime, "close", close_first)
    assert hermes_plugin.register(second_context) is None
    second_runtime = plugin_runtime._ACTIVE_RUNTIME
    second_close_calls = []
    real_second_close = second_runtime.close

    def close_second():
        second_close_calls.append(True)
        return real_second_close()

    monkeypatch.setattr(second_runtime, "close", close_second)

    assert first_close_calls == [True]
    assert leases[0].close_calls == 1
    plugin_runtime._close_process_plugin_runtime()
    plugin_runtime._close_process_plugin_runtime()
    assert second_close_calls == [True]
    assert leases[1].close_calls == 1


def test_replacement_lease_failure_keeps_old_runtime_and_owner(monkeypatch):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    old_lease = Lease()
    leases = iter((old_lease, None))
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: next(leases)
    )
    first = CountingPluginContext()
    second = CountingPluginContext()
    hermes_plugin.register(first)
    old_runtime = plugin_runtime._ACTIVE_RUNTIME
    old_close_calls = []
    real_close = old_runtime.close
    monkeypatch.setattr(
        old_runtime,
        "close",
        lambda: old_close_calls.append(True) or real_close(),
    )

    assert hermes_plugin.register(second) is None
    assert plugin_runtime._ACTIVE_RUNTIME is old_runtime
    assert plugin_runtime._PRODUCTION_BOOTSTRAP.runtime is old_runtime
    assert old_close_calls == []
    assert old_lease.close_calls == 0
    assert set(second.registered) == EXPECTED_HOOKS
    assert second.registered["pre_llm_call"](
        session_id="session-new", turn_id="turn-new", platform="feishu"
    ) is None
    assert old_runtime.turn_state("turn-new") is None


def test_replacement_callback_failure_restores_old_and_new_callbacks_are_inert(
    monkeypatch,
):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)

    class Lease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    old_lease = Lease()
    new_lease = Lease()
    leases = iter((old_lease, new_lease))
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: next(leases)
    )
    first = CountingPluginContext()
    hermes_plugin.register(first)
    old_runtime = plugin_runtime._ACTIVE_RUNTIME
    old_close_calls = []
    real_close = old_runtime.close
    monkeypatch.setattr(
        old_runtime,
        "close",
        lambda: old_close_calls.append(True) or real_close(),
    )
    second = CountingPluginContext(reject_hooks={"post_llm_call"})

    assert hermes_plugin.register(second) is None
    assert plugin_runtime._ACTIVE_RUNTIME is old_runtime
    assert plugin_runtime._PRODUCTION_BOOTSTRAP.runtime is old_runtime
    assert old_close_calls == []
    assert old_lease.close_calls == 0
    assert new_lease.close_calls == 1
    assert second.registered["pre_llm_call"](
        session_id="session-new", turn_id="turn-new", platform="feishu"
    ) is None
    assert old_runtime.turn_state("turn-new") is None


def test_replacement_heartbeat_snapshot_remains_bound_to_each_owner_runtime(
    monkeypatch,
):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)
    providers = []

    class Lease:
        def close(self):
            return None

    def acquire(**kwargs):
        providers.append(kwargs["active_work_snapshot_provider"])
        return Lease()

    monkeypatch.setattr(plugin_runtime, "acquire_runtime_control", acquire)
    hermes_plugin.register(CountingPluginContext())
    first_runtime = plugin_runtime._ACTIVE_RUNTIME
    monkeypatch.setattr(first_runtime, "runtime_activity_snapshot", lambda: (1, True))
    hermes_plugin.register(CountingPluginContext())
    second_runtime = plugin_runtime._ACTIVE_RUNTIME
    monkeypatch.setattr(second_runtime, "runtime_activity_snapshot", lambda: (2, True))

    assert providers[0]() == (1, True)
    assert providers[1]() == (2, True)


def test_runtime_callback_calls_its_bound_runtime_method_without_global_redispatch(
    monkeypatch,
):
    called = []

    class ExpectedRuntime:
        def handle_pre_llm_call(self, **kwargs):
            called.append(kwargs)

        def close(self):
            return None

    expected = ExpectedRuntime()
    plugin_runtime._swap_active_runtime(expected)
    monkeypatch.setattr(
        plugin_runtime,
        "handle_pre_llm_call",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("callback redispatched through module-global state")
        ),
    )
    callback = plugin_runtime._runtime_callback("handle_pre_llm_call", expected)

    assert callback(turn_id="turn-bound") is None
    assert called == [{"turn_id": "turn-bound"}]


def test_one_context_gate_transitions_inert_active_disabled_without_appending(
    monkeypatch,
):
    acquired = []

    class Lease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    monkeypatch.setattr(
        plugin_runtime,
        "acquire_runtime_control",
        lambda **kwargs: acquired.append(kwargs) or lease,
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)
    context = CountingPluginContext()

    assert hermes_plugin.register(context) is None
    callbacks = dict(context.registered)
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert plugin_runtime.active_plugin_runtime() is None

    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    assert hermes_plugin.register(context) is None
    runtime = plugin_runtime.active_plugin_runtime()
    assert runtime is not None
    called = []
    monkeypatch.setattr(
        runtime,
        "handle_pre_llm_call",
        lambda **kwargs: called.append(kwargs),
    )
    assert context.registered == callbacks
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert callbacks["pre_llm_call"](turn_id="turn-active") is None
    assert called == [{"turn_id": "turn-active"}]

    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "0")
    assert hermes_plugin.register(context) is None
    assert plugin_runtime.active_plugin_runtime() is None
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert callbacks["pre_llm_call"](turn_id="turn-disabled") is None
    assert called == [{"turn_id": "turn-active"}]
    assert len(acquired) == 1
    assert lease.close_calls == 1


def test_active_context_gate_callback_fails_open_when_exact_runtime_method_raises(
    monkeypatch,
):
    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: lease
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)
    context = CountingPluginContext()
    assert hermes_plugin.register(context) is None
    runtime = plugin_runtime.active_plugin_runtime()
    assert runtime is not None
    calls = []

    def fail_exact_method(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("private callback failure detail")

    monkeypatch.setattr(runtime, "handle_pre_llm_call", fail_exact_method)

    assert context.registered["pre_llm_call"](turn_id="turn-active") is None
    assert calls == [{"turn_id": "turn-active"}]
    assert plugin_runtime.active_plugin_runtime() is runtime
    assert lease.close_calls == 0


def test_context_gate_retries_only_the_missing_hook_after_transient_rejection(
    monkeypatch,
):
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "0")

    class TransientContext(CountingPluginContext):
        def __init__(self):
            super().__init__()
            self.reject_once = True

        def register_hook(self, name, callback):
            self.register_calls.append(name)
            if name == "post_llm_call" and self.reject_once:
                self.reject_once = False
                raise RuntimeError("transient host rejection")
            return PluginContext.register_hook(self, name, callback)

    context = TransientContext()
    assert hermes_plugin.register(context) is None
    assert set(context.registered) == EXPECTED_HOOKS - {"post_llm_call"}
    assert len(context.register_calls) == len(EXPECTED_HOOKS)

    assert hermes_plugin.register(context) is None
    assert set(context.registered) == EXPECTED_HOOKS
    assert context.register_calls.count("post_llm_call") == 2
    assert all(
        context.register_calls.count(name) == 1
        for name in EXPECTED_HOOKS - {"post_llm_call"}
    )
    calls_after_complete = list(context.register_calls)
    assert hermes_plugin.register(context) is None
    assert context.register_calls == calls_after_complete


def test_transient_secret_read_failure_preserves_existing_context_gate(monkeypatch):
    secret = [b"r" * 32]

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: secret[0]
    )
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: lease
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)
    context = CountingPluginContext()
    hermes_plugin.register(context)
    runtime = plugin_runtime.active_plugin_runtime()
    called = []
    monkeypatch.setattr(
        runtime,
        "handle_pre_llm_call",
        lambda **kwargs: called.append(kwargs),
    )

    secret[0] = None
    assert hermes_plugin.register(context) is None
    assert context.registered["pre_llm_call"](turn_id="turn-during-failure") is None
    secret[0] = b"r" * 32
    assert hermes_plugin.register(context) is None
    assert context.registered["pre_llm_call"](turn_id="turn-after-recovery") is None

    assert plugin_runtime.active_plugin_runtime() is runtime
    assert called == [
        {"turn_id": "turn-during-failure"},
        {"turn_id": "turn-after-recovery"},
    ]
    assert len(context.register_calls) == len(EXPECTED_HOOKS)
    assert lease.close_calls == 0


def test_active_plugin_runtime_getter_uses_active_runtime_lock(monkeypatch):
    class Runtime:
        def close(self):
            return None

    runtime = Runtime()
    plugin_runtime._swap_active_runtime(runtime)
    entered = Event()
    completed = Event()

    def read_active():
        entered.set()
        assert plugin_runtime.active_plugin_runtime() is runtime
        completed.set()

    with plugin_runtime._ACTIVE_RUNTIME_LOCK:
        thread = Thread(target=read_active)
        thread.start()
        assert entered.wait(timeout=0.5)
        assert completed.wait(timeout=0.05) is False
    thread.join(timeout=0.5)
    assert completed.is_set()


def test_replacement_teardown_exception_rolls_back_candidate_and_restores_old(
    monkeypatch,
):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)

    class Lease:
        def __init__(self, raises=False):
            self.raises = raises
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            if self.raises:
                raise RuntimeError("private lease close detail")

    old_lease = Lease(raises=True)
    new_lease = Lease()
    leases = iter((old_lease, new_lease))
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: next(leases)
    )
    first = CountingPluginContext()
    second = CountingPluginContext()
    hermes_plugin.register(first)
    old_runtime = plugin_runtime.active_plugin_runtime()
    old_close_calls = []
    real_old_close = old_runtime.close

    def fail_old_close():
        old_close_calls.append(True)
        raise RuntimeError("private runtime close detail")

    monkeypatch.setattr(old_runtime, "close", fail_old_close)

    assert hermes_plugin.register(second) is None
    active_runtime = plugin_runtime.active_plugin_runtime()
    assert active_runtime is old_runtime
    assert plugin_runtime._PRODUCTION_BOOTSTRAP.runtime is old_runtime
    assert old_close_calls == [True]
    assert old_lease.close_calls == 0
    assert new_lease.close_calls == 1
    assert old_runtime.runtime_interaction_listener_snapshot()["accepting"] is True
    called = []
    monkeypatch.setattr(
        old_runtime,
        "handle_pre_llm_call",
        lambda **kwargs: called.append(kwargs),
    )
    assert first.registered["pre_llm_call"](turn_id="turn-old") is None
    assert called == [{"turn_id": "turn-old"}]
    monkeypatch.setattr(old_runtime, "close", real_old_close)


def test_replacement_false_close_rolls_back_ready_candidate_and_resumes_old(
    monkeypatch,
):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)

    class Lease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    leases = [Lease(), Lease()]
    pending = iter(leases)
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: next(pending)
    )
    first = CountingPluginContext()
    second = CountingPluginContext()
    hermes_plugin.register(first)
    old_bootstrap = plugin_runtime._PRODUCTION_BOOTSTRAP
    old_runtime = plugin_runtime.active_plugin_runtime()
    real_close = old_bootstrap.close
    close_calls = []
    monkeypatch.setattr(
        old_bootstrap, "close", lambda: close_calls.append(True) or False
    )

    assert hermes_plugin.register(second) is None
    assert close_calls == [True]
    assert plugin_runtime.active_plugin_runtime() is old_runtime
    assert plugin_runtime._PRODUCTION_BOOTSTRAP is old_bootstrap
    assert leases[0].close_calls == 0
    assert leases[1].close_calls == 1
    assert old_runtime.runtime_interaction_listener_snapshot()["accepting"] is True
    monkeypatch.setattr(old_bootstrap, "close", real_close)


def test_listener_close_exception_preserves_old_bootstrap_and_blocks_future_changes(
    monkeypatch,
):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)

    class Lease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    leases = [Lease(), Lease()]
    pending = iter(leases)
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: next(pending)
    )
    first = CountingPluginContext()
    second = CountingPluginContext()
    hermes_plugin.register(first)
    old_bootstrap = plugin_runtime._PRODUCTION_BOOTSTRAP
    old_runtime = plugin_runtime.active_plugin_runtime()
    listener = old_runtime._runtime_interaction_listener
    real_listener_close = listener.close
    monkeypatch.setattr(
        listener,
        "close",
        lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE-CLOSE-CANARY")),
    )

    assert hermes_plugin.register(second) is None
    assert plugin_runtime.active_plugin_runtime() is old_runtime
    assert plugin_runtime._PRODUCTION_BOOTSTRAP is old_bootstrap
    assert old_bootstrap.gate._runtime is old_runtime
    assert leases[0].close_calls == 0
    assert leases[1].close_calls == 1
    assert old_runtime.runtime_interaction_listener_snapshot() == {
        "accepting": False,
        "poisoned": True,
        "worker_name": listener.snapshot()["worker_name"],
    }

    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "0")
    assert hermes_plugin.register(first) is None
    assert plugin_runtime._PRODUCTION_BOOTSTRAP is old_bootstrap
    assert plugin_runtime.active_plugin_runtime() is old_runtime
    assert leases[0].close_calls == 0

    monkeypatch.setattr(listener, "close", real_listener_close)
    listener.close()
    monkeypatch.setattr(old_runtime, "close", lambda: None)


def test_session_callbacks_do_not_close_process_runtime_or_heartbeat(monkeypatch):
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(plugin_runtime.atexit, "register", lambda _callback: None)

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    monkeypatch.setattr(
        plugin_runtime, "acquire_runtime_control", lambda **_kwargs: lease
    )
    context = CountingPluginContext()
    hermes_plugin.register(context)
    runtime = plugin_runtime._ACTIVE_RUNTIME

    assert context.registered["on_session_reset"](old_session_id="session-1") is None
    assert context.registered["on_session_finalize"](session_id="session-1") is None
    assert plugin_runtime._ACTIVE_RUNTIME is runtime
    assert lease.close_calls == 0


@contextmanager
def signed_event_server(secret, response_body=b'{"applied":true,"ok":true}'):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append((self.path, body, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def test_production_transport_posts_canonical_signed_json_to_real_loopback_server():
    secret = b"s" * 32
    with signed_event_server(secret) as (server, received):
        transport = plugin_runtime.SignedEventTransport(
            event_url=f"http://127.0.0.1:{server.server_port}/events",
            timeout_seconds=0.5,
            secret_reader=lambda: secret,
        )
        payload = {"z": "对象", "a": {"value": 1}}
        assert transport(payload, 10.0) == {"applied": True, "ok": True}

    path, body, headers = received[0]
    assert path == "/events"
    assert body == json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    EventProofVerifier(secret).verify(headers, body)


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1:18765/events",
        "http://example.com/events",
        "http://192.168.1.2/events",
        "http://localhost:18765/events",
        "http://127.0.0.2:18765/events",
        "http://127.0.0.1/events",
        "http://[::1]/events",
        "http://127.0.0.1:0/events",
        "http://127.0.0.1:65536/events",
        "http://127.0.0.1:18765/api/events",
        "http://user:secret@127.0.0.1:18765/events",
        "http://127.0.0.1:18765/events?token=x",
        "http://127.0.0.1:18765/events?",
        "http://127.0.0.1:18765/events#",
        "http://[::1%25lo0]:18765/events",
        " http://127.0.0.1:18765/events",
    ),
)
def test_production_transport_rejects_non_loopback_or_noncanonical_event_url(url):
    with pytest.raises(ValueError, match="event URL"):
        plugin_runtime.SignedEventTransport(
            event_url=url,
            timeout_seconds=0.5,
            secret_reader=lambda: b"s" * 32,
        )


@pytest.mark.parametrize(
    ("url", "canonical"),
    (
        ("http://127.0.0.1:18765/events", "http://127.0.0.1:18765/events"),
        ("http://[::1]:18765/events", "http://[::1]:18765/events"),
    ),
)
def test_production_transport_accepts_only_canonical_http_loopback_forms(
    url, canonical
):
    transport = plugin_runtime.SignedEventTransport(
        event_url=url,
        timeout_seconds=0.5,
        secret_reader=lambda: b"s" * 32,
    )
    assert transport.event_url == canonical


def test_production_bootstrap_does_not_normalize_noncanonical_event_url(monkeypatch):
    acquired = []
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL", " http://127.0.0.1:18765/events"
    )
    monkeypatch.setattr(
        plugin_runtime, "read_transport_root_secret", lambda: b"r" * 32
    )
    monkeypatch.setattr(
        plugin_runtime,
        "acquire_runtime_control",
        lambda **kwargs: acquired.append(kwargs),
    )
    context = CountingPluginContext()

    assert hermes_plugin.register(context) is None
    assert plugin_runtime.active_plugin_runtime() is None
    assert acquired == []
    assert set(context.registered) == EXPECTED_HOOKS


def test_production_transport_never_accepts_a_redirected_response():
    received = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(("POST", self.path))
            self.send_response(302)
            self.send_header("Location", "/redirected")
            self.end_headers()

        def do_GET(self):
            received.append(("GET", self.path))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = plugin_runtime.SignedEventTransport(
            event_url=f"http://127.0.0.1:{server.server_port}/events",
            timeout_seconds=0.5,
            secret_reader=lambda: b"s" * 32,
        )
        assert transport({"event": "redirect"}, 0.5) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

    assert received == [("POST", "/events")]


def test_production_transport_rejects_nonordinary_json_before_auth_or_request(
    monkeypatch,
):
    class EqualitySpoofString(str):
        def __eq__(self, _other):
            return True

        __hash__ = str.__hash__

    class IntegerSubclass(int):
        pass

    class FloatSubclass(float):
        pass

    class DictSubclass(dict):
        pass

    class ListSubclass(list):
        pass

    rejected = (
        {EqualitySpoofString("event"): "started"},
        {"value": EqualitySpoofString("started")},
        {"value": IntegerSubclass(1)},
        {"value": FloatSubclass(1.0)},
        {"value": DictSubclass({"nested": True})},
        {"value": ListSubclass([True])},
        {"value": b"not-json"},
        {"value": float("nan")},
        {"value": float("inf")},
    )
    secret_reads = []
    signatures = []

    class NeverOpen:
        def open(self, *_args, **_kwargs):
            raise AssertionError("invalid JSON reached request creation")

    monkeypatch.setattr(plugin_runtime, "_NO_PROXY_EVENT_OPENER", NeverOpen())
    monkeypatch.setattr(
        plugin_runtime,
        "sign_event_request",
        lambda *_args: signatures.append(True) or {},
    )
    transport = plugin_runtime.SignedEventTransport(
        event_url="http://127.0.0.1:18765/events",
        timeout_seconds=0.5,
        secret_reader=lambda: secret_reads.append(True) or b"s" * 32,
    )

    for payload in rejected:
        assert transport(payload, 0.5) is None

    assert secret_reads == []
    assert signatures == []


def test_production_transport_enforces_documented_request_limits_before_auth(
    monkeypatch,
):
    assert plugin_runtime.MAX_EVENT_REQUEST_BYTES == 256 * 1024
    assert plugin_runtime.MAX_EVENT_JSON_DEPTH == 16
    assert plugin_runtime.MAX_EVENT_JSON_NODES == 4096
    assert plugin_runtime.MAX_EVENT_JSON_TEXT_BYTES == 256 * 1024

    depth_overflow = {"leaf": None}
    for _index in range(plugin_runtime.MAX_EVENT_JSON_DEPTH):
        depth_overflow = {"nested": depth_overflow}
    node_overflow = {
        f"key-{index}": None
        for index in range(plugin_runtime.MAX_EVENT_JSON_NODES // 2)
    }
    text_overflow = {"text": "x" * (plugin_runtime.MAX_EVENT_JSON_TEXT_BYTES + 1)}
    body_overflow = {
        "left": "x" * (plugin_runtime.MAX_EVENT_REQUEST_BYTES // 2),
        "right": "y" * (plugin_runtime.MAX_EVENT_REQUEST_BYTES // 2),
    }
    secret_reads = []

    class NeverOpen:
        def open(self, *_args, **_kwargs):
            raise AssertionError("oversized JSON reached request creation")

    monkeypatch.setattr(plugin_runtime, "_NO_PROXY_EVENT_OPENER", NeverOpen())
    transport = plugin_runtime.SignedEventTransport(
        event_url="http://127.0.0.1:18765/events",
        timeout_seconds=0.5,
        secret_reader=lambda: secret_reads.append(True) or b"s" * 32,
    )

    for payload in (depth_overflow, node_overflow, text_overflow, body_overflow):
        assert transport(payload, 0.5) is None

    assert secret_reads == []


def test_production_transport_accepts_exact_depth_and_ordinary_scalar_types(
    monkeypatch,
):
    depth_boundary = {"value": None}
    for _index in range(plugin_runtime.MAX_EVENT_JSON_DEPTH - 1):
        depth_boundary = {"nested": depth_boundary}
    node_boundary = {
        "items": [None] * (plugin_runtime.MAX_EVENT_JSON_NODES - 3)
    }
    canonical_empty = json.dumps(
        {"text": ""}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    body_boundary = {
        "text": "x" * (plugin_runtime.MAX_EVENT_REQUEST_BYTES - len(canonical_empty))
    }
    assert plugin_runtime._is_bounded_ordinary_json_object(node_boundary) is True
    assert plugin_runtime._is_bounded_ordinary_json_object(
        {"text": "x" * plugin_runtime.MAX_EVENT_JSON_TEXT_BYTES}
    ) is True
    assert len(
        json.dumps(
            body_boundary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) == plugin_runtime.MAX_EVENT_REQUEST_BYTES
    opened = []

    class Response:
        status = 200
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"{}"

    class Opener:
        def open(self, req, timeout):
            opened.append((req, timeout))
            return Response()

    monkeypatch.setattr(plugin_runtime, "_NO_PROXY_EVENT_OPENER", Opener())
    transport = plugin_runtime.SignedEventTransport(
        event_url="http://127.0.0.1:18765/events",
        timeout_seconds=0.5,
        secret_reader=lambda: b"s" * 32,
    )

    for payload in (depth_boundary, node_boundary, body_boundary):
        assert transport(payload, 0.5) == {}
    assert len(opened) == 3


def test_production_transport_bounds_response_and_accepts_only_json_object(monkeypatch):
    class Response:
        status = 200

        def __init__(self, body, content_length=None):
            self.body = body
            self.headers = {}
            if content_length is not None:
                self.headers["Content-Length"] = str(content_length)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            return self.body[:limit]

    responses = iter(
        (
            Response(b"{}", plugin_runtime.MAX_EVENT_RESPONSE_BYTES + 1),
            Response(b"[1,2]"),
            Response(b"{" + b"x" * plugin_runtime.MAX_EVENT_RESPONSE_BYTES + b"}"),
            Response(b'{"value":NaN}'),
        )
    )

    class Opener:
        def open(self, req, timeout):
            return next(responses)

    monkeypatch.setattr(plugin_runtime, "_NO_PROXY_EVENT_OPENER", Opener())
    monkeypatch.setattr(
        plugin_runtime.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("production loopback event transport used environment proxy")
        ),
    )
    transport = plugin_runtime.SignedEventTransport(
        event_url="http://127.0.0.1:18765/events",
        timeout_seconds=0.5,
        secret_reader=lambda: b"s" * 32,
    )

    assert transport({"event": "one"}, 1.0) is None
    assert transport({"event": "two"}, 1.0) is None
    assert transport({"event": "three"}, 1.0) is None
    assert transport({"event": "four"}, 1.0) is None
