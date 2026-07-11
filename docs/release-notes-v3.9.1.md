# Hermes Feishu Streaming Card V3.9.1

Released on 2026-07-11. The `v3.9.1` tag triggers the release-assets workflow for the four packages listed below.

V3.9.1 is a focused reliability hotfix. It closes the regressions reported in #82, #92, and #96, incorporates the useful parts of PR #93, PR #97, PR #98, and PR #52, and keeps the established streaming-card footer/layout unchanged.

## Fixed

- **Completed answers remain complete (#96, PR #97).** A completed event with a substantial suffix now preserves the full final answer instead of collapsing it to a short tail. Native duplicate-reply suppression remains active. Contributed by @colinaaa.
- **Interrupted cards reach a stable terminal state (#92, PR #93).** Starting a replacement task drains the old session's coalesced updates and serializes its abandoned terminal PATCH, so a late update cannot restore a stale running card. Contributed by @colinaaa.
- **Model switching no longer times out (PR #98).** The WebSocket callback returns an immediate switching state, performs the model change in the background, and updates the original card. Exactly one fallback card is sent only when the original card cannot be updated. Contributed by @charles5g.
- **Verified marker-only damage can recover (#82).** When the manifest, backup, expected patched hash, and all non-marker content agree, repair reconstructs an owned completion marker. Unknown edits, hash mismatches, symlinks, and unrelated corruption still fail closed.
- **Source-stripped Hermes diagnostics are explicit.** A supported Gateway with no usable version file or Git metadata reports `version: unknown (source-stripped metadata)` and its anchor-derived strategy instead of presenting an invented version.
- **Loopback health checks ignore ambient proxies.** Requests to `127.0.0.1`, `localhost`, and `::1` use a no-proxy opener. This adopts the diagnosis and repair direction from PR #52 by @wjiemin49-ux; the tools package import syntax was repaired at the same boundary.

## Compatibility

- Normal streaming-card footer/layout is unchanged.
- Hermes hook behavior remains fail-open for unsupported runtime delivery paths.
- Installer recovery remains fail-closed unless the damaged state is fully attributable to this plugin.
- Python 3.9 and Python 3.12 remain supported.

## Contributors

- @colinaaa: PR #93 and PR #97.
- @charles5g: PR #98.
- @wjiemin49-ux: PR #52 diagnosis and loopback repair direction.

## Validation

- Focused regression suites cover completed-answer boundaries, interrupted-session update ordering, asynchronous callback timing and deduplication, local no-proxy health checks, marker-only recovery, and refusal of unknown edits.
- Full release gate: `1198 passed, 3 skipped` on both Python 3.9 and Python 3.12, followed by `git diff --check`.

## Release Assets

- `hermes-feishu-card-v3.9.1-macos.tar.gz`
- `hermes-feishu-card-v3.9.1-linux.tar.gz`
- `hermes-feishu-card-v3.9.1-windows.zip`
- `hermes-feishu-card-v3.9.1-checksums.txt`
