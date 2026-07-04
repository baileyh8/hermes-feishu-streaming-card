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
4. [发布手册](release-playbook.md)
   - 适合发版前按步骤核对版本号、测试、tag、release assets。

## 当前核心能力

- 普通会话流式卡片：`message.started` / `answer.delta` / `thinking.delta` / `tool.updated` / `message.completed` 聚合到同一张卡片。
- 新版 Hermes 兼容：首事件缺少 `message.started` 时也能创建初始卡片。
- Feishu/Lark 话题体验：后续事件通过 `reply_to_message_id` 回到原卡片，避免 topic timeline 停住。
- 系统提示卡片化：`Working`、上下文窗口/压缩、session reset、skill loading、自我改进 review 等归一为 `system.notice`。
- 独立命令卡片：`/new`、`/reset`、`/undo`、`/model` 走 Feishu interactive card；`/update` 保持 Hermes 后台升级语义。
- 安装与诊断：`install/setup/doctor/repair/restore/uninstall` 覆盖本机、Hermes venv、Docker/source-stripped Hermes。

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
