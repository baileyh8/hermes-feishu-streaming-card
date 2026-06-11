from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SidecarMetrics:
    events_received: int = 0
    events_applied: int = 0
    events_ignored: int = 0
    events_rejected: int = 0
    feishu_send_attempts: int = 0
    feishu_send_successes: int = 0
    feishu_send_failures: int = 0
    feishu_update_attempts: int = 0
    feishu_update_successes: int = 0
    feishu_update_failures: int = 0
    feishu_update_retries: int = 0
    cron_cards_sent: int = 0
    cron_fallbacks: int = 0
    zombie_sessions_removed: int = 0
    fallback_keys_cleared: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
