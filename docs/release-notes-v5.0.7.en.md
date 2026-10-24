# v5.0.7 — Hermes 0.22.5 compatibility and callback handoff

This candidate addresses [#456](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/456), [#453](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/453) and [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451). Acceptance is pending; the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.7) records the published result.

- Verify the v0.22.5 completion wrapper, inherited ownership and usage-limit offer ordering while retaining original delivery and `agent:end` contracts. Keep v0.22.4 as a separate regression source.
- Correct per-turn reasoning/tool callback ordering while preserving approval callbacks and isolation when an Agent is reused.
- Add fixed-category terminal diagnostics for #451. Four/five-API-call, footer, normal/waiter and actual Base-function tests did not reproduce the reported duplicate; the issue stays open for reporter verification. A stream-consumer warning alone does not prove a missing completion hook.
- Add a separately verified v0.22.5 official config-writer family, preserving byte-exact planning, failure rollback and PM generation wheel provenance.
- Unsupported-source CLI probes return exit code `1`. The reported `0` remains unreproduced and is not claimed as fixed.

Upgrade using the actual Hermes checkout, HOME, configuration and existing service owners. Refresh managed hooks through the installer, then restart sidecar and Gateway. Do not remove manifests or edit installed Hermes files to bypass unknown drift.

Archives retain the v5.0.6 versioned-root layout. Enter `hermes-feishu-card-v5.0.7-<platform>/` after extraction. Historical tags and assets remain intact.

Focused regressions, exact-commit full CI, ordinary/public package installation, real Feishu API and asset/checksum verification remain separate gates. Actual Gateway/model execution, reporter reproduction and desktop/Android visual/click acceptance are unverified; iOS and new-user trials are excluded.

Thanks to @coder-zhw (#456), @Kevinzz224 and @ywarmy (#453), and @coytus, @Lite-G and @leavrcn (#451) for reports, analysis and reproduction evidence. These credits are separate from code authorship in Git. All historical contributor credits are retained.
