# Hermes Feishu Streaming Card - V3.2.1 交付总结

> 生成时间：2026-04-29
> 分支：`main`（已合并）
> 最新 tag：`v3.2.1`
> 状态：✅ 生产就绪

---

## 📦 版本历史

| 版本 | 日期 | 类型 | 说明 | Tag |
|------|------|------|------|-----|
| V3.2.1 | 2026-04-29 | Patch | 修复 brotli 解码错误（Accept-Encoding header） | ✅ `v3.2.1` |
| V3.2.0 | 2026-04-29 | Feature | 多 bot 路由、群聊绑定、CLI 管理、routing diagnostics | ✅ `v3.2.0` |
| V3.1.0 | 2026-04-XX | Feature | sidecar-only 架构首次发布 | ✅ `v3.1.0` |

---

## ✅ 已完成功能（V3.2.1 包含 V3.2.0 全部功能）

V3.2.1 在 V3.2.0 基础上修复了 brotli 解码问题，所有 V3.2.0 功能保持兼容。

### 1. 多 Bot 注册表
- `bots.yaml` 配置支持多个 bot 定义
- 每个 bot 独立 `app_id`/`app_secret`
- 默认 bot fallback 机制
- 文件：`hermes_feishu_card/bots.py`

### 2. 群聊绑定路由
- `bindings.chats`：`chat_id → bot_id` 映射
- `bindings.fallback_bot`：未绑定会话默认 bot
- 路由逻辑：`BotRegistry.resolve(RoutingContext)`
- 文件：`hermes_feishu_card/server.py`, `hermes_feishu_card/runner.py`

### 3. Bot 管理 CLI
- `hermes_feishu_card.cli bots list` — 列出所有 bots
- `hermes_feishu_card.cli bots show <id>` — 查看详情
- `hermes_feishu_card.cli bots add/remove` — 增删 bot
- `hermes_feishu_card.cli bots bind-chat/unbind-chat` — 绑定/解绑群聊
- 文件：`hermes_feishu_card/cli.py`

### 4. Sidecar 路由诊断
- `/health.routing` 返回：
  - `bot_count`、`chat_binding_count`
  - `last_route`（最近路由的 `chat_id`/`message_id`/`bot_id`/`reason`）
  - `bots[]` 列表（`bot_id`/`name`/`app_id`，**secret 已脱敏**）
- 文件：`hermes_feishu_card/server.py`

### 5. 路由上下文透传
- `hook_runtime._event_data()` 提取：
  - `chat_type`、`tenant_key`、`agent_id`、`profile_id`
- 当前版本未使用，为 V3.3 预留
- 文件：`hermes_feishu_card/hook_runtime.py`

