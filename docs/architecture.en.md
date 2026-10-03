# Architecture

[中文](architecture.md) | [English](architecture.en.md)

The active mainline uses a sidecar-only architecture. Hermes Agent forwards lifecycle events through native plugin hooks and necessary exact compatibility hooks; Feishu/Lark card creation, updates, terminal rendering, session accumulation, diagnostics, and safe recovery live in `hermes_feishu_card/`. Hermes owns execution and authorization; HFC owns presentation and delivery. Earlier V4 releases recorded real Feishu private, group, topic, WebSocket card-action, and long-idle smoke checks. Those records are version-specific, not fresh acceptance of the current release on every client. Automated tests do not replace the real Feishu release gate.

```text
Hermes Gateway
  -> verified native plugin lifecycle hooks (hermes_plugin_runtime)
  +  exact compatibility/final-delivery hooks (hook_runtime)
     [capability-selected producers; one owner per event/delivery]
     -> signed POST /delivery/policy (before native suppression)
     -> authenticated/fail-open POST /events
     -> signed POST /runtime/events (hello/heartbeat)
  -> hermes_feishu_card.server
  -> policy + readiness + session + render + Feishu CardKit send/update
```

V4.3 established the Hybrid baseline on fixed Hermes `v2026.8.3` source. Real `hermes_agent.plugins` lifecycle hooks drive the sidecar through signed loopback transport; 17 exact patch groups supply ingress, delta, interaction, terminal, cron, and exact-Base evidence missing from that baseline's hook call sites. Its V3 manifest owns seven targets, the official plugin-config preimage, and venv identity in one transaction. Current strategies select entry points according to the verified Hermes source layout; those historical counts do not describe every version. Capability selection continues to verify source hashes/slices, runtime Python, entrypoint origin, and a real PluginManager subprocess. See the [Hermes source migration guide](wiki/hermes-decomposed-patcher.md) and CI for pinned compatibility baselines.

Approval, clarify, and slash callbacks use a separate runtime interaction listener that invokes the original Hermes pending handle/future resolver directly; it creates no second wait, poll, or queue. The sidecar event-id fence retains the first canonical response, and Feishu create/PATCH plus listener POST run outside session/message locks. Only successful card terminal delivery can suppress native success text; failed and interrupted turns remain native fail-open.

V4.1 domain-separates the event data plane from four control actions: `hfc-policy-v1` per-chat policy, `hfc-runtime-v1` runtime readiness, `hfc-native-handoff-recovery-v2` pending-descriptor recovery, and `hfc-native-handoff-ack-v1` post-delivery confirmation. Policy is enforced in both hook and sidecar. Runtime events prove liveness only and cannot authorize a file write. Recovery submits only one-way obligation, exact-content, delivery-plan, and target-scope hashes plus a canonical-route enum, never answer text or raw routing identifiers. ACK can run only after the Hermes ledger durably marks `delivered`. A control-plane failure must not stop Hermes Agent work, while install/recovery mutations remain fail closed.

The Hermes hook-to-sidecar `/events` path is fail-open. Sidecar unavailability or event rejection must not bring down Hermes; a message not confirmed as accepted by the card path continues through Hermes' native fallback. Once the card path accepts delivery, the hook suppresses duplicate gray native text.

## Components

### Minimal Hermes hook

The installer modifies Hermes only through `hermes_feishu_card.install.patcher`. Lifecycle hooks live in `gateway/run.py`; Hermes 0.19+ with a delivery ledger also receives two minimal exact hooks in `gateway/platforms/base.py`: one closes media/TTS paths with no independent text, and one submits Base-produced `text_content` after `record_obligation` / `mark_attempting` but before the native send. Both sources and the optional cron target use detectable, removable, restorable marker blocks under one manifest/backup transaction. Event extraction, delta coalescing, command cards, and Feishu adapter compatibility live in `hermes_feishu_card.hook_runtime`. HFC does not copy Hermes' media-cleaning pipeline, store Feishu credentials, or rewrite Hermes session ownership, resume, or group-admission rules.

### HTTP sidecar

