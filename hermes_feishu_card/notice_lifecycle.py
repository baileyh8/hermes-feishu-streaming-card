"""Bounded ownership for transient restart text (adapted from mouyong's PR #331).

Records are not a message history: only explicitly registered restart notices enter
this registry. A successful later delivery retires a snapshot of the same scope.
Keep failed deletions here so a later delivery can retry without losing ownership.
"""
from __future__ import annotations

from dataclasses import dataclass
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
                 retry_delay: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.max_scopes = max_scopes
        self.max_members = max_members
        self._scopes: dict[NoticeScope, dict[str, int]] = {}
        self._owners: dict[str, NoticeScope] = {}
        self._retry_after: dict[str, float] = {}
        self._retry_delay = retry_delay
        self._clock = clock
        self._generation = 0

    def register(self, scope: NoticeScope, message_id: str) -> bool:
        owner = self._owners.get(message_id)
        if owner is not None and owner != scope:
            return False
        members = self._scopes.get(scope)
        if members is None:
            if len(self._scopes) >= self.max_scopes:
                return False
            members = {}
        if message_id in members:
            return True
        if len(members) >= self.max_members:
            return False
        self._generation += 1
        members[message_id] = self._generation
        self._owners[message_id] = scope
        self._scopes[scope] = members
        return True

    def snapshot(self, scope: NoticeScope) -> tuple[tuple[str, int], ...]:
        return tuple(self._scopes.get(scope, {}).items())

    def contains(self, scope: NoticeScope, message_id: str, generation: int) -> bool:
        return self._scopes.get(scope, {}).get(message_id) == generation

    def ready(self, scope: NoticeScope, message_id: str, generation: int) -> bool:
        return (self.contains(scope, message_id, generation)
                and self._clock() >= self._retry_after.get(message_id, 0))

    def defer(self, scope: NoticeScope, message_id: str, generation: int) -> None:
        if self.contains(scope, message_id, generation):
            self._retry_after[message_id] = self._clock() + self._retry_delay

    def discard(self, scope: NoticeScope, message_id: str, generation: int) -> None:
        if not self.contains(scope, message_id, generation):
            return
        members = self._scopes[scope]
        del members[message_id]
        self._owners.pop(message_id, None)
        self._retry_after.pop(message_id, None)
        if not members:
            del self._scopes[scope]
