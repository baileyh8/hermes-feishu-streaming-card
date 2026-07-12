# Hermes Feishu Streaming Card — 主线任务清单

当前 active runtime 是 `hermes_feishu_card/`。legacy adapter、dual mode、旧 `sidecar/`、旧 `patch/` 和 `installer_v2.py` 不是 active runtime，仅保留作历史参考。

## V3.8 / V3.9 / V3.10 / V4.0 系列路线：V3.8.0 / V3.8.1 / V3.8.2 / V3.8.3 / V3.8.4 / V3.8.5 / V3.8.6 / V3.8.7 / V3.8.8 / V3.8.9 / V3.8.10 / V3.8.11 / V3.8.12 / V3.8.13 / V3.8.14 / V3.8.15 / V3.8.16 / V3.8.17 / V3.8.18 / V3.9.0 / V3.9.1 / V3.10.0 / V4.0.0

详细路线见 [docs/superpowers/specs/2026-06-30-v3-8-design.md](docs/superpowers/specs/2026-06-30-v3-8-design.md) 和 [docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md](docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md)。

### V4.0.0：实时双轨 Agent 卡片（已发布）

- [x] 运行态 Header title 保留用户配置名，subtitle 将 Hermes 工具名与最新非空 `tool.updated.detail` 整理为确定性动作摘要，工具间隙保留上一条。
- [x] 正文显示公开 `thinking.delta` 阶段输出，`answer.delta` 开始后主回答优先。
- [x] 等待态问题进入 Header，交互完成恢复动作摘要；失败保留摘要；普通聊天完成态只保留飞书原生回复引用。
- [x] 运行、等待、失败 Footer 只显示状态，完成态显示最终统计。
- [x] preview 单行化、长度限制和敏感参数脱敏；无 preview 时兼容旧布局。
- [x] 真实飞书私聊/群聊、四状态截图与本地发布包 smoke 通过；`/model` Provider、返回、切换和同卡回写通过。
- [x] `v4.0.0` tag、GitHub Release、macOS/Linux/Windows/checksums 四个 assets 与公共 tag 安装验证通过。

### V3.10.0：原生会话恢复与轻量视觉增强（已发布）

- [x] Issue #94 / @colinaaa：裸 `/resume` 使用原生 `select_static` 会话选择器，带参数命令保持 Hermes 原行为。
- [x] 选择回调即时 ACK，后台复用 original Hermes resume handler；权限、continuation、agent release 和 override cleanup 不重复实现。
- [x] 私聊不额外比较操作者；群聊/topic 必须由发起者 `open_id` 点击，身份不可验证时 fail-open 到文本列表。
- [x] PR #98 / @charles5g / jackmim：采用模型 footer 语义色创意，增加 HTML escape；footer/layout、字段顺序与字号不变。
- [x] V3.9.1 相关 issue/PR 与 2026-07-11 旧队列已完成证据化回复和关闭，仅 #94 留待 V3.10.0 发布收口。
- [x] Python 3.9 / 3.12 release gate 均为 `1216 passed, 3 skipped`，`git diff --check` 通过。
- [x] 真实 Feishu 私聊、群聊发起者和 topic 原线程 smoke 通过；换操作者拒绝因测试群仅一位真人，保留自动化回归证据。
- [x] `v3.10.0` tag、GitHub Release 与 macOS/Linux/Windows/checksums 四个资产验证通过。

### V3.9.1：可靠性热修（已发布）

