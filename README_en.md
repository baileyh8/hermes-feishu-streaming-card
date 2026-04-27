# Feishu Streaming Card for Hermes v2.3

> **Legacy reference, not the active runtime.** This English README documents the older v2.x/dual implementation. The current mainline is the sidecar-only package in `hermes_feishu_card/`: Hermes installs a minimal fail-open hook that forwards `SidecarEvent` JSON to the local sidecar. Real Feishu CardKit create/update integration is still future work in the current mainline.

The current implementation aims to add Feishu/Lark streaming card support to [Hermes Gateway](https://github.com/joeynyc/hermes-agent). At this phase, installed Hermes hooks forward events to the local sidecar; user-visible CardKit behavior is validated with fake clients and mock sidecar tests, not released as a verified real CardKit integration.

> **⚠️ Usage Risk**: This tool modifies Hermes Gateway files (event forwarding logic, ~50 lines). Although sidecar mode provides process isolation, any third-party modification carries risk. Test in non-production environments first.

---

## ✨ Preview

| Thinking | Completed |
|---|---|
| ![Thinking](thinking.png) | ![Ending](ending.png) |

**Thinking** — Typewriter effect streaming AI reasoning, tool calls tracking
**Completed** — Status switches to ✅, shows result summary and full token stats

---

## 🎯 Features

| Feature | Description |
|---|---|
| 📌 **No Spam** | AI thinking streams word-by-word into a single card |
| ⌨️ **Typewriter Effect** | Real-time streaming of AI reasoning |
| 🔧 **Tool Tracking** | Real-time display of tool call count and content |
| 📊 **Smart Footer** | Model, time, tokens (k/m), context percentage |
| 🔒 **Process Isolation** | Sidecar runs independently, won't crash Hermes |
| ⚙️ **One-Command Deploy** | Fully automated installation script |

---

## 🏗️ Architecture

**Sidecar Mode (v2.1+ Recommended)**

Streaming card logic runs in a separate process; Hermes Gateway only forwards events:

```
User sends message
    ↓
Hermes Gateway (receives message, forwards events)
    ↓ WebSocket/HTTP
Feishu Streaming Sidecar (separate process)
    ↓ Future CardKit API integration
Feishu Card (not yet verified in current mainline)
```

**Gateway Modification**: Only adds event forwarding to `gateway/platforms/feishu_forward.py` (~50 lines), no core code changes.

---

## 📋 Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.9+ | Sidecar runtime |
| Hermes Gateway | **v0.4.0 - v0.10.0+** or **v2026.3.23+** | ⚠️ See version details below |
| Feishu Bot | - | Bot capability + CardKit enabled |
| Node.js | 18+ | For lark-cli |
| lark-cli | `@larksuite/oapi-cli` | Required for tenant token |

---

## ⚠️ IMPORTANT: Hermes Version Requirements

**This plugin supports all Hermes versions that contain `_msg_start_time = time.time()` in gateway/run.py.**

| Supported | Version | Tag/Commit | Notes |
|-----------|---------|------------|-------|
| ✅ v0.10.0+ | `v2026.4.x` or `main` | Supported (user verified) |
| ✅ v0.8.0 | `v2026.4.8` or `main` | Latest supported |
| ✅ v0.7.0 | `v2026.4.3` | Supported |
| ✅ v0.6.0 | `v2026.3.30` | Supported |
| ✅ v0.5.0 | `v2026.3.28` | Supported |
| ✅ v0.4.0 | `v2026.3.23` | Supported |
| ❌ v0.3.x or earlier | `v2026.3.17` or older | **NOT supported** (function names changed) |

> **💡 How to check if supported?** Run this command - if it outputs `_msg_start_time`, it's supported:
> ```bash
> grep -n "_msg_start_time.*time.time" ~/.hermes/hermes-agent/gateway/run.py
> ```

### How to Check Your Hermes Version?

```bash
cd ~/.hermes/hermes-agent && git log --oneline -1
```

Or check the tag:

```bash
cd ~/.hermes/hermes-agent && git describe --tags
```

### If Your Version Is Wrong?

**Downgrade to v2026.3.30 (recommended):**

```bash
cd ~/.hermes/hermes-agent

# Method 1: Use tag
git checkout v2026.3.30

# Method 2: Use version number  
git checkout v0.6.0
```

**Upgrade to latest main:**

```bash
cd ~/.hermes/hermes-agent
git checkout main
git pull
```

⚠️ After switching versions, re-run the installation script:

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --mode sidecar
```

---

## 🚀 Deployment

### Step 1: Clone the Project

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git ~/github/hermes-feishu-streaming-card
cd ~/github/hermes-feishu-streaming-card
```

### Step 2: Install Python Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Install and Authenticate lark-cli

```bash
# Install
npm install -g @larksuite/oapi-cli

# Authenticate (interactive)
lark-cli auth login
```

You'll need:
- **App ID**: Feishu Open Platform → Your app → Credentials & Basic Info
- **App Secret**: Same location as above

### Step 4: Configure Feishu Bot

On [Feishu Open Platform](https://open.feishu.cn/):

1. **Enable Bot Capability**: Add App Capability → select Bot
2. **Apply Permissions** (Message Subscription → Permission Management):
   - `im:message` — Send card messages
   - `im:message:send_as_bot` — Send as bot
   - `cardkit:card` — Create and update cards
3. **Enable Long Connection**: Message Subscription → Subscription Method → Long Connection (WebSocket)
4. **Enable CardKit**: App Permissions → search CardKit → add

> ⚠️ Permissions require review (usually a few minutes to hours)

### Step 5: One-Command Install Sidecar

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --mode sidecar
```

Installation will:
1. Check port conflict (auto-select alternative if 8765 is in use)
2. Check environment dependencies
3. Backup existing config
4. Install sidecar to `~/.hermes/feishu-sidecar/`
5. Modify gateway event forwarding (~50 lines)
6. Start sidecar service
7. Verify running status

### Step 6: Configure Gateway

Add to `~/.hermes/hermes-agent/config.yaml`:

```yaml
feishu_streaming_card:
  enabled: true
  mode: "sidecar"
  sidecar:
    host: "localhost"
    port: 8765
  greeting: "Your greeting here!"
```

### Step 7: Restart Hermes Gateway

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway restart
```

---

## 🔧 Configuration

### Gateway Config (config.yaml)

```yaml
feishu_streaming_card:
  enabled: true              # Enable/disable streaming card
  mode: "sidecar"           # Fixed to sidecar mode
  sidecar:
    host: "localhost"       # Sidecar address
    port: 8765              # Sidecar port
  greeting: "Your greeting!" # Card title
```

### Sidecar Config

Config file: `~/.hermes/feishu-sidecar.yaml`

Usually no modification needed.

---

## 📊 Card Footer Format (v2.3)

```
minimax-M2.7  ⏱️ 30s  81.1k↑  1.2k↓ ctx 82k/204k
```

| Field | Description |
|-------|-------------|
| `minimax-M2.7` | Current model |
| `30s` | Processing time |
| `81.1k↑` | Input tokens (k/m abbreviated) |
| `1.2k↓` | Output tokens |
| `ctx 82k/204k` | Context current/window size (for reference only, may not reflect actual usage) |

---

## 🔍 Management Commands

```bash
# Check sidecar status
curl http://localhost:8765/health

# View sidecar logs
tail -f ~/.hermes/logs/sidecar.log

# Restart sidecar
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill
sleep 1
cd ~/github/hermes-feishu-streaming-card/sidecar && \
  PYTHONPATH=~/github/hermes-feishu-streaming-card \
  python -m sidecar.server > ~/.hermes/logs/sidecar.log 2>&1 &

# View gateway logs
tail -f ~/.hermes/logs/hermes-gateway.log
```

---

## 🐛 Troubleshooting

### Card Not Updating/Stuck

1. Check sidecar status:
   ```bash
   curl http://localhost:8765/health
   ```
2. If `active_cards` is not 0, restart sidecar
3. Check logs:
   ```bash
   tail ~/.hermes/logs/sidecar.log
   ```

### "card table number over limit" Error

This is Feishu CardKit's card limit. Usually caused by accumulated cards from abnormal exits. Restart sidecar to recover:

```bash
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill
sleep 1
cd ~/github/hermes-feishu-streaming-card/sidecar && \
  PYTHONPATH=~/github/hermes-feishu-streaming-card \
  python -m sidecar.server > ~/.hermes/logs/sidecar.log 2>&1 &
```

### Token Auth Expired

```bash
lark-cli auth login
```

### Gateway Fails to Start

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway start 2>&1
```

---


## 🗑️ Uninstall & Restore

### Option 1: Auto Uninstall with Installer

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --uninstall
```

The installer will automatically:
1. Restore Gateway files from backup
2. Delete sidecar config
3. Remove copied adapter files

### Option 2: Manual Restore

If the installer doesn't work, manual restore steps:

**1. Stop Sidecar**
```bash
# Find and stop sidecar process
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null

# Verify stopped
curl http://localhost:8765/health
```

**2. Restore Gateway Files from Backup**

The installer saves backups in `~/.hermes/.fsc_backups/`:
```bash
# List available backups
ls -la ~/.hermes/.fsc_backups/

# Restore latest backup
cp ~/.hermes/.fsc_backups/backup_YYYYMMDD_HHMMSS/* ~/.hermes/hermes-agent/gateway/
```

**3. Delete Config Files**
```bash
rm ~/.hermes/feishu-sidecar.yaml
```

**4. Restart Gateway**
```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway restart
```

### Verify Uninstall Success

```bash
# 1. Sidecar should not respond
curl http://localhost:8765/health
# Expected: Connection refused

# 2. Gateway should start normally
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway start
# Expected: No errors, starts normally

# 3. Test Bot
Send a message to the Bot — should reply as plain text (not card)
```

---

## 📝 Changelog

### v2.4 (2026-04-19)
- ✅ **Fix tool call tracking**: `_flush_updates` now uses latest `tool_count/tool_lines`, fixing missing tool calls during streaming
- ✅ **Configurable greeting**: Set card title via `feishu_streaming_card.greeting`
- ✅ **Port conflict detection**: Auto-detect port conflicts during install, auto-select alternative port
- ✅ **Patch syntax validation**: Fixed `find_pattern_end` string brace handling bug

### v2.3 (2026-04-19)
- ✅ **Footer optimization**: Model name + time + tokens (k/m) + context percentage
- ✅ **Footer font size**: x-small
- ✅ **Refresh rate optimization**: 2s or 300 chars or complete sentence
- ✅ **Flush timeout protection**: 5s timeout, failures don't block finalize

### v2.2 (2026-04-19)
- ✅ **Fix final status loss**: flush failure doesn't block finalize
- ✅ **11310 error handling**: No more infinite retries

### v2.1 (2026-04-17)
- ✅ **Sidecar architecture**: Independent process, no intrusion to Hermes

### v2.0 (2026-04-16)
- ✅ **Safe installation**: Syntax validation + injection point verification + auto backup
- ✅ **Version-aware**: Auto-detect different Hermes versions
- ✅ **Concurrency protection**: per-chat asyncio.Lock

### v1.0 (2026-04-15)
- 🎉 **Historical prototype**: Streaming typewriter card design, tool tracking, token stats. This changelog entry does not describe the current sidecar-only mainline release status.

---

## 🗂️ Project Structure

```
hermes-feishu-streaming-card/
├── README.md                    # This file (Chinese)
├── README_en.md                 # English version
├── installer_v2.py              # v2.x installer (recommended)
├── installer_sidecar.py         # Sidecar-only installer
├── requirements.txt             # Python dependencies
├── config.yaml.example         # Config example
├── sidecar/                    # Sidecar core code
│   ├── server.py               # HTTP server entry
│   ├── card_manager.py         # Card state management
│   ├── cardkit_client.py       # CardKit API wrapper
│   └── config.py              # Config loader
├── adapter/                    # Adapter pattern
├── scripts/                    # Utility scripts
└── tests/                     # Test cases
```

---

## ⚠️ Risk Disclaimer

1. **Gateway Modification Risk**: This tool modifies Hermes Gateway's `feishu_forward.py`, adding event forwarding logic. Although backups and version control are in place, any third-party modification carries risk.

2. **Feishu API Limits**: CardKit API has rate limits (`card table number over limit`). May trigger during large text output.

3. **Token Expiration**: lark-cli's tenant_access_token is valid for 2 hours. Re-auth after machine restart.

4. **Version Compatibility**: Hermes Gateway updates may require re-installation.

**Recommendations**:
- Test in non-production environments first
- Regularly backup config files
- Follow [GitHub Issues](https://github.com/baileyh8/hermes-feishu-streaming-card/issues) for updates

---

## 📚 Related Links

- [Hermes Agent](https://github.com/joeynyc/hermes-agent)
- [Feishu CardKit Docs](https://open.feishu.cn/document/ukTMukTMukTM/uEDOwedzUjL24CN04iN0kNj0)
- [lark-cli](https://github.com/larksuite/oapi-cli)

---

**Need help?** Submit an [Issue](https://github.com/baileyh8/hermes-feishu-streaming-card/issues)
