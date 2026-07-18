# Issue #133 Card Text Sizes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users configure body, reasoning, tool, notice, and footer text sizes, including PC/mobile mappings, without changing default Card JSON or promising client-controlled card dimensions.

**Architecture:** `config.py` validates and normalizes a closed `card.text_sizes` schema. Card config merging deep-merges only this nested field across base/profile/bot layers. `render_card` receives normalized roles, selects scalar sizes or stable `hfc_<role>` aliases, and emits `config.style.text_size` only when a device mapping is used.

**Tech Stack:** Python, YAML, Feishu CardKit JSON 2.0, pytest.

## Global Constraints

- Accepted roles are exactly `body`, `reasoning`, `tool`, `notice`, and `footer`.
- Accepted mapping fields are exactly `default`, `pc`, and `mobile`.
- Accepted values are exactly: `heading-0`, `heading-1`, `heading-2`, `heading-3`, `heading-4`, `heading`, `normal`, `notation`, `xxxx-large`, `xxx-large`, `xx-large`, `x-large`, `large`, `medium`, `small`, `x-small`.
- Defaults are body `normal`, reasoning `small`, tool `x-small`, notice `x-small`, footer `x-small`.
- With no `text_sizes` configuration, emitted Card JSON remains byte-for-structure equivalent to current output: body has no explicit `text_size`, current timeline/footer fields remain unchanged, and no `config.style` is emitted.
- Mapping fallback order is explicit role value, then mapping `default`, then role default; missing `pc`/`mobile` inherit the resolved default.
- Unknown roles/fields/types/empty values raise `ValueError` with the exact config path and no full-config dump.
- Do not add `card.width`, `card.height`, CSS, arbitrary JSON style, margin, padding, Header, or button size controls.

---

### Task 1: Validate and normalize `card.text_sizes`

**Files:**
- Modify: `hermes_feishu_card/config.py:13-127`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `normalize_text_sizes(value, *, path="card.text_sizes") -> dict[str, str | dict[str, str]]`.
- Produces: `merge_text_sizes(base, override) -> dict` for controlled nested merging.

- [ ] **Step 1: Write failing valid-schema tests**

Add tests for scalar and mapping inputs:

```python
def test_load_config_normalizes_card_text_sizes(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
card:
  text_sizes:
    body: large
    footer:
      default: x-small
      mobile: notation
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config["card"]["text_sizes"] == {
        "body": "large",
        "footer": {
            "default": "x-small",
            "pc": "x-small",
            "mobile": "notation",
        },
    }
```

Also assert a mapping containing only `pc` resolves default/mobile from the role default and explicit `pc` remains unchanged.

- [ ] **Step 2: Write failing invalid-path tests**

Parametrize:

```python
(
    ({"unknown": "small"}, "card.text_sizes.unknown"),
    ({"footer": {"tablet": "small"}}, "card.text_sizes.footer.tablet"),
    ({"body": ""}, "card.text_sizes.body"),
    ({"body": "normal_v2"}, "card.text_sizes.body"),
    ({"body": 12}, "card.text_sizes.body"),
    ({"footer": []}, "card.text_sizes.footer"),
    ({"footer": {}}, "card.text_sizes.footer"),
    ({"footer": {"mobile": 12}}, "card.text_sizes.footer.mobile"),
)
```

Assert `ValueError` contains the expected path and does not contain sibling config values or secrets.

