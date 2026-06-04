# V3.6.0 Release Notes

[中文](release-notes-v3.6.0.md)

V3.6.0 is an operations-focused release for Hermes Feishu Streaming Card. It keeps the V3.5.x streaming card and in-card interaction baseline, then makes installation, diagnosis, repair, media/file handling, and multi-profile operations easier to verify in real deployments.

## Highlights

- `doctor --json` and `doctor --explain` now report config, sidecar, Hermes compatibility, streaming settings, install state, and recommendations in a support-friendly shape.
- `repair --hermes-dir ... --yes` and `setup --repair` can rebuild known-safe hook manifest/backup state without touching unverifiable user edits.
- Structured Hermes attachments from `attachments`, `files`, `media_files`, image/audio/video objects, paths, and URLs are summarized in cards while native media/file delivery remains available.
- `smoke-feishu-card --profile-id` and `bots test --profile-id` let operators verify one profile or bot route directly.
- `status` and `/health.routing` now expose profile-scoped routing diagnostics for multi-bot and multi-profile deployments.
- The release matrix covers Hermes `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `0.13.x`, and `0.14.x`.

## Upgrade

```bash
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml
git checkout v3.6.0
pip install -e ".[test]" --upgrade
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
```

If `doctor --explain` reports a known-safe repair path, run:

```bash
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --yes
```

## Release Assets

Tagged releases are expected to publish:

- `hermes-feishu-card-v3.6.0-macos.tar.gz`
- `hermes-feishu-card-v3.6.0-linux.tar.gz`
- `hermes-feishu-card-v3.6.0-windows.zip`
- `hermes-feishu-card-v3.6.0-checksums.txt`

## Notes

- Docker packaging is intentionally out of scope for this release.
- Real Feishu smoke tests still require local credentials and a real chat id; never commit App Secret, tenant token, or private chat ids.
