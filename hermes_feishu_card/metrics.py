from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SidecarMetrics:
    events_received: int = 0
    events_applied: int = 0
    events_ignored: int = 0
    events_rejected: int = 0
    event_auth_rejections: int = 0
    event_id_replays: int = 0
    event_id_conflicts: int = 0
    event_id_evictions: int = 0
    sidecar_request_auth_rejections: int = 0
    runtime_control_events_received: int = 0
    runtime_control_events_accepted: int = 0
    runtime_control_auth_rejections: int = 0
    runtime_interaction_callback_attempts: int = 0
    runtime_interaction_callback_successes: int = 0
    runtime_interaction_callback_failures: int = 0
    integrity_repair_attempts: int = 0
    integrity_repair_successes: int = 0
    integrity_repair_refusals: int = 0
    policy_queries: int = 0
    policy_auth_rejections: int = 0
    policy_invalid_requests: int = 0
    policy_event_checks: int = 0
    native_bypass_events: int = 0
    native_handoff_fence_restores: int = 0
    native_handoff_fence_restore_refusals: int = 0
    feishu_send_attempts: int = 0
    feishu_noop_attempts: int = 0
    feishu_send_successes: int = 0
    feishu_send_failures: int = 0
    feishu_send_retries: int = 0
    feishu_send_unknown_outcomes: int = 0
    notice_native_fallbacks: int = 0
    notice_uncertain_warnings: int = 0
    notice_update_failures: int = 0
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
    table_compactions: int = 0
    table_truncations: int = 0
    card_limit_deferrals: int = 0
    card_native_handoffs: int = 0
    card_limit_json_bytes: int = 0
    card_limit_elements: int = 0
    card_limit_tables: int = 0
    recovery_plans_available: int = 0
    recovery_attempts: int = 0
    recovery_successes: int = 0
    recovery_refusals: int = 0
    profile_mismatches: int = 0
    sessions_collected: int = 0
    zombie_sessions_collected: int = 0
    flush_controllers_collected: int = 0
    # Short-lived message recall (the gateway asks the sidecar to withdraw an acknowledgement).
    ephemeral_recalls_scheduled: int = 0
    ephemeral_recalls_completed: int = 0
    ephemeral_recall_failures: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
