# v4.5.2: queued outcomes and transient notices

Integrates [mouyong](https://github.com/mouyong)'s [PR #310](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/310) through `d979c46`, preserving original authorship, with maintainer safeguards.

- Forward queued-turn outcomes so failures retain streamed answers instead of archiving them as successful output (#318). Prefer the finalized `_delivery_result` on newer Hermes; retain raw-result compatibility on older layouts.
- Recall successful fresh Working heartbeats and redirect acknowledgements after 15 seconds (#321). Failed sends, ordinary quoted replies, unknown platforms and missing IDs do not qualify. Existing answer-card ownership checks remain in force; successful edits do not rearm recall.
- Require a unique known async heartbeat function, success branch and adjacent fresh-send contract. Drift leaves the optional hook untouched. Test LF/CRLF exact restore, idempotency and executable send/edit/failure paths.
- Put tool ordinals before duration and show concrete work in the subtitle, preserving approval/completion states and existing redaction limits.

Fixed upstream source, session/render/server, cross-platform and public-install gates are distinct from real mobile acceptance. #282 was withdrawn by its reporter, not verified fixed. #319 missing interim content and #320 post-restart native feedback remain open pending environment evidence. Production was not upgraded in this cycle.
