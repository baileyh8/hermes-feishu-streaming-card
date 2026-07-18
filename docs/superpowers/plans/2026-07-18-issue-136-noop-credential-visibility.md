# Issue #136 Noop Credential Visibility Implementation Plan

> **Execution note:** Implement inline with systematic debugging and TDD. Do not read an implicit global `~/.hermes/.env`; only honor the config-scoped `.env`, an explicitly selected env file, and the process environment.

**Goal:** Ensure sidecar credentials selected by setup/start reach the runner, and make missing credentials observable instead of reporting fake Feishu delivery success.

**Architecture:** `load_config` accepts an optional explicit env file with deterministic precedence: YAML < config sibling `.env` < selected env file < process environment. CLI setup/start and runner use the same resolved config. Runner marks credential-free operation as Noop; the server exposes degraded health and records Noop delivery attempts as failures without calling Feishu.

**Tech Stack:** Python, aiohttp, YAML, pytest.

## Constraints

- Keep `install/envfile.py` ownership secret-free; do not add Feishu secrets to `HFC_ENV_KEYS`.
- Do not fall back unconditionally to `~/.hermes/.env`.
- Never expose credential values in logs, health, metrics, or errors.
- A degraded Noop sidecar remains a running process so `start`, `status`, and `stop` can manage it.
- Noop sends must not produce fake message IDs or increment `feishu_send_successes`.

### Task 1: Reproduce the selected-env credential loss

**Files:**
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_runner.py`

- [x] Add a config test proving an explicit env file overrides the sibling `.env`, while process environment remains highest priority.
- [x] Add a runner test proving `--env-file` is passed into `load_config` and yields a real Feishu boundary.
- [x] Run focused tests and verify RED.

### Task 2: Load the selected env consistently

**Files:**
- Modify: `hermes_feishu_card/config.py`
- Modify: `hermes_feishu_card/runner.py`
- Modify: `hermes_feishu_card/cli.py`
- Modify: `tests/integration/test_cli.py`
- Modify: `tests/integration/test_cli_install.py`

- [x] Add `load_config(path, *, env_file=None)` and apply the selected env after the sibling `.env` but before `os.environ`.
- [x] Pass the selected env from runner, `setup`, and `start` into config loading.
- [x] Keep default behavior unchanged when the selected env is absent or is the sibling `.env`.
- [x] Run config, runner, and CLI tests.

### Task 3: Expose and account for Noop mode

**Files:**
- Modify: `hermes_feishu_card/runner.py`
- Modify: `hermes_feishu_card/server.py`
- Modify: `hermes_feishu_card/metrics.py`
- Modify: `hermes_feishu_card/process.py`
- Modify: `hermes_feishu_card/cli.py`
- Modify: `tests/integration/test_server.py`
- Modify: `tests/unit/test_process.py`
- Modify: `tests/integration/test_cli_process.py`

- [x] Add a clear credential-free startup warning without paths or secret values.
- [x] Add explicit app Noop state; `/health` reports `status: degraded`, `noop_mode: true`, and `delivery.mode: noop`.
- [x] Reject Noop delivery locally as `not_sent`; increment attempts, failures, and `feishu_noop_attempts`, never successes.
- [x] Let process management recognize both healthy and degraded health payloads as a running sidecar.
- [x] Print degraded/delivery mode in CLI status.
- [x] Run focused tests and verify GREEN.

### Task 4: Documentation and verification

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/wiki/maintenance-guide.md`
- Modify: `docs/wiki/release-playbook.md` only if release behavior changes are needed.

- [x] Document selected env precedence and degraded Noop diagnostics. Defer `CHANGELOG.md` to the versioned release commit because this repository forbids an Unreleased section.
- [x] Run relevant focused tests.
- [x] Run `python -m pytest -q` and `git diff --check`.
- [x] Commit locally; do not push, tag, or publish until Bailey explicitly requests release again.
