# V4.6.2: shared maintenance proof and terminal tool rows

Native plugin observers now explicitly share the admission/HOME proof supplied by a present, same-process Gateway owner while retaining their own activity counts. Previously the native lease lacked those providers and kept the combined heartbeat conservatively draining with an unverified HOME. Missing Gateway owners, unknown leases, incomplete counts, HOME mismatch and changing owner epochs still prevent automatic shutdown.

`card.hide_completed_tool_activity` defaults to `false`, preserving existing rendering. Enable it to hide content tool rows and the legacy summary fallback after completed/failed turns. Live progress, approval layout, answers, timeline and footer counts remain unchanged. Restart the sidecar after changing configuration.

Thanks to [jackwude](https://github.com/jackwude) for [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and the [#329](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/329) evidence used in 4.6.1. Thanks to [mouyong](https://github.com/mouyong) for the configuration proposal in [#331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331). This release adapts only that feature with an opt-in default and completed/failed coverage, retaining code credit; the PR's other changes remain separate.

Regression coverage includes actual plugin bootstrap, Gateway loss, busy/incomplete native counts, HOME mismatch, real loopback HTTP terminal rendering and duplicate terminal delivery. Full tests, exact-merge CI, assets and public installation are release gates. Final evidence is recorded in the Release. Simulated Feishu delivery is not mobile-client acceptance. No model or provider credentials are changed.
