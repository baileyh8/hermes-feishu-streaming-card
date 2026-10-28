"""9b904 HOME-only uninstall through official PM selection."""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from . import legacy_9b904, legacy_home_revocation, plugin_config_bytes, plugin_config_delta
from .legacy_9b904_contract import SOURCE_SHA256


@dataclass(frozen=True, repr=False)
class DisablePlan:
    before: bytes
    after: bytes


_SELECTION_CHILD = r'''
import base64, hashlib, io, json, sys, types
request=json.loads(sys.stdin.read())
source=base64.b64decode(request['source'],validate=True)
before=base64.b64decode(request['before'],validate=True)
if hashlib.sha256(source).hexdigest()!=request['digest']:
    raise RuntimeError('serializer source changed')
module=types.ModuleType('hermes_yaml')
exec(compile(source,'<verified-hermes-yaml>','exec',dont_inherit=True),module.__dict__)
yaml=module.roundtrip_yaml(); document=yaml.load(before.decode('utf-8-sig'))
plugins=document['plugins']; plugins['enabled']=sorted(request['enabled']); plugins['disabled']=sorted(request['disabled'])
output=io.StringIO(); yaml.dump(document,output)
print(json.dumps({'after':base64.b64encode(output.getvalue().encode('utf-8')).decode('ascii')}))
'''


def plan_disable(root: Path, python: Path, home: Path, before: bytes) -> DisablePlan:
    """Plan the official selection postimage without mutating config or PM."""
    legacy_9b904.inspect_existing_config(before)
    legacy_9b904.require_canonical(root, python, home, before)
    old = plugin_config_delta._parse(before)
    enabled, disabled, _entry = plugin_config_delta._parts(old)
    if (legacy_home_revocation.PLUGIN_KEY not in enabled
            or legacy_home_revocation.DEPENDENCY_KEY not in enabled or disabled):
        raise legacy_9b904.LegacyAdoptionRefused("legacy uninstall selection is unsupported")
    target_enabled = sorted(name for name in enabled if name != legacy_home_revocation.PLUGIN_KEY)
    target_disabled = [legacy_home_revocation.PLUGIN_KEY]
    source = plugin_config_bytes._launch_path(root, 'hermes_yaml.py')
    identity, payload, version = legacy_9b904._read_versioned_evidence(source, "legacy uninstall serializer unavailable")
    digest = SOURCE_SHA256['hermes_yaml.py']
    if sha256(payload).hexdigest() != digest:
        raise legacy_9b904.LegacyAdoptionRefused("legacy uninstall serializer is unknown")
    request = {'source': base64.b64encode(payload).decode('ascii'),
               'before': base64.b64encode(before).decode('ascii'), 'digest': digest,
               'enabled': target_enabled, 'disabled': target_disabled}
    try:
        result = subprocess.run([str(python), '-I', '-B', '-c', _SELECTION_CHILD],
            input=json.dumps(request), capture_output=True, text=True, timeout=30, check=False,
            cwd=str(root), env=plugin_config_bytes.isolated_child_env(home))
        data = json.loads(result.stdout)
        if result.returncode or type(data) is not dict or set(data) != {'after'} or type(data['after']) is not str:
            raise legacy_9b904.LegacyAdoptionRefused("legacy uninstall config plan is unavailable")
        after = base64.b64decode(data['after'], validate=True)
        legacy_9b904._assert_versioned_evidence(source, identity, version, "legacy uninstall serializer changed")
        expected = copy.deepcopy(old.mapping)
        expected['plugins']['enabled'] = target_enabled
        expected['plugins']['disabled'] = target_disabled
        if not plugin_config_delta._same_value(expected, plugin_config_delta._parse(after).mapping):
            raise legacy_9b904.LegacyAdoptionRefused("legacy uninstall plan changed unrelated config")
        return DisablePlan(before, after)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, legacy_9b904.LegacyAdoptionRefused):
            raise
        raise legacy_9b904.LegacyAdoptionRefused("legacy uninstall config plan is unavailable") from exc


