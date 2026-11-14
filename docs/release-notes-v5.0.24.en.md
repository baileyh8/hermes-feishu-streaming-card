# v5.0.24: PM recovery order and optional approval-rule hints

## Changes

- **#524 recovery boundary:** `repair --hermes-home` verifies the observer HOME binding before changing managed source. When a PM rebuild coincides with reset hooks, use the existing managed-source repair before previewing `integrity rebind-legacy` against the current owned runtime. Review fences retain their exact old-target and CAS checks. Installation refusals and the [PM recovery guide](wiki/pm-recovery.md) explain that order. No marker, fence or service record is deleted, no runtime/member/wheel provenance gate is bypassed, and no service is restarted automatically.
- **#525 native rule hints:** add `card.approval_rule_hints`, default `false`. When enabled, bounded native Hermes `pattern_keys` (or the singular `pattern_key` when the list field is absent) map known categories to Chinese labels inline below the complete command and before the original controls. These labels describe native rule matches, not a complete impact assessment. Unknown keys are never echoed. HFC does not parse commands, call a model to infer effects or recommend an approval choice. Hints are not checkpointed; the final combined card drops them first when needed to fit its budget. The complete command, authorization scope, original buttons and resolver remain authoritative.

## Current evidence for #526

The report concerns missing cards on a real Windows DM Gateway with HFC v5.0.22 and Hermes v0.23.1. Its body lacks the quoted complete error log and actual installation strategy. Passing exact v0.23.1 source/topology checks establishes capability detection, not successful first-card delivery. The reported environment has not been independently reproduced; this candidate must not be described as resolving that report.

## Acceptance scope and evidence

- All 165 content files in the ordinary wheel match the reviewed Git version. A non-editable child process loads version 5.0.24.
- The real Feishu API delivered eight synthetic approval cards. Native macOS Feishu checks covered default-off behavior, known rules, unknown-key non-disclosure, complete long commands and SmartDENY's two choices. No command or authorization was executed. All eight messages were ultimately recalled after ownership checks; production configuration remained unchanged.
- [Full candidate CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/43556419366) and [CodeQL](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/43556419380) passed. The actual PR merge snapshot has the reviewed candidate tree. PM repair→rebind→reinstall→restore cases for v0.22.4/v0.22.5 passed without skips in both the ordinary upstream and uv 0.13.5 lanes.
- Bailey explicitly narrowed this release scope: Android/iOS visuals and expansion, real authorization clicks and actual Gateway/model approval turns remain **unverified**. The original #526 environment has not been independently reproduced and is not claimed fixed.

Publication requires final main/tag full CI, public ordinary installation and release-asset verification. Final evidence is collected on the [v5.0.24 GitHub release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.24). Source tests, platform APIs, client display and Gateway/model execution remain separate evidence.

## Credits

Thanks to [lvanwei180-lvan](https://github.com/lvanwei180-lvan) for the PM-rebuild report in [#524](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/524), [jackwude](https://github.com/jackwude) for the approval-hint proposal in [#525](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/525), and [nmtlcxb](https://github.com/nmtlcxb) for the Windows turn report in [#526](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/526). Reports and suggestions are distinct from code authorship; all historical credits and releases remain intact.
