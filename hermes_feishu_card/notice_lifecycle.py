"""Bounded ownership for transient restart text (adapted from mouyong's PR #331).

Records are not a message history: only explicitly registered restart notices enter
this registry. A successful later delivery retires a snapshot of the same scope.
Keep failed deletions here so a later delivery can retry without losing ownership.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Callable


@dataclass(frozen=True)
class NoticeScope:
    profile_id: str
    bot_id: str
    chat_id: str
    thread_id: str


class RestartNoticeRegistry:
    def __init__(self, *, max_scopes: int = 500, max_members: int = 8,
                 retry_delay: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 directory: str | Path | None = None,
                 identity_for_scope: Callable[[NoticeScope], str | None] | None = None,
                 wall_clock: Callable[[], float] = time.time,
                 state_changed: Callable[[str], None] | None = None):
        self.max_scopes = max_scopes
        self.max_members = max_members
        self._scopes: dict[NoticeScope, dict[str, int]] = {}
        self._owners: dict[str, NoticeScope] = {}
        self._retry_after: dict[str, float] = {}
        self._retry_delay = retry_delay
        self._clock = clock
        self._generation = 0
        self._created_at: dict[str, float] = {}
        self._identities: dict[str, str] = {}
        self._wall_clock = wall_clock
        self._identity_for_scope = identity_for_scope
        self._state_changed = state_changed
        self._root = Path(directory).absolute() / "restart-notices-v1" if directory is not None else None
        self._store_readable = True
        self.persistence_state = "disabled" if self._root is None else "ready"
        if self._root is not None:
            try:
                from .native_handoff import _prepare_private_root
                _prepare_private_root(self._root)
                self._load()
            except (OSError, ValueError, TypeError, KeyError):
                self._store_readable = False
                self._set_state("unavailable")
        self._set_state(self.persistence_state)

    def _set_state(self, state):
        self.persistence_state = state
        if self._state_changed is not None:
            self._state_changed(state)

    def _identity(self, scope):
        if self._identity_for_scope is None:
            return None
        try:
            value = self._identity_for_scope(scope)
            return value if isinstance(value, str) and len(value) == 64 else None
        except (RuntimeError, ValueError, KeyError):
            return None

    def _load(self):
        from .native_handoff import _validate_existing_private_file
        path = self._root / "owned.json"
        _validate_existing_private_file(self._root, path)
        if not path.exists():
            return
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("notice store exceeds bound")
        envelope = json.loads(path.read_bytes())
        body = envelope["record"]
        encoded = json.dumps(body, allow_nan=False, sort_keys=True).encode()
        if (hashlib.sha256(encoded).hexdigest() != envelope["digest"]
                or set(body) != {"version", "generation", "notices"}
                or type(body["version"]) is not int or body["version"] != 1 or type(body["generation"]) is not int
                or not 0 <= body["generation"] <= 2**63
                or not isinstance(body["notices"], list)
                or len(body["notices"]) > self.max_scopes * self.max_members):
            raise ValueError("invalid notice store")
        now = self._wall_clock()
        staged = []
        ids = set()
        counts = {}
        for row in body["notices"]:
            if set(row) != {"scope", "message_id", "generation", "created_at", "retry_at", "identity"}:
                raise ValueError("invalid notice record")
            scope = NoticeScope(**row["scope"])
            message_id = row["message_id"]
            if (any(not isinstance(v, str) or len(v) > 256 or any(ord(c) < 32 for c in v)
                    for v in (*asdict(scope).values(), message_id))
                    or not scope.profile_id or not scope.chat_id or not message_id or message_id in ids
                    or type(row["generation"]) is not int or not 0 < row["generation"] <= body["generation"]
                    or any(type(row[k]) not in (float, int) or not math.isfinite(row[k]) or row[k] < 0
                           for k in ("created_at", "retry_at"))):
                raise ValueError("invalid notice identity")
            ids.add(message_id)
            counts[scope] = counts.get(scope, 0) + 1
            if len(counts) > self.max_scopes or counts[scope] > self.max_members:
                raise ValueError("notice capacity exceeded")
            if not 0 <= now - row["created_at"] <= 7 * 24 * 3600:
                continue
            identity = self._identity(scope)
            if identity is None or row["identity"] != identity:
                continue
            staged.append((scope, row))
        # Do not expose partially validated ownership if any later row is corrupt.
        self._generation = body["generation"]
        for scope, row in staged:
            message_id = row["message_id"]
            self._scopes.setdefault(scope, {})[message_id] = row["generation"]
            self._owners[message_id] = scope
            self._created_at[message_id] = row["created_at"]
            self._identities[message_id] = row["identity"]
            remaining = min(self._retry_delay, max(0, row["retry_at"] - now))
            self._retry_after[message_id] = self._clock() + remaining

    def _persist(self):
        if self._root is None:
            return True
        if not self._store_readable:
            return False
        try:
            from .native_handoff import _prepare_private_root, _validate_existing_private_file, _atomic_write_private
            _prepare_private_root(self._root)
            path = self._root / "owned.json"
            _validate_existing_private_file(self._root, path)
            now = self._wall_clock()
            rows = [dict(scope=asdict(scope), message_id=mid, generation=generation,
                         created_at=self._created_at[mid], identity=self._identities[mid],
                         retry_at=now + max(0, self._retry_after.get(mid, 0) - self._clock()))
                    for scope, members in self._scopes.items() for mid, generation in members.items()]
            record = dict(version=1, generation=self._generation, notices=rows)
            encoded = json.dumps(record, allow_nan=False, sort_keys=True).encode()
            payload = json.dumps(dict(record=record, digest=hashlib.sha256(encoded).hexdigest()), allow_nan=False).encode()
            if len(payload) > 4 * 1024 * 1024:
                raise ValueError("notice store exceeds bound")
            _atomic_write_private(self._root, path, payload)
            self._set_state("ready")
            return True
        except (OSError, ValueError):
            self._set_state("unavailable")
            return False

    def register(self, scope: NoticeScope, message_id: str) -> bool:
        self._prune()
        identity = self._identity(scope) if self._root is not None else ""
        if self._root is not None and (identity is None or not self._store_readable):
            return False
        owner = self._owners.get(message_id)
        if owner is not None and owner != scope:
            return False
        members = self._scopes.get(scope)
        if members is None:
            if len(self._scopes) >= self.max_scopes:
                return False
            members = {}
        if message_id in members:
            return self._root is None or self._identities.get(message_id) == identity
        if len(members) >= self.max_members:
            return False
        self._generation += 1
        members[message_id] = self._generation
        self._owners[message_id] = scope
        self._scopes[scope] = members
        self._created_at[message_id] = self._wall_clock()
        self._identities[message_id] = identity
        if not self._persist():
            self._forget(scope, message_id)
            return False
        return True

    def snapshot(self, scope: NoticeScope) -> tuple[tuple[str, int], ...]:
        self._prune()
        return tuple(self._scopes.get(scope, {}).items())

    def expiry_schedule(self, ttl: float):
        """Restore bounded timers from creation time, preserving failed-delete cooldowns."""
        self._prune()
        return tuple((scope, mid, generation, self.expiry_delay(mid, ttl))
                     for scope, members in self._scopes.items()
                     for mid, generation in members.items())

    def expiry_delay(self, message_id: str, ttl: float) -> float:
        return max(0, self._created_at[message_id] + ttl - self._wall_clock(),
                   self._retry_after.get(message_id, 0) - self._clock())

    def contains(self, scope: NoticeScope, message_id: str, generation: int) -> bool:
        return (self._scopes.get(scope, {}).get(message_id) == generation
                and (self._root is None or self._identity(scope) == self._identities.get(message_id)))

    def ready(self, scope: NoticeScope, message_id: str, generation: int) -> bool:
        return (self.contains(scope, message_id, generation)
                and self._clock() >= self._retry_after.get(message_id, 0))

    def defer(self, scope: NoticeScope, message_id: str, generation: int) -> None:
        if self.contains(scope, message_id, generation):
            self._retry_after[message_id] = self._clock() + self._retry_delay
            self._persist()

    def discard(self, scope: NoticeScope, message_id: str, generation: int) -> None:
        if not self.contains(scope, message_id, generation):
            return
        self._forget(scope, message_id)
        self._persist()

    def _forget(self, scope, message_id):
        members = self._scopes[scope]
        del members[message_id]
        self._owners.pop(message_id, None)
        self._retry_after.pop(message_id, None)
        self._created_at.pop(message_id, None)
        self._identities.pop(message_id, None)
        if not members:
            del self._scopes[scope]

    def _prune(self):
        expired = [mid for mid, created in self._created_at.items()
                   if self._wall_clock() - created > 7 * 24 * 3600]
        for mid in expired:
            self._forget(self._owners[mid], mid)
        if expired:
            self._persist()
