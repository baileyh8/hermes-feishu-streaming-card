# V4.7.0: task cards and reading experience

**Release candidate, pending final acceptance and not yet published.** The latest stable release remains V4.6.13. These draft notes do not claim that all client or release gates have passed; see the [acceptance record](reviews/2026-10-03-task-cards-acceptance.md).

## Main changes

- Add optional `card.reading_preset: task`. Cards present one clear state and current action, prioritize the answer, fold process details and retain attributed footer metrics. Text roles are consistent and repeated labels are reduced. Existing presets and explicit settings remain effective.
- Extend `card-config` with effective appearance values and their sources, plus offline HTML/Card JSON previews for nine states. Reports describe the next configuration load, not the running service. Previews use built-in examples, read no conversations and send no Feishu messages.
- Show subscription quota only on GPT turns attributed to `openai-codex`. Unrelated or unknown routes and stale queries after a model change omit it.
- Preserve complete tool parameters, questions and permission scope. Fix missing parameters after detail truncation, paused-card dialect changes and stale interaction controls after a task ends. Compact duplicate scope only after the complete separate receipt is confirmed delivered.
- Close the original task card when Hermes explicitly returns an interrupted result or controlled `/stop` confirms cancellation of its original processing task. Display delivery does not block pending messages. Retain bounded, token-free auxiliary-card display records so expiry and restart can replace controls with an expired receipt. This restores no execution, waiter or consent. Older checkpoints without an auxiliary message identity cannot retroactively refresh that message.
- Suppress duplicate native Working heartbeats after the exact task card has accepted delivery. Classic mode, unknown identities and failed queries retain Hermes' existing behavior.

After 60 seconds without a new event, the task layout performs one bounded update explaining that it is waiting for new information and execution status is unconfirmed. Restored display likewise does not imply that execution resumed.

## Upgrade and enable

After publication, update through the existing installer and rerun `setup` or `install` against the actual Hermes directory so the package and managed hooks match. Restart the sidecar and Hermes Gateway through their existing service owners. Do not manually edit installed Hermes source or overwrite an existing configuration with an example.

Opt into the new layout explicitly:

```yaml
card:
  reading_preset: task
```

Explicit fields at the same level retain precedence; global/profile/bot inheritance is unchanged. Select `classic`, `focused` or `detailed` and restart the sidecar to revert the layout; historical cards are not rewritten. Python 3.9 remains the minimum. This release does not automatically upgrade Hermes or migrate user configuration.

```bash
hermes-feishu-card card-config --config <config-path>
hermes-feishu-card card-config --config <config-path> --preview-dir ./card-preview
```

See [task cards](task-cards.md#english-quick-reference) and [reading presets](wiki/reading-presets.md). Simulated mobile widths and themes are not real-client acceptance.

## Acceptance status

The ordinary wheel from `455b788` passed the affected pure-approval STOP flow on upgraded Hermes `3d0a61ac`: the original task finalized, expired approval controls were removed and the command did not execute. Light and dark desktop views at 100% no longer repeat the generic interruption notice, and complete tool parameters remain expandable. Local full regression recorded **4,742 passed, 22 skipped and 0 failed** in 947.66 seconds, with verified ordinary-install provenance and unchanged source before and after.

Earlier candidates have real evidence for a 210-second heartbeat-deduplication run, two selections plus custom input, a dark 125% completed card and actual scrolling to the code-line end. Verified older Hermes sources also passed install, repeat install, doctor, integrity migration and byte-for-byte restoration. The [acceptance record](reviews/2026-10-03-task-cards-acceptance.md) preserves failures and version boundaries; these separate results do not establish complete final-package acceptance.

**Not yet published.** Other relevant final-package flows, real mobile devices, narrow chat areas and light-theme native code contrast remain incomplete. All 14 GitHub checks for `455b788` passed; exact merge regression/CI, an annotated tag, assets/checksums and public-install provenance remain separate gates. The daily Hermes service is restored with the user's latest configuration preserved; Feishu appearance is restored and retired candidate services and test copies are cleaned up.

## Credits

Commits in this release are authored by [baileyh8](https://github.com/baileyh8). Reading improvements continue the requests from [jackwude](https://github.com/jackwude) in [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn) in [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333); those credits identify requests and field evidence. All earlier code authors, proposals and reports remain credited. Unmerged unrelated PRs are not represented as part of this release.
