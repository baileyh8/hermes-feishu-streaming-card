# v5.0.25: terminal acknowledgement recovery and Windows installation guidance

This release fixes terminal acknowledgement recovery, the Windows credential probe and redacted installation guidance. Public packages and final gate records are available through the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.25).

## Scope

- **#529 lost terminal acknowledgement**: reconcile the identical event once, using its existing `event_id` and full payload, when a terminal was accepted but its HTTP response was lost. Keep the initial request budget, send lock, single-use `AppliedAttempt` and strict `applied` result. Replay and native-handoff recovery share the existing failure-recovery budget. Unknown outcomes, explicit refusal, binding changes and cancellation retain their contracts. No checkpoint or similar text grants `content_delivered`; no additional execution queue or owner is introduced.
- **#526 Windows PowerShell 5.1**: run the credential probe from a temporary Python file so native argument parsing cannot strip embedded quotes from `-c` code. Preserve credential/profile selection, failure checks and temporary-file cleanup. Validate Windows PowerShell 5.1 and PowerShell 7 separately.
- **#526 redacted diagnostics**: retain fixed, safe explanations for known installation states. `clean` means that no existing hook installation state was found. Source anchors, streaming configuration and runtime activation are distinct observations. Ownership, protection records and automatic repair rules do not change.

## Reproduction and field evidence

Maintainer isolation reproduced a terminal applied by the real sidecar and patched once, followed by a disconnected or timed-out acknowledgement and a duplicate native body. Normal acknowledgement and an unaccepted request are controls. The experiment uses pinned original Hermes functions and real loopback HTTP; database and Feishu I/O remain test ports. It establishes the shared failure path, not a reproduction of the reporter's Linux / Hermes v0.23.1 process.

For #526, five reported source hashes match pristine official `3b421578` bytes. The reporter subsequently confirmed that source hooks had been absent. After `setup` and a Gateway restart, the field process applied 52 events, created one card and completed 41 updates. This reporter-provided recovery evidence is separate from this release's Windows installer and diagnostic changes. Previously requested field evidence has arrived.

PR #530 retains its Windows 3.14.9 ordinary-wheel, native-entry and Hybrid-bootstrap controls. Synthetic Feishu input and loopback model/sidecar endpoints do not establish an existing PM/observer process or client acceptance.

## Verification status

Local checks passed: 11 independent fault/cancellation regressions, 60 existing observer/native-recovery cases, 12 identity unit cases and 104 documentation/metadata checks. All 165 ordinary-wheel resources match byte for byte; every HFC module loaded by the isolated child comes from the ordinary installation. Four controls cover disconnect, a real ten-second timeout, unaccepted requests and normal acknowledgements.

Three real Feishu API controls passed: lost first acknowledgement produces one final update and no native body; an unaccepted terminal retains one complete native body; normal acknowledgement produces no duplicate. These use the actual sidecar, pinned original Hermes functions and real platform I/O. Database/model ports remain test doubles, so this does not establish a real Gateway/model or reporter-host reproduction.

Release gates require exact-commit full Python/platform/upstream CI, Windows PowerShell 5.1/7, merge/tag provenance, release assets and a public ordinary installation. Exact commits, run links and client scope are recorded separately in [PR #530](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/530) and GitHub Release. Missing checks are never counted as passed. When all terminal receipts remain unreachable or sidecar deduplication state is lost, native fallback still protects the answer; this failure boundary does not guarantee no duplicates.

## Installation and recovery

Source compatibility in `doctor` does not replace installation or a fresh-turn check. When no managed hook installation exists, review the selected Hermes root, HOME, HFC configuration and environment file, then use normal `setup`. `repair` maintains an existing verifiable installation and does not replace initial installation. Preserve manifests, backups and protection records; follow [PM recovery](wiki/pm-recovery.md).

## Credits

Thanks to [leavrcn](https://github.com/leavrcn) for the terminal duplicate-delivery timeline in [#529](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/529), and [nmtlcxb](https://github.com/nmtlcxb) for Windows recovery and PowerShell 5.1 evidence in [#526](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/526). Issue evidence is distinct from code authorship; historical credits, commits and tags remain.