- [x] Issue #96 / PR #97 / @colinaaa：完成事件携带有效 suffix 时保留完整最终答案，同时保持原生重复 reply suppression。
- [x] Issue #92 / PR #93 / @colinaaa：打断旧任务时先 drain 更新队列，再串行写入 abandoned 终态，迟到 PATCH 不再覆盖终态。
- [x] PR #98 / @charles5g：模型选择 callback 即时 ACK，后台切换并优先更新原卡，失败时只发送一张 fallback 卡。
- [x] Issue #82：对 manifest/backup 可验证且仅 owned marker 行损坏的状态安全恢复；未知用户编辑继续 fail-closed。
- [x] PR #52 / @wjiemin49-ux：采用 loopback 健康检查应绕过环境代理的诊断方向，并修复 tools package 语法。
- [x] source-stripped Hermes 的诊断文案明确显示 metadata 缺失，不伪造版本号。
- [x] 普通流式卡 footer/layout 保持不变。
- [x] Python 3.9 / 3.12 release gate 均为 `1198 passed, 3 skipped`，`git diff --check` 通过。
- [x] v3.9.1 tag、GitHub Release 与四个 release assets 按发布流程完成。
- [x] 完成相关 issue/PR 的证据化回复与状态收口。

### V3.9.0：运维与可靠性基础（已完成自动化与文档）

- [x] 运维卡覆盖诊断、重新检测、两步安全修复和重启确认；私聊不比较操作者，群聊修复/重启仅允许发起者确认；卡片不可用时保留 CLI fallback。
- [x] 运输认证零配置：secret 位于权限为私有的 sidecar state-dir transport root，不写入 config 或环境变量。
- [x] PR #84 / @Zanetach 随 V3.9.0 完成：卡片 progress-status 路由与 `.env` 白名单扩展的 profile 环境支持。
- [x] 已知安全的 manifest/backup 状态支持自动 repair，可用 `--no-repair` 关闭；不可验证的用户编辑继续拒绝覆盖。
- [x] lifecycle cleanup 与有界 metrics 覆盖 runtime state；Hermes/Docker 兼容由自动化回归覆盖。
- [x] 运维按钮 WebSocket 回调即时 ACK；认证动作通过有界后台队列重试转发，所有响应由 sidecar PATCH 原卡，慢 PATCH 不阻塞 recheck/repair/restart。
- [x] Release gate 证据：Python 3.9 / 3.12 均为 `1172 passed, 3 skipped`；普通卡片 footer/layout 不变。
- [x] 2026-07-11 真实飞书私聊：`/hfc doctor` 无灰色未知命令，中文详情、连续两次 recheck（含后台 successor）在 156–201 ms 内 ACK 且无回调超时提示；sandbox 两步安全修复、卡片实际重启 Gateway 和普通流式完成卡通过，发送/更新零失败。
- [x] 2026-07-11 真实 Feishu cron：no-agent 一次性任务结果进入普通完成卡，sidecar 接收/应用/发送成功且无 fallback；Hermes `cron run` 的一次性任务删除后状态误报记为上游问题，不扩大插件 patch 面。
- [x] 2026-07-11 profile route mismatch：临时错误 profile 被诊断为 `profile_unknown` 且 route chain 脱敏；移除临时环境后恢复默认 profile，持久配置未变。
- [ ] 待真实验收：existing-container Docker、群聊发起者与换操作者拒绝、topic。

### V3.8.0：卡片体验与流式稳定性（已完成）

- [x] 主回答与 reasoning / tool timeline 分离，默认突出最终答案。
- [x] burst update coalescing 收敛高频 PATCH，减少快速 thinking / tool burst 下的重复更新。
- [x] terminal completion 前 drain pending updates，避免终态卡片被陈旧中间态覆盖。
- [x] 长 Markdown 表格和 fenced code block 跨卡片分块时保持结构安全。
- [x] 可观测性补充 update queue length、coalesce count、terminal drain latency、Feishu API latency。

### V3.8.1：高频流式修复与只读诊断（已完成）

- [x] issue #74：Gateway runtime 内合并高频 `thinking.delta` / `answer.delta`，降低 Hermes stream-reader 热路径压力。
- [x] terminal event 前 flush 同一消息 pending delta，避免最终卡片缺少尾部内容。
- [x] 飞书内提供 `/hfc help`、`/hfc status`、`/hfc doctor`、`/hfc monitor` 只读诊断命令。
- [x] 安全清理：`/messages/{message_id}/summary` 返回中的 `chat_id` / Feishu `message_id` 改为 hash。
- [x] patcher 兼容 V3.8.0 及更早无命令 hook block 的升级和卸载。

