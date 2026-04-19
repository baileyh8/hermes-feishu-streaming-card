# Feishu Streaming Card for Hermes v2.3

为 [Hermes Gateway](https://github.com/joeynyc/hermes-agent) 添加飞书流式卡片消息支持。发消息给机器人后，卡片以打字机效果实时展示 AI 思考过程、工具调用和任务结果。

> English version: [README_en.md](README_en.md)

---

## ✨ 效果预览

| 思考中 | 已完成 |
|---|---|
| ![Thinking](thinking.png) | ![Ending](ending.png) |

**思考中** — 打字机效果实时展示 AI 推理过程，工具调用追踪中
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

| 对比 | Legacy (v2.0) | Sidecar (v2.1+) |
|------|---------------|------------------|
| Gateway 侵入性 | 高（直接修改源码） | 极低（仅事件转发） |
| 进程隔离 | 否 | ✅ 是 |
| 升级影响 | 需重注入 | 不影响 |
| 回滚难度 | 中 | 极易 |

---

## 📋 环境要求

- **Python**: 3.9+
- **Hermes**: 已安装并配置好（支持 WS 长连接模式）
- **飞书 Bot**: 已开通机器人能力 + CardKit 应用
- **Node.js**: 18+（用于 lark-cli）
- **lark-cli**: `@larksuite/oapi-cli`（用于获取 tenant token）

---

## 🚀 快速部署

### 1. 克隆项目

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git ~/github/hermes-feishu-streaming-card
cd ~/github/hermes-feishu-streaming-card
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
npm install -g @larksuite/oapi-cli
```

### 3. 配置飞书 Bot

参考 [飞书配置指南](#飞书配置指南)，完成：
- 开通 Bot 能力
- 申请权限（`im:message`, `cardkit:card` 等）
- 开启 WebSocket 长连接模式
- 认证 lark-cli

### 4. 一键安装 Sidecar 模式

```bash
cd ~/github/hermes-feishu-streaming-card
python installer_v2.py --mode sidecar
```

安装流程：
1. 检查环境依赖
2. 备份现有配置
3. 安装 sidecar 到 `~/.hermes/feishu-sidecar/`
4. 修改 gateway 事件转发（<50 行）
5. 启动 sidecar 服务
6. 验证运行状态

### 5. 启动/重启 Gateway

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
  enabled: true
  mode: "sidecar"  # "sidecar" | "legacy"
  sidecar:
    host: "localhost"
    port: 8765
  greeting: "主人，苏菲为您服务！"
```

### Sidecar 配置（自动生成）

配置文件位于：`~/.hermes/feishu-sidecar.yaml`

一般无需修改，使用默认配置即可。

---

## 📊 卡片 Footer 格式（v2.3）

```
minimax-M2.7  ⏱️ 30s  81.1k↑  1.2k↓ ctx 82k/204k 40%
```

- **模型名称**：当前使用模型
- **时间**：处理耗时（秒）
- **Token 输入**：k/m 缩略格式
- **Token 输出**：k/m 缩略格式
- **上下文占用**：当前 / 窗口大小 百分比

---

## 🔍 管理命令

```bash
# 查看 sidecar 状态
curl http://localhost:8765/health

# 查看 sidecar 日志
tail -f ~/.hermes/logs/sidecar.log

# 重启 sidecar
ps aux | grep sidecar | grep -v grep | awk '{print $2}' | xargs kill
cd ~/github/hermes-feishu-streaming-card/sidecar && \
  PYTHONPATH=~/github/hermes-feishu-streaming-card \
  python -m sidecar.server > ~/.hermes/logs/sidecar.log 2>&1 &
```

---

## 🐛 故障排查

### 卡片不更新/卡住

1. 检查 sidecar 状态：`curl http://localhost:8765/health`
2. 重启 sidecar（如上述）
3. 检查日志：`tail ~/.hermes/logs/sidecar.log`

### "card table number over limit" 错误

这是飞书 CardKit 的卡片数量限制，通常是因为之前卡片未正常结束导致累积。重启 sidecar 即可恢复。

### Token 认证过期

```bash
lark-cli auth login
```

---

## 📝 更新日志

### v2.3 (2026-04-19)
- ✅ **Footer 显示优化**：模型名称 + 时间 + Token（k/m缩略）+ 上下文百分比
- ✅ **Footer 字号**：x-small
- ✅ **刷新频率优化**：2秒 或 300字符 或完整句子
- ✅ **Flush 超时保护**：5秒超时，失败不阻塞 finalize

### v2.2 (2026-04-19)
- ✅ **修复最终状态丢失**：flush 失败不阻塞 finalize
- ✅ **11310 错误处理**：不再无限重试
- ✅ **简化卡片结构**：移除重复的 status_label

### v2.1 (2026-04-17)
- ✅ **Sidecar 架构**：独立进程，对 Hermes 无侵入
- ✅ **安装脚本**：支持 sidecar/legacy/dual 三种模式
- ✅ **健康检查**：独立 metrics 端点

### v2.0 (2026-04-16)
- ✅ **安全安装**：语法校验 + 注入点验证 + 自动备份
- ✅ **版本感知**：自动识别 Hermes 不同版本
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

## 📚 相关链接

- [Hermes Agent](https://github.com/joeynyc/hermes-agent)
- [飞书 CardKit 文档](https://open.feishu.cn/document/ukTMukTMukTM/uEDOwedzUjL24CN04iN0kNj0)
- [lark-cli](https://github.com/larksuite/oapi-cli)

---

## 飞书配置指南

### 1. 开通 Bot 能力

[飞书开放平台](https://open.feishu.cn/) → 你的应用 → **添加应用能力** → 选 **机器人**

### 2. 配置权限

**订阅消息 → 权限管理**，申请以下权限：

| 权限 | 用途 |
|---|---|
| `im:message` | 发送卡片消息 |
| `im:message:send_as_bot` | 以机器人身份发消息 |
| `cardkit:card` | 创建和更新卡片 |
| `tenant_access_token` | 调用 APIs 获取 token |

> 权限申请后需等待审核通过（通常几分钟~几小时）

### 3. 开启长连接模式

→ 应用 → **消息订阅** → 订阅方式 → 选 **长连接（WebSocket）**

### 4. 启用 CardKit

→ 应用 → **添加应用能力** → 搜索 **CardKit** → 开启

### 5. 安装并认证 lark-cli

```bash
# 安装
npm install -g @larksuite/oapi-cli

# 认证
lark-cli auth login
```

认证信息：
- **App ID**: 飞书开放平台 → 你的应用 → **凭证与基础信息**
- **App Secret**: 同上位置

---

**需要帮助？** 提交 [Issue](https://github.com/baileyh8/hermes-feishu-streaming-card/issues)。
