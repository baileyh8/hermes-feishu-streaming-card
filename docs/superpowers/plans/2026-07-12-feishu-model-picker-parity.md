# Feishu `/model` Picker Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flattened Feishu `/model` dropdown with a two-level Provider → Model picker driven by the exact provider tree Hermes already supplies to its CLI picker.

**Architecture:** Keep all behavior in the existing Feishu adapter hook. Normalize the upstream provider rows once, render provider and model views from that immutable tree, and store only navigation state beside the existing callback. Provider navigation returns a replacement card synchronously; final model selection retains the existing immediate acknowledgement and background Hermes callback.

**Tech Stack:** Python 3.10+, Hermes Gateway adapter monkeypatch, Feishu interactive-card JSON, pytest.

## Global Constraints

- `providers` supplied by Hermes is the sole availability source; do not parse `config.yaml` or credentials.
- Preserve provider and model order while removing invalid and duplicate rows.
- Keep existing Hermes `on_model_selected(chat_id, model_id, provider_slug)` as the only model-switch implementation.
- Provider navigation, Back, Cancel, and model selection must pass the existing chat and operator authorization checks.
- Never serialize API keys, credentials, base URLs, local paths, or raw Hermes configuration.
- Preserve the sidecar/text fallback and older Hermes compatibility.
- Do not edit installed Hermes `gateway/run.py` by hand.

---

### Task 1: Normalize The Hermes Provider Tree And Render Both Views

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Test: `tests/unit/test_hook_runtime.py`

**Interfaces:**
- Consumes: Hermes `providers: Any`, `current_provider: str`, and `current_model: str`.
- Produces: `_model_picker_provider_tree(providers: Any) -> list[dict[str, Any]]`, `_model_picker_provider_options(...)`, `_model_picker_model_options(...)`, and `_hfc_native_model_picker_card(...) -> dict[str, Any]`.

- [ ] **Step 1: Add failing normalization and rendering tests**

Add tests that pass duplicated, malformed, and ordered provider rows and assert the normalized tree is exactly:

```python
[
    {
        "slug": "deepseek",
        "name": "DeepSeek",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
    },
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "models": ["openai/gpt-5.5"],
    },
]
```

Assert the provider card labels include `DeepSeek (2 个模型)` and mark the current provider. Assert the model card contains only the selected provider's models, marks the current model, and includes Back and Cancel buttons.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k "model_picker_provider_tree or model_picker_provider_card or model_picker_model_card" -q
```

Expected: failures because the new helper functions do not exist.

- [ ] **Step 3: Implement normalization and view builders**

Implement these boundaries in `hook_runtime.py`:

```python
def _model_picker_provider_tree(providers: Any) -> list[dict[str, Any]]:
    """Return a sanitized, ordered provider tree from Hermes picker rows."""


def _model_picker_provider_options(
    providers: list[dict[str, Any]], *, current_provider: str, max_options: int = 100
) -> list[dict[str, str]]:
    """Return Feishu options whose values identify a provider only."""


def _model_picker_model_options(
    provider: dict[str, Any], *, current_model: str, max_options: int = 100
) -> list[dict[str, str]]:
    """Return Feishu options that preserve the existing provider/model JSON contract."""


def _hfc_native_model_picker_card(
    *,
    picker_id: str,
    providers: list[dict[str, Any]],
    current_provider: str,
    current_model: str,
    selected_provider: str = "",
) -> dict[str, Any]:
    """Render provider view when selected_provider is blank, otherwise model view."""
```

Use existing `_hfc_select_static` and `_hfc_button`. Provider-select actions carry `hfc_model_picker_view="providers"`; model-select actions carry `hfc_model_picker_view="models"`; navigation buttons carry `hfc_model_picker_nav="back"` or `"cancel"`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit the pure picker layer**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: render hierarchical Feishu model picker"
```

### Task 2: Add Native Provider Navigation, Back, Cancel, And Final Selection

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Test: `tests/unit/test_hook_runtime.py`

**Interfaces:**
- Consumes: normalized provider tree and card builder from Task 1.
- Produces: persistent `_hfc_model_picker_state[picker_id]` navigation state and a model action handler that distinguishes provider navigation from final selection.

- [ ] **Step 1: Replace flat-picker expectations with failing two-level tests**

Update `test_native_feishu_model_picker_uses_websocket_card_when_connected` to assert the initial card shows provider options and that stored state includes the normalized tree, current provider, and current model.

Add action tests proving:

```python
# Provider click: replacement card shows only DeepSeek models and keeps state.
# Back click: replacement card restores providers and keeps state.
# Cancel click: replacement card says cancelled, removes state, callback count is 0.
# Model click: immediate switching card, then callback exactly once and state removed.
```

Also assert a provider selection absent from stored state is rejected without invoking the callback.

- [ ] **Step 2: Run native-picker tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k "native_feishu_model_picker or model_picker_navigation or model_picker_cancel or resolves_native_model_picker" -q
```

Expected: old flat-card output and action handling fail the new assertions.

- [ ] **Step 3: Send the provider view and store immutable navigation data**

Change `_hfc_send_native_model_picker` to:

```python
provider_tree = _model_picker_provider_tree(providers)
if not provider_tree:
    return _send_result(False, error="no model options")
