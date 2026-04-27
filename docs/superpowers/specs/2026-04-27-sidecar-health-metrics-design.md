# Sidecar Health Metrics Design

## Goal

Add lightweight sidecar health and retry metrics so operators can see whether events are flowing, whether Feishu card delivery is succeeding, and whether update retries are happening.

## Scope

This change is limited to the sidecar runtime. It does not change the Hermes hook, installer patch, Feishu credential loading, card JSON structure, or public event schema.

## Design

The sidecar keeps in-memory counters for the current process lifetime and exposes them from `/health` under a `metrics` object. Counters cover accepted, applied, ignored, and rejected events, plus Feishu send/update attempts, successes, failures, and update retry attempts.

Initial `send_card` is not automatically retried because a lost response could still mean Feishu created a card, and blind retry may create duplicates. If send fails, the sidecar removes the newly created local session and returns a JSON error so a later `message.started` can retry cleanly.

`update_card_message` is retried once because it targets an existing Feishu `message_id` and is safer to repeat. If both attempts fail, the sidecar returns a JSON error while preserving accumulated session state so a later event can render the latest card content.

## Health Output

`/health` continues to return `status`, `active_sessions`, `process_pid`, and optional `process_token`. It additionally returns:

- `metrics.events_received`
- `metrics.events_applied`
- `metrics.events_ignored`
- `metrics.events_rejected`
- `metrics.feishu_send_attempts`
- `metrics.feishu_send_successes`
- `metrics.feishu_send_failures`
- `metrics.feishu_update_attempts`
- `metrics.feishu_update_successes`
- `metrics.feishu_update_failures`
- `metrics.feishu_update_retries`

Metrics are process-local and reset when the sidecar restarts.

## Testing

Unit-style aiohttp integration tests verify zero metrics on health, normal event flow counters, update retry success counters, send failure cleanup, and JSON error responses without tracebacks.
