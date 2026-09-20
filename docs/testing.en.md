# Testing

[中文](testing.md) | [English](testing.en.md)

## Contributor preflight (Issue #330)

From the checkout root, use the Python interpreter intended for testing:

```bash
python tools/preflight.py --check-only
python tools/preflight.py --suite focused
python tools/preflight.py --suite focused --base origin/main
python tools/preflight.py --suite focused --module runtime --module render
python tools/preflight.py --suite full
```

The default only checks readiness. It does not install dependencies, fetch, edit Hermes, or restart services. Checks cover the current checkout, Python/venv, pytest dependencies, effective package source, and fixed Hermes fixture hashes. `ready` is not a test result: `pytest.status=not_run` remains explicit. An explicit `--suite` runs real `python -m pytest`, followed on success by `git diff --check`, including staged changes.

`focused` selects tests from staged, unstaged, and new files relative to `HEAD`; `--base origin/main` also includes committed branch changes. Repeat `--module runtime|render|config|install|process|docs|preflight` for explicit groups. Unknown changes or an empty selection require explicit modules rather than silently running everything. This is a starting point: use the [maintenance guide](wiki/maintenance-guide.md) for related matrices, and run the full suite plus CI before release.

Set `HFC_FIXED_TAG_SOURCE_ROOT` to the fixed source fixture; its default matches the existing tests. The checker verifies SHA256 against repository provenance without downloading, repairing, or changing the fixture. Missing sources report `missing`; changed hashes report `digest_mismatch`. Fixture-dependent selections and `full` are blocked until verified. An unrelated focused selection can still run, but its overall result is `partial` with an independently reported pytest result.

Each run uses a private temporary HFC state, Hermes home, and pytest directory. It does not reuse production state or change the global environment. Raw pytest output stays in a private local log whose location is printed only to stderr. Shareable stdout JSON contains source categories, relative test paths, counts, and exit codes, without absolute private paths, credentials, or raw exceptions. Exit `0` means the requested checks/tests succeeded; `2` denotes incomplete preflight checks. Pytest failures retain their exit code; inspect `reason` and `pytest.exit_code` to distinguish them.

Review and redact raw local logs before attaching them to a public issue. Preflight does not replace real Feishu, desktop/mobile, public-install, or release acceptance.

If test dependencies are missing, run `python -m pip install -e ".[test]"` in your chosen development environment. The full matrix requires a separate official Hermes `v2026.8.3` source checkout at commit `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`, selected with `HFC_FIXED_TAG_SOURCE_ROOT`; see the `fixed-hermes-fixture` job in `.github/workflows/tests.yml`. Do not substitute a running production Hermes checkout for the fixed test source.

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

## V4.1.0 Safety-Control Regressions

```bash
python3 -m pytest \
  tests/unit/test_delivery_policy.py \
  tests/unit/test_text.py \
  tests/unit/test_render.py \
  tests/unit/test_runtime_control.py \
  tests/unit/test_integrity.py \
  tests/unit/test_process.py \
  tests/integration/test_hook_runtime_integration.py \
  tests/integration/test_server.py \
  tests/integration/test_cli_integrity.py \
  tests/integration/test_cli_process.py -q
```

This matrix covers exact/profile-scoped native policy with signatures/replay, fenced-code-safe table compact/truncate, the 28,000-byte terminal native handoff, `runtime.hello` / `runtime.heartbeat` readiness, strict safe repair, all four service managers, and the non-systemd Docker boundary. Loopback aiohttp tests may need an environment that allows local ephemeral ports. Automation does not replace real card → native → card, seven-table, oversized-handoff, Hermes-upgrade, Linux-manager, and ordinary Docker Compose acceptance.

## V4.0.21 content-integrity regressions

```bash
python3 -m pytest \
  tests/unit/test_prepare_completed_answer_issue96.py \
  tests/unit/test_prepare_completed_answer_issue155.py \
  tests/unit/test_hook_runtime.py -q
```

`test_prepare_completed_answer_issue155.py` locks Issue #155: only an explicit `answer -> tool` boundary can archive an answer, while `tool -> answer -> completed` retains the full user-visible answer. `test_v4021_hook_runtime_keeps_image_delivery_and_accepted_notice_in_same_turn` locks Issue #147: matching media text is suppressed once, the native image still delivers, and an accepted notice does not emit an uncertain-delivery warning. Real Feishu acceptance on 2026-07-28 observed a completion card plus native image, two answer segments retained in one completion card, no matching native duplicate or uncertain-delivery warning, and the candidate runtime in Hermes venv site-packages 4.0.21; it does not claim screenshot or desktop/mobile visual QA. Public tagged-installer and Release-asset verification remain pending post-tag.

## Sidecar Process Tests

```bash
python3 -m pytest tests/integration/test_cli_process.py -q
```

