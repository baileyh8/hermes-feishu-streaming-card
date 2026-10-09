from hermes_feishu_card.runtime_control import RuntimeControlEvent, RuntimeIntegritySupervisor, RUNTIME_HOOK_GENERATION


def event(role='gateway', runtime='gateway-runtime-0001', seq=1, kind='runtime.hello', target='a'*64, active=0, complete=True):
    return RuntimeControlEvent.from_dict(dict(schema_version='3', event=kind, runtime_id=runtime,
        sequence=seq, created_at=float(seq), hook_generation=RUNTIME_HOOK_GENERATION,
        package_version='test', active_sessions=active, admission_draining=False,
        active_work_count_complete=complete, drain_home_verified=role=='gateway',
        runtime_role=role, target_identity=target if role=='gateway' else ''))


def supervisor(clock=None):
    return RuntimeIntegritySupervisor(mode='safe', expected_package_version='test',
        expected_target_identity='a'*64, now=lambda: clock[0] if clock else 0)


def test_observer_does_not_replace_gateway_or_clear_restart():
    s=supervisor();s.record(event());s.mark_restart_required()
    before=s.snapshot()['runtime_id_hash']
    assert s.record(event('observer','desktop-runtime-0001'))
    q=s.snapshot();assert q['runtime_id_hash']==before and q['restart_required']
    assert q['observer_count']==1


def test_observer_counts_are_kept_without_poisoning_idle_gateway():
    s=supervisor();s.record(event());s.record(event('observer','desktop-runtime-0001'))
    assert s.snapshot()['active_work_count_complete'] is True
    s.record(event('observer','desktop-runtime-0001',seq=2,active=2))
    assert s.snapshot()['active_sessions']==2
    assert s.snapshot()['observer_active_sessions']==2
    s.record(event('observer','desktop-runtime-0001',seq=3,complete=False))
    assert s.snapshot()['active_work_count_complete'] is False


def test_conflicting_gateway_cannot_supply_restart_proof_until_old_stops():
    s=supervisor();s.record(event());s.mark_restart_required()
    s.record(event(runtime='gateway-runtime-0002'))
    assert s.snapshot()['restart_required']
    assert s.snapshot()['owner_conflict']
    s.record(event(seq=2,kind='runtime.goodbye'))
    assert not s.snapshot()['restart_required']
    owner=s.snapshot()['runtime_id_hash']
    assert not s.record(event(seq=1))
    assert s.snapshot()['runtime_id_hash']==owner


def test_wrong_target_and_observer_only_never_establish_gateway_readiness():
    s=supervisor();assert not s.record(event(target='b'*64))
    s.record(event('observer','desktop-runtime-0001'))
    assert s.snapshot()['status']!='ready'


def test_stale_gateway_cannot_be_kept_alive_by_observer():
    clock=[0.];s=supervisor(clock);s.record(event());clock[0]=46
    s.record(event('observer','desktop-runtime-0001'))
    assert s.snapshot()['reason']=='runtime_heartbeat_stale'


def test_stale_active_observer_remains_unsafe_and_goodbye_releases_it():
    clock=[0.];s=supervisor(clock);s.record(event());s.record(event('observer','desktop-runtime-0001',active=1))
    clock[0]=46;s.record(event(seq=2,kind='runtime.heartbeat'))
    assert not s.snapshot()['active_work_count_complete']
    s.record(event('observer','desktop-runtime-0001',seq=2,kind='runtime.goodbye'))
    assert s.snapshot()['active_work_count_complete']


def test_observer_sequence_replay_is_rejected_across_gateway_updates():
    s=supervisor();s.record(event());observer=event('observer','desktop-runtime-0001',seq=5)
    assert s.record(observer);s.record(event(seq=2,kind='runtime.heartbeat'))
    assert not s.record(observer)


def test_legacy_heartbeat_is_diagnostic_only_and_cannot_clear_restart():
    s=supervisor();s.record(event());s.mark_restart_required()
    data=event(runtime='legacy-runtime-0001').to_dict()
    data['schema_version']='2';data.pop('runtime_role');data.pop('target_identity')
    assert s.record(RuntimeControlEvent.from_dict(data))
    assert s.snapshot()['restart_required']
    assert not s.snapshot()['active_work_count_complete']


