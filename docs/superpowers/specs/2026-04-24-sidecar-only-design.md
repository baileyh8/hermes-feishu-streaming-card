# Hermes 飞书流式卡片 Sidecar-only 设计

## 摘要

本次重构会把 `hermes-feishu-streaming-card` 重新建设为一个干净的、只走 sidecar 主线的 Hermes 插件。当前仓库里的实现只作为历史参考和问题样本，不再作为新运行时的基础。新的主线会从活跃运行路径中移除 legacy 和 dual 模式，让项目成为一个稳定、可安装、适合开源维护的 Hermes Gateway 飞书/Lark 流式卡片插件。

主要兼容基线是 Hermes Agent `v2026.4.23` / `v0.11.0` 及以上。安装器在写入任何 Hermes 文件前，必须同时检查版本和代码结构/能力。低于该基线的 Hermes 版本不属于默认支持目标。

## 目标

- 为 Hermes Agent 在飞书中的对话提供可靠的流式卡片回复。
- 通过最小事件 Hook 或官方扩展点保持 Hermes Gateway 稳定。
- 在消息生成过程中渐进式展示 thinking 内容。
- 在卡片内实时追踪工具调用，并在完成后显示最终工具调用次数。
- 消息完成后，用最终答案覆盖 thinking 内容。
- 确保 `<think>` 和 `</think>` 原始标签永远不会出现在飞书卡片中。
- 提供自动安装、兼容性检查、备份、恢复、卸载和诊断能力。
- 提供清晰的安装、架构、事件协议和恢复文档。

## 非目标

- 保留 legacy 或 dual 运行模式。
- 默认支持早于 `v2026.4.23` 的 Hermes 版本。
- 在最小事件传输之外修改 Hermes 核心逻辑。
- 在单元测试或默认 CI 中依赖真实飞书 API。
- 在第一轮实现中完成完整 pip/package 发布形态。

## 架构

运行时有三个边界：

1. Hermes Gateway 保持原生 Agent 流程和文本流式能力。插件只增加尽可能薄的事件传输层。如果 Hermes 在 `v2026.4.23+` 中提供稳定的 plugin、hook 或 transport 扩展点，实现必须优先使用它。如果没有稳定扩展点，安装器才应用有边界、有 hash 校验的补丁。
2. Sidecar 负责所有流式卡片行为：事件校验、会话状态、thinking 累积、工具追踪、更新节流、飞书卡片渲染、飞书 API 调用、指标和诊断。
3. 飞书/Lark 接收来自 sidecar 的交互式卡片消息和渐进式消息更新。

Gateway Hook 失败绝不能阻塞 Hermes。当 sidecar 不可用或不健康时，Hermes 应继续使用原生文本行为，Hook 只记录 debug 级别诊断信息。

## 事件协议

事件是 Hermes Gateway 发送到 sidecar 的本地 HTTP JSON payload。

每个事件都包含：

- `schema_version`: `"1"`
- `event`: 下方定义的事件名之一
- `conversation_id`: 稳定的对话或聊天标识
- `message_id`: 本轮 assistant 回复对应的 Hermes 消息标识
- `chat_id`: 飞书 chat ID
- `platform`: `"feishu"`
- `sequence`: 每条消息内单调递增的整数
- `created_at`: Unix 时间戳
- `data`: 事件专属数据对象

事件名：

- `message.started`: 开始一个卡片会话，并创建飞书卡片。
- `thinking.delta`: 携带增量 thinking 文本。
- `tool.updated`: 汇报工具状态变化和工具元数据。
- `answer.delta`: 可选事件；当 Hermes 能在流式阶段区分最终答案时，携带增量最终答案文本。
- `message.completed`: 完成卡片，并用最终答案覆盖 thinking 内容。
- `message.failed`: 标记卡片失败，并展示简洁失败信息。

Sidecar 必须使用 `message_id` 和 `sequence` 拒绝重复事件，并忽略乱序的陈旧更新。完成和失败事件必须触发立即 flush。

## 卡片状态模型

用户可见的卡片只有两个正常状态：

- `思考中`
- `已完成`

收到 `message.failed` 时可以展示错误状态，但它不是一条独立的正常流程状态。

当卡片处于 `思考中`：

- 卡片主区域累积展示 thinking 文本。
- Thinking 内容按句子/段落感知策略渐进刷新。
- 工具调用在卡片内更新，不改变顶部主状态。
- 工具区域展示实时工具调用条目和累计次数。

当卡片变为 `已完成`：

- 卡片主区域被最终答案替换。
- Thinking 内容不再展示。
- 工具区域展示最终工具调用次数和简洁工具摘要。
- Footer 在数据可用时展示模型、耗时和 token 统计。

## 流式文本规则

Sidecar 必须在渲染前对文本做归一化。

Thinking 更新不应机械地按每个 token 刷新，而应采用句子/段落感知的刷新策略。Sidecar 持续累积 delta，并优先在以下条件之一满足时刷新：

- 出现中文或英文句末标点。
- 出现换行或段落边界。
- 收到工具事件。
- 达到最大等待阈值。
- 达到最大缓冲长度阈值。
- 收到完成或失败事件。

