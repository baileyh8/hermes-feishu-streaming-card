# V4.7.2: update shutdown protection and card callbacks

- **#375:** Reject card-driven `/update` for an active persistent sidecar before shutdown, and recheck after confirmation. If sidecar stop fails before hook mutation, request Gateway restart only with unchanged HEAD and verified hooks. A successful restart command is not a health claim. Persistent-service automatic update remains unsupported; use terminal maintenance.
- **#374:** Repair the verified lark-oapi 1.6.8 WebSocket CARD-frame drop on the live instance's WS loop. SDK fragmentation, response encoding and other events remain intact. No SDK files/global class are changed; unknown implementations remain untouched. Personal Edition / Termux still needs field retesting. Missing logs in the original Hermes callback alone do not prove platform non-delivery.
- **#372:** Add explicit `integrity acknowledge-review --rebind-target OLD_TARGET_SHA256`. Requires the exact old identity, two installed-plan/stopped-sidecar checks and unchanged snapshot CAS; preserves independent restart evidence. No automatic identity weakening.
- **#370:** Add a bilingual visual-region configuration map near the front of the user guide, with README entry points and corrected released-task comments.
- **PR #373:** Preserve Nevoker's commit supporting Python 3.14 slice constants in semantic fingerprints; unknown constants remain rejected.

## Upgrade

Use the existing installer, rerun setup/install, and restart sidecar and Gateway through their current service owners. Send `/new` again for a fresh card; expired cards cannot authorize work. For persistent services use terminal maintenance with official Hermes update and HFC installation, then status/doctor checks; do not delete ownership manifests or units to bypass checks.

See [migration](migration.en.md#explicit-orphaned-target-rebinding) for the complete identity-recovery command and prerequisites.

## Validation boundary

Final release-gate evidence belongs in the GitHub Release record. SDK/protobuf tests use synthetic data and are not Personal Edition / Android / iOS field acceptance. No new card layout is introduced. Unconfirmed field reports remain open.

## Credits

Nevoker (PR #373), DaveWang888 (#375), ffdxdynotable (#374), ywarmy (#372), jackwude (#370). Existing contribution records and original code authorship are preserved.
