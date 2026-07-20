# Hermes 飞书流式卡片插件

[项目首页](../README.md) | [English](user-guide.en.md)

<p align="center">
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&logo=github&label=Stars&color=2f80ed"></a>
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&logo=githubactions&label=Release&color=22c55e"></a>
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/baileyh8/hermes-feishu-streaming-card/tests.yml?branch=main&style=for-the-badge&label=Tests&logo=githubactions"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Feishu/Lark" src="https://img.shields.io/badge/Feishu%20%2F%20Lark-Streaming%20Cards-00D6B4?style=for-the-badge">
  <img alt="Sidecar only" src="https://img.shields.io/badge/Runtime-Sidecar--only-7C3AED?style=for-the-badge">
  <a href="../LICENSE"><img alt="License" src="https://img.shields.io/github/license/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&color=64748b"></a>
</p>

![Hermes Feishu Streaming Card 封面](assets/readme-cover.png)

Hermes 飞书流式卡片插件把 Hermes Agent Gateway 的飞书/Lark 回复变成一张持续更新的交互式卡片：思考过程、工具调用、最终答案、授权确认、选项选择和运行统计都能收束在同一张飞书卡片里，而不是被拆成刷屏的灰色原生消息。

它重点解决飞书接入 Hermes 时最常见的痛点：流式内容漏字/乱序、长表格和代码块渲染成 raw markdown、工具调用过程不可见、approval/clarify 需要手工回复、sidecar 故障难排查、多 bot / 多 profile 难运维，以及升级 Hermes 后 hook 兼容不确定。

![Hermes 飞书卡片命令交互、结果反馈与工具 timeline 横向展示](assets/feishu-card-showcase-v385.png)

V3.8.2 起，最终答案保留在主内容区，pre-tool answer 会按“正文展示 -> 下一段到来后归档进 timeline”的节奏收束，思考与工具在折叠区使用不同字号和灰度层级；卡片底部不再重复展示同一份工具调用摘要。

## 项目亮点

- **流式卡片体验**：`thinking.delta`、`answer.delta`、`tool.updated`、`message.completed` 聚合到同一张飞书卡片，减少刷屏和上下文断裂。
- **卡片内交互**：Hermes approval / clarify choices 优先渲染成飞书按钮；V3.8.5 起，飞书/Lark WebSocket 长连接场景下 `/new`、`/reset`、`/model` 等独立 slash 命令的确认、选择和执行结果都会使用原生 interactive card，不可用时再退回 Hermes 原生文本 fallback。
- **运行提示收束**：V3.8.8 起，Hermes 原生 `Working` 心跳、上下文窗口/压缩提示、自动 session reset、skill 加载和自我改进 review 会优先进入卡片或独立小卡片，减少灰色原生消息散落。
- **话题内体验一致**：V3.8.9 起，飞书/Lark 话题回复里的流式事件和系统提示会回到同一张卡片更新，避免话题面板中 timeline 不动、灰色提示重复外溢。
- **群聊诊断更清楚**：V3.8.10 起，群内 `/hfc status` 会提示 chat binding、fallback/default 路由、绑定命令和 slash command 行为边界。
- **诊断命令不双发**：V3.8.11 起，已接管的 `/hfc status` 不会再同时触发灰色 `Unknown command /hfc` 原生回复。
- **附件摘要不再重复 reply**：V3.8.12 起，完成卡片里的 `colors.csv` / `styles.csv` 等附件摘要不会再导致整段最终答案以原生 reply 重复发送。
- **Hermes 升级更稳**：V3.8.13 起，安装器以 `gateway/run.py` 的可验证 anchor 作为最终准入条件；版本字符串支持 `v2026.7.7.2` 和 `Hermes Agent v0.18.2 (...)` 这类新版格式，完全不可解析时也可在 anchors 通过后继续安装。
- **WebSocket 交互闭环**：V3.8.14 起，agent clarify/approval 按钮在 Feishu/Lark WebSocket 长连接下也能通过原生 `interaction.select` card action 回到 sidecar。
- **输入附件不再重复 reply**：V3.8.15 起，用户输入 `.docx/files` 上下文只作为卡片附件摘要，不再误触发 Hermes 原生最终文本 reply。
- **话题群第二轮继续出卡片**：V3.8.16 起，Feishu/Lark 话题群复用同一 `message_id` 时，第二条及后续消息会创建新卡片。
- **Cron 路由意图继续出卡片**：V3.8.17 起，cron `deliver: origin` / `deliver: all` / `origin,all` 会解析到 Feishu 目标并发送卡片。
- **Cron 话题线程保持一致**：V3.8.18 起，从飞书话题线程创建的 cron 任务会携带 `thread_id`，卡片回到原线程；非飞书来源的 thread id 不会泄漏到飞书路由。
- **运维恢复有边界**：V3.9.0 的运维卡提供诊断、重新检测、两步安全修复和重启确认；私聊不比较操作者，群聊只允许发起者确认，卡片不可用时使用 CLI。普通流式卡的 footer/layout 不变。
- **可靠性热修**：V3.9.1 修复完成答案截断、打断任务卡片终态、模型选择回调超时和可验证的 marker-only 安装损坏；普通流式卡的 footer/layout 仍保持不变。
- **会话恢复更直接**：V3.10.0 起，裸 `/resume` 在飞书使用原生下拉卡；群聊/topic 只允许发起者点击，卡片不可用时自动回到 Hermes 文本列表。
- **footer 轻量增强**：识别到常见模型前缀时只给模型名增加转义后的语义色，既有 footer/layout、字段顺序和字号不变。
- **长内容更稳**：长 Markdown 表格和 fenced code block 按结构边界切分，降低飞书 raw markdown 和半截代码围栏问题。
- **工具详情更可读**：`tool.updated` 可展示参数摘要、耗时和失败原因，长详情仍保持紧凑折叠。
- **多 bot / 多 profile**：支持多飞书机器人、多 Hermes profile、群聊绑定、群聊安全诊断、bot/profile 标题和路由诊断。
- **sidecar-only 架构**：Hermes hook fail-open，飞书发送/更新、状态机、重试、健康检查都在 sidecar 中独立运行。
- **安装和发布友好**：支持一行安装、Release 安装包、`doctor` 诊断、`start/status/stop` 进程管理和安全 restore/uninstall。

## 解决的真实痛点

| 痛点 | 项目能力 |
|------|----------|
| 飞书里只能看到一大段最终文本，看不到 Agent 思考和工具进度 | 思考、答案、工具状态、footer 统计持续更新在同一张卡片 |
| 模型调用工具时内容乱序、漏字、完成后又冒出灰色原生消息 | per-message 顺序、PATCH 合并、终态优先和原生 resend 抑制 |
| Hermes 运行中不断冒出 Working、上下文提示、skill loading 等灰色提示 | `system.notice` 卡片化：当前任务内进入“思考与工具”，任务外用独立小卡片 |
| 飞书话题里卡片发出来了，但思考/工具不更新，系统提示还在外面重复出现 | 话题事件按 `reply_to_message_id` 回到原卡片，系统提示被 sidecar 接管后不再原生重复发送 |
| 群聊里不知道是否已经绑定到正确 bot，或 slash command 和普通会话行为不一致 | `/hfc status` 在群内给出 binding 提示、fallback 路由说明和 slash command 边界 |
| `/hfc status` 已经出卡片，但下面还出现灰色 `Unknown command /hfc` | 已接管的 `/hfc` 命令会快速 ACK Hermes Gateway，卡片发送转后台，避免原生 unknown fallback |
| 卡片完成后已经显示附件摘要，但下面又出现一条内容相同的原生 reply | 普通附件摘要保持 card-only；真实媒体/文件路径才保留 Hermes 原生投递 |
| Cron 配置 `deliver: origin` 或 `deliver: all` 后只收到 plain text，没有卡片 | 路由意图先解析到 Feishu origin / targets，再进入 cron card delivery；`local` 仍保持本地无投递 |
| Hermes 请求授权、让用户选择选项，或 slash 命令需要确认时，需要手工输入编号 | Agent 任务内选项留在当前卡片，独立 slash 命令使用独立命令卡片；不可用时退回编号文本 |
| 长表格/长代码块被飞书渲染成 raw markdown | Markdown-aware split，重复表头和完整 code fence |
| 多机器人、多群聊、多 profile 难确认路由 | `bindings.chats`、`group_rules` 安全诊断、profile-aware session key、`/health.routing` 诊断 |
| sidecar 或 hook 出问题难定位 | `doctor`、runtime import 检查、`/health` metrics、fail-closed installer、restore/uninstall |

## V4.0.0 实时双轨卡片

- 运行态 Header title 保留用户自定义标题（默认 `Hermes Agent`），subtitle 根据工具名和 Hermes `progress_callback.preview` 显示动作摘要；完整命令留在 timeline。
- 正文显示公开 `thinking.delta` 阶段输出；`answer.delta` 开始后主回答优先。
- 等待态显示 Hermes 原始交互问题，失败态保留最后工具 preview；普通聊天完成态只保留飞书原生回复引用作为 Header，不再叠加配置标题。
- 运行、等待、失败 Footer 只显示状态；普通聊天完成态 Footer 显示“已完成”并接续最终统计。
- preview 缺失时自动回退到现有标题，不要求新的 Hermes patch 字段。
- `/model` 与 Hermes CLI 使用同一 Provider/模型列表，先选 Provider、再选模型；Provider → Model、返回、取消和最终切换都在同一张命令卡片中完成，不再摊平全部供应商模型。

| 运行中 | 等待用户 |
|---|---|
| ![真实飞书运行态：Header 实时显示当前工具动作](assets/feishu-v4-runtime-running.png) | ![真实飞书等待态：原生按钮保持在同一张卡片](assets/feishu-v4-runtime-waiting.png) |
| 失败 | 已完成 |
| ![真实飞书失败态：保留最后工具预览](assets/feishu-v4-runtime-failed.png) | ![真实飞书完成态：仅保留原生回复 Header 与最终结果](assets/feishu-v4-runtime-completed.png) |