归一化模块负责在任何文本进入渲染器前移除 `<think>` 和 `</think>`。该过滤逻辑必须集中实现并测试，确保原始 thinking 标签不会泄露到飞书卡片。

## Sidecar 模块

干净重建后的实现应使用职责明确的模块：

- `hermes_feishu_card/cli.py`: 提供 `doctor`、`install`、`start`、`stop`、`status`、`restore`、`uninstall` 命令。
- `hermes_feishu_card/server.py`: 本地 HTTP API 和服务生命周期。
- `hermes_feishu_card/events.py`: 事件 schema、校验、版本化和归一化。
- `hermes_feishu_card/session.py`: `CardSession` 状态机、幂等、排序和 flush 决策。
- `hermes_feishu_card/text.py`: thinking 标签剥离和句子/段落感知缓冲。
- `hermes_feishu_card/render.py`: 根据归一化后的会话状态渲染飞书卡片 JSON v2。
- `hermes_feishu_card/feishu_client.py`: tenant token、重试、限流处理、卡片发送和更新 API。
- `hermes_feishu_card/install/`: Hermes 检测、补丁计划、备份 manifest、恢复和卸载。
- `tests/`: 单元测试、sidecar 集成测试、安装器 fixture 测试和可选真实飞书 smoke test。

Legacy、dual 和 archived 代码不应被新运行时导入。

## 安装与兼容性

CLI 暴露以下命令：

- `doctor`: 检查 Python 版本、Hermes 路径、Hermes 版本、Hermes 结构/能力、飞书凭证配置、端口可用性和 sidecar 健康状态。
- `install`: 运行 `doctor`，创建备份，安装 sidecar 文件，应用最小 Hook 或扩展注册，写入 manifest，并验证结果。
- `start` / `stop` / `status`: 管理 sidecar 进程。
- `restore`: 从 manifest 支持的备份中恢复文件。
- `uninstall`: 移除 sidecar 文件，并在应用过插件补丁时恢复 Hermes 文件。

兼容规则：

- 默认支持从 Hermes Agent `v2026.4.23` / `v0.11.0` 开始。
- 安装器必须拒绝更旧 Hermes 版本，除非用户显式提供高级兼容开关。
- 版本检查不够；安装器还必须验证预期扩展点或补丁锚点。
- 如果结构/能力检查失败，安装必须在写入 Hermes 文件前停止。
- 安装器必须优先使用 Hermes 官方 plugin/hook/transport 扩展点，而不是源码补丁。

备份与恢复规则：

- 写入前备份每个可能被修改的文件。
- Manifest 记录时间戳、Hermes 版本、文件 hash、补丁边界、目标路径和插件版本。
- 重复安装只升级插件拥有的区域，绝不编辑无关用户代码或 Hermes 代码。
- 恢复必须使用 manifest，不依赖模糊搜索删除。

## 测试策略

单元测试覆盖：

- 事件 schema 校验。
- 重复和乱序 sequence 处理。
- Thinking 标签剥离。
- 句子/段落感知 flush 决策。
- 工具调用实时计数和最终计数。
- 完成后用最终答案覆盖 thinking 内容。
- 失败渲染和 Gateway 降级行为。
- Token 和 footer 格式化。

集成测试覆盖：

- 使用 fake Feishu client 测试 sidecar HTTP API。
- 完整会话生命周期：start、thinking、工具更新、可选 answer delta、completion。
- 基于 Hermes `v2026.4.23` fixture 测试安装器检测、补丁计划、备份、恢复和卸载。
- 基于 Hermes 上游 `main` 的兼容 fixture。

可选 smoke test 覆盖：

- 使用用户显式提供的凭证创建和更新真实飞书卡片。
- 这些测试必须 opt-in，默认在 CI 中跳过。

## 文档

项目应发布：

- `README.md`: 插件解决的问题、支持的 Hermes 版本、环境要求、快速安装、`doctor`、启动/状态、恢复和 FAQ。
- `docs/architecture.md`: sidecar-only 架构和 Gateway 边界。
- `docs/event-protocol.md`: 事件 schema 和示例。
- `docs/installer-safety.md`: 兼容性检查、备份、恢复、卸载和失败模式。
- `docs/testing.md`: 本地测试命令、fixture 测试和可选飞书 smoke test。

## 发布策略

第一阶段发布 GitHub 源码安装版本，包含可靠 CLI 和测试套件。

第二阶段在 clean-room 运行时和安装器稳定后，再发布 pip package 或 release archive。每个 release 必须声明已测试 Hermes 版本，起点为 `v2026.4.23+`，并在 Hermes 发布新稳定版时更新测试 fixture 矩阵。

## 本设计已确认的问题

- 运行模式：sidecar-only。
- Legacy/dual 支持：从活跃运行路径中移除。
- 卡片状态：正常流程只展示 `思考中` 和 `已完成`。
- 工具调用：卡片内实时更新并显示累计次数；完成后显示最终次数。
- Thinking 内容：思考中累积展示，完成后被最终答案覆盖。
- Thinking 标签：在渲染前集中剥离。
- 兼容基线：当前稳定 Hermes `v2026.4.23` / `v0.11.0` 及以上。
