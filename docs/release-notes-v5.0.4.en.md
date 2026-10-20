# V5.0.4: legacy observed rates, compression notices and cross-Task completion

This release preserves original authorship and integrates [PR #442](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/442), [PR #444](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/444) and [PR #445](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/445).

## Changes

- **Legacy output rate:** an observe-only official plugin supplies opt-in `output_rate` on source-compatible installs. Legacy remains the only event producer, without another listener, hello or heartbeat. Use exact per-invocation usage/timing, independently of cumulative footer tokens. Preview is read-only; terminal consumption happens once, and cross-Task delivery/retries reuse the frozen payload. Missing, mixed, expired or unattributable samples stay hidden. The default remains off.
- **Install and recovery:** restore only proven official-enable config postimages. Preserve unknown results, concurrent user edits and recovery evidence. Only confirmed missing observer metadata admits the existing Hybrid fallback; PermissionError/EIO and other uninspectable states disable admission.
- **Exact compression notices:** recall the complete native `📦 Preflight compression: ~…` and `✓ Context compaction complete — continuing turn...` templates after 15 seconds through the existing owner. Preserve native results, routes, Working/HEARTBEAT updates, failures, appended content and unknown text. Persistently editable status is not treated as these one-shot notices.
- **Session Authority completion:** finish cards when admission executes in a drain Task and delivery resumes in a waiter Task, including drain-owned delivery without a waiter. A bounded, single-use handoff borrows the original admission/event. Keep the original execution handle, Task gate, Base extraction/media/TTS and delivery ledger. Per-admission sender, epoch, route, cancellation and late-event checks remain independent, without another execution claim registry.

## Compatibility and fallback

The new Session Authority bridge accepts only the exact source and loaded-code topology verified against Hermes `5fc490e2b34123212d73ef316086f939567a725a`. Unknown source/topology, missing extraction seams, identity or epoch changes retain native delivery with bounded redacted diagnostics. This does not claim support for every Hermes 0.22.x or later main revision. Existing non-Authority paths keep their compatibility contracts.

Upgrade through the official installer for `v5.0.4`, preserving config, env, Hermes HOME and service ownership; see the [upgrade guide](upgrade-v4-to-v5.en.md). Enable rate display explicitly through `output_rate` in the existing `card.footer_fields`; see [measurement semantics](wiki/observed-timing.md).

## Verification scope

Release gates cover combined source regressions, actual Python 3.9, Ruff/mypy, full preflight with a verified clean Git fixture, separate pinned upstream CI, ordinary non-editable wheels/child processes, native SQLite admission/ledger, loopback HTTP, install/reinstall/byte restoration, and exact merge/tag CI, assets and public installation. Exact-candidate acceptance and the Release record carry results. Separate candidate test totals are not combined-regression evidence.

Real Feishu API smoke is separate from automation. Actual Gateway/model execution and desktop/Android visuals/clicks remain unverified; iOS and new-user trials are outside this release scope. Original #434/#443 reporter environments have not been retested. Existing #344/#361 uncertainty remains open.

## Credits

Thanks to [Lite-G](https://github.com/Lite-G) for #441 evidence and #442 original code; [Timeral](https://github.com/Timeral) for #434 reproduction and field retesting; and [JohnZhong505](https://github.com/JohnZhong505) for #443 field evidence, with additional source analysis from Lite-G. Evidence, analysis and repair code authorship are credited separately. All historical commits and earlier credits in both READMEs remain intact.
