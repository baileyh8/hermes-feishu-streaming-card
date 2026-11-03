"""Pinned producer code provenance; frame and native execution are explicit test ports."""
import gzip
import hashlib
import json
from pathlib import Path
from types import FunctionType, ModuleType, SimpleNamespace
import sys

import pytest

from hermes_feishu_card import native_rate_scope as scope
from hermes_feishu_card.admission_completion import _source_code
from hermes_feishu_card.install import patcher, rate_scope


@pytest.fixture(params=sorted(json.loads((Path(__file__).parents[1] / 'fixtures/native_rate_scope.json').read_text())['commits']))
def producer(tmp_path, monkeypatch, request):
    fixture = Path(__file__).parents[1] / 'fixtures/native_rate_scope.json'
    manifest = json.loads(fixture.read_text())
    archive = fixture.with_suffix('.json.gz').read_bytes()
    assert hashlib.sha256(archive).hexdigest() == manifest['archive_sha256']
    files = json.loads(gzip.decompress(archive))[request.param]
    assert {n: hashlib.sha256(raw.encode()).hexdigest() for n, raw in files.items()} == manifest['files'][request.param]
    modules = {}
    functions = {
        'agent.turn_context': ('export_current_turn_boundary', 'reanchor_current_turn_user_idx',
                               '_bind_turn_identity', 'build_turn_context'),
        'agent.conversation_loop': ('run_conversation', '_run_conversation_turn'),
        'agent.turn_finalizer': ('finalize_turn',),
        'agent.turn_facade': ('TurnFacadeMixin.run_conversation',),
        'gateway.run_turn_runner': ('TurnRunner._run_conversation_with_approval', 'TurnRunner.run_sync'),
    }
    if 'gateway/session_cli_turn.py' in files:
        functions['gateway.session_cli_turn'] = ('run_classic_cli_turn', 'classic_cli_turn', 'take_cli_result_fields')
        functions['gateway.run_turn_runner'] += ('TurnRunner._run_sync_scoped',)
    for name, raw in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == 'gateway/run_turn_runner.py':
            patched = patcher.apply_gateway_fragment(raw, name)
            assert rate_scope.BEGIN in patched and rate_scope.CAPTURE_BEGIN in patched
            assert patcher.remove_patch(patched) == raw
            assert patcher.apply_gateway_fragment(patched, name) == patched
            raw = patched
        path.write_bytes(raw.encode())
        module_name = name[:-3].replace('/', '.')
        if module_name not in functions:
            continue
        module = ModuleType(module_name)
        module.__file__ = str(path)
        for qualified in functions[module_name]:
            owner, _, function_name = qualified.rpartition('.')
            code = _source_code(raw, str(path), function_name, owner or None)
            function = FunctionType(code, vars(module), function_name)
            if owner:
                if not hasattr(module, owner):
                    setattr(module, owner, type(owner, (), {}))
                setattr(getattr(module, owner), function_name, function)
            else:
                setattr(module, function_name, function)
        monkeypatch.setitem(sys.modules, module_name, module)
        modules[module_name] = module
    from hermes_feishu_card.install.legacy_observer import available
    assert available(tmp_path)
    modules['agent.conversation_loop'].build_turn_context = modules['agent.turn_context'].build_turn_context
    modules['agent.conversation_loop'].finalize_turn = modules['agent.turn_finalizer'].finalize_turn
    agent = modules['agent.turn_facade'].TurnFacadeMixin()
    ctx = SimpleNamespace(source=SimpleNamespace(platform='feishu'), agent_holder=[agent])
    runner_module = modules['gateway.run_turn_runner']
    runner = runner_module.TurnRunner()
    runner._ctx = ctx
    call = agent.run_conversation
    values = dict(self=runner, ctx=ctx, agent=agent, _hfc_rate_call=call)
    frame = SimpleNamespace(f_code=runner._run_conversation_with_approval.__func__.__code__,
                            f_globals=vars(runner_module), f_locals=values)
    return SimpleNamespace(root=tmp_path, modules=modules, values=values, frame=frame,
                           call=call, files=files, version=request.param)


def test_actual_native_bound_method_and_source_cohort(producer):
    assert scope._verified(producer.root, producer.frame, producer.call)


@pytest.mark.parametrize('mutation', ['facade', 'bind_identity', 'build_alias', 'globals', 'holder', 'source'])
def test_same_source_and_identity_drift_rejected(producer, mutation, monkeypatch):
    assert scope._verified(producer.root, producer.frame, producer.call)
    if mutation == 'facade':
        producer.call = lambda *a, **k: {}
        producer.values['_hfc_rate_call'] = producer.call
    elif mutation == 'bind_identity':
        producer.modules['agent.turn_context']._bind_turn_identity = lambda *a, **k: 'old'
    elif mutation == 'build_alias':
        producer.modules['agent.conversation_loop'].build_turn_context = lambda *a, **k: None
    elif mutation == 'globals':
        producer.frame.f_globals = dict(producer.frame.f_globals)
    elif mutation == 'holder':
        producer.values['ctx'].agent_holder = [object()]
    else:
        path = producer.root / 'agent/turn_context.py'
        path.write_text(path.read_text() + '\n# drift\n')
    assert not scope._verified(producer.root, producer.frame, producer.call)


