# Hermes Feishu Streaming Card Wiki

这个目录是项目维护 wiki。README 面向用户介绍能力，`docs/release-notes-*` 记录版本变化；这里沉淀长期有效的维护知识、运行链路和验收清单。

## 一句话理解

`hermes-feishu-streaming-card` 是 Hermes Agent Gateway 的 Feishu/Lark sidecar 插件：Hermes 进程只安装最小 hook，真实卡片状态、Feishu 发送/更新、交互回调、诊断和发布资产都由本仓库维护。

## 阅读路径

1. [维护指南](maintenance-guide.md)
   - 适合改代码前阅读，说明 hot files、风险边界和测试矩阵。
2. [事件流和卡片生命周期](event-flow.md)
   - 适合排查卡片不更新、重复灰色消息、topic/thread 锚点问题。
3. [真实飞书验收清单](feishu-acceptance.md)
   - 适合每个 UX/兼容性版本发布前人工验证。
4. [飞书 CLI 验收与诊断](feishu-cli-playbook.md)
   - 适合用已认证的 `lark-cli` 旁路核对群成员、卡片回调和 topic/message 锚点。
5. [发布手册](release-playbook.md)
   - 适合发版前按步骤核对版本号、测试、tag、release assets。
6. [Fail-open 边界](fail-open-boundaries.md)
   - 适合判断异常时应退回 Hermes 原生路径，还是必须拒绝启动、请求或修复。
7. [V4.1 安全控制与排障](v4.1-safety-controls.md)
   - 适合配置 per-chat native policy、表格预算、runtime integrity 与 sidecar manager，并排查升级后的 readiness。
8. [单进程多 profile 与共享 sidecar](shared-profile-routing.md)
   - 区分 multiplex 逐消息路由与多进程固定身份，配置无需名为 `default` 的 profile。
9. [审批与思考阅读方式](card-readability.md)
   - 配置展开的思考代码块，并了解完整审批内容的展示和大小限制。

## 当前核心能力

- 普通会话流式卡片：`message.started` / `answer.delta` / `thinking.delta` / `tool.updated` / `message.completed` 聚合到同一张卡片。
- 新版 Hermes 兼容：首事件缺少 `message.started` 时也能创建初始卡片。
- Feishu/Lark 话题体验：后续事件通过 `reply_to_message_id` 回到原卡片，避免 topic timeline 停住。
- 群聊诊断：`/hfc status` 提示 chat binding、fallback/default 路由和群内 slash command 边界，真实 @/白名单准入仍由 Hermes 控制。
- 系统提示卡片化：`Working`、上下文窗口/压缩、session reset、skill loading、自我改进 review 等归一为 `system.notice`。
- 全命令反馈卡片：所有进入 Hermes 的 slash command（含 built-in、alias、plugin/quick 和 unknown command）只要产生非空文本反馈，就由独立 Feishu interactive card 承载；V4.2.0 的私聊裸 `/update` 使用专用维护确认卡，群聊/非飞书/别名/参数化更新保持 Hermes 原路径。
- 安装与诊断：`install/setup/doctor/repair/restore/uninstall` 覆盖本机、Hermes venv、Docker/source-stripped Hermes。
- V4.1 安全控制：exact `native_chats`、无损 table compact、认证 runtime readiness、strict repair 与不隐式提权的 service manager。
- V4.3 Hybrid runtime：固定 Hermes v2026.8.3 capability proof、真实 Plugin entrypoint、17-group/7-target V3 ownership、single-owner runtime interaction 与 linger 校验的 persistent user service。

## 文档分层

| 层级 | 位置 | 用途 |
|---|---|---|
| 产品说明 | `README.md` / `README.en.md` | 面向第一次访问仓库的人，讲项目价值、安装和展示图 |
| 详细使用手册 | `docs/user-guide.md` / `docs/user-guide.en.md` | 承接 README 迁出的配置、升级、CLI、版本史和排障细节 |
| 长期维护 wiki | `docs/wiki/` | 面向维护者和 Agent，讲如何安全改动和验证 |
| 版本说明 | `CHANGELOG.md` + `docs/release-notes-*` | 面向版本使用者，记录每个版本变化 |
| 测试说明 | `docs/testing.md` | 面向开发者，列出测试命令和覆盖范围 |
| 架构说明 | `docs/architecture.md` | 面向实现理解，说明 sidecar-only 结构 |

## 与 Obsidian LLM Wiki 的关系

仓库内 wiki 是公开、可随项目发布的维护资料；Bailey 的 Obsidian LLM Wiki 是长期检索层，会保存项目总览、维护规则和跨项目复用经验。

当本目录新增稳定知识时，同步到 Bailey 的 Obsidian LLM Wiki 镜像；仓库文档不记录本机绝对路径。
# v4.2 maintenance update

The private Feishu `/update` workflow is documented in
[`event-flow.md`](event-flow.md), operated through
[`maintenance-guide.md`](maintenance-guide.md), and verified with the
[`feishu-acceptance.md`](feishu-acceptance.md) checklist.
