# Testing

[中文](testing.md) | [English](testing.en.md)

## Unit Tests

```bash
python3 -m pytest tests/unit -q
```

Unit tests cover config loading, event models, text cleanup, card rendering, session state, installer detection, manifest behavior, and patcher behavior.

## Integration Tests

```bash
python3 -m pytest tests/integration -q
```

Integration tests cover the CLI, `doctor`, sidecar server, and install/restore/uninstall flows using fixture Hermes directories.

Official Hermes `v2026.4.23` Git tag source has been used for manual install/restore smoke testing. That upstream tag does not include a top-level `VERSION` file, so the installer falls back to reading the Git tag when `VERSION` is absent. A real Hermes Gateway process has completed E2E verification with a real Feishu test app.

## Hermes Hook Runtime Tests

```bash
python3 -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -q
```

These tests verify that the installed Hermes hook can send `SidecarEvent` data to a mock sidecar and remains fail-open when sending fails. They use fixtures and a mock sidecar only; they do not access real Feishu.

## Sidecar Process Tests

```bash
python3 -m pytest tests/integration/test_cli_process.py -q
```

This test starts a real local sidecar process and checks `/health`, `status`, event receipt, and `stop` cleanup. It uses a temporary pidfile directory and no-op Feishu client. It does not access real Feishu.

`/health` and `status` metrics are covered by `tests/integration/test_server.py` and `tests/integration/test_cli_process.py`, including `events_received`, `events_applied`, `events_rejected`, `feishu_send_successes`, `feishu_update_failures`, and `feishu_update_retries`. Card update retry behavior is tested with a limited retry; card creation failure returns a JSON error and clears local session state to avoid duplicate cards.

## Feishu HTTP Client Tests

```bash
python3 -m pytest tests/unit/test_feishu_client.py tests/integration/test_feishu_client_http.py -q
```

These tests use a mock Feishu server to verify tenant token, interactive card send, card message update, and error handling. They do not access real Feishu and do not require a real App Secret.

Manual real Feishu smoke:

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

This command sends and updates a real test card. Run it only when local credentials and a target `chat_id` are available. Do not write App Secret, tenant token, or real chat_id into the repository.

## Documentation Tests

```bash
python3 -m pytest tests/unit/test_docs.py -q
```

Documentation tests are low-brittleness guards: they verify that README keeps sidecar-only and `v2026.4.23` support range statements, that mainline docs clearly say legacy/dual code is not the active runtime, and that the event protocol keeps declaring card states. They do not replace human documentation review.

## E2E Visual Preview

```bash
python3 tools/generate_e2e_preview.py --output-dir docs/assets
python3 -m pytest tests/unit/test_e2e_preview.py -q
```

The generator writes `docs/assets/e2e-card-preview.svg` and `docs/assets/e2e-card-preview.json`, allowing local inspection of `思考中`, `已完成`, tool call count, `</think>` filtering, and final-answer replacement. It does not access real Feishu or read App Secret.

## Fixture Install/Restore Tests

`tests/fixtures/hermes_v2026_4_23/` is the Hermes fixture used by installer safety tests. Tests copy it to a temporary directory and verify:

- `install` writes the hook, backup, and manifest.
- `restore` restores the original `run.py`.
- `uninstall` removes install state owned by this plugin.
- User-modified `run.py`, backup, or manifest causes refusal instead of overwrite.

## Doctor

Local checks:

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --skip-hermes
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
```

`doctor` requires an explicit `--config`. `--skip-hermes` is useful for repository dry-runs; real installation should use `--hermes-dir` for read-only Hermes detection. Output includes `version_source`, `version`, `minimum_supported_version`, `run_py_exists`, and `reason`. It does not write Hermes files, backups, or manifests.

## Real Feishu Integration

Real Feishu/Lark integration must use environment variables or local config for credentials, such as `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Do not write App Secret into the repository, test fixtures, sample logs, or docs.

After integration testing, rotate test-app credentials when appropriate and check local logs for persisted secrets.

This round of real integration covered:

- Short and medium answer card creation, streaming update, and completion state.
- Tool call count display.
- Suppression of Hermes native gray text after the sidecar accepts completion.
- Footer metadata display and abnormal token filtering.
- One Feishu card continuously updated to 16k Chinese characters.
- Real Hermes directory `restore -> install` loop, ending in installed state.

Latest full automated regression:

```bash
python3 -m pytest -q -p no:cacheprovider
```

Result: `396 passed`.
