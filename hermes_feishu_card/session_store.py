"""Private, bounded card-display checkpoints; never execution/approval recovery."""
from __future__ import annotations

from dataclasses import asdict, fields
import hashlib
import json
import time
from pathlib import Path

from .card_timeline import CardTimeline, TimelineEntry
from .session import CardSession, ToolState
from .display_segments import valid_checkpoint_state
from .legacy_owner import valid_legacy_receipt
from .native_handoff import (
    _prepare_private_root, _validate_existing_private_file, _atomic_write_private,
)

MAX_RECORD_BYTES = 1024 * 1024
MAX_RECORDS = 128
RETENTION_SECONDS = 24 * 3600
_EXCLUDED = {'tools', 'timeline', 'thinking_normalizer', 'answer_normalizer',
             'active_interaction', 'terminal_handoff_record', 'route_profile_id'}
_FIELDS = {f.name for f in fields(CardSession)} - _EXCLUDED


class SessionStore:
    def __init__(self, directory):
        self.root = Path(directory).absolute() / 'card-checkpoints-v1'
        _prepare_private_root(self.root)

    def _path(self, key):
        return self.root / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def save(self, key, session, message_id, bot_id, profile_id, aliases, client_identity):
        # Handoff has its own durable proof protocol. Do not recreate that state.
        if session.terminal_disposition or session.delivery_kind != 'chat':
            self.remove(key)
            return
        body = {k: getattr(session, k) for k in _FIELDS}
        # Ordinary cards remain readable by v4.6.3 after a rollback. Records
        # using new presentation ownership need the newer reader instead.
        for optional in ('display_segment', 'legacy_owner_receipt'):
            if not body[optional]:
                body.pop(optional)
        body['tools'] = {k: asdict(v) for k, v in session.tools.items()}
        for tool in body['tools'].values():
            if not tool['call_id']:
                tool.pop('call_id')
        body['timeline'] = asdict(session.timeline)
        body['normalizers'] = [session.thinking_normalizer._pending, session.answer_normalizer._pending]
        prefix = f"{profile_id}:" if profile_id else ""
        if not key.startswith(prefix) or not key[len(prefix):]:
            raise ValueError("checkpoint turn scope mismatch")
        record = dict(version=1, key=key, turn_id=key[len(prefix):], session=body, message_id=message_id,
                      bot_id=bot_id, profile_id=profile_id, aliases=aliases, client_identity=client_identity,
                      had_interaction=(session.active_interaction is not None and session.active_interaction.status in {"pending","paused"}),
                      saved_at=time.time())
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
                data = r['session']
                # v4.6.3 records predate display segments. Preserve their exact
                # semantics; new records retain the original logical identity.
                data.setdefault('display_segment', {})
                data.setdefault('legacy_owner_receipt', {})
                if set(data) != _FIELDS | {'tools','timeline','normalizers'}:
                    continue
                if not valid_checkpoint_state(data['display_segment']):
                    continue
                if not valid_legacy_receipt(data['legacy_owner_receipt']):
                    continue
                session = CardSession(**{k:data[k] for k in _FIELDS})
                if not all(isinstance(getattr(session,k),str) and getattr(session,k)
                           for k in ('conversation_id','message_id','chat_id')):
                    continue
                session.tools = {k:ToolState(**v) for k,v in data['tools'].items()}
                timeline = dict(data['timeline'])
                timeline['_entries'] = [TimelineEntry(**x) for x in timeline['_entries']]
                session.timeline = CardTimeline(**timeline)
                session.thinking_normalizer._pending, session.answer_normalizer._pending = data['normalizers']
                # No interaction tokens, callbacks, waiter identity or native admissions are restored.
                if r['had_interaction']:
                    session.status = 'failed'
                    session.display_status = ''
                    session.answer_text += '\n\n连接已重建，原授权已失效，请重新发起请求。'
                    session.timeline.complete()
                    session.display_segment = {}
                r['session'] = session
                records.append(r)
            except (ValueError, TypeError, KeyError, AttributeError, OSError):
                continue
        return records