### V3.8.2：卡片 timeline 阅读体验补丁（已完成）

- [x] pre-tool answer 先停留在正文区，下一段 answer 或终态到来时再归档进“思考与工具”。
- [x] 完成态正文剥离已归档的中间说明，只保留最终答案。
- [x] raw `thinking.delta` 继续隐藏，不混入正文区或用户可见 timeline。
- [x] 折叠区中思考和工具使用不同字号与灰度层级，工具详情更紧凑。
- [x] README 增加 V3.8.2 折叠态和展开态真实截图。

### V3.8.3：独立命令卡片（已完成）

- [x] 明确职责边界：Agent 原卡片只承接授权、clarify / 对话选项等当前任务内交互；slash command 使用独立命令卡片。
- [x] `/new`、`/reset`、`/undo` 以及 `/model <model>` 高成本模型确认走独立三按钮卡片，点击后执行 Hermes 原 handler，并把结果更新回同一张命令卡片。
- [x] `/model` 无参数选择器走独立模型选择卡片；用户选择后调用 Hermes 原 `on_model_selected` callback，并在同一卡片展示切换结果。
- [x] sidecar 不可用、卡片未发送或配置为文本模式时保留 Hermes 原生 text fallback。
- [x] `/update` 不做交互卡片；后续单独评估后台升级完成/失败通知是否可靠送达飞书。
- [x] 真实 Hermes + Feishu 本地 smoke：重启 Gateway 后 `/new` 已出现 Feishu/Lark WebSocket 原生按钮卡；原生卡片可用时跳过 sidecar 预交互，避免重复选择卡。

### V3.8.4：Feishu WebSocket 命令卡片热修（已完成）

- [x] 修正 V3.8.3 在 Feishu/Lark WebSocket 长连接部署下 `/new`、`/reset`、`/undo` 仍退回灰色文本的问题。
- [x] 动态补上 Feishu adapter `send_slash_confirm(...)`，按钮点击经 `_on_card_action_trigger` 调用 Hermes `tools.slash_confirm.resolve(...)`。
- [x] `/model` 无参数选择器改走 Feishu 原生 interactive card，点击后执行 Hermes 原 `on_model_selected` callback 并回写同一卡片。
- [x] WebSocket 原生卡片可用时跳过 sidecar `interaction.requested` 预卡片，避免 `/new` 同时出现两张选择卡。
- [x] 修复旧安装标记残留导致 `send_slash_confirm(...)` 未真实挂载的问题，并为原生卡片发送失败补本地 warning。
- [x] 保留 Hermes 原生文本 fallback：Feishu 原生卡片不可用、sidecar 不可用或回调失败时不阻断命令。
- [x] 补齐 slash/model WebSocket 卡片发送与 action 解析回归测试。

### V3.8.5：命令结果反馈卡片补丁（已完成）

- [x] 修正 `destructive_slash_confirm: false` 或已始终允许时 `/new` 直通执行结果退回灰色原生文本的问题。
- [x] 在 patcher 的 command-card hook 中传入当前 `event`，让 hook runtime 能识别独立 slash command 的返回结果。
- [x] Feishu adapter `send()` 只对 `/new`、`/reset`、`/clear`、`/undo`、`/stop` 和直接 `/model <model>` 的结果做一次性卡片化。
- [x] `/update` 保持 Hermes 后台升级命令，不纳入命令结果卡片化。
- [x] 移除 card action 后额外调用 direct interactive `message.update` 的路径，改由 Feishu callback response 更新原卡片。
- [x] 补齐 `/new` 直通结果卡片、一次性上下文、`/update` 保持普通路径和 V3.8.4 hook block 升级兼容测试。