def test_retired_gateway_cannot_take_ownership_back():
    clock=[0.];s=supervisor(clock);s.record(event());clock[0]=46
    s.record(event(runtime='gateway-runtime-0002'))
    owner=s.snapshot()['runtime_id_hash']
    s.record(event(seq=2,kind='runtime.heartbeat'))
    assert s.snapshot()['runtime_id_hash']==owner
    assert s.snapshot()['owner_conflict']
    assert not s.snapshot()['active_work_count_complete']


def test_capacity_overflow_blocks_maintenance_without_unbounded_state():
    s=supervisor();s.record(event())
    for i in range(80):s.record(event('observer',f'observer-runtime-{i:04d}'))
    assert len(s._owners.records)==64
    assert s.snapshot()['owner_capacity_exceeded']
    assert not s.snapshot()['active_work_count_complete']


def test_emitter_reports_role_transition_and_authenticated_goodbye(monkeypatch, tmp_path):
    import json,sys
    from types import SimpleNamespace
    from hermes_feishu_card.runtime_control import RuntimeControlEmitter
    from hermes_feishu_card.runtime_ownership import runtime_target_identity
    values=[(0,False,True,False,True,False)]
    rows=[]
    def post(url,body,headers,timeout):
        rows.append(json.loads(body));return True
    e=RuntimeControlEmitter(event_url='http://127.0.0.1/events',hook_generation=RUNTIME_HOOK_GENERATION,
        package_version='test',runtime_snapshot_provider=lambda:values[0],poster=post,secret_reader=lambda:b'x'*32)
    assert e.emit_once('runtime.hello')
    assert rows[-1]['runtime_role']=='observer' and rows[-1]['active_work_count_complete']
    monkeypatch.setitem(sys.modules,'gateway.run',SimpleNamespace(__file__=str(tmp_path/'gateway/run.py')))
    values[0]=(0,True,False,True,True,True)
    assert e.emit_once('runtime.heartbeat')
    assert rows[-1]['event']=='runtime.hello' and rows[-1]['runtime_role']=='gateway'
    assert rows[-1]['target_identity']==runtime_target_identity(tmp_path)
    values.clear()  # Goodbye cannot read released providers or acquire the provider lock.
    assert e.emit_once('runtime.goodbye')
    assert rows[-1]['event']=='runtime.goodbye' and rows[-1]['runtime_role']=='gateway'


def test_failed_owner_hello_is_retried_instead_of_becoming_heartbeat():
    import json
    from hermes_feishu_card.runtime_control import RuntimeControlEmitter
    rows=[]
    def post(url,body,headers,timeout):
        rows.append(json.loads(body));return len(rows)>1
    e=RuntimeControlEmitter(event_url='http://127.0.0.1/events',hook_generation=RUNTIME_HOOK_GENERATION,
        package_version='test',runtime_snapshot_provider=lambda:(0,False,True,False,True,False),
        poster=post,secret_reader=lambda:b'x'*32)
    assert not e.emit_once('runtime.hello')
    assert e.emit_once('runtime.heartbeat')
    assert [r['event'] for r in rows]==['runtime.hello','runtime.hello']


def test_drain_requires_two_advancing_samples_from_same_gateway():
    from hermes_feishu_card.maintenance_update import _wait_for_drain
    clock = [0.0]
    def sleep(seconds):
        clock[0] += seconds
    def health():
        index = int(clock[0])
        return dict(maintenance_active_sessions=0, gateway_active_sessions=0,
            maintenance_drain=dict(active=True, valid=True),
            readiness=dict(status='ready', runtime_id_hash=str(index % 2)*64,
                last_sequence=index+1, last_seen_age_seconds=0,
                admission_draining=True, active_work_count_complete=True,
                drain_home_verified=True))
    assert not _wait_for_drain(health, timeout_seconds=3, sleep=sleep, monotonic=lambda:clock[0])
    clock[0] = 0
    def stable():
        result = health()
        result['readiness']['runtime_id_hash'] = 'a'*64
        return result
    assert _wait_for_drain(stable, timeout_seconds=3, sleep=sleep, monotonic=lambda:clock[0])


def test_malformed_owner_fields_raise_validation_error():
    import pytest
    from hermes_feishu_card.runtime_control import RuntimeControlValidationError
    for key, value in [('runtime_role', []), ('runtime_role', {}), ('event', []),
                       ('schema_version', {}), ('target_identity', [])]:
        payload = event().to_dict()
        payload[key] = value
        with pytest.raises(RuntimeControlValidationError):
            RuntimeControlEvent.from_dict(payload)


