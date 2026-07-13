# V4.0.4

V4.0.4 addresses the current issue set: it adapts issue #107's single usage-window response, fixes issue #110's literal Markdown `MEDIA:` parsing, completes issue #112's `interaction.select` background compatibility path when lark SDK retained an older bound callback, and consolidates issue #111 into issue #106, which was fixed and verified on real Feishu in V4.0.3.

## Codex usage windows

- After OpenAI temporarily removed the Plus five-hour window, Hermes can still normalize the only `primary_window` as `Session`.
- The plugin cannot reliably infer that window's duration from the normalized result, so a single `Session/Primary` now uses the neutral `limit 94%` label instead of the misleading `5h 94%`.
- Two-window responses retain `5h 26% · weekly 89%`, while an explicitly labeled single Weekly window remains `weekly`.

## Markdown media parsing

- `MEDIA:` and local paths inside inline or fenced code remain ordinary Markdown content. They create no attachment summary and do not require native media delivery.
- Card-answer cleanup, attachment extraction, `native_delivery` policy, and native-media-only response rewriting now share the same code-span boundary.
- Real `MEDIA:/path` directives outside code, local output paths, and structured Hermes media fields retain their existing behavior.

## Card action compatibility

- lark SDK can retain the original `_on_card_action_trigger` bound method before HFC installs; changing the class attribute cannot alter that saved reference.
- The main path still rebuilds the event handler. A new background compatibility path ensures that an old callback resolving `_handle_card_action_event` dynamically still forwards `interaction.select` directly to `/card/actions`.
- Forwarding runs in a worker thread instead of blocking the Feishu adapter event loop, while duplicate action tokens remain protected.
- Unrecognized actions still return to Hermes' native synthetic-command path and preserve fail-open behavior.

## Issue cleanup and credits

- Thanks to @tianqiii for promptly reporting issue #107's upstream single-window payload change.
- Thanks to @sthnow for issue #110's precise reproduction, regex diagnosis, and expected parsing boundary.
- Thanks to @zkyken for issue #112's complete logs and bound-method analysis, which exposed the missing background compatibility branch.
- Issue #111 duplicates issue #106's follow-up report. Thanks to @ShakuOvO and @blakejia for the original report, independent retesting, and screenshots. V4.0.3 passed a real Feishu acceptance with one completed card, one native image, and zero extra bot text messages.

## Verification

- Hook runtime / interaction / server / subscription usage hot-path matrix: `404 passed`.
- Full suite: `1275 passed, 3 skipped`; `git diff --check` passed.
- Regression coverage includes real media directives, inline/fenced code literals, native response preservation, and an SDK-retained old bound callback.

## Release assets

- `hermes-feishu-card-v4.0.4-macos.tar.gz`
- `hermes-feishu-card-v4.0.4-linux.tar.gz`
- `hermes-feishu-card-v4.0.4-windows.zip`
- `hermes-feishu-card-v4.0.4-checksums.txt`
