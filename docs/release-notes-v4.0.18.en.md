# V4.0.18

V4.0.18 fixes a hidden dependency mismatch after Hermes upgrades: the Gateway process can remain alive while the Feishu WebSocket repeatedly fails because an older `lark-oapi` does not accept `extra_ua_tags`.

## Fixes

- The installer checks the real `lark_oapi.ws.Client` constructor only when the Hermes Feishu adapter actually uses `extra_ua_tags`.
- `doctor --json/--explain` exposes a dedicated `feishu_sdk` state and reports `feishu_sdk_incompatible`, separating Gateway process health from Feishu connector health.
- `setup/install` installs the verified `lark-oapi==1.6.8` when needed and rechecks the constructor capability before continuing with the hook installation.
- Operations cards include localized guidance for the incompatible Feishu SDK state.

## Compatibility boundaries

- Older Hermes adapters that do not use `extra_ua_tags` do not trigger SDK installation.
- Newer SDKs that already support the parameter pass the capability check without a forced downgrade.
- `doctor` remains read-only; the Gateway venv changes only when the user explicitly runs `setup/install`.
- The repair does not edit Hermes `gateway/run.py` or the Feishu adapter directly; dependency correction stays inside the project installer.

## Validation

- A failing regression first reproduces `lark-oapi 1.5.3` without `extra_ua_tags`; the green path verifies installation of `1.6.8` and a successful signature recheck.
- A real Hermes v0.19.0 Gateway recovered `✓ feishu connected`; Gateway and sidecar stayed running, and all `214` runtime packages passed dependency compatibility checks.
- Full automation passed: `1511 passed, 4 skipped`; `git diff --check` passed.
- The release process rechecks sdist/wheel builds, isolated `site-packages` imports, the public tagged installer, and Release assets.

## Release assets

- `hermes-feishu-card-v4.0.18-macos.tar.gz`
- `hermes-feishu-card-v4.0.18-linux.tar.gz`
- `hermes-feishu-card-v4.0.18-windows.zip`
- `hermes-feishu-card-v4.0.18-checksums.txt`
