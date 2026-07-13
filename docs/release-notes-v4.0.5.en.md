# V4.0.5

V4.0.5 fixes issue #115: the one-line upgrade could update the CLI and sidecar while leaving an older plugin installed in the Hermes Gateway venv. The Gateway process therefore never loaded the newer native-message suppression behavior.

## Gateway runtime version synchronization

- `install` and `setup` now read `hermes_feishu_card.__version__` and the module path from the Hermes runtime Python.
- Installation is skipped only when the Gateway runtime version matches the invoking CLI package. An older package that still imports is no longer treated as current.
- An outdated runtime is upgraded from the `HFC_INSTALL_SPEC` supplied by the one-line installer.
- The installer checks the version and module path again after pip completes. Invalid metadata or a remaining version mismatch now fails explicitly instead of reporting a misleading success.
- Matching versions remain idempotent and perform no extra pip operation.

## Issue and credit

- Thanks to @blakejia for issue #115's complete upgrade transcript, sidecar health metrics, and duplicate-message screenshot.
- @blakejia's earlier Gateway venv check in issue #106 showed that runtime version `3.6.3` was still loaded. That evidence isolated a split between the CLI/sidecar and Gateway runtime rather than a card-delivery failure.
- The fix was merged in PR #116. The issue remains open pending confirmation on real Feishu after upgrading.

## Post-upgrade verification

After rerunning the one-line installer, inspect the Gateway runtime version and module path:

```bash
~/.hermes/hermes-agent/venv/bin/python -c 'import hermes_feishu_card; print(hermes_feishu_card.__version__, hermes_feishu_card.__file__)'
```

The output should report `4.0.5` from a location accessible to the Hermes Gateway venv.

## Verification

- Installer / patcher hot path: `139 passed`.
- Full suite: `1278 passed, 3 skipped`; `git diff --check` passed.
- Regression coverage includes an importable `3.6.3` runtime that must upgrade and an idempotent matching-version runtime that skips pip.

## Release assets

- `hermes-feishu-card-v4.0.5-macos.tar.gz`
- `hermes-feishu-card-v4.0.5-linux.tar.gz`
- `hermes-feishu-card-v4.0.5-windows.zip`
- `hermes-feishu-card-v4.0.5-checksums.txt`