This test starts a real local sidecar process and checks `/health`, `status`, event receipt, and `stop` cleanup. It uses a temporary pidfile directory and no-op Feishu client. It does not access real Feishu.

`/health` and `status` metrics are covered by `tests/integration/test_server.py` and `tests/integration/test_cli_process.py`, including `events_received`, `events_applied`, `events_rejected`, `feishu_send_successes`, `feishu_update_failures`, and `feishu_update_retries`. Card update retry behavior is tested with a limited retry; card creation failure returns a JSON error and clears local session state to avoid duplicate cards.

V3.8.7 adds a newer-Hermes compatibility regression: if the first normal message event is `answer.delta`, `thinking.delta`, `tool.updated`, or `message.completed`, the sidecar should create the initial card instead of counting the event as `events_ignored`.

V3.8.8 adds native Hermes system notice cardification regressions: `system.notice` events should enter the session timeline or create standalone notice cards; Feishu adapter `send` / `edit_message` interception should suppress gray native text when the sidecar applies the notice and remain fail-open when the sidecar is unavailable or the notice is unknown.

V3.8.9 adds Feishu/Lark topic reply regressions: when the first card is created from the original topic message `message_id` but later `tool.updated`, `answer.delta`, or `system.notice` events use a different streaming `message_id`, the sidecar should update the same card through `reply_to_message_id`, create no duplicate card, and avoid gray native fallback for applied system notices. Recognized system notices should also suppress native gray text if the card delivery attempt times out.

V3.8.10 adds group-diagnostic and tool-detail regressions: `bindings.group_rules` is diagnostic input only and must not leak raw chat/user ids; group `/hfc status` should explain chat binding, fallback/default routing, and slash-command boundaries; `tool.updated` should carry argument summaries, duration, and failure reason into the compact timeline when available.

V3.8.11 adds `/hfc` native unknown suppression regressions: `/commands` must return `handled: true` after accepting `/hfc status` while real Feishu/Lark card sending continues in the background; the patcher's early `/hfc` interception must stay before Hermes' native slash fallback so cards do not double-send with the gray `Unknown command /hfc` reply.

V3.8.12 adds issue #82 attachment-summary duplicate-reply regressions: generic `attachments` summaries should suppress the native final reply after the completed card is delivered; `MEDIA:/tmp/...`, local file paths, `files`, `media_files`, and image/audio/video locals should still preserve Hermes native file/media delivery.

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

Documentation tests are low-brittleness guards: they verify that README keeps sidecar-only, older Hermes `v2026.4.23` support range, and Hermes `0.13.0+` / `0.14.0` / `0.15.x` / `0.17.x` / `0.18.x` / `0.19.0` / `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` / `v2026.7.20` compatibility statements, that mainline docs clearly say legacy/dual code is not the active runtime, and that the event protocol keeps declaring card states. They do not replace human documentation review.

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
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --json
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
```

`doctor` requires an explicit `--config`. `--skip-hermes` is useful for repository dry-runs; real installation should use `--hermes-dir` for read-only Hermes detection. Output includes `version_source`, `version`, `minimum_supported_version`, `run_py_exists`, `hook_strategy`, `compatibility`, anchors, `reason`, and `runtime_import`. It does not write Hermes files, backups, or manifests. `--json` is for issues/automation, while `--explain` is for human troubleshooting and reports whether `repair --hermes-dir ... --yes` is available.

The automated matrix explicitly covers Hermes `v2026.4.23`, `v2026.5.7`, `v2026.5.16`, `v2026.5.29`, `v2026.6.19+`, `v2026.7.1`, `v2026.7.7.2`, `v2026.7.20`, `0.13.0`, `v0.13.0`, `0.14.0`, `v0.14.0`, `0.15.1`, `v0.15.1`, `0.17.x`, `0.18.0`, `v0.18.0`, `0.18.2`, `v0.18.2`, Hermes 0.19.0, and descriptive `Hermes Agent v0.18.2 (...)` hook strategy selection. Automated strategy detection reports `gateway_run_013_plus` for Hermes `0.13.0+`, `0.14.0`, `0.15.x`, `0.17.x`, `0.18.x`, `0.19.0` / `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` / `v2026.7.20`; older Hermes from `v2026.4.23` through `v2026.4.x` reports `legacy_gateway_run`. A separate read-only check against real local source from `v2026.7.20` confirmed V4.1 patcher insertion order, idempotency, and restore behavior; it is not a claim that a real Gateway or Feishu E2E run passed. When `VERSION` and `.git` metadata are missing but verifiable `gateway/run.py` anchors exist, diagnostics should report `version_source: gateway anchors`; when `VERSION` exists but is unparseable and anchors validate, diagnostics should report `version_source: VERSION + gateway anchors`.

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

Full automated regression:

```bash
python3 -m pytest -q -p no:cacheprovider
```

Use the local or CI output from that run as the result; this document does not pin a passed count.