完整说明见 [V4.0.0 release notes](release-notes-v4.0.0.md)。

## V3.10.0 原生会话恢复与轻量视觉增强

裸 `/resume` 会读取 Hermes 已按当前用户/会话权限过滤的最近命名会话，发送一个 `select_static` 下拉卡。选择后先即时 ACK，再把复制出的 `/resume <session_id>` 事件交给 original Hermes handler，因此 ownership、continuation、running-agent release 和 model/reasoning override cleanup 都沿用上游实现。带参数 `/resume`、非飞书、空列表、卡片失败和群聊身份不可验证均 fail-open。

群聊与 topic 必须由原发起者 `open_id` 点击；私聊不额外比较操作者。模型 footer 的颜色创意来自 PR #98（@charles5g / jackmim），主线增加 HTML escape，并保持 footer/layout 不变。Issue #94 的需求、流程与安全验收由 @colinaaa 提出。

完整说明见 [V3.10.0 release notes](release-notes-v3.10.0.md)。

## V3.9.1 可靠性热修

V3.9.1 修复 issue #96 / PR #97 的完成答案截断、issue #92 / PR #93 的打断任务卡片终态竞争，以及 PR #98 的模型选择 callback 超时。安装器可恢复 manifest/backup 完全可验证的 marker-only 损坏，source-stripped Hermes 则明确显示 metadata 缺失；普通流式卡 footer/layout 不变。

贡献者：@colinaaa（PR #93、PR #97）、@charles5g（PR #98）、@wjiemin49-ux（PR #52 的 loopback 诊断与修复方向）。完整说明见 [V3.9.1 release notes](release-notes-v3.9.1.md)。

## V3.9.0 运维与可靠性基础

V3.9.0 合并 PR #84（贡献者 @Zanetach）的卡片 progress-status 路由与 `.env` 白名单扩展的 profile 环境支持，并将诊断和恢复能力收束为可选运维卡；普通流式卡的 footer/layout 保持不变。

- **受控恢复**：运维卡只用于诊断、重新检测、两步安全修复和 Gateway 重启确认。私聊后续确认不比较操作者；群聊 repair/restart 必须由发起者确认。卡片不可用、超时或不适用时继续使用 `doctor`、`repair`、`install`、`status`、`start/stop` CLI。
- **零配置 transport root**：sidecar state-dir 自动创建私有权限的 transport secret，不写入 config 或环境变量，也不会回显到卡片、`status` 或诊断输出。
- **profile 路由排障**：setup 的 `--profile-id` / `--event-url` 显式参数优先于进程环境、选定 env file 和默认值；仅 `doctor` 显示脱敏的完整 identity/profile/event endpoint route chain；`status` 只显示运行时的 `last_route` 和各 profile 的 events/profile-source 摘要；`/health` 只返回当前 `active_sessions`、`metrics`、`routing` 和 `profile_diagnostics` 等实际字段。
- **安全 repair 与清理**：install/setup 仅自动修复已知安全的 manifest/backup 状态，`--no-repair` 可关闭；无法验证的用户编辑仍拒绝覆盖。lifecycle cleanup 回收终态 runtime state，并保留有界 hash 化 metrics/history。
- **兼容与验收边界**：Hermes/Docker 自动化回归已覆盖参数与行为边界；existing-container Docker、真实飞书私聊/群聊 repair/restart、topic、cron 和 profile mismatch 仍为待验收，不能视为已通过。

完整发布说明见 [V3.9.0 release notes](release-notes-v3.9.0.md)。

## V3.8.18 Cron 话题线程回传补丁

V3.8.18 合并 PR #91（贡献者 @colinaaa），修复 issue #90：从飞书话题群线程创建的 cron job 在触发时没有携带 `thread_id`，卡片会被发成群里的新 topic，而不是回到原线程。

- **保留原话题线程**：cron event 会优先使用 scheduler 已解析的 Feishu target，其次使用 Feishu origin，再按兼容部署使用显式环境 fallback。
- **避免跨平台泄漏**：只有 `origin.platform == feishu` 时才读取 origin thread id，Telegram 等非飞书来源不会影响 Feishu 投递。
- **普通投递不变**：没有 thread id 的 cron 仍按原有 `chat_id` 发送，不会改变普通群聊或私聊行为。

完整发布说明见 [V3.8.18 release notes](release-notes-v3.8.18.md)。

## V3.8.17 Cron 路由意图卡片投递补丁

V3.8.17 合并 PR #77（贡献者 @zayn-0101），修复 cron job 使用 `deliver: origin`、`deliver: all` 或 `origin,all` 时，完成结果没有进入 Feishu/Lark 卡片、而是退回 Hermes 原生 plain text 的问题。

- **routing intent 不再被当成平台名**：`origin` / `all` 会先通过 cron origin 或 scheduler 预解析 targets 找到真实 Feishu 目标，而不是把 platform 误判成 `origin` / `all` 后直接放弃卡片。
- **`local` 语义保持不变**：`deliver: local` 仍表示本地/无投递，不会因为 fallback 被意外送到飞书。
- **兼容性更宽**：显式 `deliver: {"platform": "feishu", "chat_id": "oc_xxx"}` 继续可用；非 Feishu origin 的 chat id 不会泄漏到 Feishu 发送路径；安装 hook 找不到 Hermes `_resolve_delivery_targets` 时保持 fail-open。

完整发布说明见 [V3.8.17 release notes](release-notes-v3.8.17.md)。

## V3.8.16 话题群 message_id 复用新卡补丁

V3.8.16 合并 PR #88（贡献者 @colinaaa），修复 issue #89：Feishu/Lark 话题群里连续消息可能复用同一个 `message_id`，第一轮完成后第二轮 `message.started` 会撞到旧的 completed session，导致不发送新卡片；如果第二轮触发 clarify/approval，交互卡片不出现，用户点击也无从完成。

- **第二条及后续消息重新出卡片**：如果同一个 topic `message_id` 对应的旧 session 已经 `completed` / `failed`，sidecar 会清理旧的 card id、bot id、card config 和 flush controller，再创建新 session 并发送新卡。
- **clarify/approval 不再无卡片挂起**：第二轮 `interaction.requested` 可以继续渲染交互卡片，按钮或文本 fallback 才有可响应的目标。
- **活跃中的重复 started 仍安全**：如果当前 session 还在 streaming，重复 `message.started` 仍会被忽略，不会误发第二张卡。

完整发布说明见 [V3.8.16 release notes](release-notes-v3.8.16.md)。

## V3.8.15 输入附件重复 reply 抑制补丁

V3.8.15 修复 issue #82 的后续复现：在延续上一天 session、并带有用户输入 `.docx` 文件上下文时，完成卡片成功发送后仍可能出现一条内容相同的原生 Feishu/Lark reply。根因是 completion hook 把 Hermes locals 里的 `files` 当成“必须保留原生文件投递”，但这个场景里的 `files` 是输入上下文，不是模型新生成的输出文件。

- **输入文件只做卡片摘要**：`files` / `file` locals 仍会显示在卡片附件摘要里，但不会自动让最终文本从 Hermes 原生路径再发一遍。
- **真实输出仍 fail-open**：最终 answer 明确包含 `MEDIA:/tmp/...` 或本地文件路径时，仍保留 Hermes 原生文件/媒体投递。
- **结构化媒体输出继续保护**：`media_files`、`image_files`、`audio_files`、`video_files` 等输出字段仍会把 `native_delivery` 标记为 required。

完整发布说明见 [V3.8.15 release notes](release-notes-v3.8.15.md)。

## V3.8.14 WebSocket 交互卡片补丁

V3.8.14 合并 PR #87，修复 issue #86：Feishu/Lark WebSocket 长连接部署下，agent 发起的 clarify / approval 交互卡片按钮会通过 Hermes adapter 的原生 card action 通道送达，而不是直接访问 sidecar 的公网 HTTP callback。现在 hook runtime 会接管 `interaction.select`，转发到 sidecar `/card/actions`，并把更新后的卡片返回给 Feishu/Lark。

- **clarify/approval 按钮不再退回编号文本**：本地/private sidecar 可以继续使用卡片按钮完成选择。
- **安全边界仍在 sidecar**：`/card/actions` 继续校验 `interaction_id`、callback token，以及 callback payload 中存在的 chat id。
- **拒绝路径保持 fail-open**：过期、无效或 sidecar 拒绝的交互会返回空 Feishu callback response，不崩溃也不落到未知原生 handler。

完整发布说明见 [V3.8.14 release notes](release-notes-v3.8.14.md)。

## V3.8.13 Hermes 升级兼容补丁

V3.8.13 修复 Hermes 升级到 `v2026.7.7.2` / `0.18.2` 后卡片失效的问题：新版 Hermes 可能使用四段 Git tag，并在升级时覆盖 `gateway/run.py`，导致旧 hook 不在但 backup/manifest 还残留。现在检测、repair 和 reinstall 都能识别这个升级场景。

- **版本格式更宽容**：`v2026.7.7.2`、`0.18.2`、`Hermes Agent v0.18.2 (...)` 这类版本 metadata 都能识别。
- **anchor 优先保持可用**：版本 metadata 完全不可解析时，只要 `gateway/run.py` anchors 可验证，仍可用 `VERSION + gateway anchors` / `git tag + gateway anchors` 兜底。
- **升级残留可修复**：当前无补丁源码与旧 backup 相同时，`repair` 会自动清理 stale backup/manifest；如果 Hermes 升级确实替换了源码，默认仍拒绝，用户确认升级后需显式使用 `--accept-hermes-upgrade --yes`，且不会用旧 backup 覆盖新源码。

