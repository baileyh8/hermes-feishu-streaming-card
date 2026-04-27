# Feishu Card Smoke Design

## Goal

Add a CLI smoke command that sends one real Feishu interactive card to a specified chat and updates that same message once, proving the Feishu CardKit send/update path works with user-provided local credentials.

## Scope

The command is a manual verification tool, not an automated CI test. It does not require Hermes Gateway to run, does not install hooks, and does not write credentials anywhere. It exercises only the sidecar Feishu HTTP client boundary.

## Command

```bash
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml --chat-id oc_xxx
```

`--config` defaults to `config.yaml.example` for CLI consistency, but real use should point at a local config file or rely on `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. `--chat-id` is required because the command sends a real message.

## Behavior

1. Load config with existing `load_config()`.
2. Require non-empty `feishu.app_id` and `feishu.app_secret` after environment overrides.
3. Build a minimal `CardSession` in thinking state and render it with existing `render_card()`.
4. Send the rendered card through `FeishuClient.send_card(chat_id, card)`.
5. Apply a `message.completed` event to the same session, render the completed card, and call `FeishuClient.update_card_message(message_id, card)`.
6. Print a concise success summary containing the Feishu `message_id`.

## Error Handling

Missing credentials or missing `--chat-id` returns non-zero with a short diagnostic. Feishu API failures bubble up as sanitized `FeishuAPIError` messages. Output must not include App Secret, tenant token, Authorization header, or request bodies containing credentials.

## Testing

Use a mock Feishu server, not real Feishu, for automated coverage. Tests cover:

- successful smoke send/update
- missing credentials
- send failure
- update failure
- no secret leakage in command output

Docs must say the command is available, but real Feishu smoke remains a manual checklist item until a user actually runs it against a live bot.
