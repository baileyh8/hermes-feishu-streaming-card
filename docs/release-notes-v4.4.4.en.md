# Hermes Feishu Streaming Card V4.4.4

V4.4.4 fixes Issue #270: Hermes shutdown and restart notices originating from a Feishu topic no longer fall back to the parent chat's main stream.

## Fixed

- The HFC runtime wrapper preserves Hermes' `reply_to_message_id`, allowing the Feishu adapter to use the reply API with `reply_in_thread=true` for the original topic.
- On the current `GatewayRunner.start()` layout, the startup hook is installed before restart/startup notifications and pending delivery redelivery, so the first sends after restart use the same route.

## Safety Boundaries

- Metadata is supplemented only for Feishu when both `thread_id` and a reply anchor are present. Other platforms and unanchored sends keep their existing behavior.
- HFC does not edit Hermes `gateway/run.py` directly. The patcher continues to own, validate, and byte-reversibly remove every source hook.
- Hermes configuration continues to own the home-channel fallback startup policy. This release fixes only sends with an explicit topic anchor.

## Validation

- Focused hot-file regressions passed with `943 passed, 1 skipped`, and documentation/package metadata regressions passed with `101 passed`. Full pytest passed with `3533 passed, 9 skipped in 752.17s`, followed by a clean `git diff --check`.
- PEP 517 sdist/wheel builds passed. A fresh Python 3.12 venv installed the regular wheel with package and distribution version `4.4.4`, one Hermes plugin entrypoint, all 24 provenance slices, and working CLI help.
- Local production Hermes 0.21.0 loaded 4.4.4 from its runtime venv. After the official patcher install, only managed `gateway/run.py` changed as expected; the restarted sidecar and Gateway reached `runtime_ready / integrity=safe`.
- On 2026-09-08, a real `/restart` in a Feishu group topic returned its success notice inside the original topic without posting it to the parent chat's main stream. The exact active-work shutdown notice remains covered by the same metadata-wrapper automation.

## Credits

- [mouyong](https://github.com/mouyong): [#270](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/270) reproduction and screenshot.