- [ ] **Step 3: Run config tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_config.py -k "text_sizes" -q
```

Expected: FAIL because no validation/normalization exists.

- [ ] **Step 4: Implement closed-schema normalization**

Add constants:

```python
CARD_TEXT_SIZE_VALUES = frozenset(
    {
        "heading-0",
        "heading-1",
        "heading-2",
        "heading-3",
        "heading-4",
        "heading",
        "normal",
        "notation",
        "xxxx-large",
        "xxx-large",
        "xx-large",
        "x-large",
        "large",
        "medium",
        "small",
        "x-small",
    }
)
CARD_TEXT_SIZE_DEFAULTS = {
    "body": "normal",
    "reasoning": "small",
    "tool": "x-small",
    "notice": "x-small",
    "footer": "x-small",
}
CARD_TEXT_SIZE_DEVICE_KEYS = frozenset({"default", "pc", "mobile"})
```

Implement `normalize_text_sizes` with no mutation of input. Scalars are stripped and returned as scalars. Mappings must be non-empty, validate every field, compute:

```python
fallback = mapping.get("default", CARD_TEXT_SIZE_DEFAULTS[role])
normalized = {
    "default": fallback,
    "pc": mapping.get("pc", fallback),
    "mobile": mapping.get("mobile", fallback),
}
```

Call it after YAML/profile expansion and environment overrides for the base card and every profile card. Bot card validation should occur where bot definitions are parsed, using paths `bots.items.<bot_id>.card.text_sizes` and `profiles.<profile>.bots.items.<bot>.card.text_sizes`.

- [ ] **Step 5: Preserve absence from defaults**

Do not add `text_sizes` to `DEFAULT_CONFIG["card"]`. Normalize only when the key exists. This is required for default Card JSON stability and existing default-config equality tests.

- [ ] **Step 6: Run config tests and commit**

Run:

```bash
python -m pytest tests/unit/test_config.py tests/unit/test_bots.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/config.py tests/unit/test_config.py tests/unit/test_bots.py
git commit -m "feat: validate card text size configuration"
```

### Task 2: Deep-merge text-size roles across base/profile/bot

**Files:**
- Modify: `hermes_feishu_card/config.py:95-111`
- Modify: `hermes_feishu_card/bots.py:238-248`
- Modify: `hermes_feishu_card/server.py:2613-2646`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_bots.py`
- Modify: `tests/integration/test_server.py`

**Interfaces:**
- Consumes: normalized `text_sizes` mappings from Task 1.
- Produces: base < profile < bot role-level precedence without shallow replacement.

- [ ] **Step 1: Write failing merge tests**

Add a direct `resolve_card_config` test:

```python
resolved = resolve_card_config(
    {"text_sizes": {"body": "normal", "footer": "x-small"}},
    {"text_sizes": {"footer": "notation"}},
    {"text_sizes": {"body": "large"}},
)
assert resolved["text_sizes"] == {"body": "large", "footer": "notation"}
```

Add profile expansion and server session tests proving an override of one role preserves other roles. Add two simultaneously active bot routes and assert their resolved session configs differ only in the configured role.

- [ ] **Step 2: Run merge tests and verify RED**

Run:

```bash
python -m pytest \
  tests/unit/test_config.py \
  tests/unit/test_bots.py \
  tests/integration/test_server.py -k "text_sizes" -q
```

Expected: FAIL because current `.update()` calls replace the nested mapping.

- [ ] **Step 3: Implement one controlled card merge helper**

In `config.py` expose:

```python
def merge_card_config(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resolved = copy.deepcopy(dict(base or {}))
    incoming = copy.deepcopy(dict(override or {}))
    incoming_sizes = incoming.pop("text_sizes", None)
    resolved.update(incoming)
    if incoming_sizes is not None:
        sizes = copy.deepcopy(resolved.get("text_sizes", {}))
        sizes.update(copy.deepcopy(incoming_sizes))
        resolved["text_sizes"] = sizes
    return resolved
```

Use this helper for profile card expansion, `bots.resolve_card_config`, and server fallback merging. Do not deep-merge arbitrary nested card fields.

- [ ] **Step 4: Run merge matrix and commit**

Run:

```bash
python -m pytest tests/unit/test_config.py tests/unit/test_bots.py tests/integration/test_server.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/config.py hermes_feishu_card/bots.py hermes_feishu_card/server.py tests/unit/test_config.py tests/unit/test_bots.py tests/integration/test_server.py
git commit -m "fix: deep merge card text size roles"
```