### V3.8.6：Docker / Hermes v0.18.0 兼容补丁（已完成）

- [x] issue #70：Docker/source-stripped Hermes 缺少 `VERSION` 和 `.git` 元数据时，`doctor` / `install` / `setup` 用 `gateway/run.py` anchor 兜底识别。
- [x] Hermes `v2026.7.1` / `0.18.0` / `v0.18.0` 加入兼容矩阵，继续使用 `gateway_run_013_plus`。
- [x] 显式非法 `VERSION` 仍 fail-closed，只对缺失版本元数据启用 anchor fallback。
- [x] README 首屏换成真实横向效果展示图，覆盖命令交互、命令结果反馈和工具 timeline。

### V3.8.7：缺失 message.started 的新版 Hermes 流修复（已完成）

- [x] issue #75：新版 Hermes 首事件可能直接是 `answer.delta` / `thinking.delta` / `tool.updated` / `message.completed`，sidecar 不再因没有 session 而全部 ignored。
- [x] 将普通消息 delta/tool/completed 首事件纳入 session 创建路径，收到首事件即可发送初始 Feishu/Lark 卡片。
- [x] 保持既有 `message.started`、interaction、cron completion 和终态诊断逻辑兼容。

### V3.8.8：Hermes 原生系统提示卡片化（已完成）

- [x] 将 Hermes 原生灰色提示归一为 `system.notice` 事件，覆盖 `Working` 心跳、上下文窗口/压缩提示、自动 session reset、skill 加载、自我改进 review 等轻量运行状态。
- [x] 当前对话运行中的提示优先并入现有飞书卡片的“思考与工具”区域；当前卡片不可更新或任务外提示则以独立小卡片发送。
- [x] 长运行心跳类提示支持同一 notice 更新，避免每次 heartbeat 都新增一条灰色消息或重复卡片。
- [x] 保留 sidecar 失败时的原生文本 fallback，不阻断 Hermes 自身发送链路。
- [x] 补单元/集成测试：事件 schema、session timeline、独立 notice 卡片、Feishu adapter `send` / `edit_message` 拦截与 fallback。
- [x] 本地 Hermes runtime 安装、Gateway/sidecar 重启、真实 Lark「奥妹」sidecar smoke：独立 notice 卡片和当前会话 notice timeline 均返回 applied；用户确认进入发版流程。

### V3.8.9：飞书话题卡片连续更新补丁（已完成）

- [x] 飞书/Lark 话题回复中，后续流式事件即使使用不同内部 `message_id`，也能通过 `reply_to_message_id` 回到原卡片 session。
- [x] `tool.updated` / `answer.delta` / `thinking.delta` / `message.completed` 在话题场景继续更新同一张卡片，不新增重复卡片。
- [x] `system.notice` 在话题内优先进入当前卡片 timeline，避免卡片内外同时出现同一条系统提示。
- [x] hook runtime 保留 Hermes Relay `source.message_id` 作为原始 Feishu reply anchor，覆盖真实 WebSocket 长连接话题元数据。
- [x] 补齐 topic stream/tool、topic `system.notice` 和 hook runtime reply anchor 回归测试。

### V3.8.10：群聊能力与工具详情增强（已完成）

- [x] 工具调用详情支持参数摘要、耗时和失败原因，并继续用紧凑 timeline 渲染。
- [x] `bindings.group_rules` 从占位升级为安全诊断输入，记录 enabled、require_mention、allowed counts，不泄漏真实 chat/user id。
- [x] 群内 `/hfc status` 提示当前 chat binding、fallback/default 路由、建议 `bots bind-chat` 命令和群内 slash command 行为边界。
- [x] 明确 @机器人触发和白名单准入仍由 Hermes Gateway 控制，sidecar 只负责卡片路由、诊断和已接管消息的呈现。
- [x] 补齐 session/render/hook/bot/server 回归测试。

