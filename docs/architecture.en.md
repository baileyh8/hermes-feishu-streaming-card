# Architecture

[中文](architecture.md) | [English](architecture.en.md)

The active mainline uses a sidecar-only architecture. Hermes Agent keeps a minimal hook that forwards message lifecycle events to an HTTP sidecar; Feishu/Lark card creation, updates, terminal rendering, session accumulation, diagnostics, and safe recovery live in `hermes_feishu_card/`. V4 has completed real Feishu private, group, topic, WebSocket card-action, and long-idle smoke checks. Automated tests do not replace the real Feishu release gate.

```text
Hermes Gateway
  -> marker-wrapped minimal hook (gateway/run.py)
  -> hermes_feishu_card.hook_runtime
  -> authenticated/fail-open HTTP POST /events
  -> hermes_feishu_card.server
  -> session + render + Feishu CardKit send/update
```

The Hermes hook-to-sidecar `/events` path is fail-open. Sidecar unavailability or event rejection must not bring down Hermes; a message not confirmed as accepted by the card path continues through Hermes' native fallback. Once the card path accepts delivery, the hook suppresses duplicate gray native text.

## Components

### Minimal Hermes hook

The installer modifies Hermes `gateway/run.py` only through `hermes_feishu_card.install.patcher`, inserting marker blocks that can be detected, removed, and restored. Event extraction, delta coalescing, command cards, and Feishu adapter compatibility live in `hermes_feishu_card.hook_runtime`. The hook stores no Feishu credentials and does not rewrite Hermes session ownership, resume, or group-admission rules.

### HTTP sidecar

`hermes_feishu_card.server` receives events, routes by profile, bot, message, and reply anchor, manages `CardSession`, coalesces high-frequency deltas into bounded PATCH calls, and drains pending content before terminal updates. `hermes_feishu_card.cli start/status/stop` manages the local process. Stop verifies both the pidfile PID/token and `/health` `process_pid/process_token_hash` before terminating anything.

`/health` exposes only sanitized, hashed, process-local state, including event, event-auth rejection, card delivery, cleanup, and routing metrics. `send_card` is not blindly retried because a retry could create duplicate cards; updates to an existing message id use bounded retries.

### Session and rendering

`hermes_feishu_card.session` stores process-local streaming state. `render` produces CardKit JSON from thinking, answer, tool preview, notice, interaction, and terminal state. Cleanup bounds this transient data, but a sidecar restart does not promise recovery of an in-flight card. Hermes remains the source of truth for the agent workflow.

### Feishu client

`hermes_feishu_card.feishu_client` implements tenant-token acquisition, interactive-card creation, and message updates. Credentials come from local config or environment variables and must not enter the repository, cards, `/health`, or logs. Real Feishu evidence lives in release notes and `docs/wiki/feishu-acceptance.md`.

## Event transport security boundary

The default `server.host: 127.0.0.1` uses **local-process trust**. For upgrade compatibility, loopback `/events` can accept unsigned events; when the private state-directory transport root is available, the hook still sends an event authentication proof.

A non-loopback listener is rejected by default. Binding is allowed only with explicit `server.allow_non_loopback: true`, and the sidecar then requires a domain-separated HMAC event authentication proof over the raw request body, timestamp, and nonce. Missing, incorrect, expired, or replayed proofs are rejected before event parsing or card delivery. The root secret never enters YAML, environment files, cards, logs, or health output.

Event authentication provides source authentication and integrity, not HTTP encryption. Non-loopback mode is only for trusted containers or private networks that share the private state directory. Do not expose the sidecar directly to the public internet; public deployment requires an additional TLS/mTLS or controlled reverse-proxy boundary.

| Endpoint | Default boundary |
|---|---|
| `POST /events` | loopback local-process trust; explicit non-loopback requires event authentication |
| `POST /commands` | state-directory command transport proof |
| `POST /card/actions` | interaction token or operations transport proof |
| `GET /health` | unauthenticated but strictly sanitized; local liveness only |
| `GET /messages/{id}/summary`, `/interactions/{id}` | local hook collaboration indexes; must not be network-exposed |

## Legacy boundary

`legacy/adapter/`, `legacy/sidecar/`, `legacy/patch/`, and the old installer/patch scripts under `legacy/` are historical legacy/dual implementations, not active runtime. Current maintenance targets `hermes_feishu_card/`, the current CLI, the installer safety model, and `docs/wiki/`.
