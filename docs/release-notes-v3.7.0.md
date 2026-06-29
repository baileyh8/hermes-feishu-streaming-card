# V3.7.0 Release Notes

[中文](release-notes-v3.7.0.md)

V3.7.0 adds Docker deployment adaptation for issue #70.

## What Changed

- Added `install-docker.sh` for running install/update inside existing Hermes containers.
- Added `docker-compose.example.yml` showing `/opt/hermes` and `/opt/data` mounts, Feishu environment variables, and one-shot installer execution.
- The Docker installer defaults to `HERMES_DIR=/opt/hermes`, `HFC_CONFIG=/opt/data/config.yaml`, and `HFC_ENV_FILE=/opt/data/.env`.
- The Docker installer uses Hermes venv Python and does not silently fall back to system `python` / `pip`.
- Release packages now include the Docker installer and Compose example.

## Upgrade

Inside an existing Hermes container:

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v3.7.0
bash install-docker.sh
```

After install:

```bash
/opt/hermes/venv/bin/python -m hermes_feishu_card.cli doctor \
  --config /opt/data/config.yaml \
  --hermes-dir /opt/hermes \
  --explain
```

## Release Assets

GitHub Releases include:

- `hermes-feishu-card-v3.7.0-macos.tar.gz`
- `hermes-feishu-card-v3.7.0-linux.tar.gz`
- `hermes-feishu-card-v3.7.0-windows.zip`
- `hermes-feishu-card-v3.7.0-checksums.txt`

## Verification

- `tests/unit/test_install_scripts.py`
- `tests/unit/test_docs.py`
- `tests/unit/test_ci_workflow.py`
- full pytest suite
