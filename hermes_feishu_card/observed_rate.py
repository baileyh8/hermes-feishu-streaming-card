"""Consume-once, bounded legacy metrics; never owns delivery or execution."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from math import isfinite
from threading import RLock
from time import monotonic

from .turn_metrics import TurnMetrics


class ObservedRates:
    def __init__(self, *, now: Callable[[], float] = monotonic,
                 capacity: int = 256, ttl: float = 3600.0) -> None:
        if (type(capacity) is not int or not 1 <= capacity <= 256
                or not isfinite(ttl) or not 0 < ttl <= 3600 or not callable(now)):
            raise ValueError("invalid observation cache bounds")
        self.now, self.capacity, self.ttl = now, capacity, ttl
        self.entries: OrderedDict[tuple[str, str], tuple[float, TurnMetrics | None]] = OrderedDict()
        self.lock = RLock()

    def _prune(self) -> None:
        now = self.now()
        for key, (created, _) in list(self.entries.items()):
            if not 0 <= now - created < self.ttl:
                del self.entries[key]

    @staticmethod
    def _key(values: dict[str, object]) -> tuple[str, str] | None:
        session, turn = values.get("session_id"), values.get("turn_id")
        if (values.get("platform") != "feishu" or not isinstance(session, str)
                or not isinstance(turn, str) or not all(
                    0 < len(v) <= 256 and v.strip() for v in (session, turn))):
            return None
        return session, turn

    def start(self, values: dict[str, object]) -> None:
        key = self._key(values)
        if key is None:
            return
        with self.lock:
            self._prune()
            if key not in self.entries and len(self.entries) >= self.capacity:
                # Retain replay suppression while space permits, but consumed
                # turns must not starve fresh observations for the whole TTL.
                for old_key, (_, metrics) in list(self.entries.items()):
                    if metrics is None:
                        del self.entries[old_key]
                        break
            if key not in self.entries and len(self.entries) < self.capacity:
                self.entries[key] = (self.now(), TurnMetrics(self.now()))
            elif key not in self.entries:
                # A rejected new turn must not consume an older same-session
                # observation merely because its output count happens to match.
                for old_key, (created, _) in list(self.entries.items()):
                    if old_key[0] == key[0]:
                        self.entries[old_key] = (created, None)

    def observe(self, values: dict[str, object]) -> None:
        key = self._key(values)
        if key is None:
            return
        with self.lock:
            self._prune()
            entry = self.entries.get(key)
            if entry is not None and entry[1] is not None:
                entry[1].observe_api(values)

    def take(self, session_id: object) -> tuple[dict[str, object], int] | None:
        if type(session_id) is not str:
            return None
        with self.lock:
            self._prune()
            matches = [(k, v) for k, v in self.entries.items()
                       if k[0] == session_id and v[1] is not None]
            # No guessing between overlapping or unconsumed turns in a session.
            for key, (created, _) in matches:
                self.entries[key] = (created, None)
            if len(matches) != 1:
                return None
            metrics = matches[0][1][1]
            if metrics is None:
                return None
            rate = metrics.output_rate()
            return (rate, metrics.output_tokens) if rate is not None else None

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()
