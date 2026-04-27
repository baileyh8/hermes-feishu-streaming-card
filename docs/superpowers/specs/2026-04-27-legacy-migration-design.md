# Legacy Migration Design

## Goal

Document a safe migration path from the repository's historical legacy/dual/patch implementations to the current sidecar-only runtime.

## Scope

This is a documentation-only change. It does not add automatic legacy patch cleanup because old scripts may have modified user Hermes files in ways the current manifest cannot verify.

## Design

Add `docs/migration.md` as the canonical migration guide. The guide directs users to stop any running sidecar, keep an external Hermes backup, restore current sidecar-only installs through the manifest-aware `restore` command, manually remove or restore legacy/dual patches only from trusted backups, run `doctor --hermes-dir`, then install the current sidecar-only hook.

The guide explicitly names legacy entry points such as `installer_v2.py`, `gateway_run_patch.py`, and `patch_feishu.py`, and states that they are not active runtime. It also repeats credential safety rules so App Secret, tenant token, and real chat IDs are not copied into public materials.

## Safety Position

Unknown install state remains fail-closed. If `restore` reports changed files or incomplete install state, the user must inspect Hermes `gateway/run.py` and backups instead of forcing cleanup.

## Verification

Documentation tests assert the migration guide is linked and includes the required commands, legacy names, fail-closed guidance, and credential warning.
