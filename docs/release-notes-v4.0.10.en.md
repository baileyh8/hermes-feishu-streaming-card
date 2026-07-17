# V4.0.10

V4.0.10 hardens the event-transport boundary between the Hermes hook and the HFC sidecar while preserving compatibility for default local installs.

## Security improvements

- Default `127.0.0.1`, `localhost`, and `::1` listeners retain the local-process trust model; upgrades need no new credential or hook URL.
- Non-loopback listeners such as `0.0.0.0`, private addresses, and named hosts now fail before binding unless `server.allow_non_loopback: true` is set explicitly.
- In explicit non-loopback mode, every `/events` request requires an HMAC-SHA256 proof over the exact raw body, timestamp, and nonce. The 30-second window rejects forged, stale, and replayed requests.
- Proofs reuse the permission-restricted operations transport root in the sidecar state directory. The secret is never written to config, env files, logs, cards, health, or diagnostics.
- HMAC provides authentication and integrity, not encryption. Cross-host deployments still require a trusted private network and TLS or mTLS before any public or untrusted link.

## Observability and maintenance boundaries

- `/health` exposes the boolean `event_auth_required`; authentication failures increment `events_rejected` and `event_auth_rejections`.
- CLI `status` and card-safe diagnostics can expose the bounded rejection count without timestamp, nonce, signature, or transport-root material.
- Chinese and English architecture docs now describe the current V4 flow. A new fail-open matrix separates safe Hermes-native fallback from security, network-exposure, and installer-ownership failures that must stop.

## Compatibility

- A loopback sidecar still accepts unsigned events from an older hook, avoiding an outage while Gateway and sidecar versions are briefly mixed during upgrade.
- The current hook signs `/events` whenever it can read the secure transport root. Read/sign failures remain fail-open for Hermes and do not affect non-HFC native paths.
- `/commands`, card callbacks, and the existing operations-proof domain remain separate from event proofs.

## Validation

- Focused security matrix: `523 passed`.
- Final full automated gate: `1362 passed, 4 skipped`, plus `git diff --check`.
- `uv build` produced both the sdist and wheel. A clean Python 3.12 venv imported `hermes_feishu_card==4.0.10` and `event_auth` from the wheel, with the expected console entry-point metadata.
- Live Hermes `v2026.7.7.2` upgraded its Gateway venv from 4.0.9 to 4.0.10 through the official `install` path; `doctor` reported consistent runtime/import/install/recovery state.
- An authenticated Feishu user sent one unique transport smoke. The sidecar received and applied 3/3 events, completed one send and two updates, and reported zero event rejections, `event_auth_rejections`, or delivery failures. The client exposed one completed interactive card and zero matching native app-text duplicates.

## Release assets

- `hermes-feishu-card-v4.0.10-macos.tar.gz`
- `hermes-feishu-card-v4.0.10-linux.tar.gz`
- `hermes-feishu-card-v4.0.10-windows.zip`
- `hermes-feishu-card-v4.0.10-checksums.txt`
