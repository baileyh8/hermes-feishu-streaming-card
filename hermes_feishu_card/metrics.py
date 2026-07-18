from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SidecarMetrics:
    events_received: int = 0
    events_applied: int = 0
    events_ignored: int = 0
    events_rejected: int = 0
    event_auth_rejections: int = 0
    feishu_send_attempts: int = 0
    feishu_send_successes: int = 0
    feishu_send_failures: int = 0
    feishu_send_retries: int = 0
    feishu_send_unknown_outcomes: int = 0
    notice_native_fallbacks: int = 0
    notice_uncertain_warnings: int = 0
    feishu_update_attempts: int = 0
    feishu_update_successes: int = 0
    feishu_update_failures: int = 0
    feishu_update_retries: int = 0
    update_scheduled: int = 0
    update_coalesced: int = 0
    update_queue_peak: int = 0
    terminal_drains: int = 0
    terminal_drain_timeouts: int = 0
    terminal_drain_latency_ms: int = 0
    feishu_update_latency_ms: int = 0
    cron_cards_sent: int = 0
    cron_fallbacks: int = 0
    recovery_plans_available: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    recovery_refusals: int = 0
    profile_mismatches: int = 0
    sessions_collected: int = 0
    zombie_sessions_collected: int = 0
    flush_controllers_collected: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
