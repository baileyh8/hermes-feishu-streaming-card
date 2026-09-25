# V4.6.7: stable heartbeats and compact approval choices

Keep Working heartbeats editable instead of deleting them after 15 seconds and forcing another send on each tick. Hermes still owns end-of-turn cleanup; one-shot status expiry stays unchanged.

Approval choices use auto-width columns, up to four per row, preserving full descriptions and legacy callback identity. Clarify layout, reading defaults, timeline budgets, producer provenance, Home isolation and expiry correction receipts remain unchanged.

Selectively adapted from [mouyong's PR #345](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/345). The old-card report in #344 has not been reproduced on current main; answer and thinking inputs survive neutral handoff. Do not close it without version/scenario evidence.

Core regressions: 4170 passed, 12 skipped. Final commit, exact merge, public installation and platform evidence are recorded separately in the GitHub Release. Desktop callback acceptance does not establish mobile visual acceptance.