完整发布说明见 [V3.8.13 release notes](release-notes-v3.8.13.md)。

## V3.8.12 附件摘要重复 reply 抑制补丁

V3.8.12 修复 issue #82 的后续复现：完成卡片已经包含 `colors.csv` / `styles.csv` 等附件摘要时，插件之前会保守放行 Hermes 原生最终 reply，导致卡片下方又出现一条内容相同的 reply。现在 completed event 会区分普通卡片摘要和真实原生文件/媒体投递需求。

- **普通附件摘要保持 card-only**：`attachments` 中的展示摘要不会再强制放行整段最终回复。
- **真实文件/媒体路径仍 fail-open**：`MEDIA:/tmp/...`、本地文件路径、`files`、`media_files` 和 image/audio/video locals 仍保留 Hermes 原生投递路径。
- **Gateway completion guard 更精细**：patcher 通过 `native_delivery` 判断是否需要保留 native delivery，而不是只看 attachments 是否为空。

完整发布说明见 [V3.8.12 release notes](release-notes-v3.8.12.md)。

## V3.8.11 `/hfc` 原生未知命令抑制补丁

V3.8.11 修复真实 Feishu/Lark 里 `/hfc status` 已经触发 Hermes Agent 卡片，但 Gateway 仍继续发送灰色原生 `Unknown command /hfc` 的竞态。根因是 `/commands` 等待真实卡片发送完成，而 Gateway hook 的接管超时时间较短；现在 sidecar 接受命令后会先返回 `handled: true`，再后台发送卡片。

- **卡片接管后不再双发**：`/hfc status` 的预期结果是一张 Hermes Agent 诊断卡片，不再附带灰色 unknown command。
- **慢 Feishu 发送不影响接管判断**：真实卡片发送、网络或 tenant token 请求变慢时，Gateway 仍知道该 `/hfc` 命令已经由插件接管。
- **Gateway 文本解析更稳**：hook runtime 会从 `event.text` / `event.content` 补读 slash command 文本，覆盖真实 Gateway event 中 helper 不完整的情况。

完整发布说明见 [V3.8.11 release notes](release-notes-v3.8.11.md)。

## V3.8.10 群聊诊断与工具详情增强

V3.8.10 把 TODO 中的群聊能力拆清楚：Hermes Gateway 继续负责真实的 @机器人触发、白名单和是否允许群消息进入 Agent；插件侧负责把已经进入 Hermes 的群聊消息渲染成正确卡片，并在 `/hfc status` 中给出可执行的路由诊断。

- **chat binding 自动提示**：在群内发送 `/hfc status` 时，如果当前群还没有出现在 `bindings.chats`，卡片会提示正在使用 fallback/default bot，并给出 `hermes-feishu-card bots bind-chat ...` 命令。
- **白名单与 @ 触发边界**：`bindings.group_rules` 只用于安全诊断和展示计数，不泄漏真实 chat/user id；真实 @bot 和 allowlist 准入仍由 Hermes Feishu adapter 控制。
- **群内 slash command 行为差异**：`/new`、`/model`、`/reset` 等独立命令先通过 Hermes 群聊准入，再使用独立命令卡片；`/update` 继续保持 Hermes 后台升级流程。
- **工具详情增强**：工具 timeline 会尽量显示参数摘要、耗时和失败原因，方便从卡片里判断慢工具、失败工具和输入是否正确。

完整发布说明见 [V3.8.10 release notes](release-notes-v3.8.10.md)。

## V3.8.9 飞书话题卡片连续更新补丁

V3.8.9 修复飞书/Lark 话题回复场景：创建话题后与 bot 对话，首张卡片能出现，但后续 `answer.delta`、`thinking.delta`、`tool.updated` 或 `system.notice` 可能因为 Hermes 内部流式 `message_id` 变化而没有更新同一张卡片，系统提示还可能同时出现在卡片 timeline 和外部灰色消息里。

- **话题内同一张卡持续更新**：sidecar 会用 `reply_to_message_id` 把后续 topic 事件映射回当前运行中的卡片 session。
- **系统提示不再重复外溢**：session-scoped `system.notice` 成功进入卡片后返回 `applied: true`；如果卡片投递短暂超时，已识别的 Hermes 系统提示也会被抑制，不再继续发送原生灰色文本。
- **适配 Hermes v0.18.x Relay 元数据**：hook runtime 会把 Relay `source.message_id` 保留下来作为话题原消息锚点。

![飞书话题内卡片连续更新与思考工具 timeline 展示](assets/feishu-topic-card-showcase-v389.png)

上图为真实飞书话题验证：话题回复面板里的卡片能持续更新，思考与工具 timeline、最终答案和 footer 统计保持在同一张卡片内；上下文窗口、自我改进等系统提示会进入卡片或独立小卡片，不再额外溢出成外部灰色消息。

完整发布说明见 [V3.8.9 release notes](release-notes-v3.8.9.md)。

## V3.8.8 Hermes 原生系统提示卡片化

V3.8.8 把 Hermes 原生灰色运行提示统一收束到飞书卡片体验里：`Working` 长任务心跳、上下文窗口/压缩提示、自动 session reset、skill 加载和 self-improvement review 会被识别为 `system.notice`。如果当前任务卡片仍可更新，提示会进入“思考与工具”区域；如果没有可用任务卡片，则发送一张紧凑的独立提示卡片。

- **少一些灰色散落消息**：上下文窗口、压缩、session reset、skill loading、自我改进 review 等提示优先卡片化。
- **长任务心跳会更新同一条记录**：`Working — iteration ...` 这类 heartbeat 会复用同一个 `notice_id`，避免 timeline 里刷出多条重复提示。
- **保持 fail-open**：sidecar 不可用、提示无法识别或卡片发送失败时，Hermes 原生文本路径继续可用，不阻断任务。

完整发布说明见 [V3.8.8 release notes](release-notes-v3.8.8.md)。

## V3.8.7 新版 Hermes 首事件兼容补丁

V3.8.7 修复 issue #75：部分新版 Hermes 流式事件可能不再先发送 `message.started`，而是直接从 `answer.delta`、`thinking.delta`、`tool.updated` 或 `message.completed` 开始。旧版 sidecar 因为没有现成 session，会把这些首事件全部计为 `events_ignored`，导致飞书里没有初始卡片。现在这些消息事件也可以创建 session 并立即发送首张 Feishu/Lark 卡片。

- **不再依赖 `message.started`**：首个 answer/thinking/tool/completed 事件都会触发卡片创建。
- **保留既有链路**：已有 `message.started`、interaction、cron completion 和终态诊断逻辑保持兼容。
- **与 V3.8.6 叠加**：Docker/source-stripped Hermes 缺 `VERSION` 的 Gateway anchor 兜底继续有效。

完整发布说明见 [V3.8.7 release notes](release-notes-v3.8.7.md)。

## V3.8.6 Docker / Hermes v0.18.0 兼容补丁

V3.8.6 修复 issue #70 的 Docker 容器安装场景：Hermes v0.18.0 / `v2026.7.1` 上游发布包可能没有顶层 `VERSION` 文件，容器镜像也常常不保留 `.git` 元数据。现在 `doctor --explain`、`install` 和 `setup` 会在缺少版本文件时继续读取 `gateway/run.py` 的真实代码 anchor，只要 anchor 可验证，就按 `gateway_run_013_plus` 安装，不再误报 `Hermes VERSION missing, unknown, or invalid`。

- **Hermes v0.18.0**：`v2026.7.1` / `0.18.0` / `v0.18.0` 已加入兼容矩阵，继续使用 `gateway_run_013_plus`。
- **Docker 无 VERSION 兜底**：诊断会显示 `version_source: gateway anchors`、`version: unknown` 和推断出的 `hook_strategy`。
- **版本文案更宽容**：后续版本会从描述型 `VERSION` 中提取数字版本；如果版本文案完全不可解析，但 `gateway/run.py` anchors 可验证，诊断会显示 `VERSION + gateway anchors` 并继续安装。文件不可读、symlink、必要 anchor 缺失或结构不兼容仍 fail-closed。

完整发布说明见 [V3.8.6 release notes](release-notes-v3.8.6.md)。

## V3.8.5 命令结果反馈卡片补丁

V3.8.5 补齐 V3.8.4 的“始终允许/无需确认”路径：当 Hermes 直接执行 `/new`、`/reset`、`/clear`、`/undo`、`/stop` 或直接 `/model <model>` 后，执行结果也会以 Feishu/Lark interactive card 回复，而不是退回灰色原生文本。`/model` 切换后的反馈继续稳定留在绿色卡片里，`/update` 仍保持 Hermes 后台升级命令，不弹交互卡片。

- **直通结果卡片化**：patcher 会把当前 `event` 传给 hook runtime，Feishu adapter `send()` 能识别独立 slash command 的返回结果。
- **交互更新更干净**：按钮/下拉点击后只依赖 Feishu callback response 更新原卡片，不再额外调用飞书不支持的 interactive `message.update`。
- **升级兼容**：重新运行 `install` 会把 V3.8.4 的旧 command-card hook block 升级为 V3.8.5 的 `event=event` 形式。

完整发布说明见 [V3.8.5 release notes](release-notes-v3.8.5.md)；上一版 WebSocket 原生命令卡片说明见 [V3.8.4 release notes](release-notes-v3.8.4.md)。

## V3.8.4 Feishu WebSocket 命令卡片热修

V3.8.4 修正 V3.8.3 在本地/private sidecar 场景下只能退回灰色文本的问题：`/new`、`/reset`、`/undo` 这类确认命令现在会直接复用 Hermes Feishu adapter 的 WebSocket card action 通道发送原生 interactive card；`/model` 也会用同一套原生卡片完成选择。正在运行的 Agent 流式卡片仍只承接 approval、clarify、对话选项和思考/工具 timeline，不和独立命令混在一起。