`hermes_feishu_card.server` receives events, routes by profile, bot, message, and reply anchor, manages `CardSession`, coalesces high-frequency deltas into bounded PATCH calls, and drains pending content before terminal updates. `hermes_feishu_card.cli start/status/stop` manages the local process. Stop verifies both the pidfile PID/token and `/health` `process_pid/process_token_hash` before terminating anything.

`/health` exposes only sanitized, hashed, process-local state, including event, event-auth rejection, card delivery, cleanup, and routing metrics. Ordinary-card create/reply allows at most three send attempts only when a stable `delivery_uuid` is supplied; without it, there is one attempt. Updates to an existing message id have separate bounded retries. Send retries do not authorize replaying every event.

### Session and rendering

`hermes_feishu_card.session` stores process-local streaming state. `render` produces card JSON from thinking, answer, tool preview, notice, interaction, and terminal state. The standalone runner's private, bounded checkpoints can restore ordinary-card message identity and presentation. Nonterminal cards first show that execution state needs synchronization, then accept valid events from the original turn. Checkpoints never restore execution, waiters, or old approvals, and cannot recover cards that have no stored record. See [card connection recovery](wiki/card-restart-recovery.md) for capacity, privacy, and rollback boundaries.

Optional [CardKit streaming](wiki/cardkit-streaming.md) uses separate entities and strictly increasing sequences. Its entity state is currently process-local; ordinary-card checkpoints do not establish cross-restart recovery for those entities. Hermes remains the source of truth for execution.

### Feishu client

`hermes_feishu_card.feishu_client` implements tenant-token acquisition, interactive-card creation, and message updates. Credentials come from local config or environment variables and must not enter the repository, cards, `/health`, or logs. Real Feishu evidence lives in release notes and `docs/wiki/feishu-acceptance.md`.

## Event transport security boundary

The default `server.host: 127.0.0.1` uses **local-process trust**. For upgrade compatibility, loopback `/events` can accept unsigned events; when the private state-directory transport root is available, the hook still sends an event authentication proof.

A non-loopback listener is rejected by default. Binding is allowed only with explicit `server.allow_non_loopback: true`, and the sidecar then requires a domain-separated HMAC event authentication proof over the raw request body, timestamp, and nonce. Missing, incorrect, expired, or replayed proofs are rejected before event parsing or card delivery. The root secret never enters YAML, environment files, cards, logs, or health output.

Windows non-loopback startup fails closed when state-directory ACL privacy cannot be verified. Windows loopback continues under local-process trust without claiming that ACL privacy has been verified.

Event authentication provides source authentication and integrity, not HTTP encryption. Non-loopback mode is only for trusted containers or private networks that share the private state directory. Do not expose the sidecar directly to the public internet; public deployment requires an additional TLS/mTLS or controlled reverse-proxy boundary.

| Endpoint | Default boundary |
|---|---|
| `POST /events` | loopback local-process trust; explicit non-loopback requires event authentication |
| `POST /delivery/policy` | state-directory transport root, short timestamp window, and nonce replay protection; responses do not echo ids |
| `POST /runtime/events` | authenticated hello/heartbeat in a separate runtime domain; updates sanitized readiness only |
| `POST /native-handoff/recover` | separate recovery domain; exactly matches obligation/content/plan/target hashes and a `create`/`thread-create` route within the one-hour window, without answer text or raw routing identifiers |
| `POST /native-handoff/ack` | separate ACK domain; confirms a handoff only after the Hermes ledger durably records `delivered` |
| `POST /commands` | state-directory command transport proof |
| `POST /card/actions` | interaction token or operations transport proof |
| `GET /health` | unauthenticated but strictly sanitized; local liveness only |
| `GET /messages/{id}/summary`, `/interactions/{id}` | local hook collaboration indexes; must not be network-exposed |

## Legacy boundary

`legacy/adapter/`, `legacy/sidecar/`, `legacy/patch/`, and the old installer/patch scripts under `legacy/` are historical legacy/dual implementations, not active runtime. Current maintenance targets `hermes_feishu_card/`, the current CLI, the installer safety model, and `docs/wiki/`.
