import os
import subprocess
import sys
from dataclasses import replace

import pytest

from hermes_feishu_card import process_identity as identity
from hermes_feishu_card.runtime_control import RuntimeControlEvent, RuntimeControlValidationError
from hermes_feishu_card.runtime_control import RuntimeIntegritySupervisor, RUNTIME_HOOK_GENERATION


def event(role='gateway', runtime='gateway-process-001'):
    return RuntimeControlEvent(schema_version='3', event='runtime.hello', runtime_id=runtime,
        sequence=1, created_at=1., hook_generation=RUNTIME_HOOK_GENERATION, package_version='test',
        active_sessions=0, admission_draining=False, active_work_count_complete=True,
        drain_home_verified=role=='gateway', runtime_role=role,
        target_identity='a'*64 if role=='gateway' else '')


def supervisor():
    return RuntimeIntegritySupervisor(mode='safe', expected_package_version='test',
        expected_target_identity='a'*64, now=lambda:0.)


def test_same_host_dead_process_is_released_without_goodbye(monkeypatch):
    monkeypatch.setattr(identity, 'host_identity', lambda:'a'*64)
    def kill(pid, signal):
        if pid == 12345: raise ProcessLookupError()
    monkeypatch.setattr(identity.os, 'kill', kill)
    s = supervisor();s.record(event())
    observer = replace(event('observer','desktop-process-0001'),
        process_identity={'pid':12345,'host':'a'*64,'start':'b'*64})
    s.record(observer)
    assert s.snapshot()['observer_count'] == 0
    assert s.snapshot()['active_work_count_complete']


def test_remote_namespace_and_permission_errors_do_not_prove_exit(monkeypatch):
    monkeypatch.setattr(identity, 'host_identity', lambda:'a'*64)
    def denied(pid, signal): raise PermissionError()
    monkeypatch.setattr(identity.os, 'kill', denied)
    monitor = identity.LocalProcessMonitor()
    assert monitor.state({'pid':12,'host':'b'*64,'start':'c'*64},0)=='unknown'
    assert monitor.state({'pid':12,'host':'a'*64,'start':'c'*64},0)=='unknown'


def test_stale_reused_pid_has_different_birth_identity(monkeypatch):
    monkeypatch.setattr(identity, 'host_identity', lambda:'a'*64)
    monkeypatch.setattr(identity.os, 'kill', lambda *_:None)
    monkeypatch.setattr(identity, '_start_identity', lambda _:'c'*64)
    monitor = identity.LocalProcessMonitor()
    assert monitor.state({'pid':12,'host':'a'*64,'start':'b'*64},0,check_start=True)=='exited'


def test_fresh_observation_does_not_spawn_ps(monkeypatch):
    monkeypatch.setattr(identity, 'host_identity', lambda:'a'*64)
    monkeypatch.setattr(identity.os, 'kill', lambda *_:None)
    def prohibited(_): raise AssertionError('no ps for fresh heartbeat')
    monkeypatch.setattr(identity, '_start_identity', prohibited)
    assert identity.LocalProcessMonitor().state({'pid':12,'host':'a'*64,'start':'b'*64},0)=='alive'


def test_real_child_death_is_detected():
    host = identity.host_identity()
    if host is None: pytest.skip('local process identity unavailable')
    child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        start = identity._start_identity(child.pid)
        assert start
        proof = {'pid':child.pid,'host':host,'start':start}
        monitor = identity.LocalProcessMonitor()
        assert monitor.state(proof,0,check_start=True)=='alive'
    finally:
        child.terminate();child.wait(timeout=5)
    assert monitor.state(proof,1)=='exited'


def test_optional_identity_roundtrip_and_invalid_shapes():
    payload = event().to_dict()
    payload['process_identity']={'pid':12,'host':'a'*64,'start':'b'*64}
    assert RuntimeControlEvent.from_dict(payload).to_dict()==payload
    for bad in [None, {}, {'pid':True,'host':'a'*64,'start':'b'*64},
                {'pid':0,'host':'a'*64,'start':'b'*64},
                {'pid':1,'host':[],'start':'b'*64},
                {'pid':1,'host':'a'*64,'start':'bad'}]:
        payload['process_identity']=bad
        with pytest.raises(RuntimeControlValidationError):RuntimeControlEvent.from_dict(payload)
