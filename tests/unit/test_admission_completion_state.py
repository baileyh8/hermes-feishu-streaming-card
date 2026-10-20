"""Display carrier boundary tests; the DB/topology adapters here are explicit ports."""

import asyncio
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from hermes_feishu_card import admission_completion as ac


class Box:
    def __init__(self, **values):
        self.__dict__.update(values)


@pytest.fixture
def state(monkeypatch):
    class Authority:
        def _schedule(self):
            pass

        def _drain(self):
            pass

        def _resolve_settled_waiter(self):
            pass

        def _execute_claimed(self):
            pass

        def _stamp_claim(self):
            pass

    source = Box(
        platform="feishu",
        chat_id="chat",
        thread_id="omt_thread",
        user_id="ou_sender",
        profile_id="default",
    )
    event = Box(source=source, message_id="om_ingress")
    runner = Box(
        _is_session_run_current=lambda key, generation: (
            key == "key" and generation == 101
        )
    )
    adapter = Box(_app_id="app")
    live = Box(
        source=source,
        route="key",
        superseded=False,
        task=None,
        event_stream=Box(execution={}),
    )
    authority = Authority()
    authority.runner = runner
    authority.epoch = 9
    authority.retiring = False
    authority.sessions = {"logical": live}
    authority.db = Box()
    authority.instance_id = "instance"
    authority.profile_id = "default"
    monkeypatch.setattr(
        ac,
        "_runtime_epoch_current",
        lambda db, epoch, instance: epoch == 9 and instance == "instance",
    )
    authority.executing = {"logical": 7}
    row = {
        "admission_id": "admission",
        "target_session_id": "logical",
        "generation": 7,
        "owner_epoch": 9,
        "status": "terminal",
        "outcome": "completed",
    }
    row["payload"] = {
        "native_text_v1": {
            "source": {
                "platform": "feishu",
                "chat_id": "chat",
                "thread_id": "omt_thread",
                "user_id": "ou_sender",
                "profile": None,
            },
            "event": {"message_id": "om_ingress"},
            "route": "key",
        }
    }
    module = ModuleType("hermes_state_runtime")
    module.get_session_admission = lambda *a, **k: row
    monkeypatch.setitem(__import__("sys").modules, "hermes_state_runtime", module)
    monkeypatch.setattr(
        ac, "_verified", lambda: (None, SimpleNamespace(SessionAuthority=Authority))
    )
    offer = ac.Completion(
        stage={"payload": {"data": {}}},
        route=("default", "chat", "omt_thread", "ou_sender", "om_ingress"),
        runner=weakref.ref(runner),
        adapter=weakref.ref(adapter),
        producer=weakref.ref(event),
        source_event=weakref.ref(event),
        authority_owner=("logical", 7),
        runtime_generation=101,
        session_key="key",
        physical_session="physical-after-compression",
        native_response="answer",
        expires=ac.monotonic() + 75,
        admission_id="admission",
        authority=weakref.ref(authority),
        epoch=9,
        live=weakref.ref(live),
        app_id="app",
        instance_id="instance",
        authority_profile="default",
        db=weakref.ref(authority.db),
    )
    return Box(
        offer=offer,
        row=row,
        source=source,
        runner=runner,
        adapter=adapter,
        live=live,
        authority=authority,
    )


@pytest.mark.parametrize(
    "outcome,display,accepted",
    [
        ("completed", None, True),
        ("completed", "incomplete", True),
        ("failed", "failed", True),
        ("interrupted", "interrupted", True),
        ("failed", None, False),
        ("failed", "incomplete", False),
        ("interrupted", "failed", False),
        ("cancelled", "interrupted", False),
        ("unknown", None, False),
    ],
)
def test_settled_outcome_requires_original_matching_display_evidence(
    state, outcome, display, accepted
):
    state.row["outcome"] = outcome
    state.offer.stage["payload"]["data"]["turn_outcome"] = display
    state.offer.turn_outcome = display
    assert ac._authority_current(state.offer, started=False) is accepted


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", 101),
        ("owner_epoch", 8),
        ("admission_id", "other"),
        ("target_session_id", "physical-after-compression"),
        ("status", "started"),
    ],
)
def test_settled_row_cannot_substitute_runtime_generation_or_physical_session(
    state, field, value
):
    state.row[field] = value
    assert not ac._authority_current(state.offer, started=False)


