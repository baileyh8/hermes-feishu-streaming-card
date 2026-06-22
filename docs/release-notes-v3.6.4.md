# V3.6.4 Release Notes

[中文](release-notes-v3.6.4.md)

V3.6.4 is a focused delivery-routing patch for Hermes Feishu Streaming Card. It fixes issues #61 and #62.

## What Changed

- Fixed issue #61: Feishu thread messages now keep the streaming card in the same thread. The hook runtime carries optional `thread_id` context, the sidecar forwards it with the user's reply anchor, and the Feishu client uses `/im/v1/messages/{message_id}/reply` with `reply_in_thread: true` when a thread reply anchor is available.
- Fixed issue #62: cron jobs with `deliver: "feishu:oc_xxx"` now extract `oc_xxx` as the Feishu chat id, so scheduled jobs can render as streaming cards instead of falling back to plain text.
- Kept the #61 implementation scoped to initial card placement and Feishu reply delivery. Existing card updates continue to patch the created card message id, so updates remain in the thread once the initial card is created there.

## Upgrade

```bash
cd /path/to/hermes-feishu-streaming-card
git checkout v3.6.4
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

- `hermes-feishu-card-v3.6.4-macos.tar.gz`
- `hermes-feishu-card-v3.6.4-linux.tar.gz`
- `hermes-feishu-card-v3.6.4-windows.zip`
- `hermes-feishu-card-v3.6.4-checksums.txt`

## Verification

- `tests/unit/test_events.py`
- `tests/unit/test_hook_runtime.py`
- `tests/unit/test_feishu_client.py`
- `tests/integration/test_feishu_client_http.py`
- `tests/integration/test_server.py`
