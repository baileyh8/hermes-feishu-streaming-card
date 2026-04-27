# E2E Preview Materials Design

## Goal

Provide reproducible visual verification material for the sidecar-only streaming card flow without requiring real Feishu credentials or network access.

## Scope

This adds a local preview generator and committed preview artifacts. It does not replace the real `smoke-feishu-card` command for live Feishu validation.

## Design

Add `tools/generate_e2e_preview.py`. The script builds sample `CardSession` objects by applying real `SidecarEvent` instances, renders cards through `render_card()`, and writes:

- `docs/assets/e2e-card-preview.json`
- `docs/assets/e2e-card-preview.svg`

The SVG visualizes thinking and completed states side by side. It must show `思考中`, `已完成`, `工具调用 2 次`, and no visible `</think>` tag.

## Verification

`tests/unit/test_e2e_preview.py` runs the generator against a temporary directory and asserts the visual and JSON artifacts include expected state labels and sanitized thinking text. Documentation tests assert the committed artifacts and usage docs are linked.
