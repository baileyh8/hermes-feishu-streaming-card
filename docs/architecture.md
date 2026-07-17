# 架构说明

[中文](architecture.md) | [English](architecture.en.md)

当前主线采用 sidecar-only 架构：Hermes Agent 内只保留最小 hook，把消息生命周期事件转发到 HTTP sidecar；飞书/Lark 卡片创建、更新、终态渲染、会话累积、诊断和安全恢复都在 `hermes_feishu_card/` 内完成。V4 已完成真实飞书私聊、群聊、topic、WebSocket card action 和长空闲连接 smoke；自动化测试不能替代发布前的真实飞书验收。

```text
Hermes Gateway
  -> marker-wrapped minimal hook (gateway/run.py)
  -> hermes_feishu_card.hook_runtime
  -> authenticated/fail-open HTTP POST /events
  -> hermes_feishu_card.server
  -> session + render + Feishu CardKit send/update
```

Hermes hook 到 sidecar `/events` 的 fail-open 转发链路已经落地：sidecar 不可用或拒绝事件时，hook 不拖垮 Hermes，未被卡片路径确认接管的消息继续遵循 Hermes 原生 fallback。卡片已经接受的路径则抑制重复灰色原生文本。

## 组件

### 最小 Hermes hook

安装器只通过 `hermes_feishu_card.install.patcher` 修改 Hermes 的 `gateway/run.py`，插入可识别、可移除、可恢复的 marker block。复杂事件抽取、delta 合并、命令卡和 Feishu adapter 兼容逻辑位于 `hermes_feishu_card.hook_runtime`；hook 不保存飞书凭据，也不重写 Hermes 的会话 ownership、resume 或群聊准入规则。

### HTTP sidecar

`hermes_feishu_card.server` 接收事件，按 profile、bot、message/reply anchor 管理 `CardSession`，把高频 delta 合并成有限 PATCH，并在 terminal 前排空待发送内容。`hermes_feishu_card.cli start/status/stop` 管理本机进程；停止时同时校验 pidfile PID/token 和 `/health` 的 `process_pid/process_token_hash`，避免 PID 复用误杀。独立进程和 systemd user service 生命周期主要面向 macOS/Linux 等 POSIX 环境。

`/health` 只暴露脱敏、hash 化和 process-local 的状态，包括事件、事件鉴权拒绝、卡片发送/更新、cleanup 和路由指标。`send_card` 不盲目重试，避免重复创建卡片；已有 message id 的更新采用有限重试。

### 会话与渲染

`hermes_feishu_card.session` 保存单进程内的流式会话状态；`render` 根据 thinking、answer、tool preview、notice、interaction 和 terminal 状态生成 CardKit JSON。状态是有界清理的暂态数据，sidecar 重启不承诺恢复正在进行的卡片；Hermes 仍是主流程事实来源。

### Feishu client

`hermes_feishu_card.feishu_client` 已实现 tenant token 获取、interactive card 创建和消息更新。凭据来自本机配置或环境变量，不进入仓库、卡片、`/health` 或日志。真实飞书验证记录在 release notes 和 `docs/wiki/feishu-acceptance.md`。

## 事件传输安全边界

默认 `server.host: 127.0.0.1` 使用**本机进程互信**：为了兼容旧安装，loopback `/events` 可以接收未签名事件；hook 在私有 state directory 的 transport root 可用时仍会发送事件鉴权 proof。

非 loopback listener 默认拒绝启动。只有显式设置 `server.allow_non_loopback: true` 才允许绑定，并且 sidecar 会强制校验基于私有 transport root、请求原始 body、时间戳和 nonce 的 HMAC 事件鉴权 proof；缺失、错误、过期或重放 proof 都在事件解析和发卡前拒绝。root secret 使用独立事件域分隔，不写进 YAML、env、卡片、日志或健康检查。

事件鉴权只证明请求来源和完整性，不为 HTTP 内容加密。非 loopback 只适合共享同一私有 state directory 的受信容器/私有网络；不要把 sidecar 直接暴露到公网。公网部署需要额外的 TLS/mTLS 或受控反向代理边界。

| 端点 | 默认边界 |
|---|---|
| `POST /events` | loopback 本机互信；显式非 loopback 强制事件鉴权 |
| `POST /commands` | state-dir command transport proof |
| `POST /card/actions` | interaction token 或 operations transport proof |
| `GET /health` | 无鉴权但严格脱敏；仅供本机探活 |
| `GET /messages/{id}/summary`, `/interactions/{id}` | 本机 hook 协作索引，不应网络暴露 |

## 旧代码边界

`legacy/adapter/`、`legacy/sidecar/`、`legacy/patch/` 及 `legacy/` 下的旧安装/patch 脚本是历史 legacy/dual 实现，不是 active runtime。当前维护只以 `hermes_feishu_card/`、当前 CLI、安装器安全模型和 `docs/wiki/` 为准。
