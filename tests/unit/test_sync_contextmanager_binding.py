"""Loaded-binding tests only; real pinned-source loading is integration coverage."""
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from types import FunctionType, ModuleType
from typing import Any
import sys

import pytest

from hermes_feishu_card import admission_topology_0226 as topology


MEDIA = "gateway.session_ingress_media"


def _cell(value):
    closure = (lambda: value).__closure__
    assert closure is not None
    return closure[0]


def _clone(function, *, globals_=None, closure=None):
    clone = FunctionType(
        function.__code__,
        function.__globals__ if globals_ is None else globals_,
        function.__name__,
        function.__defaults__,
        function.__closure__ if closure is None else closure,
    )
    clone.__kwdefaults__ = function.__kwdefaults__
    clone.__dict__.update(function.__dict__)
    return clone


@pytest.fixture
def bindings(tmp_path, monkeypatch):
    # Synthetic sources isolate verified's loaded-binding gate. Source-cohort
    # hash/render validation is intentionally not claimed by this fixture.
    sources = {
        "gateway.session_ingress": b"""from contextlib import asynccontextmanager
@asynccontextmanager
async def _admission_scope(value=None):
    yield value
""",
        "gateway.session_authority": b"",
        "gateway.session_producer_lifecycle": b"""from contextlib import asynccontextmanager
@asynccontextmanager
async def producer_turn(value=None):
    yield value

def close_producer_turn(): pass
def mark_producer_admission(): pass
def drop_producer_turn(): pass
""",
        "gateway.platforms.base": b"",
        "gateway.platforms.event": b"",
        "gateway.platforms.base_post_delivery": b"def run_post_delivery_callback(): pass\n",
        MEDIA: b"""from contextlib import contextmanager
@contextmanager
def capture_lease(authority=None):
    yield authority

def unregistered(authority=None):
    yield authority
""",
    }
    modules = {}
    for name, raw in sources.items():
        module = ModuleType(name)
        module.__file__ = str(tmp_path / (name.replace(".", "/") + ".py"))
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), vars(module))
        modules[name] = module
        monkeypatch.setitem(sys.modules, name, module)
    ingress = modules["gateway.session_ingress"]
    authority = modules["gateway.session_authority"]
    lifecycle = modules["gateway.session_producer_lifecycle"]
    base = modules["gateway.platforms.base"]
    event = modules["gateway.platforms.event"]
    event.ProcessingOutcome = type("ProcessingOutcome", (), {})
    ingress.ProcessingOutcome = lifecycle.ProcessingOutcome = event.ProcessingOutcome
    ingress.producer_turn = lifecycle.producer_turn
    ingress.close_producer_turn = lifecycle.close_producer_turn
    authority.mark_producer_admission = lifecycle.mark_producer_admission
    authority.drop_producer_turn = lifecycle.drop_producer_turn
    base.run_post_delivery_callback = modules[
        "gateway.platforms.base_post_delivery"
    ].run_post_delivery_callback
    base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS = 30.0
    functions: dict[str, tuple[str, ...]] = {name: () for name in sources}
    functions["gateway.session_ingress"] = ("_admission_scope",)
    functions["gateway.session_producer_lifecycle"] = ("producer_turn",)
    functions[MEDIA] = ("capture_lease", "unregistered")
    monkeypatch.setattr(topology, "source_bytes_0230", lambda root: (sources, sources))

    def checked():
        return topology.verified(
            tmp_path, ingress, authority,
            expected=topology.SOURCE_HASHES_0230,
            functions=functions,
            decorated=topology.DECORATED_0230,
        )

    assert checked(), "healthy sync and async wrappers must pass before fault injection"
    return modules[MEDIA], checked


def test_sync_contextmanager_is_explicit_and_retains_async_registration(bindings):
    media, checked = bindings
    assert topology.SYNC_DECORATED_0230 == frozenset({(MEDIA, "capture_lease")})
    assert (MEDIA, "capture_lease") not in topology.DECORATED_0230
    assert topology._verified_contextmanager(
        media.capture_lease, synchronous=True
    ) is media.capture_lease.__wrapped__
    assert checked()