### Task 3: Render scalar role sizes without changing defaults

**Files:**
- Modify: `hermes_feishu_card/render.py:76-176,229-468,525-566`
- Modify: `hermes_feishu_card/server.py:2529-2561`
- Modify: `hermes_feishu_card/cli.py:1819-1875`
- Modify: `tests/unit/test_render.py`
- Modify: `tests/integration/test_server.py`

**Interfaces:**
- Extends: `render_card(session, footer_fields=None, title=DEFAULT_TITLE, interaction_mode="callback", show_reasoning=True, timeline_expanded=False, max_timeline_items=12, max_reasoning_chars=1200, max_tool_result_chars=600, status_config=None, text_sizes=None) -> Dict[str, Any]`.
- Produces: role-specific `text_size` fields.

- [ ] **Step 1: Capture default Card JSON before changing renderer**

Add a regression assertion around an existing representative running and completed card. Assert exact `body.elements`, existing footer `text_size="x-small"`, and absence of `config.style`. Do not regenerate broad snapshots.

- [ ] **Step 2: Write failing scalar-role tests**

Create a session containing main content, reasoning, tool, notice, and footer. Render with:

```python
text_sizes = {
    "body": "large",
    "reasoning": "medium",
    "tool": "small",
    "notice": "notation",
    "footer": "normal",
}
```

Assert each target element receives only its role value. Assert every chunk from long split Markdown receives the same body or timeline role value. Assert attachment summary remains without a role override.

- [ ] **Step 3: Run render tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_render.py -k "text_size" -q
```

Expected: FAIL because `render_card` has no `text_sizes` parameter and sizes are hard-coded.

- [ ] **Step 4: Implement role lookup while preserving defaults**

Add:

```python
def _role_text_size(
    text_sizes: Mapping[str, Any] | None,
    role: str,
    *,
    default: str | None,
) -> str | None:
    value = (text_sizes or {}).get(role)
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return f"hfc_{role}"
    return default
```

Thread the selected role into `_render_main_content_elements`, `_render_timeline_elements`, `_timeline_markdown_elements`, tool summary, independent notice main body, and footer. Add `text_size` only when non-`None`; for body default pass `None`, so unconfigured output remains unchanged. Keep current hard-coded timeline/footer defaults when configuration is absent.

- [ ] **Step 5: Pass session text sizes from server and CLI smoke renderer**

In `_render_session_card_for_app`, pass `card_config.get("text_sizes")` only when it is a mapping. In CLI smoke paths, pass the loaded card config's normalized value. Do not read global environment variables inside renderer.

- [ ] **Step 6: Run renderer/server tests and commit**

Run:

```bash
python -m pytest tests/unit/test_render.py tests/integration/test_server.py tests/integration/test_feishu_client_http.py -q
```

Expected: PASS, including exact default JSON assertions.

Commit:

```bash
git add hermes_feishu_card/render.py hermes_feishu_card/server.py hermes_feishu_card/cli.py tests/unit/test_render.py tests/integration/test_server.py tests/integration/test_feishu_client_http.py
git commit -m "feat: apply role based card text sizes"
```

### Task 4: Emit controlled PC/mobile CardKit aliases

**Files:**
- Modify: `hermes_feishu_card/render.py`
- Modify: `tests/unit/test_render.py`

**Interfaces:**
- Consumes normalized device mappings.
- Produces aliases `hfc_body`, `hfc_reasoning`, `hfc_tool`, `hfc_notice`, `hfc_footer` under `config.style.text_size` only for mapped roles actually used by rendered elements.

- [ ] **Step 1: Write failing alias tests**

Render a card with footer mapping:

```python
{"footer": {"default": "x-small", "pc": "x-small", "mobile": "notation"}}
```

Assert:

```python
assert card["config"]["style"]["text_size"] == {
    "hfc_footer": {
        "default": "x-small",
        "pc": "x-small",
        "mobile": "notation",
    }
}
assert footer["text_size"] == "hfc_footer"
```

Add multiple mapped roles and assert deterministic role order. Configure a role whose element is absent and assert no unused alias is emitted. Assert scalar-only and no-config cards have no `config.style`.

- [ ] **Step 2: Run alias tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_render.py -k "device_text_size or text_size_alias" -q
```

