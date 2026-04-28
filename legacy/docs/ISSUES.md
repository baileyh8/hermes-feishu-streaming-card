# Known Issues

## ISSUE-002: Gateway event forwarding not implemented

**Status**: Open  
**Discovered**: 2026-04-17  
**Severity**: P0 (blocks end-to-end)

### Description
Gateway (feishu.py) does not forward events to sidecar. Sidecar server is healthy and /events works, but no events arrive from gateway.

### Required Fix
Add 4 injection points to feishu.py (see QUICKSTART.md for exact code):
1. `__init__`: create `_event_emitter` via `get_emitter(self)`
2. `_handle_message_with_agent`: emit `message_received`
3. `send` (streaming): emit `thinking` after each delta
4. `after_agent_hooks`: emit `finish`

### Verification
After patch:
- Send message via Hermes chat
- Sidecar log shows: `[CardKit] Card created: ...`
- Streaming updates appear in Feishu card
- Final card shows `final_content` with footer stats
