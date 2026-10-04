# v4.7.1: Slash-confirm callback repair

This patch repairs two callback defects affecting `/new` and other slash confirmations: missed interception after callback rebinding, and a retained dispatcher resolving a confirmation without updating its card. Existing layouts and configuration defaults remain unchanged.

## Fixes

- Inspect both actual synchronous and asynchronous callback methods instead of trusting inherited or stale installation flags. Rebound HFC buttons no longer fall through to Hermes' ordinary `/card button …` command path in the reproduced cases.
- Preserve the actual native fallback across older HFC wrapper identities, avoiding recursive wrapper stacking while keeping unrelated native buttons with Hermes.
- Publish the result card from the retained-dispatcher asynchronous confirmation path, including the existing delivery fallback. Resolve through the original Hermes handle once and use the stored IM message ID, never the callback token as a reply target.

## Validation

- **1,137 passed, 0 failed, 0 skipped** across focused callback, cold-start, SDK, adapter-resolver, runtime/server and documentation checks.
- **24 isolated cases** using upstream callback code, the real Lark SDK dispatcher and Hermes' original confirmation registry passed against **0.19.0** (`3ef6bbd`), official release **0.21.5** (`f97608f`) and the main snapshot checked for this patch (`af90026`). Cases include the native failure control, ordinary callbacks, retained dispatchers, rebinding and duplicate clicks.
- Exact-merge full CI, ordinary-wheel provenance, annotated tag, archive checksums and public installation are recorded in the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.7.1).

The reported 0.19.0 logs establish a button becoming `/card`, an unknown-command error, then Feishu 99992354 when the error reply uses a non-IM identifier. The reporter's HFC version and button `action.value` are still unavailable. These fixes address reproduced defects; the reporter's exact missed-interception trigger and real-client retest remain unverified. This patch adds no new desktop or mobile visual-acceptance claim.

## Upgrade

Update to `v4.7.1` through the existing installer, then restart the sidecar and Hermes Gateway through their existing service owners as directed by setup. Updating package files alone does not replace callbacks in an already-running Gateway. Send `/new` again and use the new confirmation card after restart; do not reuse old confirmation cards or edit Hermes source by hand.

## Credits

Code authored by [baileyh8](https://github.com/baileyh8); see [PR #371](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/371). Thanks to the user who supplied logs through the maintainer. No public attribution was provided, so their identity is not inferred. Earlier code, proposal and field-report credits remain in the READMEs, CHANGELOG and release history.
