# V4.0.13

V4.0.13 routes every Hermes slash command with non-empty text feedback through a Feishu/Lark command card. Coverage is no longer limited to a fixed command list: built-ins, aliases, plugin/quick commands, and unknown commands are included automatically.

## All-command feedback cards

- Every admitted Feishu/Lark slash command establishes a task-local command context without a fixed allowlist.
- The first feedback message creates one interactive card. Later feedback for the same command is serialized under a context lock and PATCHes that card, preventing duplicate cards and concurrent creates.
- Aliases reuse Hermes `resolve_command(...)` when available. Safe raw-name fallback means plugin, quick, and unknown commands need no HFC registration.
- Long Markdown reuses the normal card structural splitter, while topic reply anchors and `thread_id` remain intact.

## Manual `/compress`

- Manual `/compress`, including the `/compact` alias, first creates a blue in-progress card and then calls the original Hermes handler exactly once.
- Success statistics, no-op results, and aborted/warning results update the same card with the unchanged Hermes return text; HFC does not rewrite message or token counts.
- If the running card cannot be created, the original handler result is returned. If the terminal PATCH fails, the same original result returns through native delivery.

## Compatibility and fail-open behavior

- `/model`, bare `/resume`, destructive confirmations, and `/hfc` retain their dedicated interactive cards without a duplicate generic card.
- Agent turns continue through the normal streaming card, while real media and files retain Hermes native delivery.
- `/update` remains a Hermes background upgrade command: pre-restart feedback enters the command card, while post-restart status remains an independent `system.notice` card.
- Native gray text is suppressed only after confirmed card create/PATCH success. Each failed operation sends the completely unchanged Hermes feedback through the original Feishu adapter.

## Verification boundaries

- Automated coverage includes built-ins, aliases, plugin/quick and unknown commands, empty/expired/cross-chat contexts, same-card multi-feedback, concurrent single-create behavior, long Markdown, create/PATCH fallback, and `/compress` success/no-op/aborted/exception/non-Feishu branches.
- The release gate reported `1482 passed, 4 skipped`, and `git diff --check` passed.
- sdist/wheel plus isolated Python 3.12 install/import/CLI smoke are completed before release.
- No real Feishu client command matrix was run for this release, so desktop/mobile visual acceptance and live fault injection are not claimed. Automated and SDK-compatibility tests cover those protocol branches.

## Release assets

- `hermes-feishu-card-v4.0.13-macos.tar.gz`
- `hermes-feishu-card-v4.0.13-linux.tar.gz`
- `hermes-feishu-card-v4.0.13-windows.zip`
- `hermes-feishu-card-v4.0.13-checksums.txt`
