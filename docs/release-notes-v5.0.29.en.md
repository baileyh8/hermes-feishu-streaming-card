# v5.0.29: Opt-in per-chat system-notice suppression

[中文](release-notes-v5.0.29.md)

This release adds opt-in `card.suppressed_notice_kinds` (default `[]`) so operators can silently suppress non-conversation system notice cards such as self-improvement reviews per global/profile/bot scope, chat type (`group` / `p2p`), or exact `chat_id` without falling back to native plain text.

- **Per-chat system-notice suppression ([#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500) · [PR #547](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/547))**: Expose `suppressed_notice_kinds` in `card`, `card.chat_overrides`, and `card-config --chat-type/--chat-id` (without echoing raw chat IDs). Supported kinds: `self-improvement`, `skill-loading`, `compression`, `context-compaction`, `context-cap`, `session-reset`, `background-process`, `background-task`, `heartbeat`, `home-channel`, and `gateway-restart`. Non-list or unknown values are rejected at config load time.
- **Localized recognition & silent drop ([#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500) · [PR #547](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/547))**: Classify both English `Self-improvement review` and Chinese `自我改进回顾` notices as `self-improvement`, propagate `chat_type` from `hook_runtime` into `system.notice` payloads, and fall back to the chat's observed session `chat_type` on detached background callbacks. Suppressed notices emit neither a Feishu card nor a native plain-text fallback while leaving Hermes background work and logs untouched.

Follow the [installation guide](../README-install.md); use `hermes-feishu-card restore` when parking hooks before an upgrade and retain existing manifests and backups. Exact-commit CI, packaged installation, and field acceptance boundaries are recorded separately on the [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.29).

Thanks to [Lite-G](https://github.com/Lite-G) for the per-chat system-notice suppression scenario and boundary proposal in [#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500). Issue evidence and suggestions are distinct from code authorship; original commits and historical credits remain.
