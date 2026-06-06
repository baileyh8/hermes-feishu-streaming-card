# V3.6.1 Release Notes

[中文](release-notes-v3.6.1.md)

V3.6.1 is a focused compatibility patch for Hermes Feishu Streaming Card.

## Fixed

- Fixed issue #47: Hermes `VERSION` values without a leading `v`, such as `0.15.1`, are now parsed as valid semantic versions.
- Hermes `0.15.x` / `v0.15.x` now stays in the supported release matrix and selects `gateway_run_013_plus` when the required code anchors are present.
- `doctor --explain` no longer reports Hermes `0.15.1` as unsupported solely because the `VERSION` file omits the `v` prefix.

## Validation

- Added unit coverage for `0.13.0`, `0.14.0`, `0.15.1`, and `v0.15.1` hook strategy selection.
- Added CLI regression coverage for `doctor --explain` with Hermes `0.15.1`.

## Upgrade

```bash
git checkout v3.6.1
pip install -e ".[test]" --upgrade
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent --explain
```

If you are already on V3.6.0, this is a detection-only patch. Reinstall the Hermes hook only if `doctor` reports that the installed hook strategy should change.

## Release Assets

Tagged releases are expected to publish:

- `hermes-feishu-card-v3.6.1-macos.tar.gz`
- `hermes-feishu-card-v3.6.1-linux.tar.gz`
- `hermes-feishu-card-v3.6.1-windows.zip`
- `hermes-feishu-card-v3.6.1-checksums.txt`
