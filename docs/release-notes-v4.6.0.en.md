# V4.6.0: combined stability fixes

Profile-aware transient recall (#323), per-turn structured reasoning with stream/snapshot deduplication (#319), bounded and redacted terminal retries, queued-final metrics and tool/timeline presentation (PR #310), and private card-display checkpoints plus bounded startup policy readiness (#320).

Checkpoints restore display and delivery identity, never execution, approval tokens or native admissions. They are private, capped at 128 records / 1 MiB each and retained for 24 hours. Cards predating checkpoints cannot be retroactively restored. Disk failures do not cause duplicate sends. See the [recovery design](wiki/card-restart-recovery.md).

Validation covers failing reproductions, real loopback HTTP, original-card completion, duplicate/cross-scope events, expired approvals, disk errors and actual generated callbacks. Reporter Hermes `8017dfa4a87aac9b9ae478f6b465508ca904ebb6` was checked through source-only upgrade, repeat install, real callback execution and exact restore. Final full-suite, CI, asset and public-install evidence is added on publication. No production rollout or mobile-client acceptance is claimed here.

Credits: [mouyong](https://github.com/mouyong), [PR #310](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/310) through `f35a4ac` and #320 evidence, with original authorship retained; [zhangzq](https://github.com/zhangzq), [#319](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/319) reasoning diagnostics; [qqqq560204-maker](https://github.com/qqqq560204-maker), [#323](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/323) profile-routing reproduction. All previous README credits remain.
