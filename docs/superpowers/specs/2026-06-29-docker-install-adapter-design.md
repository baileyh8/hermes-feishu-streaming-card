# Docker Install Adapter Design

## Goal

Implement a Docker deployment adaptation release for issue #70. The release provides a container-friendly install/update path for existing Hermes Docker deployments where Hermes lives under `/opt/hermes`, data/config lives under `/opt/data`, files may be owned by `root`, and the usable Python interpreter is inside the Hermes venv rather than the system `python` / `pip`.

This is a script-and-documentation adaptation, not an official Docker image release.

## Scope

Add a Docker-specific installer script, a Compose example, documentation, tests, release notes, and packaging updates.

The Docker path must support:

- `HERMES_DIR=/opt/hermes` by default.
- `HFC_CONFIG=/opt/data/config.yaml` by default.
- `HFC_ENV_FILE=/opt/data/.env` by default.
- Hermes runtime Python discovery from `/opt/hermes/venv/bin/python`, `/opt/hermes/venv/bin/python3`, `/opt/hermes/.venv/bin/python`, and `/opt/hermes/.venv/bin/python3`.
- Non-interactive install/update with `HFC_NO_PROMPT=1`.
- Existing `HFC_VERSION`, `HFC_REPO`, `HFC_SKIP_START`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_CONNECTION_MODE`, and `FEISHU_HOME_CHANNEL`.
- A clear `doctor --explain` step before mutating Hermes when possible.
- Fail-fast diagnostics for missing Hermes root, missing `gateway/run.py`, missing venv Python, missing credentials, or non-writable `/opt/data`.

## Non-Goals

- Do not publish or maintain an official Docker image in this release.
- Do not replace `install.sh`; ordinary macOS/Linux installs keep using the existing installer.
- Do not add a new CLI `setup --docker` flag unless implementation shows the script cannot stay thin.
- Do not change Feishu card rendering, sidecar event handling, or Hermes hook behavior beyond what the installer needs.
- Do not require Docker to run in CI; tests can validate scripts and generated command behavior with temporary directories.

## User-Facing Files

### `install-docker.sh`

New Linux shell script intended to be run inside an existing Hermes container.

Default environment:

```bash
HERMES_DIR="${HERMES_DIR:-/opt/hermes}"
HFC_CONFIG="${HFC_CONFIG:-/opt/data/config.yaml}"
HFC_ENV_FILE="${HFC_ENV_FILE:-/opt/data/.env}"
HFC_NO_PROMPT="${HFC_NO_PROMPT:-1}"
HFC_PIP_USER="${HFC_PIP_USER:-0}"
```

Runtime selection:

1. If `HFC_PYTHON` is set, use it.
2. Else use the first executable Hermes venv Python under `HERMES_DIR`.
3. Else fail with a message naming the expected venv paths.

Package install:

- Resolve `HFC_VERSION` the same way as `install.sh`: `latest` queries GitHub releases, otherwise use the provided tag/branch.
- Install the package into the selected Hermes runtime Python with `python -m pip install --upgrade <spec>`.
- If pip is missing, run `python -m ensurepip --upgrade`, then retry.
- Do not use `--user` by default inside Docker.

Setup flow:

1. Expand and validate paths.
2. Load Feishu-related keys from `HFC_ENV_FILE` if present.
3. Require credentials in non-interactive Docker mode.
4. Ensure `/opt/data` exists and is writable.
5. Install or upgrade the package into Hermes venv Python.
6. Run `python -m hermes_feishu_card.cli doctor --config "$HFC_CONFIG" --hermes-dir "$HERMES_DIR" --explain`.
7. Run `python -m hermes_feishu_card.cli setup --hermes-dir "$HERMES_DIR" --config "$HFC_CONFIG" --yes`, adding `--skip-start` when `HFC_SKIP_START=1`.
8. Print status and doctor commands using the selected venv Python.

### `docker-compose.example.yml`

Add an example for users who already have a Hermes image or container entrypoint. It should demonstrate:

- Mounting persistent Hermes data to `/opt/hermes` and `/opt/data`.
- Supplying `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and optional Feishu connection/home channel variables.
- Running `install-docker.sh` inside the container as an init/update command.
- Keeping the image placeholder explicit, such as `image: your-hermes-image:latest`.

The file must be clearly labeled as an example, not an official supported image.

### Documentation

Update:

- `README.md`
- `README.en.md`
- `README-install.md`
- `TODO.md`
- `CHANGELOG.md`
- `docs/release-readiness.md`
- `docs/release-readiness.en.md`
- New `docs/release-notes-v3.7.0.md`

Documentation must explain:

- When to use `install-docker.sh` instead of `install.sh`.
- The default Docker paths.
- How to run one-shot install/update inside a container.
- How to override paths and Python.
- What the Compose example does and does not provide.
- How to rerun `doctor --explain` after installation.

## Internal Design

The Docker installer should reuse concepts from `install.sh`, but keep the implementation separate to avoid making the ordinary installer branchy and harder to reason about.

Shared behavior should be copied only when it is small and stable:

- logging helpers
- path expansion
- env-file loading allowlist
- GitHub release tag resolution
- pip retry after ensurepip

If duplication grows during implementation, extract a small shell library only if it reduces complexity without changing the existing installer behavior.

## Error Handling

The Docker script should exit non-zero with actionable messages:

- Hermes root missing: name the current `HERMES_DIR` and expected `/opt/hermes`.
- `gateway/run.py` missing: tell the user to verify the mount or set `HERMES_DIR`.
- venv Python missing: list checked venv paths and mention `HFC_PYTHON`.
- `/opt/data` not writable: mention container user/root ownership and mounted volume permissions.
- credentials missing: mention `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and `HFC_ENV_FILE`.
- package install failure: preserve pip output and the exact Python path used.

The script should never silently fall back to system `python` unless the user explicitly sets `HFC_PYTHON`.

## Testing

Add tests that do not require Docker daemon access:

- `install-docker.sh` defaults to `/opt/hermes`, `/opt/data/config.yaml`, and `/opt/data/.env`.
- The script chooses Hermes venv Python over system Python.
- Missing venv Python fails with a useful message.
- Missing credentials fail in non-interactive mode.
- `HFC_SKIP_START=1` passes `--skip-start` to setup.
- Release assets workflow packages `install-docker.sh` and `docker-compose.example.yml`.
- Documentation tests assert Docker install docs and release notes are linked.

Existing test groups to run:

```bash
.venv/bin/python -m pytest tests/unit/test_install_scripts.py tests/unit/test_docs.py tests/unit/test_ci_workflow.py -q
.venv/bin/python -m pytest -q
```

## Release Plan

Version: `v3.7.0`.

Release steps:

1. Implement with tests.
2. Update version metadata and docs.
3. Run full tests.
4. Open PR and merge after CI passes.
5. Tag `v3.7.0`.
6. Create GitHub Release using `docs/release-notes-v3.7.0.md`.
7. Confirm release assets include Docker installer and Compose example.
8. Reply to issue #70 with usage summary and close it.

## Acceptance Criteria

- A user inside a Docker container can run `install-docker.sh` with Hermes mounted at `/opt/hermes` and config/data at `/opt/data`.
- The script installs/upgrades the package into Hermes venv Python, not the host/system Python.
- The script can run non-interactively with env credentials.
- The script produces clear diagnostics for wrong mounts, missing venv, missing credentials, and unwritable data dir.
- Release packages include the Docker installer and Compose example.
- README and installer docs describe Docker usage in Chinese and English.
- Issue #70 is answered with the new version and Docker usage path after release.