@pytest.mark.parametrize("fault", [
    "raw_generator", "custom_wraps", "nested_wrapper", "missing_wrapped",
    "different_code", "copied_globals", "defaults", "kwdefaults",
    "wrong_closure", "empty_cell", "forged_wrapped", "callable_object",
    "regular_function", "async_generator", "async_wrapper",
])
def test_sync_wrapper_rejects_impostors(bindings, fault):
    media, checked = bindings
    original = media.capture_lease
    generator = original.__wrapped__
    bad: Any = contextmanager(generator)
    if fault == "raw_generator":
        bad = generator
    elif fault == "custom_wraps":
        @wraps(generator)
        def custom(*args, **kwargs):
            return original(*args, **kwargs)
        bad = custom
    elif fault == "nested_wrapper":
        bad = contextmanager(original)
    elif fault == "missing_wrapped":
        del bad.__wrapped__
    elif fault == "different_code":
        bad.__code__ = bad.__code__.replace(co_name="impostor")
    elif fault == "copied_globals":
        bad = _clone(bad, globals_=dict(bad.__globals__))
    elif fault == "defaults":
        bad.__defaults__ = ()
    elif fault == "kwdefaults":
        bad.__kwdefaults__ = {}
    elif fault == "wrong_closure":
        bad = _clone(bad, closure=(_cell(object()),))
    elif fault == "empty_cell":
        cell = _cell(generator)
        del cell.cell_contents
        bad = _clone(bad, closure=(cell,))
    elif fault == "forged_wrapped":
        bad.__wrapped__ = _clone(generator)
    elif fault == "callable_object":
        class Pretender:
            def __call__(self, *args, **kwargs):
                return original(*args, **kwargs)
        bad = Pretender()
        bad.__dict__.update({
            attr: getattr(original, attr) for attr in (
                "__code__", "__globals__", "__defaults__", "__kwdefaults__",
                "__closure__", "__wrapped__",
            )
        })
    elif fault == "regular_function":
        bad = contextmanager(lambda authority=None: authority)
    elif fault in {"async_generator", "async_wrapper"}:
        async def async_generator(authority=None):
            yield authority
        bad = (contextmanager(async_generator) if fault == "async_generator"
               else asynccontextmanager(async_generator))
    media.capture_lease = bad
    assert not checked(), fault
    media.capture_lease = original
    assert checked(), "rejection must not poison a subsequent valid check"


@pytest.mark.parametrize("fault", [
    "defaults", "kwdefaults", "shape", "globals", "filename",
])
def test_sync_unwrapped_generator_still_has_source_checks(bindings, fault):
    media, checked = bindings
    original = media.capture_lease
    generator = _clone(original.__wrapped__)
    if fault == "defaults":
        generator.__defaults__ = (object(),)
    elif fault == "kwdefaults":
        generator.__kwdefaults__ = {"unexpected": None}
    elif fault == "shape":
        generator.__code__ = generator.__code__.replace(
            co_consts=(*generator.__code__.co_consts, "drift")
        )
    elif fault == "globals":
        generator = _clone(generator, globals_=dict(generator.__globals__))
    elif fault == "filename":
        generator.__code__ = generator.__code__.replace(co_filename="elsewhere.py")
    media.capture_lease = contextmanager(generator)
    # The wrapper itself is genuine; rejection must come from original-source
    # defaults/globals/filename/shape validation after the single unwrap.
    assert topology._verified_contextmanager(
        media.capture_lease, synchronous=True
    ) is generator
    assert not checked(), fault
    media.capture_lease = original
    assert checked()


def test_unregistered_contextmanager_is_not_unwrapped(bindings):
    media, checked = bindings
    original = media.unregistered
    media.unregistered = contextmanager(original)
    assert not checked()
    media.unregistered = original
    assert checked()


@pytest.mark.parametrize("synchronous", [False, True])
def test_wrapper_generator_kinds_are_not_interchangeable(synchronous):
    def sync_generator():
        yield None

    async def async_generator():
        yield None

    correct = (contextmanager(sync_generator) if synchronous
               else asynccontextmanager(async_generator))
    wrong = (asynccontextmanager(async_generator) if synchronous
             else contextmanager(sync_generator))
    assert topology._verified_contextmanager(
        correct, synchronous=synchronous
    ) is (sync_generator if synchronous else async_generator)
    assert topology._verified_contextmanager(wrong, synchronous=synchronous) is None