def _ports(monkeypatch, result, *, verified=True):
    calls = []
    ctx = SimpleNamespace(source=SimpleNamespace(platform='feishu'))
    values = dict(ctx=ctx, agent=object(), self=object())
    def native(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    values['_hfc_rate_call'] = native
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename='/synthetic/gateway/run_turn_runner.py'), f_locals=values)
    monkeypatch.setattr(scope.inspect, 'currentframe', lambda: SimpleNamespace(f_back=frame))
    monkeypatch.setattr(scope, '_verified', lambda *a, **k: verified)
    return calls, native, values, frame


def test_raw_scope_projects_once_without_agent_fields(monkeypatch):
    raw = {'session_id': 'native-session', 'turn_id': 'native-turn', 'api_calls': 1}
    calls, native, values, frame = _ports(monkeypatch, raw)
    assert scope.observe_call(native, values, 'prompt', marker=1) is raw
    assert calls == [(('prompt',), {'marker': 1})]
    json.dumps(raw)  # Internal scope must not create a cycle or unserializable native envelope.
    common = {'session_id': 'rotated-session'}
    values.update(result=raw, common=common)
    values['agent'] = object()  # Projection never re-reads a reusable Agent.
    scope.project(values)
    assert common[scope._FIELD] == {'session_id': 'native-session', 'turn_id': 'native-turn'}
    assert scope._FIELD not in raw
    assert common['session_id'] == 'rotated-session'


def test_copied_result_cannot_borrow_scope(monkeypatch):
    raw = {'session_id': 'native-session', 'turn_id': 'native-turn'}
    calls, native, values, frame = _ports(monkeypatch, raw)
    scope.observe_call(native, values)
    other = dict(raw)
    values.update(result=other, common={})
    scope.project(values)
    assert scope._FIELD not in values['common']


@pytest.mark.parametrize('verified', [False, True])
def test_native_error_propagates_once(monkeypatch, verified):
    calls, native, values, frame = _ports(monkeypatch, {}, verified=verified)
    error = RuntimeError('synthetic native failure')
    def failing(*a, **k):
        calls.append('called')
        raise error
    values['_hfc_rate_call'] = failing
    with pytest.raises(RuntimeError) as raised:
        scope.observe_call(failing, values)
    assert raised.value is error and calls == ['called']


def test_unknown_producer_never_reuses_input_scope(monkeypatch):
    raw = {'session_id': 'native-session', 'turn_id': 'native-turn', scope._FIELD: {'old': True}}
    calls, native, values, frame = _ports(monkeypatch, raw, verified=False)
    assert scope.observe_call(native, values) is raw
    assert len(calls) == 1 and scope._FIELD not in raw


def test_cold_native_dependencies_are_deferred_but_required_after_call(producer, monkeypatch):
    names = ('agent.conversation_loop', 'agent.turn_context', 'agent.turn_finalizer')
    saved = {name: sys.modules[name] for name in names}
    for name in names:
        monkeypatch.delitem(sys.modules, name)
    assert scope._verified(producer.root, producer.frame, producer.call, defer_unloaded=True)
    assert not scope._verified(producer.root, producer.frame, producer.call)
    for name, module in saved.items():
        monkeypatch.setitem(sys.modules, name, module)
    assert scope._verified(producer.root, producer.frame, producer.call)


def test_post_call_loaded_proof_precedes_first_scope(monkeypatch):
    raw = {'session_id': 'native-session', 'turn_id': 'native-turn'}
    calls, native, values, frame = _ports(monkeypatch, raw)
    checks = []
    def verify(*a, defer_unloaded=False):
        checks.append((defer_unloaded, len(calls)))
        return True
    monkeypatch.setattr(scope, '_verified', verify)
    assert scope.observe_call(native, values) is raw
    assert checks == [(True, 0), (False, 1)]
    assert type(raw[scope._FIELD]) is scope._Scope


def test_rejected_loaded_constants_are_not_retained_by_expected_source_cache(producer):
    import gc
    import weakref
    class NativeObject:
        pass
    target = producer.modules['agent.turn_context'].export_current_turn_boundary
    original = target.__code__
    value = NativeObject()
    reference = weakref.ref(value)
    target.__code__ = original.replace(co_consts=(None, value))
    assert not scope._verified(producer.root, producer.frame, producer.call)
    target.__code__ = original
    del value
    gc.collect()
    assert reference() is None