@pytest.mark.parametrize(
    "change",
    ["app", "sender", "thread", "profile", "chat", "retired", "route", "current"],
)
def test_owner_or_route_drift_revokes_display_handoff(state, change):
    if change == "app":
        state.adapter._app_id = "other"
    elif change == "retired":
        state.authority.retiring = True
    elif change == "route":
        state.live.route = "other"
    elif change == "current":
        state.runner._is_session_run_current = lambda *a: False
    elif change == "sender":
        state.row["payload"]["native_text_v1"]["source"]["user_id"] = "other"
    else:
        setattr(
            state.source,
            {
                "sender": "user_id",
                "thread": "thread_id",
                "profile": "profile_id",
                "chat": "chat_id",
            }[change],
            "other",
        )
    assert not ac._authority_current(state.offer, started=False)


async def test_claim_requires_live_task_execution_stamp_and_current_db_row(state):
    state.row["status"] = "started"
    state.live.task = asyncio.current_task()
    state.live.event_stream.execution = {
        "authority_epoch": 9,
        "execution_generation": 7,
        "admission_id": "admission",
    }
    assert ac._authority_current(state.offer, started=True)
    state.live.event_stream.execution["execution_generation"] = 101
    assert not ac._authority_current(state.offer, started=True)
    state.live.event_stream.execution["execution_generation"] = 7
    state.live.task = None
    assert not ac._authority_current(state.offer, started=True)


def test_single_use_ttl_and_repr_do_not_retain_or_expose_terminal_payload(
    state, monkeypatch
):
    assert state.offer.fresh()
    assert "answer" not in repr(state.offer)
    monkeypatch.setattr(ac, "monotonic", lambda: state.offer.expires)
    assert not state.offer.fresh()
    state.offer.abort()
    assert state.offer.state == "closed" and state.offer.stage == {}


def test_full_module_compiler_keeps_import_symbol_table_contract():
    source = (
        "import asyncio\nasync def answer():\n    return asyncio.get_running_loop()\n"
    )
    first = ac._source_code(source, "source.py", "answer")
    second = ac._source_code(source, "source.py", "answer")
    assert ac._shape(first) == ac._shape(second)
    altered = ac._source_code(
        source.replace("get_running_loop", "get_event_loop"), "source.py", "answer"
    )
    assert ac._shape(first) != ac._shape(altered)


def test_live_group_route_keeps_first_sender_but_row_owns_current_sender(state):
    state.source.user_id = "ou_first-group-sender"
    assert ac._authority_current(state.offer, started=False)
    state.row["payload"]["native_text_v1"]["source"]["user_id"] = "ou_bystander"
    assert not ac._authority_current(state.offer, started=False)


def test_native_runtime_epoch_advance_revokes_historical_terminal_owner():
    from contextlib import nullcontext
    import sqlite3

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE runtime_epoch(singleton INTEGER, epoch INTEGER, instance_id TEXT)"
        )
        connection.execute("INSERT INTO runtime_epoch VALUES(1,1,'first')")
        db = Box(_read_ctx=lambda: nullcontext(connection))
        assert ac._runtime_epoch_current(db, 1, "first")
        connection.execute("UPDATE runtime_epoch SET epoch=2,instance_id='successor'")
        assert not ac._runtime_epoch_current(db, 1, "first")
        assert ac._runtime_epoch_current(db, 2, "successor")
    finally:
        connection.close()


@pytest.mark.parametrize("field", ["co_cellvars", "co_stacksize", "co_exceptiontable"])
def test_loaded_code_shape_rejects_cell_stack_and_exception_table_drift(field):
    code = ac._source_code(
        "def value():\n    try:\n        return 1\n    except Exception:\n        return 2\n",
        "source.py",
        "value",
    )
    if not hasattr(code, field):
        # Python 3.9/3.10 encode their handler control in co_code instead.
        assert field == "co_exceptiontable"
        return
    value = {
        "co_cellvars": ("unrelated",),
        "co_stacksize": code.co_stacksize + 1,
        "co_exceptiontable": b"",
    }[field]
    changed = code.replace(**{field: value})
    assert ac._shape(changed) != ac._shape(code)