### V3.8.11：`/hfc` 原生未知命令抑制补丁（已完成）

- [x] `/commands` 接受 `/hfc status` 后快速返回 `handled: true`，真实 Feishu/Lark 卡片发送转后台执行。
- [x] Gateway patch 在 Hermes 原生 unknown slash fallback 前拦截 `/hfc`，避免卡片和灰色 `Unknown command /hfc` 双发。
- [x] hook runtime 从真实 Gateway `event.text` / `event.content` 补读 slash command 文本。
- [x] 补齐慢 Feishu 发送、真实 event 文本解析和早期 patch 插入位置回归测试。

### V3.8.12：附件摘要重复 reply 抑制补丁（已完成）

- [x] issue #82：带 `colors.csv` / `styles.csv` 等附件摘要的完成卡片不再触发整段原生最终 reply。
- [x] completed event 增加 `native_delivery` 判定，区分普通卡片摘要和真实原生媒体/文件投递需求。
- [x] `MEDIA:/tmp/...`、本地文件路径、`files`、`media_files` 和 image/audio/video locals 继续保留 Hermes 原生投递路径。
- [x] 补齐附件摘要、真实媒体路径、patcher suppression guard 和 installed hook event payload 回归测试。

### V3.8.13：Hermes 升级兼容补丁（已完成）

- [x] Hermes `v2026.7.7.2` / `0.18.2` 加入兼容矩阵，四段 Git tag 继续使用 `gateway_run_013_plus`。
- [x] 版本解析支持描述型 metadata，例如 `Hermes Agent v0.18.2 (...)`。
- [x] `VERSION` / Git tag 可读但不可解析时，只要 `gateway/run.py` anchors 可验证，就用 `VERSION + gateway anchors` / `git tag + gateway anchors` 兜底。
- [x] Hermes 升级后 `run.py` 已变成未打补丁上游文件但旧 backup/manifest 残留时，`repair` 会清理 stale install state，随后可重新 `install`。
- [x] 补齐四段 tag、描述型版本、不可解析版本 anchor fallback、升级后 stale state reinstall/repair 回归测试。

### V3.8.14：WebSocket interaction.select 交互卡片补丁（已完成）

- [x] issue #86 / PR #87：Feishu/Lark WebSocket 长连接下，agent clarify/approval 按钮点击经 Hermes adapter 原生 card action 通道进入 hook runtime。
- [x] hook runtime 接管 `interaction.select`，转发 sidecar `/card/actions`，并将更新后的 card 作为 Feishu callback response 返回。
- [x] 保持 sidecar 作为安全边界：`interaction_id`、callback token 和可用的 chat id 继续在 `/card/actions` 校验。
- [x] sidecar 拒绝、过期或无 card 返回时保持空 callback response，不崩溃也不落入未知原生 handler。
- [x] 合并时保留贡献者 @colinaaa 的原始 commits，并补齐 rejected interaction 回归测试。

### V3.8.15：输入附件重复 reply 抑制补丁（已完成）

- [x] issue #82 后续复现：延续 session 且带输入 `.docx/files` 上下文时，完成卡片下方不再重复出现原生最终 reply。
- [x] `files` / `file` locals 继续作为卡片附件摘要，但不再自动触发 `native_delivery=required`。
- [x] 最终 answer 明确包含 `MEDIA:/tmp/...` 或本地文件路径时，仍保留 Hermes 原生文件/媒体投递。
- [x] `media_files`、`image_files`、`audio_files`、`video_files` 等结构化输出媒体字段继续保护原生投递路径。
- [x] 补齐输入文件 card-only 和显式媒体输出 fail-open 回归测试。

### V3.8.16：话题群 message_id 复用新卡补丁（已完成）

