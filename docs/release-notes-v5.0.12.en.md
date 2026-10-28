# v5.0.12 — Feishu source anchors and bounded terminal handoff

This patch addresses [#474](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/474) and [#469](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/469). Publication status follows the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.12).

## Changes and boundaries

HFC publishes its source message anchor only for Feishu, preserving LOCAL and other platform sources. The reporter identified a LOCAL live/receipt source mismatch that prevents subsequent turns. This does not attribute every upstream storage_unavailable failure to HFC.

Feishu terminal handoff distinguishes a verified current-turn anchor from native execution identity. The exception only accepts a missing message_id on the Feishu admission source when both the live source anchor and HFC turn marker exactly equal the current canonical message ID. Reverse omissions, different nonempty anchors and unknown turns remain rejected. It retains strict comparison of all other native source fields, original Task/claim/durable row, profile, route, sender, thread, generation/epoch and single-use acknowledgement. The reporter's identity-field allowlist alone is not a safety proof.

## Upgrade and recovery

Installing a new wheel alone does not refresh the hook already loaded by Gateway. Existing installations must use the normal installer/repair entry to refresh the managed patch and restart Gateway and the applicable sidecar through their service owner. Keep manifests, markers, backups and journals. Do not edit Hermes source or delete protection records. Old-patch upgrade, repeated installation and byte-for-byte restore require separate verification.

For a managed PM generation change performed by this installation itself, the installer first verifies the old observer, source, config and runtime provenance, then hands only the observer runtime identity to that operation’s verified new generation. Config contents remain unchanged. Source repair follows admission, and failed handoff rolls back through the registration transaction. Pre-existing external runtime drift is not automatically accepted.

If PM generation changed during an upgrade, `setup` can first refuse safely with `legacy observer runtime binding changed`. Preview the official `integrity rebind-legacy --hermes-dir <checkout> --hermes-home <HOME>` using the same actual checkout and HOME, then explicitly apply with `--yes` and retry the original `setup --repair`. Rebinding verifies ownership of the installed source and backups without mistaking a newer renderer for an uninstalled source; source drift, partial restore and unknown backups remain rejected. Do not delete markers to bypass rebinding.

This patch does not expand v5.0.11's restricted exact Hermes 9b904 installation starting states. Fresh PM, old receipts, unverified SDKs and source drift retain their existing refusal contracts. Enter hermes-feishu-card-v5.0.12-<platform>/ after extraction before running an installer.

## Validation boundaries

Both failures were independently reproduced against pinned v0.22.5 source. The repaired LOCAL recovery check passed three consecutive turns, with receipt/policy test ports. Normal/internal events passed the rendered-hook-to-completion chain on v0.22.5 and pinned 9b904. The affected completion suite passed 434 tests; two missing local 9b904-checkout checks were skipped and require the corresponding CI gate, not an assumed pass. Ordinary-wheel upgrade/restore, exact full CI, tag, public installation and actual assets remain separate gates tied to the published Release commit. Simulation and CI do not replace real Gateway/model, reporter deployment or Feishu client visual/interaction acceptance. The published Release records final evidence.

Issue #459 was acknowledged and closed based on the reporter's successful original macOS/POSIX PM environment retest, separately from this patch's acceptance.

## Credits

Thanks to [Alex-HJY](https://github.com/Alex-HJY) for #474, source analysis and field-patch evidence, and [leavrcn](https://github.com/leavrcn) for the #469 retest and mismatch analysis. Code authorship follows Git commits; issue evidence is credited separately. Existing credits and release history remain intact.