def test_gateway_hard_exit_funnel_sends_goodbye_before_original_exit(monkeypatch):
    import sys
    from types import ModuleType
    from hermes_feishu_card import runtime_control as rc
    module = ModuleType('gateway.run')
    exec('def _exit_after_graceful_shutdown(exit_code):\n return exit_code\n', module.__dict__)
    monkeypatch.setitem(sys.modules, 'gateway.run', module)
    calls = []
    monkeypatch.setattr(rc, '_stop_runtime_control_before_process_exit', lambda: calls.append('goodbye'))
    assert rc._install_gateway_exit_notifier()
    wrapped = module._exit_after_graceful_shutdown
    assert rc._install_gateway_exit_notifier()
    assert module._exit_after_graceful_shutdown is wrapped
    assert wrapped(7) == 7 and calls == ['goodbye']
    def failed_cleanup():
        raise RuntimeError('cleanup unavailable')
    monkeypatch.setattr(rc, '_stop_runtime_control_before_process_exit', failed_cleanup)
    assert wrapped(8) == 8


def test_exit_notifier_rejects_unknown_exit_contract(monkeypatch):
    import sys
    from types import ModuleType
    from hermes_feishu_card import runtime_control as rc
    for definition in ['def _exit_after_graceful_shutdown(other): pass',
                       'def _exit_after_graceful_shutdown(exit_code, extra): pass',
                       'async def _exit_after_graceful_shutdown(exit_code): pass']:
        module = ModuleType('gateway.run');exec(definition, module.__dict__)
        original = module._exit_after_graceful_shutdown
        monkeypatch.setitem(sys.modules, 'gateway.run', module)
        assert not rc._install_gateway_exit_notifier()
        assert module._exit_after_graceful_shutdown is original


def test_process_exit_releases_snapshot_lock_before_join(monkeypatch):
    from threading import Event, Thread
    from hermes_feishu_card import runtime_control as rc
    stop, completed = Event(), Event()
    def worker():
        stop.wait()
        with rc._CONTROL_LOCK:
            completed.set()
    thread = Thread(target=worker)
    monkeypatch.setattr(rc, '_CONTROL_STOP', stop)
    monkeypatch.setattr(rc, '_CONTROL_THREAD', thread)
    monkeypatch.setattr(rc, '_CONTROL_STOPPING', False)
    thread.start()
    rc._stop_runtime_control_before_process_exit()
    assert completed.is_set() and not thread.is_alive()


def test_goodbye_is_delivered_even_when_gateway_uses_os_exit(tmp_path):
    import subprocess, sys, textwrap
    from pathlib import Path
    from hermes_feishu_card import runtime_control as rc
    package_root = str(Path(rc.__file__).parent.parent)
    output = tmp_path / 'exit-events.jsonl'
    child = textwrap.dedent('''
        import json, os, sys, threading
        from types import ModuleType
        from pathlib import Path
        sys.path.insert(0, sys.argv[2])
        from hermes_feishu_card import runtime_control as rc
        module = ModuleType('gateway.run')
        module.__file__ = str(Path(sys.argv[1]).parent / 'gateway/run.py')
        module.os = os
        exec('def _exit_after_graceful_shutdown(exit_code):\\n os._exit(exit_code)\\n', module.__dict__)
        sys.modules['gateway.run'] = module
        ready = threading.Event()
        def post(url, body, headers, timeout):
            row = json.loads(body)
            with open(sys.argv[1], 'a') as f:
                f.write(json.dumps(row) + '\\n')
            ready.set()
            return True
        emitter = rc.RuntimeControlEmitter(event_url='http://127.0.0.1:1/events',
            hook_generation=rc.RUNTIME_HOOK_GENERATION, package_version='test',
            secret_reader=lambda:b'r'*32, poster=post,
            runtime_snapshot_provider=lambda:(0,True,False,True,True,True))
        stop = threading.Event()
        thread = threading.Thread(target=emitter.run, args=(stop,60), daemon=True)
        rc._CONTROL_STOP, rc._CONTROL_THREAD = stop, thread
        thread.start()
        assert ready.wait(2)
        assert rc._install_gateway_exit_notifier()
        module._exit_after_graceful_shutdown(0)
    ''')
    subprocess.run([sys.executable, '-c', child, str(output), package_root], check=True, timeout=5)
    import json
    events = [json.loads(line) for line in output.read_text().splitlines()]
    assert [e['event'] for e in events] == ['runtime.hello', 'runtime.goodbye']
    assert events[0]['runtime_id'] == events[1]['runtime_id']
