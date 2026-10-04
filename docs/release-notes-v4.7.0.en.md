# V4.7.0: task cards and reading experience

This release adds an opt-in task layout with clearer state, answer and process sections. Non-GPT routes no longer display GPT subscription quota. Existing presets and explicit configuration remain valid.

## Main changes

- Add optional `card.reading_preset: task`. Cards present one clear state and current action, prioritize the answer, fold process details and retain attributed footer metrics. Text roles are consistent and repeated labels are reduced. Existing presets and explicit settings remain effective.
- Extend `card-config` with effective appearance values and their sources, plus offline HTML/Card JSON previews for nine states. Reports describe the next configuration load, not the running service. Previews use built-in examples, read no conversations and send no Feishu messages.
- Show subscription quota only on GPT turns attributed to `openai-codex`. Unrelated or unknown routes and stale queries after a model change omit it.
- Preserve complete tool parameters, questions and permission scope. Fix missing parameters after detail truncation, paused-card dialect changes and stale interaction controls after a task ends. Compact duplicate scope only after the complete separate receipt is confirmed delivered.
- Close the original task card when Hermes explicitly returns an interrupted result or controlled `/stop` confirms cancellation of its original processing task. Display delivery does not block pending messages. Retain bounded, token-free auxiliary-card display records so expiry and restart can replace controls with an expired receipt. This restores no execution, waiter or consent. Older checkpoints without an auxiliary message identity cannot retroactively refresh that message.
- Suppress duplicate native Working heartbeats after the exact task card has accepted delivery. Classic mode, unknown identities and failed queries retain Hermes' existing behavior.
- Project safely recognized task-answer code fences into `plain_text` while retaining the language label and complete code, addressing light-theme syntax-highlight readability. Canonical content and classic behavior remain unchanged. Chunking and capacity fallback passed focused regression. V16 passed the specified desktop display and body-line copying checks; copying the trailing newline was not tested.

After 60 seconds without a new event, the task layout performs one bounded update explaining that it is waiting for new information and execution status is unconfirmed. Restored display likewise does not imply that execution resumed.

## Upgrade and enable

Update through the existing installer and rerun `setup` or `install` against the actual Hermes directory so the package and managed hooks match. Restart the sidecar and Hermes Gateway through their existing service owners. Do not manually edit installed Hermes source or overwrite an existing configuration with an example.

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

## Validation and known boundaries

- Final runtime regression: **4,854 passed, 22 skipped, 0 failed**, with all 14 CI checks passing and ordinary-wheel files matched to source. Skips concern the separate upstream matrix, Windows semantics and PowerShell, covered by their corresponding CI jobs.
- Actual desktop checks cover light/dark themes, a narrow chat area, 125% scaling, code copying, prior-answer preservation, empty-continuation STOP, Working deduplication beyond 180 seconds, multiselect and restart during pending approval. Version-specific scope, earlier failures and retests remain in the [acceptance record](reviews/2026-10-03-task-cards-acceptance.md).
- **Release scope:** On 2026-10-04, Bailey approved publication within the accepted desktop scope. **Android/iOS devices and actual handler cancellation exceeding five seconds remain unverified.** Delayed cancellation has automated regression coverage. Narrow desktop views and offline previews do not establish mobile acceptance.
- Actual copying preserved selected code-body lines without captions or line numbers; an unselected trailing newline is outside that conclusion. Formal numerical WCAG compliance and complete intermediate streaming-frame coverage are not claimed.

See the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.7.0) for exact merge, annotated tag, assets and public-install verification. Daily Hermes and Feishu appearance were restored; retired test environments were reclaimed with required evidence preserved.

## Credits

Commits in this release are authored by [baileyh8](https://github.com/baileyh8). Reading improvements continue the requests from [jackwude](https://github.com/jackwude) in [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn) in [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333); those credits identify requests and field evidence. All earlier code authors, proposals and reports remain credited. Unmerged unrelated PRs are not represented as part of this release.
