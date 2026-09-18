# V4.6.1: Hermes 0.21.3 compatibility and transient notices

- Fix #326 by binding tool callbacks after the unconditional assignment, before the later muted-notification reset. Normal turns emit start/completion once; muted turns stay silent. Conditional-only or nested-function layouts cannot prove a supported hook location.
- Integrate #325: keep status notices as text and recall only known transient templates. An hourglass alone never authorizes deleting answers, queue acknowledgements, maintenance refusals or failure explanations. Preserve profile/chat/thread identity and reject foreign or invalid contexts.
- Send restart completion as native text with native fallback on failure. Independent notices cannot take over an existing answer-card alias.
- Avoid duplicate pending controls in the session card after an auxiliary approval card was sent; retain the dedicated controls and decided outcome.
- Show the latest two tool steps in start order while preserving every running tool. Timestamp package diagnostics; retain validated recall API codes and hashed IDs, never raw exception bodies or HTTP access paths. Recall the exact interrupt/steer acknowledgement via its event profile while retaining onboarding guidance.
- Keep body reasoning chronological and the tool panel newest-first. Omit the empty metrics row and duplicate footer completion note.

## Validation and boundaries

Stable Hermes commit `345cd2b057a452236de401d3534b8502a7465e8d` and the later 0.21.3 source layout are checked separately. Executing the real callback method from `c62bd9f2078a946108f1c9d9b24bf118963277ef` reproduces missing normal-turn tool events with 4.6.0; the fix emits both lifecycle events and respects the muted branch.

Source upgrade/repeat/exact restore, full regression, cross-platform CI, the exact merge commit, release assets/checksums and public tag installation are release gates. Final evidence is appended at publication. Simulated delivery is not mobile-client acceptance. Existing display recovery does not restore execution or old approvals.

## Credits

Thanks to [mouyong](https://github.com/mouyong) for [#326](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/326) and [PR #325](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/325) through `bf7409a`; original commits and authorship are retained. Maintainer additions cover recall boundaries, profile isolation, fallback and unsupported control flow.
