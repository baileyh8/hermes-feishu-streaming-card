"""Private, bounded card-display checkpoints; never execution/approval recovery."""
from __future__ import annotations

from dataclasses import asdict, fields
import hashlib
import json
import math
import re
import time
from pathlib import Path

from .card_timeline import CardTimeline, TimelineEntry
from .event_rules import TERMINAL_EVENTS, TURN_REOPENING_EVENTS
from .session import CardSession, PendingReopenCreate, ToolState
from .display_segments import valid_checkpoint_state
from .legacy_owner import valid_auxiliary_receipt, valid_legacy_receipt
from .native_handoff import (
    _prepare_private_root, _validate_existing_private_file, _atomic_write_private,
)

MAX_RECORD_BYTES = 1024 * 1024
MAX_RECORDS = 128
RETENTION_SECONDS = 24 * 3600
_EXCLUDED = {'tools', 'timeline', 'thinking_normalizer', 'answer_normalizer',
             'active_interaction', 'approval_retirements', 'terminal_handoff_record', 'route_profile_id', '_process_activity',
             'presentation_state', 'observation', 'legacy_terminal_fingerprint', 'legacy_terminal_start_at',
             'legacy_route_thread_id',
             'pending_reopen_create', 'restored_legacy_terminal', 'restored_unconfirmed_terminal',
             # Keep the v1 display payload unchanged and the live requester private.
             'sender_user_id', 'sender_open_id_rejected', 'display_requester_digest'}
_FIELDS = {f.name for f in fields(CardSession)} - _EXCLUDED
_REOPEN_FIELDS = {f.name for f in fields(PendingReopenCreate)} - {'candidate'}


