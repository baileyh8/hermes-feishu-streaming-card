"""Single-use presentation handoff on Hermes' own admitted events.

No execution registry: the original admission maps, marker moves and Base ledger
remain authoritative. Only the pinned native topology may transfer a frozen
terminal payload across Tasks; ordinary task-local staging is unchanged.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from hashlib import sha256
import importlib
import inspect
from pathlib import Path
import stat
import sys
from time import monotonic
from types import CodeType, ModuleType
from typing import Any, Awaitable, Callable
import weakref


_ATTR = "_hfc_admission_display_completion"
TTL_SECONDS = 75.0
SOURCE_HASHES: dict[str, str] = {
    "gateway.session_ingress": "fc8b666f74aa430c832c973e64a6b6332ae8c0c43ac5d3b69766debf31fb8dd4",
    "gateway.session_authority": "e5f3f9b24ccb765dc7fccb98d9c61bed359e3b1da3a73c855c713be425df5d7c",
}
Finish = Callable[[Any, bool], Awaitable[None]]


def _shape(code: CodeType) -> Any:
    return (
        code.co_code,
        code.co_names,
        code.co_varnames,
        code.co_argcount,
        code.co_kwonlyargcount,
        code.co_posonlyargcount,
        code.co_flags,
        code.co_freevars,
        code.co_cellvars,
        code.co_stacksize,
        code.co_nlocals,
        getattr(code, "co_exceptiontable", b""),
        tuple(_shape(v) if isinstance(v, CodeType) else v for v in code.co_consts),
    )


def _source_code(
    source: str | bytes, filename: str, name: str, owner: str | None = None
) -> CodeType:
    # Compile the complete module without executing imports. CPython's symbol
    # table distinguishes imported globals; isolated function ASTs lose that.
    code = compile(source, filename, "exec", dont_inherit=True)
    return _compiled_code(code, name, owner)


def _compiled_code(code: CodeType, name: str, owner: str | None = None) -> CodeType:
    if owner is not None:
        code = next(
            v for v in code.co_consts if isinstance(v, CodeType) and v.co_name == owner
        )
    return next(
        v for v in code.co_consts if isinstance(v, CodeType) and v.co_name == name
    )


def _original(function: Any) -> Any:
    return getattr(function, "_hfc_completion_original", function)


def _verified() -> tuple[ModuleType, ModuleType] | None:
    modules = []
    root = None
    for name, functions in (
        (
            "gateway.session_ingress",
            (
                "_hand_over_turn_marker",
                "_move_marker",
                "deliver_response",
                "deliver_settled",
                "execute_admission",
                "dispatch_shared_busy",
            ),
        ),
        (
            "gateway.session_authority",
            (
                "_schedule",
                "_drain",
                "_resolve_settled_waiter",
                "_execute_claimed",
                "_stamp_claim",
            ),
        ),
    ):
        module = sys.modules.get(name)
        if module is None:
            return None
        try:
            filename = module.__file__
            if not isinstance(filename, str):
                return None
            path = Path(filename)
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > 256 * 1024:
                return None
            raw = path.read_bytes()
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                return None
            if sha256(raw).hexdigest() != SOURCE_HASHES[name]:
                return None
            current_root = path.resolve().parent.parent
            if root is not None and current_root != root:
                return None
            root = current_root
            compiled_module = compile(raw, str(path), "exec", dont_inherit=True)
            for function_name in functions:
                owner = (
                    module.SessionAuthority
                    if name == "gateway.session_authority"
                    else module
                )
                original = _original(getattr(owner, function_name, None))
                if not inspect.iscoroutinefunction(original) and function_name not in {
                    "_move_marker",
                    "_schedule",
                    "_resolve_settled_waiter",
                    "_stamp_claim",
                }:
                    return None
                if (
                    not callable(original)
                    or Path(original.__code__.co_filename).resolve() != path.resolve()
                ):
                    return None
                scope = (
                    "SessionAuthority" if name == "gateway.session_authority" else None
                )
                expected = _compiled_code(compiled_module, function_name, scope)
                if _shape(original.__code__) != _shape(expected):
                    return None
        except (
            OSError,
            AttributeError,
            KeyError,
            StopIteration,
            SyntaxError,
            TypeError,
            ValueError,
        ):
            return None
        modules.append(module)
    return modules[0], modules[1]


def mode(values: dict[str, Any]) -> str:
    """ordinary, handoff, or native (unknown admitted execution must fail open)."""
    ingress = sys.modules.get("gateway.session_ingress")
    if ingress is None:
        return "ordinary"
    flag = getattr(ingress, "executing_admission", None)
    try:
        active = isinstance(flag, ContextVar) and flag.get() is True
    except LookupError:
        active = False
    if not active:
        return (
            "native"
            if getattr(values.get("event"), "_claimed_owner", None)
            else "ordinary"
        )
    return "handoff" if _verified() is not None else "native"


def _text(value: Any, *, empty: bool = False) -> str | None:
    return (
        value
        if isinstance(value, str)
        and len(value) <= 256
        and (empty or bool(value.strip()))
        else None
    )


def _route(event: Any, profile: str) -> tuple[str, ...] | None:
    source = getattr(event, "source", None)
    platform = getattr(source, "platform", None)
    if str(getattr(platform, "value", platform)) != "feishu":
        return None
    source_profile = getattr(source, "profile_id", None) or getattr(
        source, "profile", None
    )
    if source_profile and source_profile != profile:
        return None
    values = (
        _text(profile),
        _text(getattr(source, "chat_id", None)),
        _text(getattr(source, "thread_id", "") or "", empty=True),
        _text(getattr(source, "user_id", None)),
        _text(getattr(event, "message_id", None)),
    )
    return (
        tuple(v for v in values if v is not None)
        if all(v is not None for v in values)
        else None
    )


@dataclass(repr=False)
class Completion:
    stage: dict[str, Any]
    route: tuple[str, ...]
    runner: Any
    adapter: Any
    producer: Any
    source_event: Any
    authority_owner: tuple[str, int]
    runtime_generation: int
    session_key: str
    physical_session: str
    native_response: str
    expires: float
    state: str = "prepared"
    admission_id: str = ""
    authority: Any = None
    epoch: Any = None
    live: Any = None
    app_id: str = ""
    profile_source: str = ""
    turn_outcome: Any = None
    instance_id: str = ""
    authority_profile: str = ""
    db: Any = None
    target: Any = None

    def abort(self) -> None:
        self.state = "closed"
        self.stage.clear()

    def fresh(self) -> bool:
        return self.state in {"prepared", "offered"} and monotonic() < self.expires


def _current(offer: Completion) -> bool:
    runner = offer.runner()
    try:
        return (
            runner is not None
            and runner._is_session_run_current(
                offer.session_key, offer.runtime_generation
            )
            is True
        )
    except Exception:
        return False


def _base_extractor(adapter: Any) -> CodeType | None:
    """Bind the actual post-extraction caller to the reversible exact seam."""
    from .install import admission_extraction

    try:
        function = adapter._extract_response_content
        code = function.__func__.__code__
        ingress = sys.modules["gateway.session_ingress"]
        path = Path(code.co_filename)
        if (
            function.__self__ is not adapter
            or path.resolve()
            != Path(str(ingress.__file__)).resolve().parent / "platforms" / "base.py"
        ):
            return None
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            return None
        source = path.read_text(encoding="utf-8")
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            return None
        expected_source = admission_extraction.apply(source)
        if "capture_admission_base_extraction" not in code.co_names:
            return None
        expected = _source_code(
            expected_source,
            str(path),
            "_extract_response_content",
            "BasePlatformAdapter",
        )
        return code if _shape(code) == _shape(expected) else None
    except (
        OSError,
        AttributeError,
        KeyError,
        StopIteration,
        SyntaxError,
        TypeError,
        ValueError,
    ):
        return None


def _on_stack(code: CodeType | None) -> bool:
    if code is None:
        return False
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if frame.f_code is code:
                return True
            frame = frame.f_back
    finally:
        del frame
    return False


def _claim_frame(ingress: ModuleType) -> dict[str, Any] | None:
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if frame.f_code is ingress.execute_admission.__code__:
                return dict(frame.f_locals)
            frame = frame.f_back
    finally:
        del frame
    return None


def _runtime_epoch_current(db: Any, epoch: Any, instance_id: str) -> bool:
    """Read the current native owner; terminal rows retain their historical epoch."""
    try:
        with db._read_ctx() as connection:
            row = connection.execute(
                "SELECT epoch, instance_id FROM runtime_epoch WHERE singleton=1"
            ).fetchone()
        return (
            row is not None
            and type(epoch) is int
            and row[0] == epoch
            and _text(instance_id) is not None
            and row[1] == instance_id
        )
    except Exception:
        return False


def _authority_current(offer: Completion, *, started: bool) -> bool:
    authority = offer.authority() if offer.authority is not None else None
    live = offer.live() if offer.live is not None else None
    verified = _verified()
    if authority is None or live is None or verified is None:
        return False
    try:
        cls = verified[1].SessionAuthority
        if any(
            getattr(authority, name).__func__ is not getattr(cls, name)
            for name in (
                "_schedule",
                "_drain",
                "_resolve_settled_waiter",
                "_execute_claimed",
                "_stamp_claim",
            )
        ):
            return False
        if (
            authority.epoch != offer.epoch
            or authority.runner is not offer.runner()
            or authority.retiring
            or authority.instance_id != offer.instance_id
            or authority.profile_id != offer.authority_profile
            or offer.db is None
            or authority.db is not offer.db()
            or not _runtime_epoch_current(authority.db, offer.epoch, offer.instance_id)
            or authority.sessions.get(offer.authority_owner[0]) is not live
            or not _live_route_matches(live.source, offer.route)
            or live.route != offer.session_key
            or getattr(live, "superseded", False)
            or getattr(offer.adapter(), "_app_id", None) != offer.app_id
            or not _current(offer)
        ):
            return False
        get_session_admission = importlib.import_module(
            "hermes_state_runtime"
        ).get_session_admission
        row = get_session_admission(authority.db, admission_id=offer.admission_id)
        if (
            not isinstance(row, dict)
            or row.get("admission_id") != offer.admission_id
            or row.get("target_session_id") != offer.authority_owner[0]
            or row.get("generation") != offer.authority_owner[1]
            or row.get("owner_epoch") != offer.epoch
            or not _row_route_matches(row, offer)
        ):
            return False
        if started:
            execution = live.event_stream.execution
            return (
                row.get("status") == "started"
                and live.task is asyncio.current_task()
                and authority.executing.get(offer.authority_owner[0])
                == offer.authority_owner[1]
                and execution
                == {
                    "authority_epoch": offer.epoch,
                    "execution_generation": offer.authority_owner[1],
                    "admission_id": offer.admission_id,
                }
            )
        outcome = row.get("outcome")
        display_outcome = offer.turn_outcome
        return row.get("status") == "terminal" and (
            outcome == "completed"
            or (outcome in {"failed", "interrupted"} and display_outcome == outcome)
        )
    except Exception:
        return False


def _live_route_matches(source: Any, route: tuple[str, ...]) -> bool:
    # A group LiveSession keeps its first sender; the current immutable row,
    # never that initial source, supplies each admission's sender identity.
    current = _route_source(source, route[-1], route[0])
    return current is not None and current[:3] == route[:3]


def _row_route_matches(row: dict[str, Any], offer: Completion) -> bool:
    try:
        native = row["payload"]["native_text_v1"]
        source = native["source"]
        platform = source["platform"]
        profile = source.get("profile_id") or source.get("profile")
        route = (
            offer.route[0],
            source["chat_id"],
            source.get("thread_id") or "",
            source["user_id"],
            native["event"]["message_id"],
        )
        return (
            platform == "feishu"
            and route == offer.route
            and (not profile or profile == offer.route[0])
            and native["route"] == offer.session_key
        )
    except (KeyError, TypeError, AttributeError):
        return False


def _route_source(source: Any, message_id: str, profile: str) -> tuple[str, ...] | None:
    from types import SimpleNamespace

    return _route(SimpleNamespace(source=source, message_id=message_id), profile)


def prepare(
    values: dict[str, Any], stage: dict[str, Any], finish: Finish
) -> Completion | None:
    """Bind the existing claim and canonical payload; never infer an execution claim."""
    verified = _verified()
    if verified is None or mode(values) != "handoff":
        return None
    claim = _claim_frame(verified[0])
    if claim is None:
        return None
    authority: Any = claim.get("authority")
    ref, row = claim.get("ref"), claim.get("row")
    event, runner, source = (
        values.get("event"),
        values.get("self"),
        values.get("source"),
    )
    if runner is None or authority is None:
        return None
    payload = stage.get("payload", {})
    data = payload.get("data", {})
    profile = data.get("profile_id")
    route = _route(event, profile)
    owner = getattr(event, "_claimed_owner", None)
    generation = values.get("run_generation")
    key = _text(values.get("session_key"))
    physical = _text(getattr(values.get("session_entry"), "session_id", None))
    response = values.get("response")
    if (
        route is None
        or source is not getattr(event, "source", None)
        or payload.get("turn_id") != route[-1]
        or payload.get("chat_id") != route[1]
        or type(owner) is not tuple
        or len(owner) != 2
        or _text(owner[0]) is None
        or type(owner[1]) is not int
        or owner[1] <= 0
        or type(generation) is not int
        or generation <= 0
        or key is None
        or physical is None
        or not isinstance(response, str)
        or claim.get("event") is not event
        or not isinstance(row, dict)
        or getattr(ref, "session_id", None) != owner[0]
        or row.get("generation") != owner[1]
        or data.get("sender_open_id") != route[3]
        or (payload.get("thread_id") or "") != route[2]
        or str(data.get("profile_source", "")).startswith("sanitized_")
    ):
        return None
    try:
        adapter = runner._adapter_for_source(source)
        if (
            _text(getattr(adapter, "_app_id", None)) is None
            or _base_extractor(adapter) is None
        ):
            return None
        offer = Completion(
            dict(stage),
            route,
            weakref.ref(runner),
            weakref.ref(adapter),
            weakref.ref(asyncio.current_task()),
            weakref.ref(event),
            owner,
            generation,
            key,
            physical,
            response,
            monotonic() + TTL_SECONDS,
        )
        offer.authority = weakref.ref(authority)
        offer.live = weakref.ref(authority.sessions[owner[0]])
        offer.epoch = authority.epoch
        offer.instance_id = authority.instance_id
        offer.authority_profile = authority.profile_id
        offer.db = weakref.ref(authority.db)
        offer.admission_id = row["admission_id"]
        offer.app_id = adapter._app_id
        offer.profile_source = data["profile_source"]
        offer.turn_outcome = data.get("turn_outcome")
        from .profile_sources import (
            profile_from_hermes_home_path,
            legacy_profile_identity,
        )

        authority_profile = profile_from_hermes_home_path(str(authority.profile_id))
        if authority_profile is None:
            authority_value = str(authority.profile_id)
            if Path(authority_value).is_absolute():
                home = Path(authority_value).resolve()
                db_home = Path(authority.db.db_path).resolve().parent
                if db_home != home:
                    return None
                authority_profile = (
                    home.name
                    if home.parent.name == "profiles" and not home.name.startswith(".")
                    else "default"
                )
            else:
                authority_profile, authority_source = legacy_profile_identity(
                    authority_value, "authority"
                )
                if authority_source.startswith("sanitized_"):
                    authority_profile = None
        if (
            authority_profile != profile
            or not _row_route_matches(row, offer)
            or not _authority_current(offer, started=True)
        ):
            return None
        previous = getattr(event, _ATTR, None)
        if isinstance(previous, Completion):
            previous.abort()
        setattr(event, _ATTR, offer)
        _install(verified[0], finish)
        return offer
    except (TypeError, AttributeError):
        return None


def _abort(offer: Any) -> None:
    try:
        if isinstance(offer, Completion):
            offer.abort()
    except Exception:
        pass


def _trusted_move_code(
    caller: CodeType, settled: CodeType, busy: CodeType | None
) -> bool:
    return caller is settled or (busy is not None and caller is busy)


def _install(ingress: Any, finish: Finish) -> None:
    if (
        getattr(ingress._hand_over_turn_marker, "_hfc_completion_original", None)
        is not None
    ):
        return
    original_handoff, original_move, original_deliver = (
        ingress._hand_over_turn_marker,
        ingress._move_marker,
        ingress.deliver_response,
    )

    @wraps(original_handoff)
    async def handoff(authority: Any, admission_id: str, event: Any) -> Any:
        offer = None
        valid = False
        try:
            offer = getattr(event, _ATTR, None)
            if isinstance(offer, Completion):
                valid = (
                    offer.fresh()
                    and offer.state == "prepared"
                    and offer.source_event() is event
                    and offer.producer() is asyncio.current_task()
                    and offer.runner() is authority.runner
                    and authority.executing.get(offer.authority_owner[0])
                    == offer.authority_owner[1]
                    and admission_id == offer.admission_id
                    and _authority_current(offer, started=True)
                )
        except Exception:
            _abort(offer)
        # The original native function is outside display-error handling. Its
        # return and exception (including cancellation) remain authoritative.
        try:
            result = await original_handoff(authority, admission_id, event)
        except BaseException:
            _abort(offer)
            raise
        try:
            if isinstance(offer, Completion):
                if not valid:
                    offer.abort()
                    return result
                target = getattr(authority, "marker_adopters", {}).get(admission_id)
                delivery = getattr(authority, "waiter_replies", {}).get(admission_id)
                if target is None:
                    target = event
                    delivery = authority.pending_deliveries.get(admission_id)
                if (
                    offer.fresh()
                    and _authority_current(offer, started=True)
                    and isinstance(delivery, tuple)
                    and len(delivery) == 5
                    and delivery[0] is offer.adapter()
                    and delivery[1] is event
                    and delivery[2] == offer.session_key
                    and delivery[3] == offer.native_response
                    and _route(target, offer.route[0]) == offer.route
                ):
                    offer.admission_id = admission_id
                    offer.authority = weakref.ref(authority)
                    offer.target = weakref.ref(target)
                    offer.state = "offered"
                    setattr(target, _ATTR, offer)
                else:
                    offer.abort()
        except Exception:
            _abort(offer)
        return result

    @wraps(original_move)
    def move(source: Any, target: Any) -> Any:
        result = original_move(source, target)
        offer = None
        try:
            offer = getattr(source, _ATTR, None)
            if isinstance(offer, Completion) and offer.state == "offered":
                frame = inspect.currentframe()
                caller = frame.f_back if frame is not None else None
                try:
                    busy_code = next(
                        (
                            v
                            for v in ingress.dispatch_shared_busy.__code__.co_consts
                            if isinstance(v, CodeType) and v.co_name == "observe"
                        ),
                        None,
                    )
                    settled_code = ingress.deliver_settled.__code__
                    trusted = caller is not None and _trusted_move_code(
                        caller.f_code, settled_code, busy_code
                    )
                    if caller is not None and caller.f_code is settled_code:
                        trusted = (
                            trusted
                            and caller.f_locals.get("authority") is offer.authority()
                            and caller.f_locals.get("admission_id")
                            == offer.admission_id
                        )
                finally:
                    del caller, frame
                if (
                    trusted
                    and offer.fresh()
                    and offer.target() is source
                    and _route(target, offer.route[0]) == offer.route
                    and _authority_current(offer, started=False)
                ):
                    setattr(source, _ATTR, None)
                    setattr(target, _ATTR, offer)
                    offer.target = weakref.ref(target)
                else:
                    offer.abort()
        except Exception:
            _abort(offer)
        return result

    @wraps(original_deliver)
    async def deliver(adapter: Any, event: Any, session_key: str, response: Any) -> Any:
        ok = False
        try:
            result = await original_deliver(adapter, event, session_key, response)
            ok = True
            return result
        finally:
            try:
                await finish(event, ok)
            except Exception:
                pass
            try:
                _abort(getattr(event, _ATTR, None))
                setattr(event, _ATTR, None)
            except Exception:
                pass

    for wrapper, original in (
        (handoff, original_handoff),
        (move, original_move),
        (deliver, original_deliver),
    ):
        wrapper.__dict__["_hfc_completion_original"] = original
    ingress._hand_over_turn_marker, ingress._move_marker, ingress.deliver_response = (
        handoff,
        move,
        deliver,
    )


def consume_extracted(values: dict[str, Any], profile: str) -> dict[str, Any] | None:
    """Consume only after the real extractor finished; no native ACK is granted here."""
    event = values.get("event")
    offer = getattr(event, _ATTR, None)
    if not isinstance(offer, Completion):
        return None
    if (
        not offer.fresh()
        or offer.state != "offered"
        or offer.target() is not event
        or _route(event, profile) != offer.route
        or values.get("self") is not offer.adapter()
        or values.get("session_key") != offer.session_key
        or values.get("pre_extract") != offer.native_response
        or _verified() is None
        or not _authority_current(offer, started=False)
        or not _on_stack(_base_extractor(offer.adapter()))
    ):
        offer.abort()
        setattr(event, _ATTR, None)
        return None
    stage = dict(offer.stage)
    stage["task_id"] = id(asyncio.current_task())
    stage["base_context"] = {
        name: list(values[name]) for name in ("images", "local_files", "media_files")
    }
    stage["_admission_event"] = event
    stage["_admission_text"] = values.get("text_content")
    offer.abort()
    offer.state = "consumed"
    stage["_admission_claim"] = offer
    setattr(event, _ATTR, None)
    return stage


def valid_consumed(stage: dict[str, Any]) -> bool:
    """Recheck after TTS/ledger awaits, before completion submission or suppression."""
    offer = stage.get("_admission_claim")
    event = stage.get("_admission_event")
    return (
        isinstance(offer, Completion)
        and offer.state == "consumed"
        and monotonic() < offer.expires
        and offer.target() is event
        and _route(event, offer.route[0]) == offer.route
        and _verified() is not None
        and _base_extractor(offer.adapter()) is not None
        and _authority_current(offer, started=False)
    )


def unsupported_reason() -> str:
    """Fixed diagnostic categories only; never return filenames or identity values."""
    for name, digest in SOURCE_HASHES.items():
        try:
            filename = getattr(sys.modules.get(name), "__file__", None)
            if not isinstance(filename, str):
                return "unsupported-source"
            path = Path(filename)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                return "unsupported-source"
            if sha256(path.read_bytes()).hexdigest() != digest:
                return "unsupported-source"
        except OSError:
            return "unsupported-source"
    return "unsupported-loaded-topology"


def extractor_available(adapter: Any) -> bool:
    return _base_extractor(adapter) is not None
