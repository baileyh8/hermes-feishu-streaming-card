# V3.6.5 Release Notes

[中文](release-notes-v3.6.5.md)

V3.6.5 is a focused streaming terminal stability patch for Hermes Feishu Streaming Card. It fixes issues #64 and #65.

## What Changed

- Fixed issue #64: for Hermes `gateway_run_013_plus`, the injected `message.started` hook now resolves the Feishu reply anchor through `_reply_anchor_for_event(event)` and emits that value as the card session `message_id`. Streaming callbacks already use that anchor, so Feishu thread sessions now stay on one session key instead of creating a card under one id and ignoring later events under another.
- Fixed issue #65: completed-only / burst-output models such as DeepSeek can now populate the final card from `message.completed` even when no `thinking.delta` or `answer.delta` arrived. The runtime falls back to `agent_result.final_response` and related final-answer fields before emitting the terminal event.
- Added a sidecar regression test proving a started card updates to completed from `message.completed` alone, with `feishu_update_attempts` incrementing and `answer_chars` populated.

## Upgrade

```bash
cd /path/to/hermes-feishu-streaming-card
git checkout v3.6.5
pip install -e ".[test]" --upgrade

python3 -m hermes_feishu_card.cli doctor \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --explain

python3 -m hermes_feishu_card.cli install \
  --hermes-dir ~/.hermes/hermes-agent \
  --yes
```

## Release Assets

GitHub Releases include:

- `hermes-feishu-card-v3.6.5-macos.tar.gz`
- `hermes-feishu-card-v3.6.5-linux.tar.gz`
- `hermes-feishu-card-v3.6.5-windows.zip`
- `hermes-feishu-card-v3.6.5-checksums.txt`

## Verification

- `tests/unit/test_hook_runtime.py`
- `tests/unit/test_patcher.py`
- `tests/integration/test_server.py`