- **WebSocket 原生确认卡片**：插件动态补上 Feishu adapter 的 `send_slash_confirm(...)`，按钮点击由 `_on_card_action_trigger` 进入 `tools.slash_confirm.resolve(...)`。
- **WebSocket 原生模型选择卡片**：当 Hermes 请求 Feishu adapter 的 `send_model_picker(...)` 时，插件会补上 Feishu-only picker，选择模型后回写同一张命令卡片。
- **不重复弹选择卡**：WebSocket 原生卡片可用时会跳过 sidecar 预交互，`/new` 不再同时出现 sidecar 选项卡和原生按钮卡。
- **`/update` 不弹卡片**：`/update` 仍按 Hermes 后台升级命令处理，不做交互按钮卡片，避免把升级流程误当成用户确认。
- **安全 fallback**：Feishu 原生卡片不可用、sidecar 不可用、卡片应用失败、超时或完成态更新失败时，继续交给 Hermes 原生文本路径，避免命令卡死。

完整发布说明见 [V3.8.4 release notes](release-notes-v3.8.4.md)；上一版独立命令卡片基础说明见 [V3.8.3 release notes](release-notes-v3.8.3.md)。

## V3.8.2 卡片 timeline 阅读体验补丁

V3.8.2 聚焦飞书卡片折叠区的真实阅读体验：工具调用前的自然语言预分析会先停留在正文区，直到下一段预分析或完成态再归档到“思考与工具”；完成态会剥离已经归档过的中间说明，只把最终答案留在主内容区。

- **pre-tool answer 延迟折叠**：上一段预分析不会一闪而过，只有下一段预分析或终态到来时才移入折叠 timeline。
- **完成态正文更干净**：若最终答案包含已经归档的 preface，terminal card 会自动去重，避免“分析过程 + 最终答案”一起挤在正文。
- **timeline 层级更清楚**：思考条目保持小字号主层级，工具详情使用更小字号和弱化灰度，长命令不再抢主回答注意力。
- **raw thinking 保持隐藏**：底层 `thinking.delta` 仍只作为内部流式状态，不混入正文和折叠区；折叠区只展示用户可读的 pre-tool answer。

完整发布说明见 [V3.8.2 release notes](release-notes-v3.8.2.md)。

## V3.8.1 高频流式与飞书内诊断补丁

V3.8.1 修复 issue #74：在 Hermes Agent 0.17.0+、thinking model、长上下文和 token-by-token 高频 delta 场景下，hook 会先在 Hermes Gateway 进程内合并 `thinking.delta` / `answer.delta`，再发送到 sidecar，降低 stream-reader 线程上的对象构造、锁竞争和 HTTP 调度压力，避免启用插件后触发 `Stream stale for 180s`。

- **Gateway-side delta 合并**：新增 `HERMES_FEISHU_CARD_DELTA_COALESCE_MS`、`HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS`、`HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING`，默认无需配置。
- **终态前主动 flush**：`message.completed` / `message.failed` 前会先 flush 同一消息的 pending delta，避免最后卡片缺少尾部内容。
- **飞书内只读诊断命令**：支持 `/hfc help`、`/hfc status`、`/hfc doctor`、`/hfc monitor`，结果以飞书卡片回复，不会执行写操作。
- **诊断信息脱敏**：`/messages/{message_id}/summary` 和 `/hfc` 卡片只展示 hash 后的 chat/message/thread 上下文。

完整发布说明见 [V3.8.1 release notes](release-notes-v3.8.1.md)。

## V3.8.0 卡片体验与流式稳定性升级

V3.8.0 聚焦真实飞书阅读体验：最终答案固定在最重要的主内容区，reasoning / tool timeline 收进辅助区域；长表格、代码块、工具 burst 和终态事件堆积时，卡片会先合并并 drain 待更新内容，再写入最终态。

- **主回答更清楚**：最终答案不被长思考和工具列表挤到卡片底部，工具过程进入辅助 timeline。
- **减少重复展示**：启用辅助 timeline 时，底部不再额外渲染一份“工具调用 N 次”摘要。
- **流式终态更稳**：terminal card 渲染前 drain pending updates，避免最后一张卡被旧的中间状态覆盖。
- **诊断更准**：`doctor` 的 Hermes runtime import 检查会在 Hermes 项目根目录执行，避免当前仓库路径导致误判。
- **Docker 同步发布**：V3.8.0 发布时同步了 Docker 安装示例，容器内安装仍使用 `install-docker.sh`。

完整发布说明见 [V3.8.0 release notes](release-notes-v3.8.0.md)。

## V3.6.6 中断去重与安装诊断补丁

V3.6.6 修复 issues #67 和 #68：`message.completed` 会基于 sidecar 返回的 `applied` 结果判断卡片链路是否真正接管，terminal 事件不再等待慢 Feishu PATCH 才响应 Hermes，避免中断或更新堆积后同时出现流式卡片和灰色原生答复；`doctor --explain` / `install` 在 `--hermes-dir` 指错时会读取 `hermes -V` 的 `Project:` 路径，并直接提示正确的 Hermes 目录。

完整发布说明见 [V3.6.6 release notes](release-notes-v3.6.6.md)。

## V3.7.0 Docker 容器适配

V3.7.0 在已有 Hermes 容器中补齐安装与升级路径（issue #70）。`install-docker.sh` 会按容器默认路径执行更新。

完整发布说明见 [V3.7.0 release notes](release-notes-v3.7.0.md)。

## V3.6.5 流式终态稳定性补丁

V3.6.5 修复 issues #64 和 #65：Feishu thread / 话题场景下，`message.started` 现在会使用和 streaming callbacks 一致的 reply anchor 作为 card session `message_id`，避免 `events_applied=0`；DeepSeek 等一次性返回最终答案的模型，即使没有任何 `thinking.delta` / `answer.delta`，也会从 `message.completed` / `agent_result.final_response` 回填最终内容并完成同一张卡片。

完整发布说明见 [V3.6.5 release notes](release-notes-v3.6.5.md)。

## V3.6.4 话题回复与定时投递补丁

V3.6.4 修复 issues #61 和 #62：用户在飞书 thread / 话题里发消息时，初始 streaming card 会使用飞书 reply API 发回同一 thread，并继续用同一张卡片做后续 PATCH 更新；cron job 配置 `deliver: "feishu:oc_xxx"` 时也能从 `deliver` 字段解析 chat id，避免定时任务退回普通文本。

完整发布说明见 [V3.6.4 release notes](release-notes-v3.6.4.md)。

## V3.6.3 Hermes v0.17 兼容与交互补丁

V3.6.3 修复 issues #56-#59：Hermes v0.17.0+ / `v2026.6.19+` 将真实 streaming callback 移到 `_run_agent_inner` 后，patcher 会优先在 `_run_agent_inner` 注入 `tool.updated`、`answer.delta`、`thinking.delta`、clarify 和 approval hook，避免卡片停在“思考中”。

localhost/private sidecar 默认使用 `card.interaction_mode: auto`，sidecar-owned choices 会通过 Feishu/Lark WebSocket 长连接原生 card action 保持按钮交互，不需要公网 HTTP callback。只有显式配置 `card.interaction_mode: text` 时才会显示编号选项并交还 Hermes 原生文本交互。`/new`、`/reset`、`/model` 等独立命令同样复用这条路径，命令执行结果也会保持卡片反馈。

这一版还隔离了非飞书平台事件，安装插件后不会影响 Telegram 原生消息；Windows `HERMES_HOME=C:\Users\...\AppData\Local\hermes\profiles\thinking` 也能正确解析 profile。

完整发布说明见 [V3.6.3 release notes](release-notes-v3.6.3.md)。

## V3.6.2 安装可靠性补丁

V3.6.2 修复 issue #53：`setup` / `install` 不只把插件装进当前用户 Python，还会检测 Hermes Gateway 实际运行的 venv Python（例如 `~/.hermes/hermes-agent/venv/bin/python`），并在打 hook 前把同一版本的 `hermes-feishu-streaming-card` 安装进去。

`doctor --explain` / `doctor --json` 新增 `runtime_import` 检查，会明确告诉你 Hermes runtime 是否能 import `hermes_feishu_card.hook_runtime`。hook import/emit 失败也不再完全静默，仍保持 fail-open，但会在 Hermes stderr 写入 `[hermes-feishu-card] hook failed: ...` 诊断 warning。

完整发布说明见 [V3.6.2 release notes](release-notes-v3.6.2.md)。

## V3.6.1 兼容补丁

V3.6.1 修复 issue #47：Hermes `VERSION` 文件写成 `0.15.1` 这类无 `v` 前缀语义版本时，`doctor --explain` 不再误报 unsupported。`0.15.x` / `v0.15.x` 会继续按新版 Hermes 走 `gateway_run_013_plus`，前提是 `gateway/run.py` 的必要 anchor 仍存在。

完整发布说明见 [V3.6.1 release notes](release-notes-v3.6.1.md)。

## V3.6.0 运维增强

V3.6.0 面向真实线上使用后的排障和维护场景：当 Hermes 升级、hook 状态异常、媒体/文件消息进入会话，或一个 sidecar 服务多个 profile/bot 时，用户可以更快判断“哪里坏了、能不能自动修、该验证哪条路由”。

