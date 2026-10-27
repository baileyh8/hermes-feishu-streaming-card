# v5.0.10 — Asynchronous completion and owned-config recovery

This patch addresses [#469](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/469) and [#470](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/470), with diagnostic follow-up for [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451). Published status and exact acceptance evidence are recorded in the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.10).

## Changes

- **Asynchronous notification handoff:** borrow the original execution owner only from a verified native automation admission with matching claim, durable row and route. Stock Hermes v0.22.5 encodes composite message IDs as JSON strings; arbitrary list/tuple concatenation and identity inferred from display fields remain unsupported.
- **Terminal acknowledgement race:** retain strict current-owner, generation, epoch and superseded checks before submission. Once the sidecar explicitly accepts a terminal event, consume only that submission's acknowledgement, preventing a generation change during the await from causing the same native text to be sent again. `applied=true` means the sidecar owns presentation delivery, not that the Feishu PATCH has finished; delivery and retries remain with the sidecar.
- **Legacy observer config ownership and recovery:** inspect and reverse provable HFC-owned changes while preserving unrelated user edits. Conflicting managed values, unverified evidence and concurrent filesystem changes remain fail-closed. Runtime identity changes still use explicit recovery; markers and historical evidence are not silently removed. Fixed-tag Hybrid v3 retains its existing config recovery protocol.
- **Diagnostics:** distinguish fixed admission rejection categories without logging raw user/session identifiers or configuration content.

## Acceptance boundaries

Pinned upstream reproductions, positive and negative cases, duplicate/late events, independent QA/review, ordinary-wheel child processes and provenance, full platform CI and controlled real Feishu API checks are separate gates. Public tag installation, anonymous downloads, checksums and actual extraction are independently verified; pending stages are never reported as passed.

The latest #451 report includes custom profile routing and other local hotfixes. This patch does not claim to reproduce or resolve every symptom in that deployment. Reporter environments, real Gateway/model conversations and desktop/Android visual or click acceptance remain separate and unverified; iOS and new-user trials are outside this release scope.

Upgrade using the actual Hermes checkout, HOME and configuration, refresh owned hooks through the official installer, and restart the matching sidecar/Gateway through their original service manager. Retain manifests, markers, preimages and journals. Release archives use a versioned top-level directory; enter `hermes-feishu-card-v5.0.10-<platform>/` before running the installer.

New legacy markers/journals contain `config_delta`, which v5.0.9 cannot directly read. Before downgrading, use v5.0.10 for controlled restore/uninstall and confirm unrelated user configuration is preserved, then install the older release. Do not downgrade the package first and delete ownership evidence to bypass refusal.

Stop Gateway, auto-updaters and other config writers before recovery, and do not edit the configuration concurrently. Recovery rechecks files, directories and ownership evidence before writing and refuses detected changes. POSIX validation and replacement are not one atomic conditional operation; a non-cooperating writer racing the final replacement is outside this supported boundary. Without drift, recovery restores the original bytes; with unrelated drift, it retains current non-HFC configuration and list order while reversing only owned changes.

## Credits

Thanks to [leavrcn](https://github.com/leavrcn) for the #469/#470 reports and analysis, and [kludgezol](https://github.com/kludgezol) for the multi-profile field evidence in #451. Issue evidence is credited separately from code authorship. Existing authors, tags, release assets and historical credits are retained.
