# 历史版本更新

[返回 README](../README.md) · [完整 CHANGELOG](../CHANGELOG.md)

| 版本 | 重点 |
|---|---|
| [v4.6.8](release-notes-v4.6.8.md) | macOS 安装与恢复提示，明确自主管理登录启动和未知进程归属 |
| [v4.6.7](release-notes-v4.6.7.md) | 保留可编辑心跳，紧凑审批按钮与完整正文 |
| [v4.6.6](release-notes-v4.6.6.md) | 审批回执确认后精简重复、运行工具可见与原生通知自动收尾 |
| [v4.6.5](release-notes-v4.6.5.md) | 重启通知持久归属、工具调用去重、可选时间线显示与模型报错去重 |
| [v4.6.4](release-notes-v4.6.4.md) | 首次按钮接线、顺序续答、可选阅读预设与作用域通知清理 |
| [v4.6.3](release-notes-v4.6.3.md) | 实时思考正文开关、工具耗时与中断用量 |
| [v4.6.2](release-notes-v4.6.2.md) | 原生插件共存维护证明与可选终态工具区 |
| [v4.6.1](release-notes-v4.6.1.md) | Hermes 0.21.3 hook、状态撤回和审批展示修复 |
| [v4.6.0](release-notes-v4.6.0.md) | 撤回路由、结构化思考、重试时限与卡片重启恢复 |
| [v4.5.2](release-notes-v4.5.2.md) | 排队任务失败保留正文、心跳提示安全撤回 |
| [v4.5.1](release-notes-v4.5.1.md) | 修复 CardKit 长 ID、话题投递、审批状态与重启反馈 |
| [v4.5.0](release-notes-v4.5.0.md) | 话题通知与手机交互修复，CardKit 流式更新、正文提及和审批暂停 |
| [v4.4.6](release-notes-v4.4.6.md) | 恢复终局投递，兼容新 Hermes 附件契约，保留未完成状态与交互内容 |
| [v4.4.5](release-notes-v4.4.5.md) | 修复失败与被替代任务误报完成；支持已验证的拆分账本契约，补强稳定性测试规则 |
| [v4.4.4](release-notes-v4.4.4.md) | 修复 Hermes 重启/关闭通知从飞书话题错投父群主会话，并让启动期路由 hook 在 boot 通知前生效 |
| [v4.4.3](release-notes-v4.4.3.md) | 兼容携带旧 owned hook 的 Hermes 升级、保留本机源码定制的完整性快照，并隐藏零思考/零工具的空 timeline |
| [v4.4.2](release-notes-v4.4.2.md) | Hermes 0.21 完整性迁移、无 Git 元数据源码安装、multiplex adapter 与审批交互修复 |
| [v4.4.1](release-notes-v4.4.1.md) | Hermes 0.21 facade 拆分安装兼容、话题后续回复、单进程多 profile、完整审批命令与可选思考代码块、实际 provider 页脚、CodeQL 更新 |
| [v4.4.0](release-notes-v4.4.0.md) | 基于新版 Hermes `COMMAND_REGISTRY` 的飞书原生能力中心、分类/详情/安全快捷命令与 KPI 可视化；支持 `/bg`、`/btw`、`/plan` 等新契约，并加入真实 backlog 指标和极端 Markdown 安全折叠 |
| [v4.3.8](release-notes-v4.3.8.md) | `setup` 能力就绪时默认启用开机常驻、不可用时明确 transient 风险；修复 batch clarify 下一题 sequence 竞态，并让远程 Feishu/Lark HTTP 请求遵循 proxy 环境变量而本机/私网继续绕过 |
| [v4.3.7](release-notes-v4.3.7.md) | 兼容 Hermes 2026-08-25 core 的 session-scoped delivery filters；安装器严格接受 `session_key=session_key` 新调用，同时保留旧调用并拒绝其他关键字形态 |
| [v4.3.6](release-notes-v4.3.6.md) | 修复无 reply anchor 的话题 create 路径使用非法 `receive_id_type=thread_id` 导致的 `99992402`；approval/clarify 交互卡与 completion notification 支持可配置地 `@` 发起人，并保持 schema 2.0 主卡 owner 不变 |
| [v4.3.5](release-notes-v4.3.5.md) | 兼容 Hermes v2026.8.3 Feishu adapter 的 `edit_message` 无 `metadata` 形参：wrapper 只移除原方法明确不支持的内部 metadata，支持 metadata/`**kwargs` 的 adapter 继续透传，无关未知参数仍正常抛出 `TypeError` |
| [v4.3.4](release-notes-v4.3.4.md) | 修复 runtime interaction listener 启动时的 reverse-DNS 阻塞与未关闭 listener 导致的 CLI 退出挂起；V3 Hybrid 安装改由 V3 inspector 驱动 `doctor --json`，避免误报 Legacy manifest/hash/path 问题 |
| [v4.3.3](release-notes-v4.3.3.md) | 首回复建 thread 时固定 reply anchor 与 `reply_in_thread` placement；completion notification 保持同一 thread，显式 thread 回复缺 anchor 则 fail-closed，绝不退回群聊顶层文本 |
| [v4.3.2](release-notes-v4.3.2.md) | 修复 Issue #227：schema 2.0 流式卡与 legacy 交互卡保持稳定双轨，避免 clarify/approval 完成后触发 `230099/200800`；Gateway 拒绝把 schema 2.0 卡作为 callback raw card，避免 `200673` |
| [v4.3.1](release-notes-v4.3.1.md) | 修复 Hermes 0.20 / 飞书 WebSocket 下 clarify/approval 点击后 runtime 已继续但卡片流式更新消失的问题；修复 text fallback 首次回复不唤醒；修复 v4.3.0 persistent service identity、systemd 工作目录与 tokenless health 对账 |
| [v4.2.11](release-notes-v4.2.11.md) | 修复 Issue #202：新交互卡发送成功后，旧流式卡会冻结为绿色“已转入交互卡片”历史快照；旧卡 PATCH 失败保持 fail-open，只有最新卡继续接收选择与后续更新 |
| [v4.2.10](release-notes-v4.2.10.md) | 非回环 sidecar 的回调/结果读取使用 method/path/body 绑定 HMAC；交互绝对过期会拒绝晚到按钮与表单并刷新原卡；跨平台 CI、CodeQL、Dependabot 和 Node 24 Action SHA 门禁同步落地，上一版见 [v4.2.9](release-notes-v4.2.9.md) |
| [v4.2.8](release-notes-v4.2.8.md) | 修复 `install.sh`、`install-docker.sh` 与 `install.ps1` 只在当前进程使用环境凭据、未持久化到私有 `.env` 的安装契约缺口 |
| [v4.2.7](release-notes-v4.2.7.md) | 修复 Issue #193 的 Windows 冷启动探针超时与旧 manifest 反斜杠路径，合入 PR #180 的 parent `HERMES_HOME` 查找和 PR #181 的 detached runner PID 安全重绑，并让 PowerShell 安装器正确传播失败 |
| [v4.2.6](release-notes-v4.2.6.md) | 修复 Issue #187 重复选项卡位置、#188 终态短后记覆盖正文、#189/PR #190 Hermes 0.20 exact Base 兼容，并修复飞书裸 `/update` 的 venv symlink、慢 fetch 与 Hermes 0.20 版本误报；上一版审查安全热修见 [v4.2.5](release-notes-v4.2.5.md) |
| [v4.2.4](release-notes-v4.2.4.md) | 修复飞书/Lark 话题中连续引用同一消息时复用旧 session、覆盖首张回复卡的问题；每条新消息创建独立卡片，同一轮流式更新仍通过 reply alias 关联 |
| [v4.2.3](release-notes-v4.2.3.md) | 修复 WebSocket hook 转发 `/update` 按钮动作时遗漏 `update_evidence_fingerprint` 的问题，使 sidecar 能完成证据绑定的确认/取消状态转换；缺失或不匹配证据仍 fail-closed |
| [v4.2.2](release-notes-v4.2.2.md) | 修复 `/update` 确认卡按钮回调只更新服务端状态、未 PATCH 原卡片的问题；取消会进入“已取消更新”终态且绝不启动 updater，确认会先显示准备更新再启动维护任务 |
| [v4.2.1](release-notes-v4.2.1.md) | 修复 Gateway 重启后首个 heartbeat 未绑定 live runner，确保第一条私聊裸 `/update` 即可获得完整任务计数证据；缺失计数仍 fail-closed |
| [v4.2.0](release-notes-v4.2.0.md) | 飞书私聊裸 `/update` 经 120 秒确认后，使用独立维护进程运行官方 Hermes updater，并自动恢复同版本 HFC、钩子、sidecar 与 Gateway；群聊和参数化命令保持 Hermes 原行为 |
| [v4.1.4](release-notes-v4.1.4.md) | 修复 Issue #171：Windows 上旧版 owned hook 与 backup 存在、manifest 缺失时，官方 install/setup 可在逐字验证 gateway、cron 与 exact Base 证据后安全重建 manifest；块外改动继续 fail-closed |
| [v4.1.3](release-notes-v4.1.3.md) | 修复 Issue #158 的同 target fence binding 收敛；合入 PR #168 的原生 delta 回调选择；修复 Issue #169 中 Hermes `TurnRunner` 重构造成的 tool/streaming/interaction hook 丢失与 doctor 误报 |
| [v4.1.0](release-notes-v4.1.0.md) | 按会话精确选择原生/卡片投递；第 6 张及后续表格默认无损 compact；认证 runtime 完整性监控与 strict repair；四种显式 sidecar manager，`auto` 不提权；后续修复见 [v4.1.1](release-notes-v4.1.1.md) 和 [v4.1.2](release-notes-v4.1.2.md) |
| [v4.0.21](release-notes-v4.0.21.md) | Issue #155：仅显式 `answer -> tool` 边界归档答案，避免 post-tool 最终答案被移入 timeline；Issue #147 真实飞书验收已观测到 completion card + native image、无匹配原生重复或 uncertain-delivery warning，UI 与配置不变 |
| [v4.0.20](release-notes-v4.0.20.md) | 修复 Issue #153：已有卡片的 notice 异步更新返回 `accepted`，不再误报投递未知；真实 PATCH 失败保留脱敏指标和错误码 |
| [v4.0.19](release-notes-v4.0.19.md) | 修复 one-line installer 在 Hermes venv 中误用 `pip --user`、并确保 pip 失败时立即停止，避免“显示升级但仍运行旧版本” |
| [v4.0.18](release-notes-v4.0.18.md) | 检测 Hermes Feishu SDK 的真实构造能力；旧版 `lark-oapi` 会被 doctor 明确诊断，并由 setup/install 自动修复 |
| [v4.0.17](release-notes-v4.0.17.md) | 并行同名工具按真实调用 ID 独立关联，调用计数不再重复，详情不再残留第二个耗时 |
| [v4.0.16](release-notes-v4.0.16.md) | 去除初始 Header/正文加载文案重复；工具开始后空正文不再保留加载占位，并恢复真实工具耗时显示 |
| [v4.0.15](release-notes-v4.0.15.md) | 修复 Issue #141：工具事件改为紧凑语义时间线并增加真实加载动画；CLI 主动识别 Hermes 升级覆盖 hook |
| [v4.0.14](release-notes-v4.0.14.md) | 修复 Issue #142：长任务 orphan heartbeat 保持运行态并按原始消息锚点更新同一卡，最终完成事件继续收束该卡 |
| [v4.0.13](release-notes-v4.0.13.md) | 所有 Hermes slash command 的非空文本反馈统一进入独立命令卡；多条反馈更新同一卡，手动 `/compress` 原位显示运行态与终态，失败精确回退原生文本 |
| [v4.0.12](release-notes-v4.0.12.md) | Issue #133：上下文压缩阶段可见、正文/思考/工具/提示/footer 字号可配置；Issue #136：selected env 凭据加载与显式 degraded Noop 诊断 |
| [v4.0.11](release-notes-v4.0.11.md) | 修复 Issue #135：初始卡片使用稳定 UUID 有界重试，并按 `delivered/not_sent/unknown` 安全选择抑制、原文回退或通用提示 |
| [v4.0.10](release-notes-v4.0.10.md) | 收紧 sidecar 事件传输边界：非回环监听必须显式授权并启用 HMAC-SHA256 防伪与防重放，本机回环安装保持兼容 |
| [v4.0.9](release-notes-v4.0.9.md) / [v4.0.8](release-notes-v4.0.8.md) | 修复 Issue #130 的 live WebSocket handler 身份与 Issue #127 的 cron 原生附件投递 |
| [v4.0.7](release-notes-v4.0.7.md) | Linux/systemd sidecar 使用独立可重启 user service，升级时优先选择 Hermes venv Python；合入 PR #124 修复自我改进通知误占下一轮卡片 |
| [v4.0.6](release-notes-v4.0.6.md) | 修复 Hermes 0.18.x 完成 hook、队列完成 hook，以及无灰色原生输出且可正确收束的 background 通知卡片；新增显式且 fail-closed 的 Hermes 升级恢复 |
| [v4.0.5](release-notes-v4.0.5.md) | 修复升级后 Gateway venv 仍加载旧插件的问题；安装器会比较 runtime 版本、自动同步并在安装后复核版本与路径 |
| [v4.0.4](release-notes-v4.0.4.md) | 修复 Markdown `MEDIA:` 字面量、SDK 预绑定旧 callback 的交互转发，以及 Codex 只返回单个限额窗口时的错误 `5h` 标签 |
| [v4.0.3](release-notes-v4.0.3.md) | 修复仅升级包并重启、但仍保留 V4.0.0 completion hook 时的媒体回答灰色正文重复；匹配正文只抑制一次，原生图片/文件继续发送 |
| [v4.0.0](release-notes-v4.0.0.md) | 运行态 Header 实时显示 Hermes 工具 preview，正文独立流式显示公开阶段输出；等待、失败、完成状态自然衔接并保持现有 Footer/引用边界 |
| [v3.10.0](release-notes-v3.10.0.md) | 裸 `/resume` 使用原生会话下拉卡并沿用 Hermes 安全恢复路径；模型 footer 增加转义后的轻量语义色，不改变布局和字段顺序 |
| [v3.9.1](release-notes-v3.9.1.md) | 可靠性热修：完成答案不截断、打断任务终态串行化、模型选择回调异步化，以及可验证的 marker-only 安装损坏恢复；普通流式卡 footer/layout 保持不变 |
| [v3.8.18](release-notes-v3.8.18.md) | cron 卡片携带 `thread_id` 回到飞书话题原线程（PR #91，贡献者 @colinaaa） |
| [v3.8.17](release-notes-v3.8.17.md) | cron `deliver=origin/all` 等路由意图会解析到飞书目标并发送卡片 |
| [v3.8.16](release-notes-v3.8.16.md) | 话题群连续消息复用 `message_id` 时，第二条及后续消息会重新发送卡片 |
| [v3.8.15](release-notes-v3.8.15.md) | 输入 `.docx/files` 上下文只做卡片附件摘要，不再放行重复原生最终 reply |
| [v3.8.14](release-notes-v3.8.14.md) | WebSocket 长连接下 agent clarify/approval 按钮通过 `interaction.select` 原生 card action 闭环 |
| [v3.8.13](release-notes-v3.8.13.md) | Hermes `v2026.7.7.2` / `0.18.2` 升级后可用 anchors 兜底并修复 stale install state |
| [v3.8.12](release-notes-v3.8.12.md) | 修复带 `colors.csv` / `styles.csv` 等附件摘要的完成卡片仍重复发送原生 reply 的问题 |
| [v3.8.11](release-notes-v3.8.11.md) | `/hfc status` 卡片接管后不再同时触发灰色 `Unknown command /hfc` 原生回复 |
| [v3.8.10](release-notes-v3.8.10.md) | 群聊 `/hfc status` 自动提示 chat binding 与 slash command 边界；工具详情显示参数、耗时和失败原因 |
| [v3.8.9](release-notes-v3.8.9.md) | 飞书/Lark 话题内卡片连续更新，`system.notice` 不再重复外溢 |
| [v3.8.8](release-notes-v3.8.8.md) | Hermes 原生系统提示卡片化：Working、上下文压缩、skill loading、自我改进 review |
| [v3.8.7](release-notes-v3.8.7.md) | 新版 Hermes 缺少 `message.started` 时也能从首个 delta/completed 事件创建卡片 |
| [v3.8.6](release-notes-v3.8.6.md) | Docker/source-stripped Hermes 缺 `VERSION` 时用 Gateway anchors 兜底，兼容 Hermes v0.18.0 |
| [v3.8.5](release-notes-v3.8.5.md) | 直通 slash command 的执行结果以交互卡片反馈 |