- **诊断可读可机器解析**：`doctor --explain` 给人工排障摘要，`doctor --json` 输出 config、sidecar、Hermes、streaming、install_state 和 recommendations。
- **安装状态可自救**：新增 `repair --hermes-dir ... --yes` 和 `setup --repair`，只修复可验证的 manifest/backup 状态，遇到用户改动会拒绝覆盖。
- **媒体/文件更安全**：识别 Hermes 结构化 `attachments` / `files` / `media_files` / image/audio/video 对象，卡片保留摘要，同时不抑制 Hermes 原生媒体/文件投递路径。
- **多 Profile 更好排障**：`smoke-feishu-card --profile-id`、`bots test --profile-id`、CLI `status` 和 `/health.routing.profiles` 都能按 profile 展示路由状态。
- **兼容矩阵更明确**：自动化覆盖 Hermes `v2026.4.23`、`v2026.5.7`、`v2026.5.16+`、`v2026.5.29`、`0.13.x`、`0.14.x`、`0.15.x` 和带/不带 `v` 前缀语义版本的 hook strategy。

完整发布说明见 [V3.6.0 release notes](release-notes-v3.6.0.md)，历史路线见 [docs/roadmap-v3.6.0.md](roadmap-v3.6.0.md)。

## V3.6.0 解决了什么

| 场景 | V3.6.0 的变化 |
|------|---------------|
| 用户只知道“卡片没回来”，不知道 hook、sidecar 还是 Hermes anchor 出问题 | `doctor --explain` 给出分区诊断和下一步建议 |
| manifest/backup 丢失导致 restore/install 拒绝，但当前 patch 其实可验证 | `repair` 自动重建缺失状态文件，无法验证时保持拒绝 |
| 图片、文件、音频、视频进入 Hermes locals 后卡片里看不到上下文 | 卡片显示附件摘要，原生媒体/文件发送路径继续保留 |
| 多 profile / 多 bot 下不知道消息走了哪个机器人 | `status` 和 `/health.routing.profiles` 展示 bot 数、群聊绑定、last_route、last_route_error |
| 升级 Hermes 后不确定用新版还是旧版 hook strategy | 测试和文档明确各 Hermes key release 对应的 `gateway_run_013_plus` / `legacy_gateway_run`，并覆盖 `0.15.x` |

## V3.5.x 功能基线

**交互能力**

- 新增飞书按钮交互闭环：`interaction.requested` 渲染按钮，`/card/actions` 记录选择，Hermes hook 轮询 `/interactions/{interaction_id}` 后继续执行。
- 授权/选项按钮采用飞书 JSON 2.0 `button + behaviors.callback`，避免旧式 action 容器在飞书端更新失败。
- approval / clarify hook 透传 Hermes event loop，交互请求和流式 delta 共用同一条 message send lock，减少事件乱序。

**流式稳定性**

- 修复 issue #41：多条回复和新版 Hermes streaming flow 继续走卡片更新，最终回答不再从第二条开始退回原生 text。
- 修复 issue #39：`message.completed.answer` 为空时不再清空已通过 `answer.delta` 展示的内容，避免 DeepSeek V4 Pro 工具调用后卡片最终无内容。
- 修复 issue #31：同一张飞书卡片 PATCH 串行化，避免旧快照后到覆盖新内容。
- 修复 issue #25：Hermes v2026.5.7 的 `event_message_id` 作为显式 `message_id` 使用，保证 `message.started` 和 `message.completed` 落在同一张 fallback 卡片。
- 同一消息的 HTTP 事件发送按 message id 加锁；多线程 Hermes callback 产生的 sequence 不会互相踩踏。
- `answer.delta` / `thinking.delta` 保留原始边界空格，避免中文、英文、代码片段被拼坏。
- `thinking.delta(mode=append_block)` 以完整思考片段追加，修复思考过程句子漏字、截断和粘连。
- sidecar 事件快速 ACK，卡片 PATCH 合并更新，刷新间隔降到 `0.2s`，终态事件在后台优先补齐最终卡片。
- queued follow-up 完成后抑制原生 resend，避免“卡片已完成但下面又冒出一大段灰色原生消息”。

**长内容与渲染**

- 长 Markdown 表格超过 `MAIN_CONTENT_CHUNK_CHARS` 后重复表头分块，仍保持合法表格。
- fenced code block 超长后拆成多个完整 fenced block，避免飞书显示半截代码围栏。
- 保留 V3.3.0 的飞书 5 表格限制保护，超限时自动截断并提示。

**兼容与安装**

- Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x、`v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` 使用 `gateway_run_013_plus`。
- 旧版本 Hermes（`v2026.4.23` 到 `v2026.4.x` / `0.12.x`）继续使用 `legacy_gateway_run`。
- `doctor` 输出 `version_source`、`version`、`hook_strategy`、`compatibility`、anchor/anchors 和 `reason`，便于安装前确认。
- 升级插件后必须重新安装 hook：运行 `install --hermes-dir ... --yes`，让 Hermes 使用匹配当前版本的 hook。
- 处理 PR #42：cron 卡片路由优先使用 `job['deliver']` 和 scheduler 解析出的 Feishu target。
- 多 profile / multi bot 体验补齐：issue #23、per-bot/profile title、cron final cards、attachment summaries + native media delivery、reply card context。
- V3.6.0 进一步补齐 profile 定向 smoke、routing profile diagnostics、structured attachment summaries 和 safe repair。

**配置与运行安全**

- 默认 `server.host: 127.0.0.1` 属于本机进程互信；不要把 sidecar 未鉴权暴露到网络。
- 非 loopback 必须显式设置 `server.allow_non_loopback: true`，并强制使用私有 state directory 的 HMAC 事件鉴权；事件鉴权不提供加密，公网仍需 TLS/mTLS 或受控反向代理。
- `--config` 指向的配置文件同目录存在 `.env` 时，会自动读取 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`HERMES_FEISHU_CARD_HOST`、`HERMES_FEISHU_CARD_PORT`；`setup` / `start --env-file ...` 选中的 env file 也会进入同一配置加载链。
- 优先级固定为 YAML < 配置同目录 `.env` < 显式选中的 env file < 真实进程环境变量；不会隐式读取全局 `~/.hermes/.env`。
- 多 Profile 模式下继续要求每个 profile 显式配置 Feishu 凭据，顶层环境变量不会覆盖 profile 凭据。
- 无凭据时 sidecar 使用 no-op client，不会向真实飞书发卡；`/health` 显示 `status: degraded`、`noop_mode: true`、`delivery.mode: noop`，发送返回 `not_sent` 并计入 `feishu_noop_attempts` / failure，不再伪造成功 message id。

## 一行安装

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex
```

安装脚本会自动安装/升级插件、读取或提示填写飞书凭据、写入 `~/.hermes/.env`，并调用整合安装器：

```bash
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes/config.yaml --yes
```

本地脚本、Docker 脚本和 PowerShell 安装器支持同一组显式参数：`--config`、`--env-file`、`--version`、`--profile-id`、`--event-url`、`--no-repair`；PowerShell 使用对应的 `-Config`、`-EnvFile`、`-Version`、`-ProfileId`、`-EventUrl`、`-NoRepair`。旧的一行安装命令无需修改。参数解析优先级固定为：显式参数 > 进程环境变量 > 选中的 `.env` > 脚本默认值。

```bash
bash install.sh \
  --config ~/.hermes/config.yaml \
  --env-file ~/.hermes/.env \
  --version latest \
  --profile-id child \
  --event-url http://127.0.0.1:8765/events \
  --no-repair
```

`setup` 只会原子更新 selected `.env` 中的 `HERMES_FEISHU_CARD_PROFILE_ID` 和 `HERMES_FEISHU_CARD_EVENT_URL`，其他注释、顺序和未知 key 保持不变。event URL 必须是无凭据、query 和 fragment 的 HTTP(S) `/events` endpoint；host 可使用 loopback、`host.docker.internal` 或单标签 Docker Compose service 名称。

安装完成后可以检查 sidecar 状态：

```bash
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
```

常用环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HFC_VERSION` | `latest` | 指定安装版本，例如 `v3.8.18`、`v3.6.6` 或 `main` |
| `HERMES_DIR` | `~/.hermes/hermes-agent` | Hermes Agent Gateway 目录 |
| `HFC_PYTHON` | 优先自动检测 Hermes venv | 显式覆盖 `install.sh` 使用的 Python |
| `HFC_CONFIG` | `~/.hermes/config.yaml` | sidecar 配置路径 |
| `HFC_ENV_FILE` | `HFC_CONFIG` 同目录 `.env` | 飞书凭据保存位置 |
| `HFC_SKIP_START` | `0` | 设为 `1` 时只安装 hook，不启动 sidecar |
| `HFC_NO_PROMPT` | `0` | 设为 `1` 时禁止交互式输入，适合自动化安装 |

高频流式调优变量默认无需配置，只有在超高频 thinking/burst 场景下才需要调整：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MS` | `250` | Gateway runtime 内合并 delta 的最大等待时间；设为 `0` 可关闭 |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS` | `600` | pending delta 累积到该字符数后立即 flush |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING` | `128` | 同时保留的 pending delta session 上限 |

## Docker 容器内安装 / 更新

如果 Hermes 运行在已有 Docker 容器里，优先使用 `install-docker.sh`。它默认读取：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HERMES_DIR` | `/opt/hermes` | 容器内 Hermes Agent Gateway 目录 |
| `HFC_CONFIG` | `/opt/data/config.yaml` | sidecar 配置路径 |
| `HFC_ENV_FILE` | `/opt/data/.env` | 飞书凭据文件 |
| `HFC_VERSION` | `latest`（脚本）/ `v4.0.0`（Compose 示例） | 指定安装 tag 或分支 |
| `HFC_PYTHON` | 自动检测 Hermes venv | 显式指定容器内 Python |

