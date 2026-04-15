# Feishu Streaming Card for Hermes

为 [Hermes Gateway](https://github.com/joeynyc/hermes-agent) 添加飞书流式卡片消息支持。发消息给机器人后，卡片以打字机效果实时展示 AI 思考过程、工具调用和任务结果。

> English version: [README_en.md](README_en.md)

---

## 🤔 你是否也遇到过这些问题？

| 痛点 | 描述 |
|---|---|
| 🔴 **消息刷屏** | Agent 思考时飞书里蹦出一堆零散消息，夹杂思考碎片和工具日志，聊天记录被淹没 |
| 🔴 **焦虑等待** | 发出消息后界面毫无反应，不知道 Agent 是死是活、要等多久 |
| 🔴 **格式丢失** | Markdown 发到飞书变成纯文本，代码块、表格、列表全部乱成一团 |
| 🔴 **调试困难** | 工具调用状态不透明，不知道 Agent 在用哪个工具、处理到哪一步 |

## ✨ 一张卡片，全部解决

**Feishu Streaming Card** 将飞书消息升级为实时协作面板：

- 📌 **消息不刷屏** — 思考过程在同一张卡片内逐字更新，聊天记录干干净净
- ⏳ **状态一目了然** — 思考中 ⚡ → 执行中 → ✅已完成，每个阶段清晰可见
- 🎨 **原生格式保留** — Markdown 表格、代码块、列表直接渲染，不丢失样式
- 🔍 **工具透明** — 实时展示工具调用次数和内容，掌握 Agent 工作全貌
- 📊 **统计自动生成** — 用时、Token 消耗、上下文占用，底部 footer 一眼看清

| 特性 | 说明 |
|---|---|
| 🎯 卡片预创建 | 收到消息立即创建卡片，无需等待模型响应 |
| ⌨️ 打字机效果 | AI 思考过程逐字显示在卡片内 |
| 🔧 工具调用追踪 | 实时展示工具调用次数和内容 |
| 📊 Token 统计 | 底部显示用时、输入/输出 token、上下文占用 |
| 🔒 Sequence 并发保护 | asyncio.Lock 防止多协程更新卡片时的 sequence 冲突 |
| ⚙️ 配置简单 | 一行命令安装，配置文件控制所有参数 |

---

## 效果预览

| 思考中 | 已完成 |
|---|---|
| ![Thinking](thinking.png) | ![Ending](ending.png) |

**思考中** — 打字机效果实时展示 AI 推理过程，工具调用追踪中
**已完成** — 状态切换为 ✅，展示结果摘要和完整 token 统计

---

## 环境要求

- Python 3.9+
- [hermes-agent](https://github.com/joeynyc/hermes-agent) 已安装
- 飞书 Bot 已配置好（WS 长连接模式）
- 依赖包：`pyyaml`、`regex`

---

## 安装

### 一步安装

```bash
cd ~/github/hermes-feishu-streaming-card
pip install -r requirements.txt
python installer.py --greeting "你的自定义问候语"
```

`--hermes-dir` 可指定 hermes-agent 路径（默认：`~/.hermes/hermes-agent`）。

### 手动安装（不自动检测）

```bash
python installer.py \
  --hermes-dir /path/to/hermes-agent \
  --greeting "主人，苏菲为您服务！" \
  --pending-timeout 30
```

### 安装后检查

```bash
python installer.py --check
```

---

## 配置

安装后，在 `~/.hermes/hermes-agent/config.yaml` 中添加或修改：

```yaml
feishu_streaming_card:
  # 卡片标题 — 机器人名字和问候语
  greeting: "主人，苏菲为您服务！"

  # 是否启用流式卡片（false = 使用普通消息）
  enabled: true

  # 等待卡片创建的超时时间（秒）
  # 模型初始化较慢时建议调大
  pending_timeout: 30
```

---

## 重启 Hermes

```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
python -m hermes_cli.main gateway restart
```

---

## 卸载

```bash
python installer.py --uninstall
# 然后重启 hermes
python -m hermes_cli.main gateway restart
```

---

## 工作原理

```
用户发消息
  ↓
run.py: 立即调用 send_streaming_card() 创建卡片
  ├─ header: greeting + model
  ├─ thinking_content: "⏳ 执行中..."
  ├─ status_label: "🤔思考中"
  ├─ tools_label: "🔧 工具调用 (0次)"
  ├─ tools_body: "⏳ 等待开始..."
  └─ footer: "⏳ 执行中..."
  ↓
流式文本到达 → edit_message → send()
  └─ 写入 thinking_content（覆盖模式，打字机效果）
  ↓
工具调用到达 → send()
  ├─ status_label: "⚡执行中"
  ├─ tools_label: "🔧 工具调用 (N次)"
  └─ tools_body: 工具日志
  ↓
Agent 完成 → finalize_streaming_card()
  ├─ status_label: "✅已完成"
  ├─ thinking_content: result_summary
  ├─ tools_label: "🔧 工具调用 (N次)  ✅完成"
  └─ footer: token 统计
```

---

## 已知限制

1. **卡片创建延迟**：模型初始化约需 10-30s，此期间卡片显示初始状态
2. **CardKit PUT 限制**：仅支持根级 `markdown`/`plain_text`/`lark_md` 元素更新，不支持嵌套结构
3. **图片/视频附件**：图片等 MEDIA 附件会作为普通消息发出，不在卡片内

---

## 文件结构

```
hermes-feishu-streaming-card/
├── README.md                    # 本文件（中文）
├── README_en.md                 # English version
├── installer.py                 # 一键安装脚本
├── requirements.txt             # Python 依赖
├── config.yaml.example          # 配置示例
└── patch/
    ├── __init__.py
    ├── feishu_patch.py          # feishu.py 补丁
    └── run_patch.py             # run.py 补丁
```

---

## 故障排查

**卡片没有出现？**
```bash
# 检查补丁状态
python installer.py --check

# 查看日志
tail -f ~/.hermes/logs/agent.log | grep -i "feishu\|streaming\|card"
```

**Sequence 冲突错误（300317）？**
确保 hermes-agent 是最新版本，旧版本可能缺少必要的锁机制。

**卡片标题/状态不更新？**
检查飞书 Bot 是否使用 WebSocket 长连接模式（WS 模式才支持 CardKit 更新）。

---

## License

MIT