@pytest.fixture
def wrapper_ports(state):
    calls = []

    async def handoff(*a):
        calls.append("handoff")
        return "native-handoff"

    def move(*a):
        calls.append("move")
        return "native-move"

    async def deliver(*a):
        calls.append("deliver")
        return "native-deliver"

    async def finish(*a):
        pass

    async def busy(*a):
        pass

    async def settled(*a):
        pass

    ingress = Box(
        _hand_over_turn_marker=handoff,
        _move_marker=move,
        deliver_response=deliver,
        dispatch_shared_busy=busy,
        deliver_settled=settled,
    )
    return Box(ingress=ingress, finish=finish, calls=calls, state=state)


def test_pending_move_unknown_partial_helper_cannot_break_original_native_result(
    wrapper_ports,
):
    from functools import partial

    ports = wrapper_ports
    state = ports.state
    event = Box(source=state.source, message_id="om_ingress")
    target = Box(source=state.source, message_id="om_ingress")
    state.offer.state = "offered"
    state.offer.target = weakref.ref(event)
    setattr(event, ac._ATTR, state.offer)
    ac._install(ports.ingress, ports.finish)
    ports.ingress.dispatch_shared_busy = partial(ports.ingress.dispatch_shared_busy)
    assert ports.ingress._move_marker(event, target) == "native-move"
    assert ports.calls == ["move"]
    assert state.offer.state == "closed"


@pytest.mark.parametrize("phase", ["pre", "post"])
async def test_handoff_display_metadata_error_preserves_native_result(
    wrapper_ports, monkeypatch, phase
):
    ports = wrapper_ports
    state = ports.state
    event = Box(source=state.source, message_id="om_ingress")
    offer = state.offer
    offer.source_event = weakref.ref(event)
    offer.producer = weakref.ref(asyncio.current_task())
    setattr(event, ac._ATTR, offer)

    class MetadataError:
        @property
        def runner(self):
            if phase == "pre":
                raise RuntimeError("unknown display metadata")
            return state.runner

        @property
        def marker_adopters(self):
            raise RuntimeError("unknown display metadata")

        executing = {"logical": 7}

    monkeypatch.setattr(ac, "_authority_current", lambda *a, **k: True)
    ac._install(ports.ingress, ports.finish)
    assert (
        await ports.ingress._hand_over_turn_marker(MetadataError(), "admission", event)
        == "native-handoff"
    )
    assert ports.calls == ["handoff"] and offer.state == "closed"


@pytest.mark.parametrize(
    "error", [RuntimeError("native-error"), asyncio.CancelledError("native-cancel")]
)
async def test_original_handoff_exception_identity_is_preserved_once(
    wrapper_ports, error
):
    ports = wrapper_ports
    calls = []

    async def native(*a):
        calls.append(1)
        raise error

    ports.ingress._hand_over_turn_marker = native
    ac._install(ports.ingress, ports.finish)
    with pytest.raises(type(error)) as caught:
        await ports.ingress._hand_over_turn_marker(Box(), "admission", Box())
    assert caught.value is error and calls == [1]


def test_move_caller_requires_exact_code_object_not_equal_copy():
    async def settled():
        pass

    original = settled.__code__
    copied = original.replace()
    assert copied == original and copied is not original
    assert ac._trusted_move_code(original, original, None)
    assert not ac._trusted_move_code(copied, original, None)


def test_original_move_exception_identity_is_preserved_once(wrapper_ports):
    ports = wrapper_ports
    calls = []
    error = RuntimeError("native-move-error")

    def native(*a):
        calls.append(1)
        raise error

    ports.ingress._move_marker = native
    ac._install(ports.ingress, ports.finish)
    with pytest.raises(RuntimeError) as caught:
        ports.ingress._move_marker(Box(), Box())
    assert caught.value is error and calls == [1]


@pytest.mark.parametrize("fails", [True, False])
async def test_deliver_cleanup_metadata_error_does_not_change_native_outcome(
    wrapper_ports, fails
):
    ports = wrapper_ports
    calls = []
    error = RuntimeError("native-delivery-error")

    class UnknownMetadata:
        def __getattribute__(self, name):
            raise RuntimeError("display-metadata-error")

    async def native(*a):
        calls.append(1)
        if fails:
            raise error
        return "native-delivery"

    ports.ingress.deliver_response = native
    ac._install(ports.ingress, ports.finish)
    if fails:
        with pytest.raises(RuntimeError) as caught:
            await ports.ingress.deliver_response(
                None, UnknownMetadata(), "key", "answer"
            )
        assert caught.value is error
    else:
        assert (
            await ports.ingress.deliver_response(
                None, UnknownMetadata(), "key", "answer"
            )
            == "native-delivery"
        )
    assert calls == [1]
