from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class FlushController:
    def __init__(self, *, interval_seconds: float, metrics: Any) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self.metrics = metrics
        self._task: asyncio.Task[None] | None = None
        self._latest_render: Callable[[], Awaitable[bool]] | None = None
        self._pending = False
        self._pending_terminal = False
        self._pending_count = 0
        self._closed = False
        self._last_flush_at = 0.0

    def schedule(
        self,
        render_update: Callable[[], Awaitable[bool]],
        *,
        terminal: bool = False,
    ) -> asyncio.Task[None]:
        if self._closed and not terminal:
            return self._task or asyncio.create_task(self._noop())
        self.metrics.update_scheduled += 1
        self._latest_render = render_update
        if self._task is not None and not self._task.done():
            self._pending = True
            self._pending_terminal = self._pending_terminal or terminal
            self._pending_count += 1
            self.metrics.update_coalesced += 1
            self.metrics.update_queue_peak = max(
                self.metrics.update_queue_peak,
                self._pending_count,
            )
            return self._task
        self._pending = False
        self._pending_terminal = terminal
        self._pending_count = 0
        self._task = asyncio.create_task(self._run())
        return self._task

    async def drain(self, timeout_seconds: float) -> bool:
        started_at = time.monotonic()
        self.metrics.terminal_drains += 1
        task = self._task
        if task is None or task.done():
            self.metrics.terminal_drain_latency_ms = int(
                (time.monotonic() - started_at) * 1000
            )
            return True
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=max(0.0, timeout_seconds),
            )
        except asyncio.TimeoutError:
            self.metrics.terminal_drain_timeouts += 1
            self.metrics.terminal_drain_latency_ms = int(
                (time.monotonic() - started_at) * 1000
            )
            return False
        self.metrics.terminal_drain_latency_ms = int(
            (time.monotonic() - started_at) * 1000
        )
        return True

    def close(self) -> None:
        self._closed = True

    def snapshot(self) -> dict[str, int | float]:
        return {
            "interval_seconds": self.interval_seconds,
            "pending": int(self._pending),
            "pending_terminal": int(self._pending_terminal),
            "pending_count": self._pending_count,
            "closed": int(self._closed),
            "last_flush_at": self._last_flush_at,
            "task_active": int(self._task is not None and not self._task.done()),
        }

    async def _run(self) -> None:
        current_task = asyncio.current_task()
        try:
            while True:
                terminal = self._pending_terminal
                if not terminal:
                    delay = max(
                        0.0,
                        self.interval_seconds - (time.monotonic() - self._last_flush_at),
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                render_update = self._latest_render
                if render_update is None:
                    return
                self._pending = False
                self._pending_terminal = False
                self._pending_count = 0
                started_at = time.monotonic()
                await render_update()
                self.metrics.feishu_update_latency_ms = int(
                    (time.monotonic() - started_at) * 1000
                )
                self._last_flush_at = time.monotonic()
                if terminal or not self._pending:
                    return
        finally:
            if self._task is current_task:
                self._task = None

    async def _noop(self) -> None:
        return None
