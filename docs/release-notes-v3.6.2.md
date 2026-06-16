# V3.6.2 Release Notes

[中文](release-notes-v3.6.2.md)

V3.6.2 is a focused installer/runtime reliability patch for Hermes Feishu Streaming Card.

## What Changed

- Fixed issue #53: `setup` / `install` now detects the Hermes Gateway runtime venv Python, such as `~/.hermes/hermes-agent/venv/bin/python`, and installs `hermes-feishu-streaming-card` into that interpreter before patching `gateway/run.py`.
- Added a `runtime_import` section to `doctor --json` and `doctor --explain`, so users can verify whether Hermes runtime Python can import `hermes_feishu_card.hook_runtime`.
- Hook import/emit failures are no longer fully silent. The hook still fails open, but writes `[hermes-feishu-card] hook failed: ...` to Hermes stderr for diagnosis.
- Installers now pass the resolved Git install spec into setup via `HFC_INSTALL_SPEC`, so the Hermes venv receives the same release users requested.

## Upgrade

```bash
cd /path/to/hermes-feishu-streaming-card
git checkout v3.6.2
pip install -e ".[test]" --upgrade

python3 -m hermes_feishu_card.cli doctor \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --explain

python3 -m hermes_feishu_card.cli install \
  --hermes-dir ~/.hermes/hermes-agent \
  --yes
```

If `doctor --explain` reports `Runtime import: failed`, rerun `setup` or `install` after upgrading. The command will install the package into the Hermes runtime venv before patching the hook.

## Release Assets

GitHub Releases include:

- `hermes-feishu-card-v3.6.2-macos.tar.gz`
- `hermes-feishu-card-v3.6.2-linux.tar.gz`
- `hermes-feishu-card-v3.6.2-windows.zip`
- `hermes-feishu-card-v3.6.2-checksums.txt`

## Scope

The `.env` search-path question raised alongside issue #53 is intentionally kept as a separate follow-up item. This release focuses on the Hermes runtime venv install path and hook diagnostics.
