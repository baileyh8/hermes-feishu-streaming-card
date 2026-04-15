# Feishu Streaming Card for Hermes

Adds Feishu/Lark streaming card support to [Hermes Gateway](https://github.com/joeynyc/hermes-agent). When a user sends a message to the bot, a card appears with a real-time typewriter effect showing AI thinking, tool calls, and the final result.

---

## Features

| Feature | Description |
|---|---|
| 🎯 Pre-created card | Card is created immediately when message arrives |
| ⌨️ Typewriter effect | AI thinking is streamed word-by-word into the card |
| 🔧 Tool tracking | Shows real-time tool call count and content |
| 📊 Token stats | Footer shows elapsed time, I/O tokens, context usage |
| 🔒 Sequence protection | asyncio.Lock prevents 300317 race condition |
| ⚙️ Easy config | One-command install, all settings in config.yaml |

---

## Preview

```
┌─────────────────────────────────────────┐
│  主人，苏菲为您服务！                    │  ← Header title (customizable)
│  MiniMax-M2.7  🤔思考中                 │  ← Model + status
├─────────────────────────────────────────┤
│  用户想了解可灵AI的关键信息...          │  ← AI thinking (typewriter)
│                                         │
│  🤔思考中                                │  ← Status label
│  🔧 工具调用 (1次)                       │  ← Tool count
│  ⚙️ mcp_tavily_search: "可灵AI 2026"  │  ← Tool log
│                                         │
│  3.2s  ·  4.5K↑ / 2.1K↓  ·  上下文... │  ← Token stats
└─────────────────────────────────────────┘
          ↓ after completion
┌─────────────────────────────────────────┐
│  ✅已完成                                │  ← Status updated
│  可灵AI（Kling AI）是快手旗下...        │  ← Result summary
│  🔧 工具调用 (1次)  ✅完成              │
│  3.2s  ·  4.5K↑ / 2.1K↓  ·  上下文... │
└─────────────────────────────────────────┘
```

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
