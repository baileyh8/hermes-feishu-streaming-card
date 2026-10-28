"""HOME uninstall: retain owned evidence through PM uncertainty and retry."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install import (
    legacy_9b904, legacy_9b904_uninstall as uninstall, legacy_home_revocation,
    legacy_observer, plugin, pm_dependency, posix_transaction,
)


BEFORE = (b'_config_version: 52\nplugins:\n  enabled:\n'
          b'    - hermes-feishu-card\n    - hfc-runtime-dependency\n  disabled: []\n'
          b'  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n')
AFTER = (b'_config_version: 52\nplugins:\n  enabled:\n    - hfc-runtime-dependency\n'
         b'  disabled:\n    - hermes-feishu-card\n  entries:\n    hermes-feishu-card:\n'
         b'      allow_tool_override: false\n')


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip('secure directory-relative I/O is unavailable')
    root, home = tmp_path / 'checkout', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    config = home / 'config.yaml'
    config.write_bytes(BEFORE)
    generation = home / 'environment'
    python = generation / 'venv' / 'bin' / 'python'
    binding = plugin.HermesRuntimeBinding(root, python, python, 'sha256:' + 'a' * 64,
                                           home, config, root, root)
    monkeypatch.setattr(plugin, 'probe_plugin_entrypoint', lambda *_a, **_k: SimpleNamespace(status='verified'))
    monkeypatch.setattr(plugin, 'resolve_runtime_binding', lambda **_k: binding)
    monkeypatch.setattr(pm_dependency.Registration, '__post_init__', lambda _self: None)
    monkeypatch.setattr(pm_dependency.Registration, 'verify_adoption', lambda *_a: None)
    admission = pm_dependency.Registration(root, python, home, home / 'member', None, False,
                                           source_family=legacy_9b904.ADAPTER_ID)
    legacy_observer.prepare(binding, adoption=admission)
    receipt = legacy_observer.inspect(root, home=home)
    assert receipt is not None
    marker = root / legacy_observer.MARKER
    backup, journal = receipt[2].backup_path, receipt[2].journal_path
    member = home / 'plugins' / pm_dependency.NAME
    member.mkdir(parents=True)
    owner = {'schema': 3, 'version': '5.0.11', 'selection_added': False}
    monkeypatch.setattr(pm_dependency, '_owned', lambda _path: owner)
    facts, lock = home / 'facts.json', home / 'resolved.lock'
    facts.write_bytes(b'facts')
    lock.write_bytes(b'lock')
    facts_id, lock_id = (posix_transaction.read_path_snapshot(path, ValueError, 'changed') for path in (facts, lock))
    graph = legacy_9b904.GraphEvidence(facts, facts_id, (1, 2, 3, 4, 5),
                                       lock, lock_id, (1, 2, 3, 4, 5), {})
    files = {path: posix_transaction.read_path_snapshot(path, ValueError, 'changed')
             for path in (marker, backup, journal)}
    config_id = posix_transaction.read_path_snapshot(config, ValueError, 'changed')
    member_id = posix_transaction.read_directory_identity(member, ValueError, 'changed')
    home_id = posix_transaction.read_directory_identity(home, ValueError, 'changed')
    evidence = SimpleNamespace(
        graph=graph, marker_identity=files[marker], marker_files=files,
        config=legacy_9b904.ExistingConfig(BEFORE, sha256(BEFORE).hexdigest()),
        config_identity=config_id, old_member_identity=member_id, home_identity=home_id,
        generation=str(generation / 'venv'),
    )
    # PM facts/runtime calls are controlled here; ticket, receipt and bound filesystem I/O are real.
    monkeypatch.setattr(uninstall, '_capture', lambda *_a: (binding, owner, evidence))
    monkeypatch.setattr(uninstall, 'plan_disable', lambda _r, _p, _h, before: uninstall.DisablePlan(before, AFTER))
    monkeypatch.setattr(legacy_9b904, 'verify_before', lambda *_a, **_k: None)
    monkeypatch.setattr(legacy_9b904, 'verify_pm_graph', lambda *_a, **_k: graph)
    monkeypatch.setattr(legacy_9b904, 'inspect_unmanaged_environment', lambda *_a: object())
    monkeypatch.setattr(legacy_9b904, 'verify_unmanaged_environment', lambda *_a: None)
    monkeypatch.setattr(legacy_9b904, 'verify_runtime', lambda *_a, **_k: None)
    state = {'fault': None, 'mutations': 0}

    def verified_call(_root, _python, _home, request, _family):
        if request['operation'] == 'probe':
            return {'generation': evidence.generation, 'expected': sha256(config.read_bytes()).hexdigest()}
        assert request == {'operation': 'disable-hfc', 'name': legacy_home_revocation.PLUGIN_KEY,
                           'expected': sha256(BEFORE).hexdigest()}
        state['mutations'] += 1
        if state['fault'] == 'before':
            raise pm_dependency.PMAdmissionOutcomeUncertain('outcome uncertain')
        config.write_bytes(AFTER)
        if state['fault'] == 'after':
            raise pm_dependency.PMAdmissionOutcomeUncertain('outcome uncertain')
        return {}

    monkeypatch.setattr(pm_dependency, '_verified_call', verified_call)
    return SimpleNamespace(root=root, home=home, config=config, marker=marker,
                           backup=backup, journal=journal, state=state, evidence=evidence)


@pytest.mark.parametrize('failure', [None, 'before', 'after'])
def test_uninstall_retries_owned_pre_or_exact_post_and_retains_shared_evidence(transaction, failure):
    case = transaction
    preserved = {path: path.read_bytes() for path in (case.marker, case.backup, case.journal)}
    case.state['fault'] = failure
    ticket = legacy_home_revocation.ticket_path(case.root, case.home)
    if failure:
        with pytest.raises(pm_dependency.PMAdmissionOutcomeUncertain):
            uninstall.uninstall(case.root, home=case.home)
        assert json.loads(ticket.read_bytes())['phase'] == 'planned'
        assert case.config.read_bytes() == (BEFORE if failure == 'before' else AFTER)
        case.state['fault'] = None
    uninstall.uninstall(case.root, home=case.home)
    assert case.config.read_bytes() == AFTER
    assert legacy_home_revocation.inspect(case.root, case.home) == 'disabled'
    assert json.loads(ticket.read_bytes())['phase'] == 'completed'
    assert {path: path.read_bytes() for path in preserved} == preserved
    expected_mutations = 2 if failure == 'before' else 1
    assert case.state['mutations'] == expected_mutations
    snapshot = ticket.read_bytes()
    uninstall.uninstall(case.root, home=case.home)
    assert ticket.read_bytes() == snapshot
    assert case.state['mutations'] == expected_mutations


def test_uninstall_unknown_config_after_failure_retains_pending_ticket(transaction):
    case = transaction
    case.state['fault'] = 'before'
    with pytest.raises(pm_dependency.PMAdmissionOutcomeUncertain):
        uninstall.uninstall(case.root, home=case.home)
    ticket = legacy_home_revocation.ticket_path(case.root, case.home)
    pending = ticket.read_bytes()
    case.config.write_bytes(BEFORE + b'# another writer\n')
    case.state['fault'] = None
    with pytest.raises(ValueError, match='outcome is unknown'):
        uninstall.uninstall(case.root, home=case.home)
    assert ticket.read_bytes() == pending
    assert case.state['mutations'] == 1


def test_uninstall_retry_finishes_after_pm_success_but_final_check_fails(transaction, monkeypatch):
    case = transaction
    verify = uninstall._current_after
    monkeypatch.setattr(uninstall, '_current_after',
                        lambda *_a: (_ for _ in ()).throw(ValueError('PM graph unavailable')))
    with pytest.raises(ValueError, match='PM graph unavailable'):
        uninstall.uninstall(case.root, home=case.home)
    ticket = legacy_home_revocation.ticket_path(case.root, case.home)
    assert case.config.read_bytes() == AFTER
    assert json.loads(ticket.read_bytes())['phase'] == 'planned'
    assert case.state['mutations'] == 1
    monkeypatch.setattr(uninstall, '_current_after', verify)
    uninstall.uninstall(case.root, home=case.home)
    assert json.loads(ticket.read_bytes())['phase'] == 'completed'
    assert case.state['mutations'] == 1


def test_ticket_writer_refuses_changed_parent_before_creating_file(transaction):
    case = transaction
    path = legacy_home_revocation.ticket_path(case.root, case.home)
    identity = posix_transaction.read_directory_identity(path.parent, ValueError, 'changed')
    with pytest.raises(ValueError, match='directory changed'):
        legacy_observer._write_restore_ticket(path, b'{}', expected_parent=(identity[0], identity[1] + 1))
    assert not path.exists()


def test_uninstall_drift_is_refused_before_ticket_or_pm(transaction):
    case = transaction
    changed = BEFORE + b'# unrelated user edit\n'
    case.config.write_bytes(changed)
    case.evidence.config = legacy_9b904.ExistingConfig(changed, sha256(changed).hexdigest())
    case.evidence.config_identity = posix_transaction.read_path_snapshot(case.config, ValueError, 'changed')
    with pytest.raises(ValueError, match='config drift is unsupported'):
        uninstall.uninstall(case.root, home=case.home)
    assert case.config.read_bytes() == changed
    assert not legacy_home_revocation.ticket_path(case.root, case.home).exists()
    assert case.state['mutations'] == 0


def test_cli_dispatches_bound_v2_to_home_uninstall(transaction, monkeypatch, capsys):
    case = transaction
    calls = []
    monkeypatch.setattr(uninstall, 'uninstall', lambda root, *, home: calls.append((root, home)))
    monkeypatch.setattr(cli, '_restore', lambda *_a, **_k: pytest.fail('must retain shared source'))
    assert cli._run_uninstall(SimpleNamespace(hermes_dir=case.root, hermes_home=case.home)) == 0
    assert calls == [(case.root, case.home)]
    assert 'restart Hermes Gateway through its service owner' in capsys.readouterr().out


def test_cli_refuses_orphaned_ticket_before_source_restore(transaction, monkeypatch):
    case = transaction
    ticket = legacy_home_revocation.ticket_path(case.root, case.home)
    ticket.write_bytes(b'pending')
    case.marker.unlink()
    monkeypatch.setattr(cli, '_restore', lambda *_a, **_k: pytest.fail('must retain shared source'))
    assert cli._run_uninstall(SimpleNamespace(hermes_dir=case.root, hermes_home=case.home)) == 1
    assert ticket.read_bytes() == b'pending'


def test_official_9b_serializer_plans_only_home_selection(tmp_path):
    fixture = os.environ.get('HFC_TEST_9B_SOURCE_ROOT')
    if not fixture:
        pytest.skip('exact 9b904 source fixture was not supplied')
    root = Path(fixture)
    plan = uninstall.plan_disable(root, Path(sys.executable), tmp_path, BEFORE)
    assert plan.before == BEFORE
    assert plan.after == AFTER