示例：

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v4.0.13
bash install-docker.sh --profile-id child --event-url http://hfc-sidecar:8765/events
```

`docker-compose.example.yml` 只是适配示例，不是官方镜像。它展示 `/opt/hermes`、`/opt/data` 挂载和非交互安装方式。

也可以从 Release 下载 `hermes-feishu-card-<version>-macos.tar.gz`、`hermes-feishu-card-<version>-linux.tar.gz` 或 `hermes-feishu-card-<version>-windows.zip`，解压后运行包内的 `install.sh` / `install.ps1`。完整安装包说明见 [README-install.md](../README-install.md)。

## 手动安装

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git
cd hermes-feishu-streaming-card
pip install -e ".[test]"

export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx

python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` 是整合安装器：自动生成配置、检查 Hermes 版本和代码 anchor、把插件安装到 Hermes Gateway 实际运行的 venv Python、安装 hook、启动 sidecar 并做健康检查。Linux 上若 systemd user manager 可用，sidecar 会进入独立、可自动重启的 transient user service，不再与 `hermes-gateway` 共用 cgroup；macOS、Windows 和无可用 user manager 的环境继续使用 detached-process fallback。它支持 `v2026.4.23` 起的旧版 Hermes，也支持 Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x 与 `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` 新版 anchor；Hermes `VERSION` 可带或不带 `v` 前缀，也可从 `Hermes Agent v0.18.2 (...)` 这类描述型版本中提取数字版本。V3.8.6 起，Docker/source-stripped 环境缺少 `VERSION` 和 `.git` 时也可用 `gateway/run.py` anchor 兜底识别；当前版本在 `VERSION` 可读但不可解析时，也会在 anchors 可验证后继续安装。

多 profile 安装后可用 `doctor` 只读检查完整脱敏 route chain；`status` 仅摘要运行时路由和 profile 事件，`/health` 仅报告实际 routing health 字段。`doctor` 输出不包含 App Secret、token 或 URL credentials：

```bash
python3 -m hermes_feishu_card.cli doctor \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --profile-id child \
  --explain
```

如果你使用 Hermes 默认目录，也可以把凭据放在 `~/.hermes/.env`：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CONNECTION_MODE=websocket
FEISHU_HOME_CHANNEL=oc_xxx
```

之后使用：

```bash
python3 -m hermes_feishu_card.cli start --config ~/.hermes/config.yaml
```

V3.6.2 会继续自动读取 `~/.hermes/config.yaml` 同目录的 `~/.hermes/.env`。更宽泛的 `.env` 搜索路径会作为独立小项评估，不和 venv 安装修复混在同一版里。

## 升级

从 V3.2.x/V3.3.0/V3.4.x/V3.5.x/V3.6.x/V3.7.x/V3.8.0-V3.8.17 升级到 V3.8.18 向后兼容，**单 Profile 配置无需任何修改**。如果 Hermes 使用自己的 venv，升级后请重新跑 `setup` 或 `install`，让插件同时进入 Hermes runtime Python 并刷新 hook。V3.8.18 保留 V3.8.10 的群聊诊断、V3.8.11 的 `/hfc` 命令接管修复、V3.8.12 的附件摘要重复 reply 抑制、V3.8.13 的 Hermes 升级兼容、V3.8.14 的 WebSocket interaction 按钮闭环、V3.8.15 的输入附件重复 reply 抑制、V3.8.16 的话题群复用 `message_id` 新卡修复、V3.8.17 的 cron 路由意图修复，并修复 cron 卡片无法回到飞书话题原线程的问题；建议升级后执行一次 `doctor --explain`，并在普通会话、话题和目标群聊里各发送一次普通问题、`/hfc status`、`/new`、`/model`，在同一话题里连续发送两条消息，并用一个从话题线程创建的 cron 任务验证卡片回到原线程。

```bash
# 1. 停止 sidecar
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml

# 2. 更新代码
cd /path/to/hermes-feishu-streaming-card
git checkout v3.8.18
pip install -e ".[test]" --upgrade

# 3. 诊断 Hermes hook strategy 与 anchors
python3 -m hermes_feishu_card.cli doctor \
  --config ~/.hermes_feishu_card/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent

# 4. 重新安装 hook
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes

# 5. 启动 sidecar
python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
```

`doctor` 会优先从 `VERSION` 或 Git tag `v2026.4.23+` 判断 Hermes 支持状态。Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x 与 `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` 应命中 `gateway_run_013_plus`；旧版本 Hermes 应命中 `legacy_gateway_run`。如果 Docker 镜像缺少 `VERSION` 和 `.git` 元数据，V3.8.6 会用 `gateway/run.py` anchor 兜底，输出 `version_source: gateway anchors`；如果 `VERSION` 或 Git tag 存在但格式不可解析，当前版本会在 anchors 可验证时输出 `VERSION + gateway anchors` 或 `git tag + gateway anchors`。若 `doctor --explain` 提示可自动修复，先执行 `repair --hermes-dir ... --yes` 再重新安装 hook。

## 核心功能

- **飞书流式卡片**：`message.started`、`thinking.delta`、`answer.delta`、`tool.updated`、`message.completed`、`message.failed` 汇聚到同一张卡片。
- **授权/选项交互**：Hermes approval、clarify choices 和独立 slash 命令优先显示为飞书按钮卡片；不可用时显示编号/文本 fallback，并交还 Hermes 原生文本选择流程。
- **多 bot 与群聊绑定**：`bots.items` 注册多个飞书机器人，`bindings.chats` 按 `chat_id` 路由，`group_rules` 用于群聊安全诊断；真实 @机器人触发和白名单准入仍由 Hermes Gateway 控制。
- **多 Profile 进程内隔离**：一个 sidecar 服务多个 Hermes profile，使用 `profile_id:message_id` 隔离 session。
- **Profile / Bot 卡片标题**：全局、profile、bot 均可设置标题，bot 级优先。
- **Cron 最终卡片与回复上下文**：cron 任务可发送最终卡片，reply card context 保留必要上下文。
- **附件摘要与原生媒体投递**：卡片内展示 attachment summaries，hook 不抑制 Hermes 原生媒体/文件投递路径。
- **DeepSeek 思维链兼容**：过滤 `<think>`/`</think>` 与 `<thinking>`/`</thinking>` 标签。
- **工具调用跟踪**：累计工具调用次数和每个工具的当前状态。
- **运行统计 footer**：显示耗时、模型、token、上下文占比；非终态卡片显示旋转生成中状态。
- **故障隔离**：sidecar 不可用时 hook fail-open，Hermes 原生文本继续运行。
- **安全安装/恢复**：安装器 fail-closed，`restore`/`uninstall` 检测文件改动后拒绝覆盖。

## 配置

复制 `config.yaml.example` 到本地使用，不要提交真实凭据。

**单 Profile 最小配置**

```yaml
server:
  host: 127.0.0.1
  port: 8765
feishu:
  app_id: ""
  app_secret: ""
card:
  title: Hermes Agent
  footer_fields: [duration, model, input_tokens, output_tokens, context]
```

**单 Profile + 多 Bot / 群聊**

```yaml
server:
  host: 127.0.0.1
  port: 8765
feishu:
  app_id: ""
  app_secret: ""          # fallback
bots:
  default: default
  items:
    sales:
      app_id: "cli_sales_xxx"
      app_secret: "xxx"
    support:
      app_id: "cli_support_yyy"
      app_secret: "yyy"
bindings:
  fallback_bot: default
  chats:
    oc_5cc6a25d8815790fa890dd0226005e83: sales
  group_rules:
    enabled: false
    require_mention: true
    allowed_chats: []
    allowed_users: []
card:
  title: Hermes Agent
  footer_fields: [duration, model, input_tokens, output_tokens, context]
```

**多 Profile**

```yaml
server:
  host: 127.0.0.1
  port: 8765
profiles:
  engineering:
    feishu:
      app_id: "cli_eng_xxx"
      app_secret: "xxx"
    bots:
      default: default
      items:
        default:
          app_id: "cli_eng_xxx"
          app_secret: "xxx"
    bindings:
      fallback_bot: default
      chats: {}
  sales:
    feishu:
      app_id: "cli_sales_xxx"
      app_secret: "xxx"
    bots:
      default: default
      items:
        default:
          app_id: "cli_sales_xxx"
          app_secret: "xxx"
    bindings:
      fallback_bot: default
      chats: {}
card:
  title: Hermes Agent
  footer_fields: [duration, model, input_tokens, output_tokens, context]
```

多 Profile 模式下，`FEISHU_APP_ID` / `FEISHU_APP_SECRET` 不会覆盖 profile 内的 `feishu` 配置。`footer_fields` 支持 `duration`、`model`、`input_tokens`、`output_tokens`、`context`、`subscription_usage`。其中 `subscription_usage` 默认关闭；显式加入后，完成态会通过 Hermes runtime 的 `fetch_account_usage("openai-codex")` 显示 `5h 26% · weekly 89%` 风格的剩余额度。旧 Hermes、未登录、网络错误或超时会静默跳过。

`card.text_sizes` 可配置 `body`、`reasoning`、`tool`、`notice`、`footer`。base、profile、bot 按角色合并，bot 优先级最高：

```yaml
card:
  text_sizes:
    body: large
    reasoning: small
    footer:
      default: x-small
      pc: x-small
      mobile: notation
```

映射字段只允许 `default`、`pc`、`mobile`。字号只允许 `heading-0`、`heading-1`、`heading-2`、`heading-3`、`heading-4`、`heading`、`normal`、`notation`、`xxxx-large`、`xxx-large`、`xx-large`、`x-large`、`large`、`medium`、`small`、`x-small`；`normal_v2` 是平台示例里的自定义 alias，不接受。未配置时保持原 Card JSON。卡片物理 width/height 由 Feishu/Lark 客户端控制。

## 飞书应用配置

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx

# 真实飞书 smoke 测试
python3 -m hermes_feishu_card.cli smoke-feishu-card \
  --config config.yaml.example \
  --chat-id oc_xxx
```

如果凭据未配置，sidecar 会使用 no-op client；进程仍可被 `start/status/stop` 管理，但 health 为 degraded，任何发卡事件都会明确返回 `not_sent`。真实联调前请检查：

```bash
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
```