def _owner_digest(owner: dict[str, Any]) -> str:
    return sha256(json.dumps(owner, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _graph_record(binding: Any, owner: dict[str, Any], member: tuple[int, int],
                  generation: str, graph: legacy_9b904.GraphEvidence) -> dict[str, Any]:
    return {'generation': generation, 'python_identity': binding.python_identity, 'member': list(member),
            'owner_sha256': _owner_digest(owner), 'facts_path': str(graph.facts_path),
            'facts': list(graph.facts_identity), 'facts_version': list(graph.facts_version),
            'lock_path': str(graph.lock_path), 'lock': list(graph.lock_identity),
            'lock_version': list(graph.lock_version)}


def _receipt(root: Path, home: Path, ticket: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Prove the original B receipt without treating C as its binding."""
    from . import legacy_observer, plugin, posix_transaction as io

    marker = root / legacy_observer.MARKER
    io.assert_path_snapshot(marker, tuple(ticket['marker']), ValueError, "legacy uninstall marker changed")
    raw = legacy_observer._read(marker)
    if raw is None or sha256(raw).hexdigest() != ticket['marker'][2]:
        raise ValueError("legacy uninstall marker changed")
    record = legacy_observer._decode(raw)
    if (record['protocol'] != legacy_observer.ADOPTION_PROTOCOL or record['adapter'] != legacy_9b904.ADAPTER_ID
            or record['home'] != str(home) or record['python_identity'] != ticket['pm_before']['python_identity']):
        raise ValueError("legacy uninstall marker is unsupported")
    ownership = plugin.PluginOwnership(**record['plugin_config'])
    preimage = legacy_observer._preimage(home, ownership)
    for path, expected, message in ((preimage.backup_path, ticket['backup'], 'legacy uninstall backup changed'),
                                    (preimage.journal_path, ticket['journal'], 'legacy uninstall journal changed')):
        io.assert_path_snapshot(path, tuple(expected), ValueError, message)
    before = plugin._read_private_backup(preimage)
    expected_journal = {'protocol': 'hfc-plugin-config-preimage-v1', 'phase': 'installed',
                        **ownership.sanitized(), 'config_delta': record['config_delta'], 'adapter': record['adapter']}
    journal = legacy_observer._read(preimage.journal_path)
    if (journal is None or json.loads(journal) != expected_journal
            or sha256(before).hexdigest() != ticket['before'][2]):
        raise ValueError("legacy uninstall receipt changed")
    plugin_config_delta.verify_receipt(before, record['config_delta'])
    return record, before


def _verify_ticket_paths(root: Path, home: Path, ticket: dict[str, Any]) -> None:
    from . import posix_transaction as io
    for path, key in ((root, 'root_dir'), (home, 'home_dir'),
                      (home / '.hermes_feishu_card' / 'install', 'state_dir')):
        io.assert_directory_snapshot(path, tuple(ticket[key]), ValueError,
                                     "legacy uninstall directory changed", allow_original_absence=False)


def _capture(root: Path, home: Path) -> tuple[Any, dict[str, Any], legacy_9b904.AdmissionEvidence]:
    from . import plugin, pm_dependency
    binding = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None)
    probe = pm_dependency._verified_call(root, binding.runtime_python, home, {'operation': 'probe'},
                                         legacy_9b904.ADAPTER_ID)
    member = home / 'plugins' / pm_dependency.NAME
    owner = pm_dependency._owned(member)
    evidence = legacy_9b904.capture_admission(root, binding.runtime_python, home, probe, owner)
    if owner is None or owner.get('schema') != 3 or evidence.graph is None or evidence.marker_identity is None:
        raise ValueError("legacy uninstall ownership is unverified")
    return binding, owner, evidence


def _prepare_ticket(root: Path, home: Path) -> tuple[dict[str, Any], tuple[int, int, str]]:
    from . import legacy_observer, plugin, posix_transaction as io

    binding, owner, evidence = _capture(root, home)
    if evidence.graph is None or evidence.marker_identity is None:
        raise ValueError("legacy uninstall PM graph is unverified")
    marker = legacy_observer.inspect(root, home=home)
    if marker is None or marker[0].get('adapter') != legacy_9b904.ADAPTER_ID:
        raise ValueError("legacy uninstall marker is unsupported")
    plan = plan_disable(root, binding.runtime_python, home, evidence.config.contents)
    preimage = marker[2]
    if plugin._read_private_backup(preimage) != evidence.config.contents:
        raise ValueError("legacy uninstall config drift is unsupported; evidence retained")
    state_identity = io.read_directory_identity(preimage.state_dir, ValueError, 'legacy uninstall state changed')
    record = {
        'protocol': legacy_home_revocation.PROTOCOL, 'phase': 'planned',
        'root_key': legacy_home_revocation.root_key(root), 'home': str(home),
        'marker': list(evidence.marker_identity),
        'backup': list(evidence.marker_files[preimage.backup_path]),
        'journal': list(evidence.marker_files[preimage.journal_path]),
        'before': list(evidence.config_identity), 'after_sha256': sha256(plan.after).hexdigest(),
        'root_dir': list(io.read_directory_identity(root, ValueError, 'legacy uninstall root changed')),
        'home_dir': list(evidence.home_identity),
        'state_dir': list(state_identity),
        'pm_before': _graph_record(binding, owner, evidence.old_member_identity, evidence.generation, evidence.graph),
        'pm_after': None,
    }
    legacy_9b904.verify_before(root, home, evidence)
    legacy_9b904.verify_pm_graph(root, binding.runtime_python, home, owner, evidence.generation,
                                evidence.config.digest, expected=evidence.graph)
    encoded = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode()
    ticket_path = legacy_home_revocation.ticket_path(root, home)
    snapshot = legacy_observer._write_restore_ticket(ticket_path, encoded, expected_parent=state_identity)
    target = io.bind_target(ticket_path, ValueError, staging_refusal='legacy uninstall state changed',
                            parent_refusal='legacy uninstall state changed', wrap_open_error=True)
    try:
        if target.parent_identity != state_identity:
            raise ValueError("legacy uninstall state changed")
        os.fsync(target.parent_fd)
    finally:
        target.release()
    return record, snapshot


def _current_after(root: Path, home: Path, ticket: dict[str, Any]) -> tuple[dict[str, Any], tuple[int, int, str]]:
    from . import plugin, pm_dependency, posix_transaction as io

    _verify_ticket_paths(root, home, ticket)
    _record, before = _receipt(root, home, ticket)
    binding = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None)
    environment = legacy_9b904.inspect_unmanaged_environment(root, binding.runtime_python, home)
    config_identity, config = legacy_9b904._read_evidence(home / 'config.yaml')
    plan = plan_disable(root, binding.runtime_python, home, before)
    if config != plan.after or config_identity[2] != ticket['after_sha256']:
        raise ValueError("legacy uninstall config outcome is unknown")
    member = home / 'plugins' / pm_dependency.NAME
    member_identity = io.read_directory_identity(member, ValueError, 'legacy uninstall member changed')
    owner = pm_dependency._owned(member)
    previous = ticket['pm_before']
    if (owner is None or owner.get('schema') != 3 or list(member_identity) != previous['member']
            or _owner_digest(owner) != previous['owner_sha256']):
        raise ValueError("legacy uninstall member changed")
    probe = pm_dependency._verified_call(root, binding.runtime_python, home, {'operation': 'probe'},
                                         legacy_9b904.ADAPTER_ID)
    generation = probe.get('generation')
    if (probe.get('expected') != config_identity[2] or type(generation) is not str
            or binding.runtime_python.parent.parent.resolve() != Path(generation).resolve()):
        raise ValueError("legacy uninstall PM selection is unverified")
    legacy_9b904.verify_runtime(binding, version=owner['version'])
    graph = legacy_9b904.verify_pm_graph(root, binding.runtime_python, home, owner, generation,
                                        config_identity[2], plugin_enabled=False)
    legacy_9b904.verify_unmanaged_environment(environment)
    _verify_ticket_paths(root, home, ticket)
    _receipt(root, home, ticket)
    io.assert_path_snapshot(home / 'config.yaml', config_identity, ValueError, 'legacy uninstall config changed')
    return _graph_record(binding, owner, member_identity, generation, graph), config_identity


def _complete_ticket(root: Path, home: Path, ticket: dict[str, Any],
                     ticket_identity: tuple[int, int, str]) -> None:
    from . import posix_transaction as io

    after, config_identity = _current_after(root, home, ticket)
    if ticket['phase'] == 'completed':
        return
    completed = {**ticket, 'phase': 'completed', 'pm_after': after}
    value = json.dumps(completed, sort_keys=True, separators=(',', ':')) + '\n'
    path = legacy_home_revocation.ticket_path(root, home)
    target = io.bind_target(path, ValueError, staging_refusal='legacy uninstall state changed',
                            parent_refusal='legacy uninstall state changed', wrap_open_error=True)
    staged = None
    try:
        if target.parent_identity != tuple(ticket['state_dir']):
            raise ValueError("legacy uninstall state changed")
        staged = io.stage_file(path, value, binding=target, mode=0o600, durable_text=True,
            refused=ValueError, staging_refusal='legacy uninstall ticket stage failed',
            target_refusal='legacy uninstall ticket is unsafe', stage_refusal='legacy uninstall ticket stage changed')
        staged_identity, staged_contents, staged_mode = io.read_file_state(
            staged.basename, parent_fd=staged.parent_fd, missing_allowed=False, refused=ValueError,
            non_regular_refusal='legacy uninstall ticket stage is unsafe',
            changed_refusal='legacy uninstall ticket stage changed', wrap_read_error=True)
        if (staged_identity is None or staged_identity[:2] != staged.identity
                or staged_contents != value.encode('utf-8') or staged_mode != 0o600):
            raise ValueError("legacy uninstall ticket stage changed")
        selected_stage = staged
        def validate() -> None:
            _verify_ticket_paths(root, home, ticket)
            _receipt(root, home, ticket)
            io.assert_path_snapshot(path, ticket_identity, ValueError, 'legacy uninstall ticket changed')
            io.assert_path_snapshot(home / 'config.yaml', config_identity, ValueError, 'legacy uninstall config changed')
            io.assert_path_parent(target, ValueError, 'legacy uninstall state changed')
            actual, contents, mode = io.read_file_state(
                selected_stage.basename, parent_fd=selected_stage.parent_fd, missing_allowed=False, refused=ValueError,
                non_regular_refusal='legacy uninstall ticket stage is unsafe',
                changed_refusal='legacy uninstall ticket stage changed', wrap_read_error=True)
            if actual != staged_identity or contents != value.encode('utf-8') or mode != 0o600:
                raise ValueError("legacy uninstall ticket stage changed")
            repeated, repeated_config = _current_after(root, home, ticket)
            if repeated != after or repeated_config != config_identity:
                raise ValueError("legacy uninstall PM outcome changed")
        io.replace_stage(target, staged, check_parent=True, before_replace=validate)
        staged = None
        os.fsync(target.parent_fd)
        io.assert_path_snapshot(path, staged_identity, ValueError, 'legacy uninstall ticket changed')
        if legacy_home_revocation.inspect(root, home) != 'disabled':
            raise ValueError("legacy uninstall completed evidence is unverified")
    finally:
        if staged is not None:
            staged.cleanup()
        target.release()


def uninstall(root: Path, *, home: Path | None = None) -> None:
    """Disable this HOME; retain the shared sources, dependency and v2 receipt."""
    from . import legacy_observer, pm_dependency, posix_transaction as io
    from .transaction_lock import _root_lock

    root = root.expanduser().absolute()
    with _root_lock(root):
        raw = legacy_observer._read(root / legacy_observer.MARKER)
        if raw is None:
            raise ValueError("legacy uninstall receipt is missing")
        record = legacy_observer._decode(raw)
        if record['protocol'] != legacy_observer.ADOPTION_PROTOCOL or record['adapter'] != legacy_9b904.ADAPTER_ID:
            raise ValueError("legacy uninstall receipt is unsupported")
        selected = Path(record['home'])
        if home is not None and Path(home).expanduser().resolve() != selected:
            raise ValueError("legacy uninstall HOME binding changed")
        path = legacy_home_revocation.ticket_path(root, selected)
        try:
            path.lstat()
        except FileNotFoundError:
            ticket, ticket_identity = _prepare_ticket(root, selected)
        else:
            ticket_identity, ticket_bytes = legacy_9b904._read_evidence(path)
            ticket = legacy_home_revocation.decode(ticket_bytes, root, selected)
        _verify_ticket_paths(root, selected, ticket)
        _receipt(root, selected, ticket)
        config_identity, config = legacy_9b904._read_evidence(selected / 'config.yaml')
        if config_identity[2] != ticket['after_sha256']:
            if ticket['phase'] != 'planned' or list(config_identity) != ticket['before']:
                raise ValueError("legacy uninstall config outcome is unknown; evidence retained")
            binding, owner, evidence = _capture(root, selected)
            if evidence.graph is None or _graph_record(binding, owner, evidence.old_member_identity,
                                                        evidence.generation, evidence.graph) != ticket['pm_before']:
                raise ValueError("legacy uninstall PM evidence changed")
            plan = plan_disable(root, binding.runtime_python, selected, config)
            if sha256(plan.after).hexdigest() != ticket['after_sha256']:
                raise ValueError("legacy uninstall planned postimage changed")
            legacy_9b904.verify_before(root, selected, evidence)
            io.assert_path_snapshot(path, ticket_identity, ValueError, 'legacy uninstall ticket changed')
            pm_dependency._verified_call(root, binding.runtime_python, selected,
                {'operation': 'disable-hfc', 'name': legacy_home_revocation.PLUGIN_KEY,
                 'expected': config_identity[2]}, legacy_9b904.ADAPTER_ID)
        _complete_ticket(root, selected, ticket, ticket_identity)
