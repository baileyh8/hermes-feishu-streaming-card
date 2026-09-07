# Hermes Feishu Streaming Card V4.4.1

[中文](release-notes-v4.4.1.md) | [English](release-notes-v4.4.1.en.md)

V4.4.1 addresses installation compatibility after Hermes 0.21 source decomposition, topic follow-up delivery, and single-process profile routing, with approval and reasoning readability improvements.

## Installation and topic delivery

- Incorporates PR #257's multi-file facade/mixin support with verified hook contracts, ownership, backups, and reversible restoration. Unknown source shapes remain rejected.
- Diagnosing patches carried away by Hermes upgrades still relies on current source, manifests, and ownership evidence; uncertain user changes are not overwritten.
- Incorporates PR #251's follow-up, queue/redirect, and cron reply-anchor work. Explicit turn identity remains an isolation boundary and valid reply anchors reach final delivery.

## Profiles and footer attribution

- Issues #83 / #259: single-process multiplexing follows trusted per-message profile identity rather than assuming that every configuration contains a profile named `default`. Fixed-profile multi-process operation remains supported.
- Issue #250: when the runtime reports an actual provider, the footer uses `provider/model` without duplicating the prefix. Unknown fallback attribution is not guessed from initial configuration.

## Approval and reasoning readability

- Issue #258: approval commands are no longer silently truncated at 3,000 characters. Escaped ordinary Markdown retains the complete scope and avoids fenced-code horizontal scrolling.
- Approval descriptions exceeding 12,000 UTF-8 bytes after JSON encoding return to native Hermes approval before card creation. No approval button is offered for a truncated command.
- Issue #253: opt-in `card.reasoning_format: code` displays recorded reasoning as visible code blocks while tool records remain in their panel. The default `panel`, `show_reasoning`, length limits, and final card-size gate remain active. See [configuration and behavior](wiki/card-readability.md).

## CI and validation boundaries

CodeQL init/analyze and their workflow tests are updated together. Focused renderer/config/runtime tests and server rendering integration passed; full pytest, regular-wheel, GitHub CI, and release-asset outcomes belong to this release's final validation record.

Real Feishu/Lark client acceptance has not been performed for this candidate. The original Issue #258 screenshot could not be retrieved; changes address confirmed code truncation and presentation behavior and still require reporter retesting. Historical Issue #73 has no current reproduction and awaits updated diagnostics.

## Contributors

- [liooil](https://github.com/liooil) contributed the Hermes facade-decomposition implementation in [PR #257](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/257); [Clarence-G](https://github.com/Clarence-G) contributed topic follow-up, queue/redirect, and cron delivery work in [PR #251](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/251). Original code commits and authorship are retained.
- [mouyong](https://github.com/mouyong) supplied multiplex-profile, topic, and readability feedback in [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83), [#252](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/252), [#253](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/253), [#258](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/258), and [#259](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/259); [shiboyumm](https://github.com/shiboyumm) opened the original [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83) configuration question; [Boer2333](https://github.com/Boer2333) requested provider attribution in [#250](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/250).
- [sp960817](https://github.com/sp960817), [Kevin32623](https://github.com/Kevin32623), and [shichenshuo-star](https://github.com/shichenshuo-star) reported Hermes 0.21 incompatibility in [#254](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/254), [#255](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/255), and [#256](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/256); [hnzwx](https://github.com/hnzwx) and [leavrcn](https://github.com/leavrcn) supplied additional reproduction and compatibility evidence in [#254](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/254). [micah928](https://github.com/micah928) supplied historical no-card evidence in [#73](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/73), whose current environment still needs retesting.
- Dependabot supplied the CodeQL updates in [PR #247](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/247) and [PR #248](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/248).
- Restored historical acknowledgements: [lanx214](https://github.com/lanx214) supplied the Linux reproduction in [Issue #240](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/240) ([V4.3.7](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.7)); [Lite-G](https://github.com/Lite-G) reported, reproduced, tested, and implemented the Feishu edit-fallback fix in [PR #235](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/235) ([V4.3.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.5)); [lyp88997](https://github.com/lyp88997) supplied the toast-only `200673` fix direction and observations of updates across environments ([V4.3.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.2)). These contributions belong to earlier releases; this cycle restores their missing historical attribution.

All earlier contributor acknowledgements remain in the [README](../README.en.md#contributors).