确认 health 不是 degraded、`/health.routing.bot_count` 大于 0，且 `last_route_error` 为空。

## Hermes Gateway 流式配置

确保 Hermes `config.yaml` 中启用流式编辑：

```yaml
streaming:
  enabled: true
  transport: edit
```

不要设置 `display.platforms.feishu.streaming: false`。也不要把 `display.show_reasoning` 当成本插件的必需开关；它可能在最终回复中追加 reasoning 代码块，反而干扰卡片流式体验。若模型只返回最终答案、没有 thinking 增量，卡片会直接显示最终答案。

## CLI 命令

| 命令 | 说明 |
|------|------|
| `setup --hermes-dir ... --yes` | 一键安装：配置、检测、hook、sidecar、健康检查；确认 Hermes 替换了源码时可加 `--accept-hermes-upgrade` |
| `doctor --config ... --hermes-dir ...` | 诊断 Hermes 版本、runtime import、`hook_strategy`、`compatibility`、anchors 和原因；支持 `--explain` / `--json` |
| `install --hermes-dir ... --yes` | 安装插件到 Hermes runtime venv，并安装 hook；确认 Hermes 替换了源码时可加 `--accept-hermes-upgrade` 一步恢复并重装 |
| `repair --hermes-dir ... --yes` | 修复可验证的 hook manifest/backup 状态，不覆盖用户改动；真实升级源码变更需显式加 `--accept-hermes-upgrade` |
| `setup --repair ... --yes` / `--no-repair` | 自动修复已知安全状态，或显式关闭自动 repair |
| `restore --hermes-dir ... --yes` | 恢复原始 Hermes 文件 |
| `uninstall --hermes-dir ... --yes` | 卸载并恢复 |
| `start --config ...` | 启动 sidecar；Linux/systemd 优先使用独立 user service |
| `stop --config ...` | 停止 sidecar；校验 PID/token，并通过记录的 systemd unit 或进程身份安全停止 |
| `status --config ...` | 查看 sidecar 状态、routing、profile diagnostics 与 metrics |
| `smoke-feishu-card --profile-id ... --chat-id ...` | 按指定 profile 发送真实飞书 smoke 卡片 |
| `bots list|show|add|remove --config ...` | 管理飞书 Bot 注册 |
| `bots test --profile-id ... --chat-id ...` | 按指定 profile/bot 做真实飞书 bot smoke |
| `bots bind-chat|unbind-chat --config ...` | 管理 `bindings.chats` 群聊绑定 |

## 架构

```text
Hermes Gateway
  └─ minimal hook in gateway/run.py
       └─ hermes_feishu_card.hook_runtime
            └─ HTTP POST /events ——→  sidecar server
                                      ├─ CardSession 状态机
                                      ├─ render_card() 卡片渲染
                                      ├─ Feishu CardKit HTTP client 已实现
                                      ├─ tenant token / send / update
                                      ├─ 节流、合并、重试、锁、诊断
                                      └─ /health 指标
```