### 6. 配置与环境
- `config.py`：新增 `bots`、`bindings`、`group_rules` schema 与默认值
- `config.yaml.example`：完整 V3.2 配置示例（双 bot）
- `cli.py`：`_default_setup_config_text()` 生成含 V3.2 字段的模板
- 环境变量：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`（多 bot 建议写在 config）

### 7. 文档全覆盖
- `README.md` / `README.en.md`：V3.2 独立章节（配置步骤、完整示例、FAQ、路由逻辑）
- `CHANGELOG.md`：V3.2.0 详细变更（Added/Changed/Fixed/Docs）
- `docs/testing.md` / `docs/e2e-verification.md`：测试数更新为 **398**
- `docs/architecture.md`、`event-protocol.md`、`migration.md` 等同步更新

---

## 🐛 热修复（V3.2.1）

### PR #12：Accept-Encoding 头避免 brotli 解码错误
- **问题**：飞书 API 返回 brotli (`br`) 编码，aiohttp 无法解码，导致 `ClientPayloadError: Can not decode content-encoding: br`
- **修复**：在 `feishu_client.py` 的请求头添加 `Accept-Encoding: gzip, deflate`
- **影响文件**：`hermes_feishu_card/feishu_client.py`（1 行）
- **验证**：8 个不同 bot 实测通过
- **Commit**：`efdf3e9`
- **Tag**：`v3.2.1`

---

## 📊 测试覆盖

| 测试类型 | 命令 | 结果 |
|---------|------|------|
| 全量回归 | `pytest -q` | **398 passed** |
| 单元测试 | `pytest tests/unit -q` | ✅ |
| 集成测试 | `pytest tests/integration -q` | ✅ |
| 文档测试 | `pytest tests/unit/test_docs.py -q` | ✅ |
| Feishu HTTP 客户端 | `pytest tests/unit/test_feishu_client.py tests/integration/test_feishu_client_http.py -q` | ✅ |
| Hermes hook runtime | `pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -q` | ✅ |
| Sidecar 进程 | `pytest tests/integration/test_cli_process.py -q` | ✅ |

**V3.2 专项测试**：
- `tests/unit/test_bots.py` — BotRegistry、路由、配置验证、secret 脱敏
- `tests/unit/test_config.py` — 多 bot 配置加载、legacy 兼容
- `tests/integration/test_server.py` — 路由生命周期（send/update 同一 bot）
- `tests/integration/test_cli.py` — bots CLI 命令
- `tests/unit/test_hook_runtime.py` — 可选 routing context 提取

---

## 🔗 相关 Issue / PR

| 编号 | 类型 | 标题 | 状态 |
|------|------|------|------|
| #10 | Issue | 卡片里表格过多会报错（11310 table limit） | 🔴 Open（已补充 FAQ） |
| #11 | PR | feat: V3.2 multi-bot group chat support | ✅ Merged |
| #12 | PR | fix: add Accept-Encoding header to avoid brotli decoding error | ✅ Merged |

---

## 📁 关键文件变更速览

### 新增文件
```
hermes_feishu_card/bots.py                # BotRegistry + FeishuClientFactory
tests/unit/test_bots.py                  # 单元测试
CHANGELOG.md                             # 版本日志
```

### 核心修改
```
hermes_feishu_card/config.py              # + bots/bindings 默认 schema
hermes_feishu_card/server.py              # + bot_router 参数 + 路由诊断
hermes_feishu_card/runner.py              # + build_feishu_boundary()
hermes_feishu_card/cli.py                 # + bots 命令组
hermes_feishu_card/hook_runtime.py        # + 可选 routing context 提取
hermes_feishu_card/feishu_client.py       # + Accept-Encoding header (V3.2.1)
```

### 文档更新
```
README.md                                # V3.2 章节 + FAQ（表格限制）
README.en.md                             # 英文版同步
config.yaml.example                      # 完整多 bot 示例
docs/testing.md                          # 测试数改为 398
docs/e2e-verification.md                 # 验收状态 398
```

---

## 🚀 部署检查清单

### 安装前
- [ ] Hermes Agent 版本 ≥ `v2026.4.23`
- [ ] 飞书应用已创建，具备 `send_message` / `update_message` 权限
- [ ] 如使用多 bot：所有 bot 的 `app_id`/`app_secret` 已准备

### 安装
```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git
cd hermes-feishu-streaming-card
python3 -m pip install -e ".[test]"

# 单 bot：通过环境变量
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx

# 多 bot：编辑配置文件
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --yes
```

### 验证
```bash
# 健康检查
curl http://127.0.0.1:8765/health | jq '.routing'

# 列出 bots
python3 -m hermes_feishu_card.cli bots list --config ~/.hermes_feishu_card/config.yaml

# 诊断
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml
```

### 群聊绑定（V3.2）
```bash
# 绑定群聊到 bot
python3 -m hermes_feishu_card.cli bots bind-chat oc_xxxxxx sales --config ~/.hermes_feishu_card/config.yaml

# 验证路由
curl http://127.0.0.1:8765/health | jq '.routing.last_route'
```

---

## ⚠️ 已知限制与待办

| 事项 | 说明 | 优先级 |
|------|------|--------|
| **Issue #10** | 卡片表格数量超限（最多 5 个）目前仅 FAQ，未代码截断 | P2（V3.2.1 可能） |
| **Group Rules** | `bindings.group_rules.enabled` 当前为 `false`（预留字段） | P3（V3.3） |
| **多语言表格** | CardKit 多语言下每语言独立计数 5 表，未特殊处理 | P3 |
| **自动拆分** | 超限表格可考虑自动拆分为多条消息（需设计） | P3 |

---

## 📝 版本升级建议

- **V3.1.0 → V3.2.0**：单 bot 用户无需改动；多 bot 用户需配置 `bots`/`bindings`
- **V3.2.0 → V3.2.1**：纯 bugfix，直接升级（tag 已发布）

---

## 📞 支持与反馈

- 仓库：https://github.com/baileyh8/hermes-feishu-streaming-card
- Issues：https://github.com/baileyh8/hermes-feishu-streaming-card/issues
- 文档：`docs/` 目录与 `README.md`

---

**报告结束** —— 所有 V3.2 功能已就绪，V3.2.1 热修复已发布，生产环境可安全使用。
