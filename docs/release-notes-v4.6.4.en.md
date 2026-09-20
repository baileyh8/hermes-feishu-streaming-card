# V4.6.4 candidate: First-click interaction, ordered continuation and optional reading modes

[中文](release-notes-v4.6.4.md) | [English](release-notes-v4.6.4.en.md)

This is an incremental V4.6.x candidate. Implementation and automated checks do not mean a release, production upgrade, or desktop/mobile acceptance has completed. Record results separately in the [current acceptance checklist](wiki/feishu-acceptance-v4.6.4.md).

## No slash-card warmup

Enabled Feishu turns and first interaction requests wire card callbacks without requiring a prior slash confirmation, model picker or resume picker. Real generated Hermes closures may capture only `ctx`; ownership is recovered through the original TurnRunner's bound callbacks and checked against the same source, profile and live adapter. Reconnect refreshes the existing processor callback on its WebSocket loop and never replaces the live SDK dispatcher. Unknown structures and disconnected adapters remain fail-open.

## Read the continuation after your choice

A completed clarify or approval selection records a lazy display boundary. Actual subsequent text, tool/subagent activity or terminal content creates a schema 2.0 continuation on the same bot/profile/chat/topic. Consecutive questions do not insert empty continuation cards. Questions, options, operation scope and decisions remain readable; canonical answers, tools, attachments, timeline and turn metrics are not cleared.

An existing schema 2.0 owner is never replaced directly by a legacy interaction message. The display owner switches only after confirmed continuation delivery. Failed or uncertain creation keeps the existing owner and content instead of retrying a fresh send for every delta. When an interaction is the first and only legacy owner, fallback, terminal delivery and display recovery retain that dialect and a token-free static receipt. They never PATCH schema 2.0 onto it or resurrect an old approval.

Incremental segments show content after the choice. An authoritative full terminal snapshot is retained intact and labeled as the full turn result; the renderer does not guess and remove an old textual prefix. See the [continuation contract](wiki/interaction-continuation.md).

## Reading presets are opt-in

Add `card.reading_preset: classic|focused|detailed` and read-only `hermes-feishu-card card-config`. **Omitted configuration, fresh installs and upgrades keep the existing defaults.** Explicit fields win within each scope; scopes merge global → profile → bot.

- `focused` moves live thinking into a bounded panel, hides body tool activity on successful completion and retains failed activity.
- `detailed` expands process details within existing item and card budgets.
- Explicit `hide_completed_tool_activity: true` still hides completed/failed body tools; explicit `false` retains both, overriding the preset.

`card-config` explains effective YAML values and their sources, not whether a live process reloaded them. It changes no files or services. Adopting or reverting a preset requires a sidecar restart. See [reading presets](wiki/reading-presets.md).

## Retire only notices with proven ownership

Only recognized transient restart notices with an established route are registered. A later successful delivery/update retires a captured snapshot of that exact profile/bot/chat/thread; an empty thread is not a wildcard. Failed deletions retain ownership for a later eligible retry. Capacity, duplicate and late-deletion boundaries remain enforced. Answers, interaction receipts and failure explanations do not enter this cleanup path. Native home notices without explicit provenance remain in place; activity elsewhere under the same bot/profile is insufficient.

## Contributor preflight

`python tools/preflight.py --check-only` defaults to read-only checkout, interpreter/import-source and fixed-fixture checks. Explicit `--suite focused|full` runs real pytest and checks the selected diff base, using private state, Hermes home and an isolated operations target. Missing fixtures are never a pass; exit codes are preserved. Shareable JSON excludes raw logs, credentials and absolute private paths. See [testing](testing.en.md).

## Credits and limits

Thanks to [sthnow](https://github.com/sthnow) for reproduction and ordering evidence in [#335](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/335), and attached-patch author **babypanda**; eager-hook adaptation retains `Co-authored-by`. Thanks to [mouyong](https://github.com/mouyong) for continuation/notice design and code in [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331) and the validation workflow request in [#330](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/330). Reading presets also respond to [jackwude](https://github.com/jackwude)'s [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn)'s [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333). Both READMEs retain all historical credits.

Parts of #331 are adapted independently; the whole PR is not merged, old defaults are not reversed, and ambiguous home routes do not authorize deletion. This cycle's real-client verification for #335 remains to be recorded. Unverified historical reports such as #282 are not considered fixed by proximity. AMD/9Router, default-model changes and Hermes core upgrades are outside this candidate.

Release gates still include full regression, exact-merge CI, fixed-Hermes install/restore, the SDK matrix, platform assets/checksums, ordinary public-tag installation provenance, and recorded real-client results. This document is not a substitute for that evidence.