def _validated_requester_digest(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else ''


def _display_payload(session):
    body = {key: getattr(session, key) for key in _FIELDS}
    # Leave the v1 session schema readable by older releases when possible.
    for optional in ('display_segment', 'legacy_owner_receipt', 'terminal_reasoning_notice'):
        if not body[optional]:
            body.pop(optional)
    body['tools'] = {key: asdict(tool) for key, tool in session.tools.items()}
    for tool in body['tools'].values():
        if not tool['call_id']:
            tool.pop('call_id')
    body['timeline'] = asdict(session.timeline)
    body['normalizers'] = [session.thinking_normalizer._pending, session.answer_normalizer._pending]
    return body


def _load_display_payload(data):
    data.setdefault('display_segment', {})
    data.setdefault('legacy_owner_receipt', {})
    data.setdefault('terminal_reasoning_notice', '')
    if (set(data) != _FIELDS | {'tools', 'timeline', 'normalizers'}
            or type(data['terminal_reasoning_notice']) is not str
            or not valid_checkpoint_state(data['display_segment'])
            or not valid_legacy_receipt(data['legacy_owner_receipt'])):
        raise ValueError('invalid checkpoint display')
    session = CardSession(**{key: data[key] for key in _FIELDS})
    if not all(isinstance(getattr(session, key), str) and getattr(session, key)
               for key in ('conversation_id', 'message_id', 'chat_id')):
        raise ValueError('invalid checkpoint route')
    session.tools = {key: ToolState(**tool) for key, tool in data['tools'].items()}
    timeline = dict(data['timeline'])
    timeline['_entries'] = [TimelineEntry(**entry) for entry in timeline['_entries']]
    session.timeline = CardTimeline(**timeline)
    session.thinking_normalizer._pending, session.answer_normalizer._pending = data['normalizers']
    return session


def _load_pending_reopen(data):
    if (not isinstance(data, dict) or set(data) != _REOPEN_FIELDS | {'candidate'}
            or type(data['start_event']) is not str
            or data['start_event'] not in TURN_REOPENING_EVENTS | TERMINAL_EVENTS | {'message.started'}
            or type(data['start_sequence']) is not int or data['start_sequence'] < 0
            or type(data['attempts']) is not int or not 0 <= data['attempts'] <= 3
            or any(type(data[key]) is not bool for key in ('reply_in_thread', 'streaming', 'uncertain'))
            or any(type(data[key]) not in (int, float) or not math.isfinite(data[key])
                   for key in ('start_at', 'attempted_at', 'predecessor_updated_at'))
            or data['terminal_at'] is not None and (
                type(data['terminal_at']) not in (int, float) or not math.isfinite(data['terminal_at']))
            or any(not isinstance(data[key], str) for key in (
                'generation', 'bot_id', 'client_identity', 'thread_id', 'reply_to_message_id'))
            or not re.fullmatch(r'[0-9a-f]{64}', data['client_identity'])
            or data['generation'] and not re.fullmatch(r'[0-9a-f]{32,64}', data['generation'])):
        raise ValueError('invalid pending reopen')
    candidate = _load_display_payload(data['candidate'])
    terminal_start = data['start_event'] in TERMINAL_EVENTS
    if (candidate.delivery_kind != 'chat'
            or (candidate.status in {'completed', 'failed'}) != terminal_start
            or terminal_start and data['terminal_at'] != data['start_at']
            or candidate.last_sequence < data['start_sequence']):
        raise ValueError('invalid pending candidate')
    return PendingReopenCreate(candidate=candidate, **{key: data[key] for key in _REOPEN_FIELDS})


class SessionStore:
    def __init__(self, directory):
        self.root = Path(directory).absolute() / 'card-checkpoints-v1'
        _prepare_private_root(self.root)

    def _path(self, key):
        return self.root / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def save(self, key, session, message_id, bot_id, profile_id, aliases, client_identity,
             *, auxiliary_receipt=None):
        # Keep confirmed segmented display ownership across restart.
        # Native handoff uses its own delivery ledger.
        if session.terminal_disposition not in {'', 'segmented'} or session.delivery_kind != 'chat':
            self.remove(key)
            return
        body = _display_payload(session)
        prefix = f"{profile_id}:" if profile_id else ""
        if not key.startswith(prefix) or not key[len(prefix):]:
            raise ValueError("checkpoint turn scope mismatch")
        record = dict(version=1, key=key, turn_id=key[len(prefix):], session=body, message_id=message_id,
                      bot_id=bot_id, profile_id=profile_id, aliases=aliases, client_identity=client_identity,
                      had_interaction=(session.active_interaction is not None and session.active_interaction.status in {"pending","paused"}),
                      saved_at=time.time())
        requester = _validated_requester_digest(session.checkpoint_requester_digest())
        if requester:
            record['display_requester_digest'] = requester
        if session.legacy_route_thread_id:
            record['legacy_route_thread_id'] = session.legacy_route_thread_id
        if (session.status in {'completed', 'failed'}
                and (session.legacy_terminal_fingerprint or not session.restored_legacy_terminal)):
            # The fingerprint identifies a retry. The v1 delivery state says whether it reached Feishu.
            record['legacy_terminal_fingerprint'] = session.legacy_terminal_fingerprint or None
        if session.legacy_terminal_start_at is not None and session.terminal_card_confirmed:
            record['legacy_terminal_start_at'] = session.legacy_terminal_start_at
        if session.pending_reopen_create is not None:
            pending = session.pending_reopen_create
            record['pending_reopen_create'] = {key: getattr(pending, key) for key in _REOPEN_FIELDS}
            record['pending_reopen_create']['candidate'] = _display_payload(pending.candidate)
            requester = _validated_requester_digest(pending.candidate.checkpoint_requester_digest())
            if requester:
                record['pending_reopen_requester_digest'] = requester
        if auxiliary_receipt:
            if (not valid_auxiliary_receipt(auxiliary_receipt)
                    or auxiliary_receipt['message_id'] == message_id):
                raise ValueError('invalid auxiliary display receipt')
            record['auxiliary_receipt'] = auxiliary_receipt
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        payload = json.dumps({'record':record, 'digest':hashlib.sha256(encoded).hexdigest()},
                             ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > MAX_RECORD_BYTES:
            raise ValueError('checkpoint exceeds bound')
        self.prune()
        path = self._path(key)
        if not path.exists() and len(list(self.root.glob('*.json'))) >= MAX_RECORDS:
            raise ValueError('checkpoint capacity reached')
        _atomic_write_private(self.root, path, payload)

    def remove(self, key):
        path = self._path(key)
        _validate_existing_private_file(self.root, path)
        path.unlink(missing_ok=True)

    def prune(self):
        for path in self.root.glob('*.json'):
            _validate_existing_private_file(self.root, path)
            if time.time() - path.stat().st_mtime > RETENTION_SECONDS:
                path.unlink()

    def load(self):
        self.prune()
        records = []
        for path in sorted(self.root.glob('*.json'))[:MAX_RECORDS]:
            try:
                _validate_existing_private_file(self.root, path)
                if path.stat().st_size > MAX_RECORD_BYTES:
                    continue
                envelope = json.loads(path.read_bytes())
                r = envelope['record']
                encoded = json.dumps(r, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
                if hashlib.sha256(encoded).hexdigest() != envelope['digest']:
                    continue
                if r['version'] != 1 or self._path(r['key']) != path:
                    continue
                if not 0 <= time.time() - r['saved_at'] <= RETENTION_SECONDS:
                    continue
                if not all(isinstance(r[k], str) for k in ('key','turn_id','message_id','profile_id')):
                    continue
                prefix = f"{r['profile_id']}:" if r['profile_id'] else ''
                if not r['turn_id'] or r['key'] != prefix + r['turn_id']:
                    continue
                if not r['message_id'] or r['bot_id'] is not None and not isinstance(r['bot_id'],str):
                    continue
                auxiliary = r.get('auxiliary_receipt', {})
                if (not valid_auxiliary_receipt(auxiliary)
                        or auxiliary and auxiliary['message_id'] == r['message_id']):
                    continue
                session = _load_display_payload(r['session'])
                session.display_requester_digest = _validated_requester_digest(r.get('display_requester_digest'))
                thread = r.get('legacy_route_thread_id', '')
                if isinstance(thread, str) and thread.startswith(('omt_', 'om_')):
                    session.legacy_route_thread_id = thread
                digest = r.get('legacy_terminal_fingerprint', '')
                if session.status in {'completed', 'failed'} and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
                    session.legacy_terminal_fingerprint = digest
                    start_at = r.get('legacy_terminal_start_at')
                    if session.terminal_card_confirmed and type(start_at) in (int, float) and math.isfinite(start_at):
                        session.legacy_terminal_start_at = start_at
                session.restored_legacy_terminal = (
                    session.status in {'completed', 'failed'} and 'legacy_terminal_fingerprint' not in r
                )
                session.restored_unconfirmed_terminal = (
                    session.status in {'completed', 'failed'} and session.delivery_kind == 'chat'
                    and not session.terminal_disposition and not session.terminal_card_confirmed
                    and (bool(session.legacy_terminal_fingerprint) or session.terminal_delivery_state in {
                        'pending', 'recovering', 'unknown', 'not_sent', 'failed',
                    })
                )
                if 'pending_reopen_create' in r:
                    if session.status not in {'completed', 'failed'}:
                        continue
                    pending = _load_pending_reopen(r['pending_reopen_create'])
                    pending.candidate.display_requester_digest = _validated_requester_digest(
                        r.get('pending_reopen_requester_digest'),
                    )
                    session.pending_reopen_create = pending
                # No interaction tokens, callbacks, waiter identity or native admissions are restored.
                if r['had_interaction']:
                    session.status = 'failed'
                    session.display_status = ''
                    session.answer_text += '\n\n连接已重建，原授权已失效，请重新发起请求。'
                    session.terminal_reasoning_notice = ''
                    session.timeline.complete()
                    session.display_segment = {}
                r['session'] = session
                records.append(r)
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                continue
        return records
