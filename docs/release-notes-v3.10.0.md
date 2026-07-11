# Hermes Feishu Streaming Card V3.10.0

V3.10.0 adds one focused interaction improvement and one restrained visual improvement: bare `/resume` becomes a native Feishu/Lark picker, and recognized model names gain sanitized semantic color in the existing footer. The normal streaming-card footer/layout and element order remain unchanged.

## Native `/resume` Picker (#94)

- Bare `/resume` queries up to ten recent named sessions already visible under Hermes' `_resume_row_visible(...)` policy and sends one native `select_static` card.
- Session values are opaque session ids. Labels include the title, at most the existing 40-character preview, and a current-session marker.
- Topic reply metadata is preserved so the picker stays in the originating thread.
- Topic replies always carry the command message as their reply anchor, including Hermes adapters that expose an `om_...` root message as `thread_id`; this avoids Feishu create-message field validation failures.
- `/resume <number|title|session_id>` is unchanged.
- Missing session DB, no named sessions, non-Feishu platforms, unavailable card runtime, unverifiable group ownership, and send failures all fail-open to the original Hermes text flow.

On selection, HFC validates picker expiry, chat, visible session id, adapter authorization, and group initiator `open_id`. It returns an immediate callback card, then calls the original Hermes `_handle_resume_command` with a copied `/resume <session_id>` event. HFC does not reimplement session switching: the original Hermes security path retains ownership checks, continuation resolution, current-session handling, running-agent release, boundary cleanup, and model/reasoning override cleanup. The result updates the original card; only an update failure may send one fallback result card.

Private chats intentionally do not compare the callback operator with the initiating operator. Group and topic cards require the same initiating user, matching the confirmed product rule.

## Sanitized Model Footer Color (PR #98)

- Recognized GPT/o1/o3, Claude, DeepSeek, Kimi/Moonshot, GLM, and Tencent/Hunyuan prefixes use a restrained Feishu semantic color around the model label only.
- Every model name receives HTML escape before entering card markdown, including unknown providers and malicious-looking names.
- The divider, `element_id=footer`, configured field order, separators, `x-small` text size, thinking/failed states, and empty-field behavior are unchanged.

## Contributors

- @colinaaa proposed issue #94, including the native picker UX, fail-open contract, topic behavior, and acceptance criteria.
- @charles5g opened PR #98; jackmim authored the model-color concept. The mainline implementation retains that contribution while adding escaping and layout-invariant coverage.

## Validation

- Focused interaction/installer/render matrix: `416 passed` before release metadata changes.
- Full release gate: `1216 passed, 3 skipped` on both Python 3.9 and Python 3.12, followed by `git diff --check`.
- Real Feishu acceptance passed for private selection/current state, group initiating-user selection, topic picker placement, and same-card PATCH. Changed-operator rejection remains automation-backed because the dedicated test group had one human participant.

## Release Assets

- `hermes-feishu-card-v3.10.0-macos.tar.gz`
- `hermes-feishu-card-v3.10.0-linux.tar.gz`
- `hermes-feishu-card-v3.10.0-windows.zip`
- `hermes-feishu-card-v3.10.0-checksums.txt`