Hermes hook 将事件 fail-open 转发给 sidecar。sidecar 持有完整会话状态和飞书边界，可独立测试、重启、诊断。历史实现集中归档在 `legacy/`（`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等），不是 active runtime；当前主线以 `hermes_feishu_card/` 为准。迁移说明见 [docs/migration.md](migration.md)。

## 常见问题

- **卡片没有思考/不流式**：检查 `streaming.enabled: true` 与 `streaming.transport: edit`，确认模型确实输出 `thinking.delta`。
- **真实飞书没有卡片**：检查凭据是否进入 sidecar；没有凭据时是 no-op client。V3.6.2 会读取 config 同目录 `.env`，真实环境变量仍优先。
- **hook 已安装但 Hermes 卡片完全不工作**：跑 `doctor --explain`，查看 `Runtime import` 是否为 `ok`。如果 Hermes venv 不能 import `hook_runtime`，重新执行 `setup` 或 `install --hermes-dir ... --yes`。
- **卡片停在“思考中”**：看 `/health.diagnostics.last_terminal_event` 和 `feishu_update_failures`，确认终态事件是否到达、飞书 PATCH 是否成功。
- **出现灰色原生文本**：通常说明 sidecar 未成功接收或更新终态；V3.5.x 已补 queued follow-up suppression 和终态优先更新。
- **思考过程漏字/截断**：V3.5.x 已用 ordered send、append_block、PATCH 合并与终态优先修复；若仍出现，先检查 `/health.metrics.feishu_update_failures`。
- **长表格/代码显示成 raw markdown**：V3.5.x 会结构化拆分；如果仍异常，减少单个表格列宽或代码块长度。
- **重复卡片**：检查 `/health` metrics（`events_received`、`events_applied`、`feishu_send_successes`）。多 Profile 下 session key 为 `profile_id:message_id`。
- **多 Profile 路由不确定**：跑 `status --config ...`，查看 `routing.last_route`、`profile.<id>.events`、`profile.<id>.last_profile_source`，再用 `smoke-feishu-card --profile-id ...` 或 `bots test --profile-id ...` 定向验证。
- **Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x 升级后无卡片**：先跑 `doctor --config ... --hermes-dir ...`，确认 `hook_strategy` 为 `gateway_run_013_plus`。如果安装器报告当前无补丁源码与旧 backup 不同，并且你已确认这是有意的 Hermes 升级，执行 `install --hermes-dir ... --accept-hermes-upgrade --yes`；否则不要绕过默认拒绝。
- **恢复失败**：`restore`/`uninstall` 检测到文件改动会拒绝覆盖，先跑 `doctor --explain` 看 manifest/backup/run.py 状态；若提示可自动修复，执行 `repair --hermes-dir ... --yes`。只有确认 Hermes 升级替换了源码时才使用 `repair --hermes-dir ... --accept-hermes-upgrade --yes`；其他差异先备份再人工确认。
- **只想验证本地 sidecar**：可以用 no-op client 跑测试；真实飞书 smoke 需要真实 App ID/Secret 和 chat id。

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| [v4.0.13](release-notes-v4.0.13.md) | 2026-07-20 | 全部 Hermes slash command 的非空文本反馈统一卡片化，同命令多反馈原位更新，手动 `/compress` 显示运行态与终态 |
| [v4.0.12](release-notes-v4.0.12.md) | 2026-07-18 | Issues #133/#136：上下文压缩可见、五类字号与 PC/mobile 映射、selected env 凭据加载，以及 degraded Noop 健康状态和失败指标 |
| [v4.0.9](release-notes-v4.0.9.md) | 2026-07-16 | Issue #130：保持 live Lark WebSocket event handler identity，仅在 WS 线程更新卡片 callback；感谢 @Jasonsun77 提供完整 crash-loop 证据 |
| [v4.0.8](release-notes-v4.0.8.md) | 2026-07-16 | Issue #127：cron 卡片保留正文，Hermes 原生 `media_files` 链路继续上传真实附件；感谢 @zyq2552899783-lgtm 报告 |
| [v4.0.7](release-notes-v4.0.7.md) | 2026-07-16 | Linux/systemd sidecar 独立可重启 user service、Hermes venv Python 优先，以及 PR #124 自我改进通知卡片隔离 |
| [v4.0.6](release-notes-v4.0.6.md) | 2026-07-15 | Hermes 0.18.x terminal/queued completion、无灰色且可收束的 background 通知卡片，以及显式 fail-closed 的 Hermes 升级恢复 |
| [v4.0.0](release-notes-v4.0.0.md) | 2026-07-12 | 实时工具 preview Header、公开阶段输出正文、等待/失败/完成状态衔接与兼容降级 |
| [v3.10.0](release-notes-v3.10.0.md) | 2026-07-11 | 裸 `/resume` 原生会话下拉与模型 footer 安全语义色；布局和 Hermes 安全恢复路径不变 |
| [v3.9.1](release-notes-v3.9.1.md) | 2026-07-11 | 完成答案、打断终态、模型选择回调和 marker-only 安装恢复可靠性热修；普通 footer/layout 不变 |
| [v3.9.0](release-notes-v3.9.0.md) | 2026-07-11 | PR #84 / @Zanetach：卡片 progress-status 路由与 `.env` 白名单扩展的 profile 环境支持、运维安全修复/重启与 CLI fallback；普通流式卡的 footer/layout 保持不变 |
| [v3.8.18](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.18) | 2026-07 | PR #91：cron 卡片携带 `thread_id` 回到飞书话题原线程 |
| [v3.8.17](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.17) | 2026-07 | PR #77：cron `deliver=origin/all` 等路由意图会解析到 Feishu 目标并发送卡片 |
| [v3.8.16](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.16) | 2026-07 | issue #89 / PR #88：话题群复用 `message_id` 时第二条及后续消息重新发送卡片 |
| [v3.8.15](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.15) | 2026-07 | issue #82 后续复现：输入 `.docx/files` 上下文不再误触发重复原生最终 reply |
| [v3.8.14](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.14) | 2026-07 | issue #86 / PR #87，Feishu/Lark WebSocket 长连接下 agent clarify/approval `interaction.select` 按钮可原生回到 sidecar |
| [v3.8.13](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.13) | 2026-07 | Hermes `v2026.7.7.2` / `0.18.2` 升级兼容，anchor fallback 与 stale install state repair |
| [v3.8.12](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.12) | 2026-07 | issue #82，带 `colors.csv` / `styles.csv` 等附件摘要的完成卡片不再重复发送原生最终 reply |
| [v3.8.11](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.11) | 2026-07 | `/hfc status` 卡片接管后不再同时触发灰色 `Unknown command /hfc` 原生回复 |
| [v3.8.10](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.10) | 2026-07 | 群内 `/hfc status` 提示 chat binding、fallback/default 路由和 slash command 边界；工具详情展示参数摘要、耗时和失败原因 |
| [v3.8.9](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.9) | 2026-07 | 飞书/Lark 话题内卡片连续更新：后续流式事件和 `system.notice` 通过 `reply_to_message_id` 回到原卡片，避免 timeline 停住和灰色提示重复 |
| [v3.8.8](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.8) | 2026-07 | Hermes 原生系统提示卡片化：Working 心跳、上下文窗口/压缩提示、session reset、skill loading、自我改进 review 进入卡片或独立小卡片 |
| [v3.8.7](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.7) | 2026-07 | issue #75，新版 Hermes 缺少 `message.started` 时，`answer.delta` / `thinking.delta` / `tool.updated` / `message.completed` 首事件也会创建卡片 |
| [v3.8.6](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.6) | 2026-07 | issue #70，Docker/source-stripped Hermes 缺少 `VERSION` 时用 Gateway anchor 兜底，补齐 Hermes v0.18.0 / `v2026.7.1` 兼容 |
| [v3.8.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.5) | 2026-07 | 始终允许 `/new` 等直通命令的执行结果也保持 Feishu/Lark 卡片反馈，移除无效 interactive direct update |
| [v3.8.4](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.4) | 2026-07 | Feishu/Lark WebSocket 长连接 slash/model 原生命令卡片，修复 V3.8.3 本地 sidecar 只显示灰色文本 fallback 的问题 |
| [v3.8.3](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.3) | 2026-07 | 独立 slash 命令卡片，`/new`/`/reset`/`/undo` 确认、`/model` 选择卡片、`/update` 非交互边界和文本 fallback |
| [v3.8.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.2) | 2026-07 | 卡片 timeline 阅读体验补丁，pre-tool answer 延迟归档、终态正文去重、思考/工具折叠区层级区分、真实折叠/展开截图更新 |
| [v3.8.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.1) | 2026-07 | issue #74，高频 delta Gateway-side 合并、终态前 flush、飞书内 `/hfc` 只读诊断命令、summary/诊断上下文 hash 脱敏 |
| [v3.8.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.0) | 2026-07 | 卡片主回答与辅助 timeline 分离、工具摘要去重、terminal drain、Hermes runtime import 诊断修正、Docker 示例同步 |
| [v3.7.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.7.0) | 2026-06 | issue #70，补齐现有 Hermes Docker 容器内安装与更新路径 |
| [v3.6.6](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.6) | 2026-06 | issues #67/#68，中断/慢 PATCH 场景避免卡片和原生答复双发，doctor/install 提示 `hermes -V` 的真实 Project 目录 |
| [v3.6.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.5) | 2026-06 | issues #64/#65，Feishu thread `message_id` 归一化、DeepSeek completed-only 最终答案回填并更新同一卡片 |
| [v3.6.4](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.4) | 2026-06 | issues #61/#62，飞书 thread 卡片回复到原话题、cron `deliver: "feishu:oc_xxx"` 解析为卡片投递 |
| [v3.6.3](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.3) | 2026-06 | issues #56-#59，Hermes v0.17 `_run_agent_inner` hook、localhost 交互 text fallback、Telegram 隔离、Windows profile path |
| [v3.6.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.2) | 2026-06 | issue #53，安装到 Hermes runtime venv、doctor runtime import 检查、hook 失败 warning 诊断 |
| [v3.6.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.1) | 2026-06 | issue #47，支持 Hermes `0.15.x` 和无 `v` 前缀 VERSION，避免 `doctor --explain` 误报 unsupported |
| [v3.6.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.0) | 2026-06 | `doctor --json/--explain`、安全 `repair`、结构化媒体/文件摘要、多 profile 定向 smoke、routing profile diagnostics、Hermes 兼容矩阵 |
| [v3.5.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.5.2) | 2026-06 | 跨平台一行安装、Release 安装包、macOS `.env` 安全解析、uv/PEP 668 Python 安装适配、Windows installer CI 解析验证 |
| [v3.5.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.5.1) | 2026-06 | 流式更新排序与合并、飞书 JSON 2.0 按钮修复、queued follow-up 原生消息抑制、`.env` 凭据回退、README 首页重整 |
| [v3.5.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.5.0) | 2026-06 | 飞书按钮交互闭环、issue #41、PR #42、长表格/代码块结构拆分、thinking 漏字修复 |
| [v3.4.3](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.3) | 2026-05 | issue #39、Markdown 结构化切分、Hermes v0.14.0 / v2026.5.16+ 验证 |
| [v3.4.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.2) | 2026-05 | issue #31，避免并发 PATCH 和 sequence 竞争导致内容回退/漏字 |
| [v3.4.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.1) | 2026-05 | issue #25，Hermes v2026.5.7 fallback message id 生命周期一致 |
| [v3.4.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.0) | 2026-05 | Hermes 0.13.0+ 兼容、旧版本 strategy、issue #23、多 profile/multi bot、附件与回复上下文 |
| [v3.3.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.3.0) | 2026-05 | 多 Profile、DeepSeek 兼容、表格保护、Footer 动画、平台判断修复 |
| [v3.2.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.2.1) | 2026-04 | Accept-Encoding 修复 |
| [v3.2.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.2.0) | 2026-04 | 多 Bot 路由、群聊绑定、Bot CLI、路由诊断 |
| [v3.1.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.1.0) | 2026-04 | Sidecar 架构、流式卡片、健康端点、安装向导 |
| [v3.0.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.0.0) | 2026-04 | Sidecar-only 初始发布 |

完整更新日志：[CHANGELOG.md](../CHANGELOG.md)。

## 测试与验收

```bash
python3 -m pytest -q
```

本版本自动化覆盖配置加载、hook patch、Hermes runtime event、sidecar session、飞书按钮 callback、长表格/代码块切分、queued follow-up completion、cron deliver precedence 和文档约束。

已完成的人工/集成验收包括：真实 Feishu E2E 主链路、真实 Hermes Gateway E2E、真实飞书应用卡片验证、16k 长卡压力测试、`doctor -> install -> restore` 闭环、多 Profile 路由、DeepSeek 标签过滤。Feishu CardKit HTTP client 已实现，并通过 mock Feishu server 和真实飞书 smoke 验证。

## 文档

- 项目维护 Wiki：[docs/wiki](wiki/README.md)
- 架构说明：[中文](architecture.md) / [English](architecture.en.md)
- 事件协议：[中文](event-protocol.md) / [English](event-protocol.en.md)
- 安装安全：[中文](installer-safety.md) / [English](installer-safety.en.md)
- 迁移说明：[中文](migration.md) / [English](migration.en.md)
- 端到端验证：[中文](e2e-verification.md) / [English](e2e-verification.en.md)
- 发布准备：[中文](release-readiness.md) / [English](release-readiness.en.md)
- 测试说明：[中文](testing.md) / [English](testing.en.md)

## 贡献者

感谢以下贡献者对项目的改进：

- [gischuck](https://github.com/gischuck) — [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding 修复（V3.2.1 brotli 兼容）
- [gischuck](https://github.com/gischuck) — [PR #76](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/76) 思考与工具 timeline 体验建议与实现探索（V3.8.x）
- [fengs2021](https://github.com/fengs2021) — [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) 锁架构优化与更新间隔改进（V3.3.0）
- [colinaaa](https://github.com/colinaaa) — [PR #87](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/87) WebSocket `interaction.select` clarify/approval 卡片交互支持（V3.8.14）
- [colinaaa](https://github.com/colinaaa) — [PR #88](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/88) 话题群 `message_id` 复用下第二轮消息新卡片修复（V3.8.16）
- [colinaaa](https://github.com/colinaaa) — [PR #91](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/91) cron 结果回到飞书话题群原线程的 `thread_id` 路由修复（V3.8.18）
- [zayn-0101](https://github.com/zayn-0101) — [PR #77](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/77) cron `deliver=origin/all` 路由意图卡片投递修复（V3.8.17）
- [Zanetach](https://github.com/Zanetach) — [PR #84](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/84) 卡片 progress-status 路由与 `.env` 白名单扩展的 profile 环境支持（V3.9.0）
- [colinaaa](https://github.com/colinaaa) — [PR #93](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/93) 打断任务终态；[PR #97](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/97) 完成答案保留（V3.9.1）
- [charles5g](https://github.com/charles5g) — [PR #98](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/98) 模型选择异步 callback 与原卡更新（V3.9.1）
- [wjiemin49-ux](https://github.com/wjiemin49-ux) — [PR #52](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/52) loopback 健康检查代理诊断与修复方向（V3.9.1 采用）
- [colinaaa](https://github.com/colinaaa) — [Issue #94](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/94) 裸 `/resume` 原生选择器的需求、流程与安全边界（V3.10.0）
- [charles5g](https://github.com/charles5g) / jackmim — [PR #98](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/98) 模型 footer 语义色创意（V3.10.0，主线补充 HTML 转义）
- [tianqiii](https://github.com/tianqiii) — [Issue #107](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/107) Codex 订阅配额 footer 的需求、Hermes 原生接口方案与展示格式（V4.0.2）
- [zyq2552899783-lgtm](https://github.com/zyq2552899783-lgtm) — [Issue #127](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/127) cron 定时任务附件只显示文件名、未执行原生上传的报告（V4.0.8）
- [Jasonsun77](https://github.com/Jasonsun77) — [Issue #130](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/130) Linux 上安装 hook 前后 WebSocket 稳定性 A/B、3–6 分钟断连时间线、SDK 版本与上游 reconnect 缺陷关联证据（V4.0.9）

## 安全说明

默认 loopback 采用本机进程互信；非 loopback 必须显式启用 `allow_non_loopback` 并通过事件鉴权。不要把 App Secret、tenant token、真实 chat_id 提交到仓库。效果图仅用于展示卡片效果，生产凭据保存在本机配置或环境变量中。

## License

MIT License，详见 [LICENSE](../LICENSE)。
