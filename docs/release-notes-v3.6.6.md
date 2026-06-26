# V3.6.6 Release Notes

[中文](release-notes-v3.6.6.md)

V3.6.6 is a focused reliability patch for Hermes Feishu Streaming Card. It fixes issues #67 and #68.

## What Changed

- Fixed issue #67: terminal events such as `message.completed` now ACK Hermes before slow Feishu PATCH calls finish. The card still updates in the background, but Hermes no longer waits long enough to fall back to a duplicate native text reply during interrupted or backlogged sessions.
- Fixed issue #67: the Hermes async hook now reads the sidecar JSON response and treats `ok: false` or `applied: false` as not delivered. This keeps native-response suppression aligned with whether the streaming card actually accepted the terminal event.
- Fixed issue #68: if `--hermes-dir` points at the wrong directory and `gateway/run.py` is missing, detection reads `hermes -V`, extracts the `Project:` path, and shows a concrete `Use --hermes-dir ...` recommendation in `doctor --explain` / install diagnostics.

## Upgrade

```bash
cd /path/to/hermes-feishu-streaming-card
git checkout v3.6.6
pip install -e ".[test]" --upgrade

python3 -m hermes_feishu_card.cli doctor \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --explain

python3 -m hermes_feishu_card.cli install \
  --hermes-dir ~/.hermes/hermes-agent \
  --yes
```

If `doctor --explain` says `gateway/run.py missing` and prints a `Hermes CLI reports project: ...` path, rerun the command with that suggested `--hermes-dir`.

## Release Assets

GitHub Releases include:

- `hermes-feishu-card-v3.6.6-macos.tar.gz`
- `hermes-feishu-card-v3.6.6-linux.tar.gz`
- `hermes-feishu-card-v3.6.6-windows.zip`
- `hermes-feishu-card-v3.6.6-checksums.txt`

## Verification

- `tests/unit/test_hook_runtime.py`
- `tests/integration/test_server.py`
- `tests/integration/test_cli.py`
- `tests/integration/test_cli_install.py`
- `tests/unit/test_installer_detection.py`
