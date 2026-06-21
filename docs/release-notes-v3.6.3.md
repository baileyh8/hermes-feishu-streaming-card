# V3.6.3 Release Notes

[中文](release-notes-v3.6.3.md)

V3.6.3 is a focused compatibility and interaction reliability patch for Hermes Feishu Streaming Card. It fixes issues #56-#59.

## What Changed

- Fixed issue #59: Hermes v0.17.0+ / v2026.6.19+ can split the real streaming implementation into `_run_agent_inner`; the patcher now prefers `_run_agent_inner` before falling back to `_run_agent`, so tool, answer, thinking, clarify, and approval hooks are injected into the active callback body.
- Fixed issue #57: localhost/private sidecars now default `card.interaction_mode: auto` to text-choice fallback. The card shows numbered choices and the hook immediately falls back to Hermes' native text interaction path instead of waiting for an unreachable Feishu Card Action callback URL. Explicit `card.interaction_mode: callback` still keeps real CardKit buttons for public callback deployments.
- Fixed issue #56: non-Feishu platforms such as Telegram are ignored at `hook_runtime` event construction time, so installing the Feishu card hook no longer posts Telegram events to the sidecar or changes native Telegram delivery.
- Fixed issue #58: Windows `HERMES_HOME` paths such as `C:\Users\...\AppData\Local\hermes\profiles\thinking` now resolve the profile id correctly, including both `hermes/profiles/<id>` and `.hermes/profiles/<id>`.
- Extracted the useful part of PR #52: local/private sidecar HTTP calls bypass system proxies, public sidecar URLs still use the default proxy behavior, and Windows sidecar stop/status no longer goes through POSIX process-group signals.

## Upgrade

```bash
cd /path/to/hermes-feishu-streaming-card
git checkout v3.6.3
pip install -e ".[test]" --upgrade

python3 -m hermes_feishu_card.cli doctor \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --explain

python3 -m hermes_feishu_card.cli install \
  --hermes-dir ~/.hermes/hermes-agent \
  --yes
```

For localhost-only deployments, no extra callback URL is required. Keep the default `card.interaction_mode: auto`, or set `card.interaction_mode: text` explicitly. If you have a public Feishu Card Action callback URL, set `card.interaction_mode: callback`.

## Release Assets

GitHub Releases include:

- `hermes-feishu-card-v3.6.3-macos.tar.gz`
- `hermes-feishu-card-v3.6.3-linux.tar.gz`
- `hermes-feishu-card-v3.6.3-windows.zip`
- `hermes-feishu-card-v3.6.3-checksums.txt`

## Verification

- `tests/unit/test_hook_runtime.py`
- `tests/unit/test_render.py`
- `tests/unit/test_runner.py`
- `tests/unit/test_config.py`
- `tests/unit/test_process.py`
- `tests/unit/test_patcher.py`
- `tests/integration/test_server.py`