Expected: FAIL because aliases/styles are not emitted.

- [ ] **Step 3: Track used roles during rendering**

Use a local `used_text_size_roles: set[str]` owned by `render_card`. When `_role_text_size` returns an alias, record that role only if the element is appended. Avoid module/global state so concurrent bot renders cannot cross-contaminate.

- [ ] **Step 4: Emit deterministic controlled styles**

After elements are built:

```python
mapped_styles = {
    f"hfc_{role}": dict(text_sizes[role])
    for role in ("body", "reasoning", "tool", "notice", "footer")
    if role in used_text_size_roles and isinstance(text_sizes.get(role), Mapping)
}
if mapped_styles:
    card["config"]["style"] = {"text_size": mapped_styles}
```

Do not merge arbitrary caller style JSON.

- [ ] **Step 5: Run render tests and commit**

Run:

```bash
python -m pytest tests/unit/test_render.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/render.py tests/unit/test_render.py
git commit -m "feat: support device specific card text sizes"
```

### Task 5: Configuration examples, docs, and real visual acceptance

**Files:**
- Modify: `config.yaml.example`
- Modify: setup template in `hermes_feishu_card/cli.py`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `README-install.md`
- Modify: `docs/wiki/event-flow.md`
- Modify: `docs/wiki/feishu-acceptance.md`
- Modify: `tests/unit/test_docs.py`
- Modify: `tests/unit/test_package_metadata.py` only if release version work occurs in the same commit series

**Interfaces:**
- Documents: five roles, allowed values, device mapping, inheritance, and unsupported card dimensions.

- [ ] **Step 1: Write failing example/docs assertions**

Require `config.yaml.example` and generated setup template to stay schema-equivalent. Require Chinese and English README/docs to contain `card.text_sizes`, `body`, `footer`, `mobile`, and an explicit statement that physical card width/height are controlled by Feishu/Lark clients.

- [ ] **Step 2: Run docs tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_docs.py tests/unit/test_config.py -q
```

Expected: FAIL because examples and docs lack the new schema.

- [ ] **Step 3: Add a commented minimal example**

Use this example without enabling it by default:

```yaml
# card:
#   text_sizes:
#     body: normal
#     footer:
#       default: x-small
#       pc: x-small
#       mobile: notation
```

List all roles and allowed values in the detailed guide, not in every README. State that `normal_v2` is not accepted because it is a custom alias in platform examples.

- [ ] **Step 4: Run focused and full release-B gates**

Run:

```bash
python -m pytest \
  tests/unit/test_config.py \
  tests/unit/test_bots.py \
  tests/unit/test_render.py \
  tests/integration/test_server.py \
  tests/integration/test_feishu_client_http.py \
  tests/unit/test_docs.py -q
python -m pytest -q
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 5: Commit documentation**

```bash
git add config.yaml.example hermes_feishu_card/cli.py README.md README.en.md README-install.md docs/wiki tests/unit/test_docs.py
git commit -m "docs: explain card text size configuration"
```

- [ ] **Step 6: Perform desktop/mobile visual acceptance**

Install through the supported setup path and render one running and one completed card on desktop and mobile. Cover body, reasoning, tool, notice, and footer; long Chinese text; code block; table; dark mode; base/profile/bot overrides; streaming-to-terminal stability. Confirm no unexpected truncation, no alias leakage as visible text, and no cross-bot size contamination. Do not treat physical card width/height as an acceptance item.
