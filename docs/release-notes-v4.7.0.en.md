# V4.7.0: task cards and reading experience

**Release candidate, pending final acceptance and not yet published.** The latest stable release remains V4.6.13. These draft notes do not claim that all client or release gates have passed; see the [acceptance record](reviews/2026-10-03-task-cards-acceptance.md).

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

The ordinary wheel from `455b788` passed the affected pure-approval STOP flow on upgraded Hermes `3d0a61ac`. V14 additionally verified preservation of the earlier answer, STOP on an empty continuation, Working deduplication beyond 180 seconds and an actual restart during pending approval. Original owners and auxiliary approvals finalized appropriately; forbidden execution markers stayed at zero and old consent was not restored. Hermes issued a new recovery response without retrying the command or requesting another approval. Light and dark pure-approval views at 100% omit the duplicate generic interruption notice, with complete tool parameters expandable. Local full regression recorded **4,742 passed, 22 skipped and 0 failed** in 947.66 seconds. `f07c24f` changes documentation only; runtime code and installed-package provenance are unchanged.

V14 exercised selection, long waiting, STOP and restart in an actual desktop chat area approximately 414 logical pixels wide, calibrated against a same-display baseline at 2x backing scale. Screenshot pixels are neither the logical chat width nor physical panel pixels. V11 evidence for two selections plus custom input, dark 125% completion and actual code-end scrolling remains valid for unchanged paths, attributed to its original version. Older Hermes sources also passed install, repeat install, doctor, integrity migration and byte-for-byte restoration. The [acceptance record](reviews/2026-10-03-task-cards-acceptance.md) preserves failures and impact boundaries; documentation-only commits do not require repeating every client flow.

V15 used the new projection wheel from `d9a0033`. Code bodies exactly matched the fixture and canonical content retained the original language fences, but the actual completed card merged the language caption into the preceding table. Missing blank lines outside the code exposed the renderer's lack of a block boundary before the inserted caption. At that stage, caption and copying checks remained pending; V16 below records the retest. Full regression was deliberately stopped after **547 passed and 1 skipped**; this is not a full pass. CI for that commit was still running at this stage, and subsequent source changes require their own checks.

V16 uses the ordinary wheel from `b3d1f04` and closes the caption-in-table regression on actual input where a code fence directly follows the table without a blank line. DB, canonical and expected Markdown are identical at 421 characters. Independent visual review passed narrow and normal-width views in both light 100% and dark 125%; actual scrolling reached table and code ends. Copying into an unsent composer preserved all three selected body lines without captions, fences or line numbers. The trailing LF was not selected, so this establishes line-content equality only. The final light 100% normal-width view passed; original Feishu appearance and daily Hermes are restored, both candidate processes have exited and the test port is closed. Full regression passed **4,854 tests, with 22 skipped and 0 failures**, in 927.32 seconds; source and ordinary-wheel provenance matched before and after. All 14 GitHub checks passed. Pytest/preflight exited 0; the runner exited 4 because temporary files still had open references during cleanup. The references belonged to Spotlight; after they cleared naturally, the temporary tree was reclaimed. Original failure and successful cleanup follow-up are retained separately.

**Not yet published.** V14B stopped successfully after a natural approval pause but did not trigger handler cancellation lasting more than five seconds. That real timing branch and mobile clients remain unverified; no explicit desktop-only scope reduction has been granted. All 14 GitHub checks for `b3d1f04` passed; exact merge regression/CI, an annotated tag, assets/checksums and public-install provenance remain separate gates. V16 teardown restored daily Hermes and original Feishu appearance, with the production HEAD and four baseline files matching. The retired candidate source and lightweight venv were removed after reference checks, with about 280.4 MiB allocated before deletion. Shared test dependencies and required evidence remain protected.

## Credits

Commits in this release are authored by [baileyh8](https://github.com/baileyh8). Reading improvements continue the requests from [jackwude](https://github.com/jackwude) in [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn) in [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333); those credits identify requests and field evidence. All earlier code authors, proposals and reports remain credited. Unmerged unrelated PRs are not represented as part of this release.
