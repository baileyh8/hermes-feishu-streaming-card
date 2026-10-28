# v5.0.11 — Admission ownership checks for pinned Hermes main

This patch addresses [#471](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/471) by checking admission completion and the first display-policy owner for the pinned Hermes source `9b904dcc22c23fe8b82834339bbdd96495072e59`. Publication status is recorded in the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.11).

## Changes

- Add the full ingress/authority source digest pair for this exact revision, retaining checks of loaded code, native claim, row, route, generation, epoch and single-use terminal acknowledgement. This cohort covers admission completion; it does not support arbitrary Hermes main revisions.
- Before the first card, notice or interaction display-policy decision, borrow the original executing Task's claim and verify ownership; recheck after the policy request. If ownership is uncertain, pin the turn to native Hermes delivery from the first event, avoiding a card started with a stale profile or route that cannot complete handoff. The recheck does not make all concurrent changes atomic.
- An unpinned first event from an independent Task or thread without the original `execute_admission` frame falls back to native delivery. Cross-Task terminal handoff for a card already owned by HFC retains the existing protocol. A shared group's first sender can differ from the current turn sender; terminal handoff still verifies the complete turn identity.

## Installation and compatibility

This new migration path requires secure POSIX directory-bound transactions. Its successful installation on Windows has not been verified; existing Windows support for older source families is separate.

The new adapter accepts only exact `9b904dcc22` and verified official PM, configuration and bootstrap sources. Its candidate migration path requires an existing official PM generation, schema52 configuration and a valid boot receipt; canonical HFC already enabled with `allow_tool_override: false`; an existing schema2 HFC member without an observer marker; and either no SDK or PM-managed SDK 1.7.5 through the official `feishu` extra. Successful migration uses PM-owned HFC and SDK dependencies while preserving configuration and other profiles.

Fresh installations, uninitialized PM, old v1 receipts, missing/true flags, manually installed SDK 1.6.8, managed overlays, external secrets and unverified sources/configurations remain rejected before PM admission. This is not support for every historical upgrade; the reporter's exact starting state is still unknown. Refusal does not mean the entire CLI writes no files, for example private locks may be created. Do not delete protection markers or manually edit Hermes source to bypass checks.

Repeat installation and reinstall after `restore` require the same version and `spec_sha` (installation declaration), and reuse the verified receipt. `restore` restores managed source in the shared checkout while retaining PM, SDK, configuration and receipts. It is not a complete uninstall or an operation limited to one profile. Restart Gateway through its service owner. HOME-scoped `uninstall` uses official PM selection publication to move HFC from enabled to disabled for the current HOME. Dependency selection, member, SDK, shared source and original receipts remain; restart Gateway through its service owner for the change to take effect. It does not purge shared installation material or immediately disable an already-running process. Configuration drift and direct reinstall after this uninstall are rejected; do not delete revocation records to bypass protection.

Once released, enter `hermes-feishu-card-v5.0.11-<platform>/` after extracting an asset before running its installer. Preserve existing manifests, markers, backups and recovery material.

## Validation and remaining gates

The [PR #473](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/473) candidate included reproductions using pinned source and real SQLite with controlled model and transport ports. Ordinary-wheel child processes covered 18 scenarios on pinned `9b904dcc22` and 5 on v0.22.5. These checks do not replace real-service acceptance. The PR head `66a29a702414669eecb2283d31e9b8e6884a46a1` passed [full CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/40490693602) with 57 jobs and CodeQL; that evidence belongs to that commit and does not automatically cover a new commit containing release material.

The production-enabled ordinary wheel `202a0c0bb7845d6d30a10b2233043df2c5e3c4a872812ebdefb9de9253c0aada` completed the restricted 5.0.10→5.0.11 migration using real PM in a private environment. Uninstall covered failures before and after PM publication, an unknown ticket, successful retry, and fresh-process loopback delivery for two HOMEs: the target stopped requests while the other retained delivery. The selected generation was not rebuilt; this is not evidence of a new C generation. An earlier ordinary wheel completed same-version/same-declaration source restore and reinstall; that evidence is recorded separately from the final wheel uninstall checks.

Publication requires full CI for the exact final commit, public installation, tag and release-asset acceptance, recorded with the GitHub Release. The reporter's deployment, a real Gateway/model conversation and Feishu client visuals and interaction have not been retested; the field report in #471 is not claimed resolved by these checks.

## Credits

Thanks to [lv86snorlax](https://github.com/lv86snorlax) for the source revision, failure report and source analysis in #471. Repair and test commits are by [baileyh8](https://github.com/baileyh8); issue evidence and code authorship are credited separately, and all earlier credits remain.
