# 架构说明

[中文](architecture.md) | [English](architecture.en.md)

当前主线采用 sidecar-only 架构：Hermes Agent 通过原生 plugin 与必要的精确兼容 hook，把消息生命周期事件转发到 HTTP sidecar；飞书/Lark 卡片创建、更新、终态渲染、会话累积、诊断和安全恢复都在 `hermes_feishu_card/` 内完成。执行与授权由 Hermes 负责，展示与投递由 HFC 负责。历史 V4 版本记录过真实飞书私聊、群聊、topic、WebSocket card action 和长空闲连接 smoke；这些记录不等于当前版本在所有客户端重新验收通过，自动化测试也不能替代发布前的真实飞书验收。

```text
Hermes Gateway
  -> verified native plugin lifecycle hooks (hermes_plugin_runtime)
  +  exact compatibility/final-delivery hooks (hook_runtime)
     [capability-selected producers; one owner per event/delivery]
     -> signed POST /delivery/policy (before native suppression)
     -> authenticated/fail-open POST /events
     -> signed POST /runtime/events (hello/heartbeat)
  -> hermes_feishu_card.server
  -> policy + readiness + session + render + Feishu CardKit send/update
```

V4.3 在固定 Hermes `v2026.8.3` 上建立 Hybrid 基线：真实 `hermes_agent.plugins` lifecycle hooks 通过 signed loopback transport 驱动 sidecar，17 个精确 patch group 补齐该基线 hook call-site 未提供的 ingress、delta、interaction、terminal、cron 与 exact Base delivery 证据；V3 manifest 把 7 个 target、官方 plugin config preimage 和 venv identity 纳入同一 ownership transaction。当前兼容策略会随 Hermes 源码布局选择入口，不能把这组历史数量套用到所有版本。能力选择继续验证源码 hashes/slices、runtime Python、entrypoint origin 与真实 PluginManager subprocess；最新固定兼容基线见 [Hermes 源码迁移](wiki/hermes-decomposed-patcher.md)及 CI。

approval/clarify/slash callback 使用独立的 runtime interaction listener，直接调用 Hermes 原 pending handle/future 的 resolver，不创建第二套 wait/poll/queue。Sidecar 的 event-id fence 保留首次 canonical response；Feishu create/PATCH 与 listener POST 都在 session/message lock 外发生。只有 card terminal success 可以抑制原生成功正文，failed/interrupted 保持 native fail-open。

V4.1 使用域分隔的事件数据面与四条控制动作：`hfc-policy-v1` per-chat policy、`hfc-runtime-v1` runtime readiness、`hfc-native-handoff-recovery-v2` pending descriptor 恢复，以及 `hfc-native-handoff-ack-v1` delivered 后确认。policy 在 hook 和 sidecar 两侧执行；runtime 事件只证明活性，不授权文件写入；handoff recovery 只提交 obligation、exact content、delivery plan、canonical route 与目标作用域的单向 hash/枚举，不提交正文或原始路由标识，ACK 只能发生在 Hermes ledger 已持久化 `delivered` 之后。任一控制面失败都不应阻断 Hermes Agent 工作，安装/恢复 mutation 则继续 fail-closed。

Hermes hook 到 sidecar `/events` 的 fail-open 转发链路已经落地：sidecar 不可用或拒绝事件时，hook 不拖垮 Hermes，未被卡片路径确认接管的消息继续遵循 Hermes 原生 fallback。卡片已经接受的路径则抑制重复灰色原生文本。

## 组件

### 最小 Hermes hook

安装器只通过 `hermes_feishu_card.install.patcher` 修改 Hermes。生命周期入口位于 `gateway/run.py`；具备 delivery ledger 的 Hermes 0.19+ 还在 `gateway/platforms/base.py` 安装两个最小 exact hook：一个收束无独立正文的媒体/TTS 路径，另一个在 `record_obligation` / `mark_attempting` 后、原生 send 前提交 Base 已生成的 `text_content`。两份源码及可选 cron target 都使用可识别、可移除、可恢复的 marker block，并由同一 manifest/backup transaction 管理。复杂事件抽取、delta 合并、命令卡和 Feishu adapter 兼容逻辑位于 `hermes_feishu_card.hook_runtime`；hook 不复制 Hermes 媒体清洗 pipeline，不保存飞书凭据，也不重写 Hermes 的会话 ownership、resume 或群聊准入规则。

### HTTP sidecar

