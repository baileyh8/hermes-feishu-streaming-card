# V4.6.9: Concurrent group cards and clearer onboarding

## Changes

- Fix [#348](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/348): a new group turn no longer retires another requester's active card solely because the chat and conversation match. Legacy fallback events preserve requester and chat metadata; unknown identity keeps the old turn active. Native plugin turns use a hashed Gateway execution scope, and redirects retire only their explicit source turn.
- Each turn finishes in its own card. Failed, duplicate and late events retain terminal protection; stopped tasks and lost pre-upgrade in-memory execution state are not resurrected.
- Merge [PR #349](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/349): compact rows for all single-select choices, preserving descriptions, order, callback values, custom input and multi-select behavior.
- Rewrite both READMEs around benefits, examples, installation and configuration. Retain all historical credits and recent updates; move older releases to linked history pages.

## Validation boundaries

- Identical regression selection: 26 failures / 4 passes on main, 30 passes after the fix. Covers legacy/native paths, missing started events, three concurrent requesters with one failure, late/duplicate completion and redirects.
- Candidate regular wheels, full tests, exact-merge CI, annotated tags, assets and public installation are separate release gates. Final evidence is recorded on the GitHub Release.
- The Feishu platform smoke uses an isolated candidate with simulated lifecycle events sent through the real API to the existing test group. It does not establish concurrent Gateway/model execution by multiple real humans or mobile visual acceptance.
- #344 remains open pending reporter versions and reproduction details.

## Credits

Thanks to [cainiaozp](https://github.com/cainiaozp) for #348's reproduction and root-cause evidence, and [mouyong](https://github.com/mouyong) for PR #349's implementation and regression evidence. The original code author and all historical README credits are preserved.
