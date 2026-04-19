# Feishu Streaming Card for Hermes v2.3

为 [Hermes Gateway](https://github.com/joeynyc/hermes-agent) 添加飞书流式卡片消息支持。发消息给机器人后，卡片以打字机效果实时展示 AI 思考过程、工具调用和任务结果。

> **⚠️ 使用风险**：本工具修改 Hermes Gateway 文件（事件转发逻辑，约 50 行）。虽然 sidecar 模式已做进程隔离，但任何第三方修改都存在风险。建议在非生产环境先测试。

---

## ✨ 效果预览

| 思考中 | 已完成 |
|---|---|
| ![Thinking](thinking.png) | ![Ending](ending.png) |

**思考中** — 打字机效果实时展示 AI 推理过程，工具调用追踪
**已完成** — 状态切换为 ✅，展示结果摘要和完整 token 统计

---

## 🎯 核心特性

| 特性 | 说明 |
|---|---|
| 📌 **消息不刷屏** | 思考过程在同一张卡片内逐字更新 |
| ⌨️ **打字机效果** | AI 思考过程逐字显示在卡片内 |
| 🔧 **工具调用追踪** | 实时展示工具调用次数和内容 |
| 📊 **智能 Footer** | 显示模型、时间、Token（k/m缩略）、上下文百分比 |
| 🔒 **进程隔离** | Sidecar 独立运行，崩溃不影响 Hermes |
| ⚙️ **一键部署** | 安装脚本全自动，配好后即可使用 |

---

## 🏗️ 架构说明

**Sidecar 模式（v2.1+ 推荐）**

流式卡片逻辑运行在独立进程，Hermes Gateway 仅转发事件：

```
用户发消息
    ↓
Hermes Gateway（接收消息，转发事件）
    ↓ WebSocket/HTTP
Feishu Streaming Sidecar（独立进程）
    ↓ CardKit API
飞书卡片
```

**对 Hermes 的修改**：仅在 `gateway/platforms/feishu_forward.py` 添加事件转发（约 50 行），不做核心代码修改。

---

## 📋 环境要求

| 要求 | 版本 | 说明 |
|------|------|------|
| Python | 3.9+ | Sidecar 运行环境 |
| Hermes Gateway | **v0.4.0 - v0.8.0** 或 **v2026.3.23 - v2026.3.30** | ⚠️ 必须在此范围内，见下方详细说明 |
| 飞书 Bot | - | 已开通机器人能力 + CardKit |
| Node.js | 18+ | 用于 lark-cli 获取 token |
| lark-cli | `@larksuite/oapi-cli` | 必装，用于获取 tenant token |

---

## ⚠️ 重要：Hermes 版本要求

**本插件支持所有包含 `_msg_start_time = time.time()` 的 Hermes 版本。**

| 支持的版本 | Tag/Commit | 说明 |
|-----------|------------|------|
| ✅ v0.10.0+ | `v2026.4.x` 或 `main` | 支持（用户验证） |
| ✅ v0.8.0 | `v2026.4.8` 或 `main` | 最新支持版本 |
| ✅ v0.7.0 | `v2026.4.3` | 支持 |
| ✅ v0.6.0 | `v2026.3.30` | 支持 |
| ✅ v0.5.0 | `v2026.3.28` | 支持 |
| ✅ v0.4.0 | `v2026.3.23` | 支持 |
| ❌ v0.3.x 及更早 | `v2026.3.17` 之前 | **不支持**（函数名已改变） |

> **💡 怎么判断支不支持？** 运行以下命令，如果输出包含 `_msg_start_time`，则支持：
> ```bash
> grep -n "_msg_start_time.*time.time" ~/.hermes/hermes-agent/gateway/run.py
> ```

### 如何确认你的 Hermes 版本？

```bash
cd ~/.hermes/hermes-agent && git log --oneline -1
```

或查看 tag：

```bash
cd ~/.hermes/hermes-agent && git describe --tags
```

### 如果版本不对怎么办？

**降级到 v2026.3.30（推荐）：**

```bash
cd ~/.hermes/hermes-agent

# 方法1：使用 tag
git checkout v2026.3.30

# 方法2：使用版本号
git checkout v0.6.0
```

**升级到最新 main：**

```bash
cd ~/.hermes/hermes-agent
git checkout main
git pull
```

⚠️ 切换版本后需要重新运行安装脚本：

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --mode sidecar
```

---


---

## 🚀 部署步骤

### 第一步：克隆项目

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git ~/github/hermes-feishu-streaming-card
cd ~/github/hermes-feishu-streaming-card
```

### 第二步：安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 第三步：安装并认证 lark-cli

```bash
# 安装
npm install -g @larksuite/oapi-cli

# 认证（交互式）
lark-cli auth login
```

认证需要：
- **App ID**: 飞书开放平台 → 你的应用 → 凭证与基础信息
- **App Secret**: 同上位置

### 第四步：配置飞书 Bot

在 [飞书开放平台](https://open.feishu.cn/) 完成：

1. **开通机器人能力**：添加应用能力 → 选机器人
2. **申请权限**：
   - `im:message` — 发送卡片消息
   - `im:message:send_as_bot` — 以机器人身份发消息
   - `cardkit:card` — 创建和更新卡片
3. **开启长连接**：消息订阅 → 订阅方式 → 长连接（WebSocket）
4. **启用 CardKit**：添加应用能力 → 搜索 CardKit → 开启

> ⚠️ 权限申请后需等待审核通过（通常几分钟~几小时）

### 第五步：一键安装 Sidecar

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --mode sidecar
```

安装流程：
1. 检查端口冲突（8765 端口被占用时自动选用其他端口）
2. 检查环境依赖
3. 备份现有配置
4. 安装 sidecar 到 `~/.hermes/feishu-sidecar/`
5. 修改 gateway 事件转发（约 50 行）
6. 启动 sidecar 服务
7. 验证运行状态

### 第六步：配置 Gateway

在 `~/.hermes/hermes-agent/config.yaml` 中添加：

```yaml
feishu_streaming_card:
  enabled: true
  mode: "sidecar"
  sidecar:
    host: "localhost"
    port: 8765
  greeting: "主人，苏菲为您服务！"
```

### 第七步：重启 Hermes Gateway

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway restart
```

---

## 🔧 配置说明

### Gateway 配置（config.yaml）

```yaml
feishu_streaming_card:
  enabled: true              # 是否启用流式卡片
  mode: "sidecar"           # 固定为 sidecar 模式
  sidecar:
    host: "localhost"       # Sidecar 地址
    port: 8765              # Sidecar 端口
  greeting: "你的问候语！"   # 卡片标题
```

### Sidecar 配置

配置文件位于：`~/.hermes/feishu-sidecar.yaml`

一般无需修改，使用默认配置即可。

---

## 📊 卡片 Footer 格式（v2.3）

```
minimax-M2.7  ⏱️ 30s  81.1k↑  1.2k↓ ctx 82k/204k
```

| 字段 | 说明 |
|------|------|
| `minimax-M2.7` | 当前使用模型 |
| `30s` | 处理耗时 |
| `81.1k↑` | 输入 Token（k/m 缩略） |
| `1.2k↓` | 输出 Token |
| `ctx 82k/204k` | 上下文当前值/窗口大小（注：数据仅供参考，非真实上下文占用） |

---

## 🔍 管理命令

```bash
# 查看 sidecar 状态
curl http://localhost:8765/health

# 查看 sidecar 日志
tail -f ~/.hermes/logs/sidecar.log

# 重启 sidecar
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill
sleep 1
cd ~/github/hermes-feishu-streaming-card/sidecar && \
  PYTHONPATH=~/github/hermes-feishu-streaming-card \
  python -m sidecar.server > ~/.hermes/logs/sidecar.log 2>&1 &

# 查看 gateway 日志
tail -f ~/.hermes/logs/hermes-gateway.log
```

---

## 🐛 故障排查

### 卡片不更新/卡住

1. 检查 sidecar 状态：
   ```bash
   curl http://localhost:8765/health
   ```
2. 如果 `active_cards` 不为 0，重启 sidecar
3. 检查日志：
   ```bash
   tail ~/.hermes/logs/sidecar.log
   ```

### "card table number over limit" 错误

这是飞书 CardKit 的卡片数量限制，通常是因为之前卡片未正常结束导致累积。重启 sidecar 即可恢复：

```bash
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill
sleep 1
cd ~/github/hermes-feishu-streaming-card/sidecar && \
  PYTHONPATH=~/github/hermes-feishu-streaming-card \
  python -m sidecar.server > ~/.hermes/logs/sidecar.log 2>&1 &
```

### Token 认证过期

```bash
lark-cli auth login
```

### Gateway 启动失败

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway start 2>&1
```

查看具体报错信息。

### Patch 失败（Hermes 版本不兼容）

如果安装时报错 `Could not find _msg_start_time` 或 `Could not find _handle_message_with_agent`：

**原因**：你安装的 Hermes 版本可能太新（v0.7+）或太旧（< v0.4.0），代码结构与插件不兼容。

**解决方案**：
1. 检查 Hermes 版本：
   ```bash
   cd ~/.hermes/hermes-agent && git log --oneline -1
   ```
2. 如果版本过新，安装兼容版本：
   ```bash
   cd ~/.hermes/hermes-agent
   git checkout v2026.3.30  # 或 v0.6.0
   ```
3. 重新安装插件

---


## 🗑️ 卸载与恢复

### 方式一：使用安装脚本自动卸载

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --uninstall
```

安装脚本会自动：
1. 从备份恢复 Gateway 原始文件
2. 删除 sidecar 配置文件
3. 删除复制的适配器文件

### 方式二：手动恢复

如果安装脚本不工作，手动恢复步骤：

**1. 停止 Sidecar**
```bash
# 查找并停止 sidecar 进程
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null

# 确认已停止
curl http://localhost:8765/health
```

**2. 恢复 Gateway 原始文件**

安装脚本会在 `~/.hermes/.fsc_backups/` 目录保存备份：
```bash
# 查看可用备份
ls -la ~/.hermes/.fsc_backups/

# 恢复最新的备份
cp ~/.hermes/.fsc_backups/backup_YYYYMMDD_HHMMSS/* ~/.hermes/hermes-agent/gateway/
```

**3. 删除配置文件**
```bash
rm ~/.hermes/feishu-sidecar.yaml
```

**4. 重启 Gateway**
```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway restart
```

### 验证卸载成功

```bash
# 1. Sidecar 应该无响应
curl http://localhost:8765/health
# 预期：Connection refused

# 2. Gateway 正常启动
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway start
# 预期：无报错，正常启动

# 3. 测试 Bot
给 Bot 发一条消息，应该以普通文本方式回复（不再是卡片）
```

---

## 📝 更新日志

### v2.4 (2026-04-19)
- ✅ **修复工具调用追踪**：`_flush_updates` 使用最新的 `tool_count/tool_lines`，解决过程中工具调用不显示的问题
- ✅ **支持配置 greeting**：在 `feishu_streaming_card.greeting` 中配置卡片标题
- ✅ **端口冲突检测**：安装时自动检测端口是否被占用，冲突时自动选用其他端口
- ✅ **Patch 语法验证**：修复 `gateway_run_patch.py` 的字符串括号处理 bug，防止语法错误

### v2.3 (2026-04-19)
- ✅ **Footer 显示优化**：模型名称 + 时间 + Token（k/m缩略）+ 上下文百分比
- ✅ **Footer 字号**：x-small
- ✅ **刷新频率优化**：2秒 或 300字符 或完整句子
- ✅ **Flush 超时保护**：5秒超时，失败不阻塞 finalize

### v2.2 (2026-04-19)
- ✅ **修复最终状态丢失**：flush 失败不阻塞 finalize
- ✅ **11310 错误处理**：不再无限重试

### v2.1 (2026-04-17)
- ✅ **Sidecar 架构**：独立进程，对 Hermes 无侵入
- ✅ **安装脚本**：支持 sidecar/legacy/dual 三种模式

### v2.0 (2026-04-16)
- ✅ **安全安装**：语法校验 + 注入点验证 + 自动备份
- ✅ **版本感知**：支持 Hermes Gateway v0.10.0 及以上版本
- ✅ **并发保护**：per-chat asyncio.Lock

### v1.0 (2026-04-15)
- 🎉 **首发版本**：流式打字机卡片、工具调用追踪

---

## 🗂️ 项目结构

```
hermes-feishu-streaming-card/
├── README.md                    # 本文件
├── installer_v2.py              # v2.x 安装脚本（推荐）
├── installer_sidecar.py         # Sidecar 专用安装脚本
├── requirements.txt             # Python 依赖
├── config.yaml.example          # 配置示例
├── sidecar/                    # Sidecar 核心代码
│   ├── server.py               # HTTP 服务入口
│   ├── card_manager.py         # 卡片状态管理
│   ├── cardkit_client.py       # CardKit API 封装
│   └── config.py               # 配置加载
├── adapter/                    # 适配器模式
├── scripts/                    # 工具脚本
└── tests/                      # 测试用例
```

---

## ⚠️ 使用风险声明

1. **Gateway 修改风险**：本工具会修改 Hermes Gateway 的 `feishu_forward.py` 文件，添加事件转发逻辑。虽然做了备份和版本控制，但任何第三方修改都存在风险。

2. **飞书 API 限制**：CardKit API 有频率限制（`card table number over limit`），大文本输出时可能触发限制。

3. **Token 过期**：lark-cli 的 tenant_access_token 有效期 2 小时，重启机器后需重新认证。

4. **版本兼容性**：Hermes Gateway 更新后可能需要重新适配安装。

**建议**：
- 在非生产环境先测试
- 定期备份配置文件
- 关注 [GitHub Issues](https://github.com/baileyh8/hermes-feishu-streaming-card/issues) 获取更新

---

## 📚 相关链接

- [Hermes Agent](https://github.com/joeynyc/hermes-agent)
- [飞书 CardKit 文档](https://open.feishu.cn/document/ukTMukTMukTM/uEDOwedzUjL24CN04iN0kNj0)
- [lark-cli](https://github.com/larksuite/oapi-cli)

---

**需要帮助？** 提交 [Issue](https://github.com/baileyh8/hermes-feishu-streaming-card/issues)