`hermes_feishu_card.server` 接收事件，按 profile、bot、message/reply anchor 管理 `CardSession`，把高频 delta 合并成有限 PATCH，并在 terminal 前排空待发送内容。`hermes_feishu_card.cli start/status/stop` 管理本机进程；停止时同时校验 pidfile PID/token 和 `/health` 的 `process_pid/process_token_hash`，避免 PID 复用误杀。独立进程和 systemd user service 生命周期主要面向 macOS/Linux 等 POSIX 环境。

`/health` 只暴露脱敏、hash 化和 process-local 的状态，包括事件、事件鉴权拒绝、卡片发送/更新、cleanup 和路由指标。普通卡片的 create/reply 只有携带稳定 `delivery_uuid` 才允许最多 3 次发送尝试；没有 UUID 时只尝试一次。已有 message id 的更新使用独立的有限重试；不能把发送重试扩展为所有事件的重放。

### 会话与渲染

`hermes_feishu_card.session` 保存单进程内的流式会话状态；`render` 根据 thinking、answer、tool preview、notice、interaction 和 terminal 状态生成卡片 JSON。独立 runner 的私有、有界检查点可以恢复普通卡片的 message ID 和展示内容；非终态卡先显示等待执行状态同步，收到原轮有效事件后继续更新。检查点不恢复执行、waiter 或旧审批，也不能追溯恢复没有记录的卡片。具体容量、隐私和回滚边界见[卡片连接恢复](wiki/card-restart-recovery.md)。

可选 [CardKit streaming](wiki/cardkit-streaming.md) 使用独立实体与严格递增的 sequence；实体状态目前在进程内，不能由普通卡片检查点推导出其跨重启恢复能力。Hermes 始终是执行流程的事实来源。

### Feishu client

`hermes_feishu_card.feishu_client` 已实现 tenant token 获取、interactive card 创建和消息更新。凭据来自本机配置或环境变量，不进入仓库、卡片、`/health` 或日志。真实飞书验证记录在 release notes 和 `docs/wiki/feishu-acceptance.md`。

## 事件传输安全边界

默认 `server.host: 127.0.0.1` 使用**本机进程互信**：为了兼容旧安装，loopback `/events` 可以接收未签名事件；hook 在私有 state directory 的 transport root 可用时仍会发送事件鉴权 proof。

非 loopback listener 默认拒绝启动。只有显式设置 `server.allow_non_loopback: true` 才允许绑定，并且 sidecar 会强制校验基于私有 transport root、请求原始 body、时间戳和 nonce 的 HMAC 事件鉴权 proof；缺失、错误、过期或重放 proof 都在事件解析和发卡前拒绝。root secret 使用独立事件域分隔，不写进 YAML、env、卡片、日志或健康检查。

Windows non-loopback 在无法验证 state directory 的 ACL 私有性时 fail-closed；Windows loopback 继续使用本机进程互信，但不宣称 ACL 私有性已验证。

事件鉴权只证明请求来源和完整性，不为 HTTP 内容加密。非 loopback 只适合共享同一私有 state directory 的受信容器/私有网络；不要把 sidecar 直接暴露到公网。公网部署需要额外的 TLS/mTLS 或受控反向代理边界。

| 端点 | 默认边界 |
|---|---|
| `POST /events` | loopback 本机互信；显式非 loopback 强制事件鉴权 |
| `POST /delivery/policy` | state-directory transport root、短 timestamp window 与 nonce replay protection；响应不回显 id |
| `POST /runtime/events` | 独立 runtime domain 的认证 hello/heartbeat；只更新脱敏 readiness |
| `POST /native-handoff/recover` | 独立 recovery domain；用 obligation/content/plan/target hash 与 `create`/`thread-create` route 精确匹配一小时窗口内的 pending descriptor，不发送正文或原始路由标识 |
| `POST /native-handoff/ack` | 独立 ACK domain；仅在 Hermes ledger 已持久化 `delivered` 后确认 handoff |
| `POST /commands` | state-dir command transport proof |
| `POST /card/actions` | interaction token 或 operations transport proof |
| `GET /health` | 无鉴权但严格脱敏；仅供本机探活 |
| `GET /messages/{id}/summary`, `/interactions/{id}` | 本机 hook 协作索引，不应网络暴露 |

## 旧代码边界

`legacy/adapter/`、`legacy/sidecar/`、`legacy/patch/` 及 `legacy/` 下的旧安装/patch 脚本是历史 legacy/dual 实现，不是 active runtime。当前维护只以 `hermes_feishu_card/`、当前 CLI、安装器安全模型和 `docs/wiki/` 为准。
