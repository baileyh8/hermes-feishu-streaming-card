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

The full regression for `1e993bd` recorded **4,509 passed and 22 skipped**. All 14 GitHub checks for `9d986ad` passed, with real desktop evidence for long tasks, multi-select and light/dark themes. A pure approval `/stop` exposed another cancellation order, and a long wait produced a duplicate Working message. Both source fixes still require real verification on a new candidate; earlier successful cases do not establish final acceptance.

Final-commit regression and cross-platform CI, relevant real flows, visual gates, exact merge provenance, an annotated tag, assets/checksums and public-install provenance remain required. Android/iOS, dark theme and enlarged type are separate evidence; missing coverage stays unverified. Automated results do not substitute for client acceptance.

## Credits

Commits in this release are authored by [baileyh8](https://github.com/baileyh8). Reading improvements continue the requests from [jackwude](https://github.com/jackwude) in [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn) in [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333); those credits identify requests and field evidence. All earlier code authors, proposals and reports remain credited. Unmerged unrelated PRs are not represented as part of this release.