- [x] issue #89 / PR #88：Feishu/Lark 话题群连续消息复用同一 `message_id` 时，第二条及后续消息会重新发送新卡片。
- [x] 已完成或失败的旧 session 会清理 per-key card delivery 状态，再创建新 session，避免 clarify/approval 第二轮无卡片而挂起。
- [x] 当前轮仍在 streaming 时，重复 `message.started` 继续 ignored，不会误发第二张卡。
- [x] 合并时保留贡献者 @colinaaa 的原始 commit，并在 README / release notes 中体现 PR #88 贡献。
- [x] 补齐 topic reused `message_id` 新卡和 active duplicate started guard 回归测试。

### V3.8.17：cron 路由意图卡片投递补丁（已完成）

- [x] PR #77（贡献者 @zayn-0101）：cron `deliver=origin` / `deliver=all` / `origin,all` 不再被误判为真实 platform，完成结果会解析到 Feishu 目标并发送卡片。
- [x] `deliver=local` 保持本地/无投递语义，不被 fallback 意外送到 Feishu。
- [x] 保留 dict-shaped `deliver` 兼容，避免非 Feishu origin chat id 泄漏到 Feishu delivery。
- [x] 安装 hook 对 Hermes `_resolve_delivery_targets` 做 optional guard，缺失 helper 时保持 fail-open。
- [x] 合并时保留贡献者 @zayn-0101 的原始 commits，并在 README / release notes 中体现 PR #77 贡献。
- [x] 补齐 cron routing-intent、dict deliver、non-Feishu origin、`local` 和 patcher optional pre-resolve 回归测试。

### V3.8.18：cron 话题线程回传补丁（已完成）

- [x] Issue #90 / PR #91（贡献者 @colinaaa）：cron 卡片携带 Feishu topic `thread_id`，回到原话题线程而不是创建新 topic。
- [x] 保留 scheduler-resolved Feishu target、Feishu origin 和显式环境 fallback 的优先级。
- [x] 非 Feishu origin 的 thread id 不进入 Feishu 事件，补齐跨平台隔离回归测试。
- [x] 合并时保留 @colinaaa 的原始 commit，并在 README、双语用户指南和 release notes 中体现贡献。

### V3.8.x 后续维护与扩展面（待办）

- [ ] 卡片内提供“继续”“重试”“取消”等写操作入口，需要单独做权限、幂等和误触发设计。
- [ ] 补齐 E2E / fixture 覆盖，验证 V3.8.x 卡片体验和终态 drain 主链路。
- [ ] 完成 agent guide、维护手册和开放扩展面的文档整理。
- [ ] 评估卡片 timeline/metrics 的长期兼容边界，并补发布回归清单。
- [ ] 完全兜住极端 Markdown table 边界：当结构化拆分失败时输出安全折叠提示，避免回退 plain split。
- [ ] 清理 terminal 后的 closed `FlushController`，并评估更有诊断价值的 queue depth / coalesced backlog 指标。
- [ ] V3.8.x 候选：按真实使用反馈补充更多 Hermes 原生 notice 分类、去重策略和中英文文案微调。
- [ ] V3.9 候选：Docker 完整运维体验（镜像内安装、外部 Hermes 目录挂载、doctor 一键诊断、升级流程）。
- [ ] V3.9 候选：群聊体验后续（可视化配置向导、更多真实 E2E fixture、跨群会话迁移策略）。
- [ ] V4.0 候选：卡片交互中台化（slash command、授权请求、对话选项、运行提示统一 action/state 模型）。

## V3.3.0 (已完成)

