# V3.9.0 — Operations and Reliability

Pending release. Release candidate for acceptance. The tag has not been created, and the release assets have not been created.

V3.9.0 establishes an operations and reliability foundation for the sidecar-only plugin. It adds focused recovery controls and diagnostics without changing the normal streaming-card layout or footer.

## Highlights

- Feishu/Lark operations cards can show diagnosis, recheck, two-step **安全修复** (safe repair), and Gateway restart confirmation. When operations cards are unavailable, use the existing CLI commands; normal card delivery remains fail-open.
- Ownership is explicit: private operations do not compare operators, while group repair/restart confirmation remains with the initiating operator. The stateful command transport uses a zero-configuration secret in the private sidecar state-directory transport root, rather than configuration or environment variables.
- Profile-aware setup resolves `--profile-id` / `--event-url` before process environment, then the selected env file, then defaults. Only `doctor` shows the complete redacted identity/profile/event-endpoint route chain; `status` summarizes runtime routing and profile events, while `/health` reports actual routing health fields.
- Known-safe install evidence may be automatically repaired during install/setup; `--no-repair` opts out. Unverifiable user edits remain refused. Lifecycle cleanup keeps runtime state and cleanup history bounded.
- Hermes compatibility and existing-container Docker install paths remain supported by automated coverage. Existing-container Docker smoke is still pending acceptance.

## Contribution

PR #84 by @Zanetach contributed card progress-status routing and `.env` allowlist expansion for profile environment support.

## Validation

- Automated release gate: `1164 passed, 3 skipped`.
- Real Feishu private-chat acceptance passed on 2026-07-11: `/hfc doctor` without a gray native unknown-command reply, localized details, two consecutive rechecks including the background successor, same-card updates, sandboxed two-step safe repair, restart confirmation/cancel, and the normal streaming-card footer.
- Pending real Feishu acceptance: actual card-triggered Gateway restart, group initiator repair/restart, changed-operator rejection, topic, cron, and profile route mismatch. These are not claimed as verified here.
- Pending existing-container Docker smoke: fresh install, pinned upgrade, known-safe corrupt-marker auto-repair, refusal of user edits, main/child profile endpoint mapping, and final `doctor`.

## Expected Release Assets

The release-assets workflow is expected to publish four assets after the approved tag is created; this preparation does not create them:

- `hermes-feishu-card-v3.9.0-macos.tar.gz`
- `hermes-feishu-card-v3.9.0-linux.tar.gz`
- `hermes-feishu-card-v3.9.0-windows.zip`
- `hermes-feishu-card-v3.9.0-checksums.txt`
