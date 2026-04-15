# Feishu Streaming Card for Hermes

Adds Feishu/Lark streaming card support to [Hermes Gateway](https://github.com/joeynyc/hermes-agent). When a user sends a message to the bot, a card appears with a real-time typewriter effect showing AI thinking, tool calls, and the final result.

---

## 🤔 Tired of these problems?

| Pain Point | Description |
|---|---|
| 🔴 **Message Spam** | Agent thinking floods your chat with scattered messages — thinking fragments, tool logs, all mixed together |
| 🔴 **Waiting Anxiety** | You send a message and get nothing back. Is it dead? Still thinking? No idea how long to wait |
| 🔴 **Format Lost** | Markdown turns into plain text in Feishu — tables, code blocks, lists all broken |
| 🔴 **Debug Blindspot** | Tool calls are invisible. You have no idea what tools the Agent is using or how far it's gotten |

## ✨ One card. Solves all of it.

**Feishu Streaming Card** upgrades Feishu messages into a real-time collaboration panel:

- 📌 **No message spam** — AI thinking streams word-by-word into a single card, chat stays clean
- ⏳ **Status is obvious** — 🤔Thinking → ⚡Running → ✅Completed, clear at every stage
- 🎨 **Native format preserved** — Markdown tables, code blocks, lists render natively in Feishu
- 🔍 **Tools are transparent** — Real-time tool call count and content, full visibility into Agent behavior
- 📊 **Stats auto-generated** — Elapsed time, token usage, context usage, all in the footer

| Feature | Description |
|---|---|
| 🎯 Pre-created card | Card is created immediately when message arrives |
| ⌨️ Typewriter effect | AI thinking is streamed word-by-word into the card |
| 🔧 Tool tracking | Shows real-time tool call count and content |
| 📊 Token stats | Footer shows elapsed time, I/O tokens, context usage |
| 🔒 Sequence protection | asyncio.Lock prevents 300317 race condition |
| ⚙️ Easy config | One-command install, all settings in config.yaml |

---

## Changelog

### v1.1.0（2026-04-15）
- ✨ **Support latest Hermes**: Rewrote patch engine to adapt to latest NousResearch/hermes-agent code structure (send() signature change)
- 🔧 **Auto version detection**: `installer.py --check` auto-detects target Hermes version, skips if already installed
- 🐛 Fix: Agent footer no longer leaks into thinking_content body

### v1.0.0（2026-04-15）
- 🎉 Initial release
- Streaming typewriter card, tool call tracking, Token stats footer

---

## Preview

| Thinking | Completed |
|---|---|
| ![Thinking](thinking.png) | ![Ending](ending.png) |

**Thinking** — Typewriter effect streaming AI reasoning, tool calls tracking in progress
**Completed** — Status switches to ✅, shows result summary and full token stats

---

## Requirements

- Python 3.9+
- [hermes-agent](https://github.com/joeynyc/hermes-agent) installed
- Feishu Bot configured with WebSocket long connection mode
- Python packages: `pyyaml`, `regex`

---

## Installation

### One-step install

```bash
cd ~/github/hermes-feishu-streaming-card
pip install -r requirements.txt
python installer.py --greeting "Your custom greeting"
```

Use `--hermes-dir` to specify the hermes-agent path (default: `~/.hermes/hermes-agent`).

### Manual install

```bash
python installer.py \
  --hermes-dir /path/to/hermes-agent \
  --greeting "主人，苏菲为您服务！" \
  --pending-timeout 30
```

### Verify installation

```bash
python installer.py --check
```

---

## Configuration

After install, add this to `~/.hermes/hermes-agent/config.yaml`:

```yaml
feishu_streaming_card:
  # Card header title — the first thing users see
  greeting: "主人，苏菲为您服务！"

  # Enable/disable the streaming card feature
  enabled: true

  # How long to wait for card creation before sending normal message (seconds)
  pending_timeout: 30
```

---

## Restart Hermes

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway restart
```

---

## Uninstall

```bash
python installer.py --uninstall
# Then restart hermes
python -m hermes_cli.main gateway restart
```

---

## How It Works

```
User sends message
  ↓
run.py: call send_streaming_card() immediately
  ├─ header: greeting + model
  ├─ thinking_content: "⏳ 执行中..."
  ├─ status_label: "🤔思考中"
  ├─ tools_label: "🔧 工具调用 (0次)"
  ├─ tools_body: "⏳ 等待开始..."
  └─ footer: "⏳ 执行中..."
  ↓
Streaming text arrives → edit_message → send()
  └─ writes to thinking_content (overwrite mode, typewriter effect)
  ↓
Tool call arrives → send()
  ├─ status_label: "⚡执行中"
  ├─ tools_label: "🔧 工具调用 (N次)"
  └─ tools_body: tool log
  ↓
Agent finishes → finalize_streaming_card()
  ├─ status_label: "✅已完成"
  ├─ thinking_content: result_summary
  ├─ tools_label: "🔧 工具调用 (N次)  ✅完成"
  └─ footer: token stats
```

---

## Known Limitations

1. **Card creation delay**: Model init takes 10-30s; card shows initial state during this time
2. **CardKit PUT restriction**: Only root-level `markdown`/`plain_text`/`lark_md` elements can be updated
3. **Image/video attachments**: MEDIA: directives are sent as separate messages, not inside the card

---

## File Structure

```
hermes-feishu-streaming-card/
├── README.md                    # This file (Chinese)
├── README_en.md                 # English version
├── installer.py                 # One-command installer
├── requirements.txt             # Python dependencies
├── config.yaml.example          # Config template
└── patch/
    ├── __init__.py
    ├── feishu_patch.py          # feishu.py patch
    └── run_patch.py             # run.py patch
```

---

## Troubleshooting

**Card not appearing?**
```bash
# Check patch status
python installer.py --check

# View logs
tail -f ~/.hermes/logs/agent.log | grep -i "feishu\|streaming\|card"
```

**Sequence conflict errors (300317)?**
Make sure hermes-agent is up to date. Older versions may lack the necessary Lock mechanism.

**Card not updating?**
Verify your Feishu Bot uses WebSocket long connection mode (WS mode required for CardKit updates).

---

## License

MIT