- [x] 多 Profile 进程内支持（一个 sidecar 服务多个 Hermes profile，`profile_id:message_id` 复合键）
- [x] 多 Bot 独立凭据路由（`_resolve_route` 注入 profile prefix，`_client_for_bot` 按 profile 分发）
- [x] DeepSeek `<thinking>`/`</thinking>` 标签过滤
- [x] 卡片表格超限保护（`MAX_CARD_TABLES=5`，自动截断）
- [x] Footer braille spinner 旋转动画
- [x] COMPLETE_PATCH 平台判断修复（非飞书平台不再吞掉响应）
- [x] 工具次数改为累计调用次数（`_tool_call_count`）
- [x] 锁优化：飞书 API 调用移出事件锁，更新间隔 2.0→0.5s
- [x] 跨 Profile 数据泄漏修复（feishu_message_ids 等改用 session key）
- [x] README 全面重写（安装→功能→配置→FAQ 结构，214 行）
- [x] CHANGELOG、LICENSE、config.yaml.example、AGENTS.md 更新
- [x] 真实环境 E2E 测试（3 bot × 3 profile，飞书卡片发送验证）
- [x] 425 个测试，0 失败

## V3.0-V3.2 (已完成，归档)

- [x] Sidecar-only 架构、流式卡片、健康端点、安装向导（V3.0）
- [x] 多 Bot 注册与路由、群聊绑定、Bot CLI、路由诊断（V3.2）
- [x] Accept-Encoding 修复 brotli 兼容（V3.2.1）
- [x] 真实 Feishu E2E 主链路验收（Hermes hook 到 sidecar `/events` 的 fail-open 转发链路）
- [x] 实现 Feishu CardKit HTTP client，并用 mock server 验证 tenant token、发送和更新。
- [x] 提供 `smoke-feishu-card` 手动命令用于真实飞书卡片发送/更新验证。
- [x] 使用真实飞书应用做人工 CardKit smoke test，凭据仅使用本机配置或环境变量。
- [x] 完成真实飞书长卡片压力测试，同一张卡片更新到 16k 中文字符。
- [x] 将 sidecar 进程管理从占位 `status` 扩展为可启动、可停止、可探活。
- [x] 增加 sidecar 健康检查和重试指标。
- [x] 增加安装前 Hermes 版本展示和更友好的错误提示。
- [x] 补齐官方 Hermes `v2026.4.23` Git tag 源码的安装/恢复 smoke test。
- [x] 补齐基于 Hermes fixture 和 mock sidecar 的最小 hook 事件转发验证。
- [x] 在真实 Hermes Gateway 进程中做人工 smoke test。
- [x] 编写从 legacy/dual（installer_v2.py、gateway_run_patch.py、patch_feishu.py）安装迁移到 sidecar-only 的安全迁移说明。
- [x] 端到端截图与验证材料（e2e-card-preview.svg、e2e-card-preview.json、generate_e2e_preview.py）。

## V3.5.0 (已完成)

- [x] Hermes 授权/选项请求在飞书卡片中渲染按钮，用户点击后原任务继续并更新原卡片
- [x] issue #41：多条回复/新版 Hermes 流式链路第二条开始不再退回 text 模式
- [x] PR #42：cron deliver 与 scheduler resolved targets 优先于陈旧 `origin.platform`
- [x] 超过 `MAIN_CONTENT_CHUNK_CHARS` 的长表格/代码块按完整 Markdown 结构切分，避免飞书 raw markdown
- [x] thinking/interim assistant 使用 `append_block` 完整块追加，减少句子截断、漏字和粘连

## V3.4 (计划)

- [x] issue #39：修复 DeepSeek V4 Pro 工具调用后 blank completed answer 清空流式答案（V3.4.3）
- [x] PR #38 核心能力：Markdown 长内容按表格/代码块结构边界切分（V3.4.3）
- [x] Hermes `v0.14.0` / `v2026.5.16+`：确认使用 `gateway_run_013_plus`，`v2026.4.x` 保持 legacy（V3.4.3）
- [x] issue #31：修复并发 PATCH / sequence 竞争导致的流式卡片内容回退与漏字（V3.4.2）
- [x] issue #25：修复 Hermes v2026.5.7 fallback `message_id` 生命周期一致性（V3.4.1）
- [ ] 旧 V3.4 未完成项已迁移到 V3.6.0 / V3.7.0 下一版计划。