card = _hfc_native_model_picker_card(
    picker_id=picker_id,
    providers=provider_tree,
    current_provider=current_provider,
    current_model=current_model,
)
```

Store `providers`, `current_provider`, `current_model`, `selected_provider`, and the existing routing/callback fields in `_hfc_model_picker_state`.

- [ ] **Step 4: Route navigation before final model switching**

Extend `_hfc_prepare_native_model_action` to return `view` and `navigation` values. In `_hfc_handle_native_model_action`:

```python
if navigation == "cancel":
    state.pop(picker_id, None)
    return cancelled_result_card
if navigation == "back":
    item["selected_provider"] = ""
    return provider_picker_card
if view == "providers":
    validate selected provider against item["providers"]
    item["selected_provider"] = provider_slug
    return model_picker_card
# Existing model selection continues through the background callback path.
```

Return navigation cards through `_hfc_raw_feishu_callback_response` so Feishu replaces the same message immediately. Do not schedule the model-switch background task for provider, Back, or Cancel actions.

- [ ] **Step 5: Run native-picker tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 6: Run the full hook/patch/install compatibility matrix**

```bash
python -m pytest tests/unit/test_hook_runtime.py tests/unit/test_patcher.py tests/integration/test_cli_install.py -q
```

Expected: all tests pass, including old Hermes and text-fallback cases.

- [ ] **Step 7: Commit native navigation**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: navigate Feishu model picker by provider"
```

### Task 3: Document And Verify CLI Parity

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/release-notes-v4.0.0.md`
- Modify: `docs/release-notes-v4.0.0.en.md`
- Modify: `docs/wiki/feishu-acceptance.md`
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: the final native picker behavior from Task 2.
- Produces: public V4 documentation and a repeatable real-Feishu acceptance record.

- [ ] **Step 1: Add failing documentation assertions**

Require the V4 docs to state that Feishu `/model` uses the Hermes-supplied provider tree and navigates Provider → Model instead of flattening all models.

- [ ] **Step 2: Run docs tests and verify RED**

```bash
python -m pytest tests/unit/test_docs.py -q
```

Expected: the new parity wording assertions fail.

- [ ] **Step 3: Update bilingual docs and acceptance checklist**

Document:

```text
/model 与 Hermes CLI 使用同一 Provider/模型列表；飞书先选 Provider，再选模型。
```

Add acceptance steps for provider-count comparison, entering DeepSeek, confirming its model count, Back navigation, final model selection, and absence of duplicate gray text.

- [ ] **Step 4: Run docs tests and verify GREEN**

Run the command from Step 2. Expected: all docs tests pass.

- [ ] **Step 5: Run real CLI and Feishu comparison**

In the authenticated local Hermes environment:

```text
1. Open CLI `/model` and record provider names/counts without credentials.
2. Send `/model` to the authorized Feishu test chat.
3. Confirm provider names/counts match the Hermes-supplied list.
4. Open DeepSeek and confirm the model list/count matches CLI.
5. Use Back, re-enter a provider, select a model, and confirm the active model changes.
6. Confirm there is no native gray duplicate message or callback timeout toast.
```

Do not commit the real chat ID, operator IDs, credentials, or unredacted screenshots.

- [ ] **Step 6: Commit docs and acceptance evidence**

```bash
git add README.md README.en.md docs/user-guide.md docs/user-guide.en.md \
  docs/release-notes-v4.0.0.md docs/release-notes-v4.0.0.en.md \
  docs/wiki/feishu-acceptance.md tests/unit/test_docs.py
git commit -m "docs: explain Feishu model picker parity"
```

### Task 4: Complete The V4.0.0 Release Gate

**Files:**
- Modify only if verification exposes a defect: existing V4 source, tests, screenshots, readiness docs, or release metadata.

**Interfaces:**
- Consumes: all V4 runtime-card and model-picker commits.
- Produces: a reviewed, merged, tagged, published, and publicly installable V4.0.0 release.

- [ ] **Step 1: Inspect all four screenshots and replace any stale layout**

Verify running, waiting, failed, and completed images are real Feishu captures, redacted, visually consistent with the final V4 title/subtitle/native-reply design, and larger than 20 KB. Replace the stale failed-state image if it still uses the pre-title/subtitle layout.

- [ ] **Step 2: Run the release gate**

```bash
python -m pytest -q && git diff --check
```

Expected: the complete suite passes and `git diff --check` prints nothing.

- [ ] **Step 3: Review the complete branch diff**

Check for secrets, real chat/operator IDs, accidental `.venv`, raw local paths, regression in native reply behavior, duplicate completion status, and missing contributor credit. Resolve every finding and rerun Step 2.

- [ ] **Step 4: Commit remaining V4 source, tests, docs, and redacted assets**

Stage explicit paths only; never stage `.venv`.

```bash
git status --short
git diff --cached --check
git commit -m "feat: release V4 live runtime cards"
```

- [ ] **Step 5: Push, open the PR, and wait for CI**

```bash
git push -u origin codex/v4.0.0-live-runtime-card
gh pr create --fill --base main --head codex/v4.0.0-live-runtime-card
gh pr checks --watch
```

Expected: all required checks pass.

- [ ] **Step 6: Merge and publish V4.0.0**

Merge the approved PR, update local `main`, create annotated tag `v4.0.0`, push the tag, create or verify the GitHub Release, and wait for release-assets workflow completion.

- [ ] **Step 7: Verify public installation**

Install V4.0.0 into a clean temporary environment from the public release artifact, run package metadata/import smoke tests, and confirm reported version is exactly `4.0.0`.
