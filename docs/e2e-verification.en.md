# E2E Verification Materials

[中文](e2e-verification.md) | [English](e2e-verification.en.md)

The project provides reproducible local verification artifacts for checking the sidecar-only streaming card rendering result.

## Generated Artifacts

- [`docs/assets/e2e-card-preview.svg`](assets/e2e-card-preview.svg): visual preview for thinking and completed card states.
- [`docs/assets/e2e-card-preview.json`](assets/e2e-card-preview.json): Feishu CardKit JSON generated from real `CardSession`, `SidecarEvent`, and `render_card()`.

The preview covers:

- `思考中` and `已完成` normal states.
- Accumulated thinking content with `<think>` / `</think>` tags filtered.
- Real-time tool call count, shown as `工具调用 2 次` in the sample.
- Final answer replacement after completion, while preserving tool summary and duration/token stats.

## Regenerate

```bash
python3 tools/generate_e2e_preview.py --output-dir docs/assets
```

The generator uses only repository code and the standard library. It does not call real Feishu, read App Secret, or send network requests.

## Real Feishu Smoke

Real Feishu app verification uses:

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

Do not commit App Secret, tenant token, real chat_id, or sensitive content from real screenshots.

## Completed Real Acceptance

The current mainline has completed these checks with a real Hermes Gateway and real Feishu test app:

- New user messages create new cards and do not reuse previous unfinished cards.
- `thinking.delta`, `answer.delta`, `tool.updated`, and `message.completed` enter the sidecar lifecycle.
- Answer content streams inside the card; after the sidecar accepts completion, Hermes no longer emits gray native text.
- Tool calls show real-time counts and final totals.
- Footer shows duration, model, input/output tokens, and context usage; abnormal token accumulation is filtered.
- A real long-card stress test updated one Feishu card to 16k Chinese characters.
- Installer validation completed a `restore -> install` loop against a real Hermes `v2026.4.23` directory and left it installed.

Latest full automated regression: `396 passed`.
