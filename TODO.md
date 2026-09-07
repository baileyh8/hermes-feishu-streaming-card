# Hermes Feishu Streaming Card — 主线任务清单

当前 active runtime 是 `hermes_feishu_card/`。legacy adapter、dual mode、旧 `sidecar/`、旧 `patch/` 和 `installer_v2.py` 不是 active runtime，仅保留作历史参考。

## V3.8 / V3.9 / V3.10 / V4 系列路线：V3.8.0 / V3.8.1 / V3.8.2 / V3.8.3 / V3.8.4 / V3.8.5 / V3.8.6 / V3.8.7 / V3.8.8 / V3.8.9 / V3.8.10 / V3.8.11 / V3.8.12 / V3.8.13 / V3.8.14 / V3.8.15 / V3.8.16 / V3.8.17 / V3.8.18 / V3.9.0 / V3.9.1 / V3.10.0 / V4.0.0 / V4.0.1 / V4.0.2 / V4.0.3 / V4.0.4 / V4.0.5 / V4.0.6 / V4.0.7 / V4.0.8 / V4.0.9 / V4.0.10 / V4.0.11 / V4.0.12 / V4.0.13 / V4.0.14 / V4.0.15 / V4.0.16 / V4.0.17 / V4.0.18 / V4.0.19 / V4.0.20 / V4.0.21 / V4.1.0 / V4.1.1 / V4.1.2 / V4.1.3 / V4.1.4 / V4.2.0 / V4.2.1 / V4.2.2 / V4.2.3 / V4.2.4 / V4.2.5 / V4.2.6 / V4.2.7 / V4.2.8 / V4.2.9 / V4.2.10 / V4.2.11 / V4.2.12 / V4.3.0 / V4.3.1 / V4.3.2 / V4.3.3 / V4.3.4 / V4.3.5 / V4.3.6 / V4.3.7 / V4.3.8 / V4.4.0 / V4.4.1

详细路线见 [docs/superpowers/specs/2026-06-30-v3-8-design.md](docs/superpowers/specs/2026-06-30-v3-8-design.md) 和 [docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md](docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md)。

### V4.4.1：Hermes 0.21、多 profile 与话题兼容修复（发布候选）

- [x] PR #257 / Issues #254/#255/#256：按已验证 facade/mixin 契约安装，保留多文件 ownership 与可逆恢复。
- [x] PR #251 / Issue #252：话题后续、queue/redirect 与 cron 回复锚点；显式 turn 身份保持隔离。
- [x] Issues #83/#259：单进程多 profile 身份与缺少字面 `default` 的路由处理。
- [x] Issues #253/#258：可选 reasoning code、审批完整范围与超限原生回退；Issue #250：实际 provider 页脚归属。
- [x] PRs #247/#248：CodeQL init/analyze 与测试同步更新；保留全部历史贡献者。
- [ ] 当前候选普通 wheel、完整 pytest、GitHub CI、exact release merge、annotated tag 与 Release assets/checksums 按最终结果登记。
- [ ] 真实 Feishu/Lark 私聊、群聊、话题重启后 queue/interrupt、审批手机/桌面阅读、多 profile 复测。
- [ ] Issue #73：等待新版环境诊断，不能从旧版本报告推断已解决。

### V4.4.0：新版 Hermes 原生能力中心与可视化交互（发布候选）

- [x] 基于 Hermes `v2026.8.27` / `0.20.6` 与 `main@4f225435` 核对 `COMMAND_REGISTRY`、Gateway dispatch、plugin/skill command 与 Feishu adapter 契约。
- [x] `/commands` 动态生成能力中心，支持分类、详情、alias、subcommand、argument mode、busy policy 和 plugin/skill commands，不再复制固定命令 allowlist。
- [x] 只读快捷入口与 `/model`、`/resume` 原生 picker 通过 copied `MessageEvent` 回到 Hermes adapter；群聊绑定原发起人，状态变更命令拒绝一键执行。
- [x] `/status`、`/context`、`/usage`、`/agents`、`/sessions` 等原生输出增加 KPI 可视化且保留完整原文。
- [x] `pending_count` / `update_queue_peak` 记录真实 coalesced backlog，而不是 0/1 布尔值。
- [x] 极端 Markdown 表头与无法保持列结构的超长行安全折叠；普通长表格、五表格 policy 与 terminal native handoff 不变。
- [x] 设计文档、版本元数据、双语文档和本地聚焦测试完成。
- [ ] release PR CI、exact release merge、annotated tag、public tagged install 与 Release assets/checksums。
- [ ] 真实 Feishu/Lark 私聊与群聊验收：能力中心导航、快捷命令、原生 picker、KPI 卡片、群聊非发起人拒绝和极端表格；自动化不冒充平台验收。

### V4.3.8：常驻 setup、batch clarify 与 proxy 可靠性热修（发布候选）

- [x] Issue #244：systemd user + linger 能力就绪时 `setup` 默认启用 persistent service；不可用时明确 transient 重启风险并给出精确 `enable` 命令；`--transient` 可显式退出。
- [x] Issue #245：out-of-band card action completion 不推进 Hermes transport sequence；下一条 batch clarify 请求按相同 next sequence 正常接收，第一题 callback card 不混入下一题。
- [x] PR #242：远程 Feishu/Lark HTTP 遵循 proxy 环境变量；loopback/private/link-local/unspecified 目标继续 bypass，并保留 Pure White 原始作者提交。
- [x] focused proxy `81 passed`；session/hook/server `937 passed`；persistent/install `649 passed, 5 skipped`；fresh normal-wheel process `8 passed`。
- [x] fresh Python 3.12 normal-wheel 完整 pytest `3343 passed, 6 skipped in 690.84s`；`git diff --check`、双语文档与贡献归属已验证。
- [ ] release PR CI、exact release merge、annotated tag、public tagged install 与 Release assets/checksums。
- [ ] 真实 Feishu/Lark 客户端 smoke 与真实 Linux systemd user + linger 主机 smoke；自动化不冒充平台验收。

### V4.3.7：Hermes session-scoped delivery filter 兼容（已发布）

- [x] Issue #240 / PR #241：Base media/local delivery filter exact matcher 同时接受旧版单位置参数调用与新版唯一 `session_key=session_key` 关键字调用。
- [x] extra/wrong/unpacked keyword、错误值与缺少/增加位置参数继续 fail-closed；apply/remove/restore 保持幂等和逐字恢复。
- [x] PR 精确 head 定向回归 `460 passed, 1 skipped`；fresh normal-wheel 完整 pytest `3330 passed, 5 skipped`；真实 Hermes `82b32f32ef` source roundtrip 与 6 种对抗调用形态通过。
- [x] PR #241 的 12 项 GitHub checks、maintainer approval 与 exact merge `7fcf3cbd67d3a5100739e9e3d3d7cdcce080cb62`。
- [x] release PR CI、exact release merge `82e22928b22e49795cb475c41f8057b1ef7fe95e`、annotated tag、public tagged install 与 Release assets/checksums。
- [ ] 真实飞书 smoke；本修复只影响 installer AST contract，自动化不冒充平台验收。

### V4.3.6：话题 create 兼容与可配置 @ 提及（已发布）

- [x] Issue #237 / PR #238：无 reply anchor 的话题 create 不再使用飞书不支持的 `receive_id_type=thread_id`，改为向父 `chat_id` 创建；有 anchor 的话题路径继续使用 reply API。
- [x] Gateway native-handoff 的逻辑 topic route 与 UUID identity 保持稳定，但实际无锚点 create 会移除 adapter metadata 中的 `thread_id`，避免同源非法请求。
- [x] PR #228：approval/clarify 交互卡与 opt-in completion notification 支持 `@` 发起人；`mentions_in_cards: false` 是总关闭开关，per-kind 与 completion 开关只在总开关未关闭时生效。
- [x] schema 2.0 streaming card 保持唯一 PATCH owner；legacy 交互卡继续作为 auxiliary message。`completion_notify.mention: false` 在无 sender 场景发送普通完成通知，mention 开启时仍拒绝非法 `open_id`。
- [x] #237 正常 wheel 全量回归 `3283 passed, 5 skipped`；#228 最终组合相关 unit `225 passed`、server integration `324 passed`，最终 rebased head 12 项 CI 全绿。
- [x] v4.3.6 release candidate：`git diff --check`、sdist/wheel 与 normal-wheel provenance 通过；完整 pytest `3325 passed, 5 skipped in 560.94s`；package/distribution `4.3.6` 来自隔离 `site-packages`，唯一 plugin entrypoint、24 slices 与 CLI help 均已验证。
- [x] release PR CI、exact release merge `a2a244659f198ecd57c862455d3f4d658a827b66`、annotated tag、public tagged install 与 Release assets/checksums。
- [ ] 独立真实飞书 smoke；当前仅有 Issue #237 报告者的真实 API 对照与本地热修验证，自动化不冒充平台验收。

### V4.3.5：Feishu edit fallback metadata 兼容热修（已发布）

- [x] 合入 PR #235：HFC wrapper 仅在原 `edit_message` 明确不支持时移除内部 `metadata`，兼容 Hermes v2026.8.3 Feishu adapter。
- [x] 支持显式 `metadata` 或 `**kwargs` 的 adapter 继续接收原参数；无关未知关键字仍抛 `TypeError`，不被兼容层吞掉。
- [x] 独立本地回归 `4 passed`，hook/server 热区 `841 passed`，精确 PR HEAD 完整 pytest `3279 passed, 6 skipped`。
- [x] PR #235 HEAD `5b3bf428eb688df4b95607cba1a4ce50e2eeb8d0`：Tests run `32719244038`（attempt 3，10 jobs）与 CodeQL run `32719244032` 通过；exact merge `d56555bf9e716de67ed14f8ed992df1ec55cea21`。
- [x] docs/package/native provenance 聚焦门禁 `99 passed`；一次性 wheel 环境完整 pytest `3280 passed, 5 skipped in 555.86s`；sdist/wheel、fresh `site-packages` provenance、唯一 plugin entrypoint、24 slices 与 CLI help 通过。
- [x] release PR CI、exact release merge `7829e51c4c7851aa09347e56bb8c2a7136c4b0cb`、annotated tag 与 Release assets/checksums 已完成。

### V4.3.4：runtime listener 与 V3 doctor 热修（已发布）

- [x] 合入 PR #229：listener bind 不调用 reverse DNS；`serve_forever` thread 使用 daemon，不显式 `close()` 的短命令仍可退出。
- [x] 修复 Issue #233：有效 V3 Hybrid 安装的 `doctor --json` 只走 V3 runtime binding、plugin entrypoint 与 fixed-tag inspector，不再触发 Legacy recovery/integrity 误报。
- [x] phase/config/target/backup/runtime identity 漂移输出 V3-specific finding、拒绝 Legacy 自动 repair，并引导官方 V3 restore/reinstall。
- [x] hosted macOS blocked-delivery close 使用 Future deadline 验证有界完成，不放宽生产 timeout。
- [x] #229/#233/diagnostics/CLI/macOS timing 联合回归 `191 passed`。
- [x] 一次性 4.3.4 venv 完整 pytest `3275 passed, 6 skipped in 634.95s`；`git diff --check`、sdist/wheel、fresh-wheel `site-packages` provenance、唯一 plugin entrypoint、24 slices 与 CLI help 通过。
- [x] PR #234 candidate HEAD `435ea4e355719e0f2d904cf1bac986ff18f70876`：Tests run `32710110323`（10 jobs）与 CodeQL run `32710110375` 通过。
- [x] exact merge `2f1abcfcad50997c615103e3cdf1302c61f94c91`、annotated tag、Release assets/checksums 与公开下载校验完成。

### V4.3.3：首回复建 thread 锚点与 text fail-closed 热修（发布候选）

- [x] 合入 #231：`reply_in_thread` 与真实 reply anchor 固定在 `CardSession`，普通/重复/runtime-admission interaction 与 opt-in completion notification 继续使用同一 placement。
- [x] `reply_in_thread=true` 或非空 `thread_id` 表示 text thread placement；缺少 `reply_to_message_id` 时在 API 边界拒绝，不再静默发到群聊顶层；没有 thread placement intent 的默认路径保持兼容。
- [x] 单元与 HTTP 回归覆盖首回复无 concrete `thread_id`、completion notification thread placement，以及缺 anchor 时不请求 token/不发 API 请求。
- [x] 本地完整 pytest `3267 passed, 6 skipped`；`git diff --check`、sdist/wheel、fresh Python 3.12 wheel-only provenance、唯一 Hermes plugin entrypoint、24 slices 与 CLI help smoke 通过。
- [x] PR #232 candidate HEAD `f7de533d67f9e50afcd2c4d80fad89b572054605`：Tests run `32657674121`（10 jobs）与 CodeQL run `32657674120` 通过。
- [ ] exact merge SHA、annotated tag、public install 与 Release assets/checksums。
- [ ] 真实飞书首回复建 thread / missing-anchor smoke（自动化不替代真实客户端证据）。

### V4.3.2：Issue #227 卡片方言双轨热修（发布候选）

- [x] schema 2.0 streaming message 保持稳定 owner；legacy clarify/approval message 不再晋升为 PATCH 目标。
- [x] `/card/actions` 返回 legacy completed/failed 终态卡；Gateway 对意外 schema 2.0 callback card 降级为 success toast。
- [x] standard/runtime admission、direct/form、连续 interaction、过期和 predecessor failure 使用方言感知 fake 覆盖。
- [x] 热区联合回归 `932 passed, 1 skipped`；`git diff --check` 通过。
- [x] 隔离候选完整 pytest `3253 passed, 5 skipped`；sdist/wheel、fresh Python 3.12 + lark-oapi 1.6.8 wheel-only provenance、唯一 plugin entrypoint、24 slices 与 CLI help。
- [ ] PR CI、exact merge SHA、tag、public install、Release assets/checksums 与真实飞书验收。

### V4.3.1：Hermes 0.20 交互恢复与 persistent service 热修（发布候选）

- [x] Issue #216：真实 Feishu WebSocket 点击使用可回调卡片 payload 并携带 exact profile；listener 直接唤醒原 pending handle，后续 answer/thinking 继续流式更新。
- [x] 显式 text mode 在 mutation 前拒绝 runtime callback ownership，第一条文本回复交回 Hermes 原生 interceptor。
- [x] PR #226：修复 `python-sha256:` identity、systemd `WorkingDirectory` 与 tokenless `process_token_hash` 对账。
- [x] 中英文 README 贡献者与历史 releases、PR/issues、commit/co-author 对账，保留之前版本署名。
- [x] 完整 pytest `3245 passed, 6 skipped`；sdist/wheel；fresh Python 3.12 wheel-only `site-packages`、唯一 plugin entrypoint、24 slices 与 CLI help。
- [ ] diff-check、exact SHA、远端 CI、tag、public install 与 Release assets。

### V4.3.0：Hermes 0.20 Hybrid runtime bridge（已发布）

- [x] 固定 Hermes `v2026.8.3` capability probe、真实 PluginManager evidence 与 17 groups / 7 targets Hybrid patch descriptor。
- [x] signed Plugin runtime bootstrap、event-id fence、subagent timeline、terminal/native handoff 与 direct pending-handle interaction listener。
- [x] V3 installer transaction、official plugin enable、idempotent inspect、incomplete repair、byte-exact restore/uninstall 与 legacy V2 安全迁移。
- [x] Issues #210/#211/#212/#214/#215/#217/#221/#222 和 PR #213/#218/#219/#220/#223 可本地吸收部分；Issue #216 标记为平台零事件边界；PR #203 因只改 `legacy/` 排除。
- [x] 真实固定 tag 副本完成 venv entrypoint、install/idempotence/restore；V3 联合门禁 `340 passed, 5 skipped`，persistent process/CLI `302 passed`。
- [x] 完整 pytest `3227 passed, 6 skipped in 378.84s`；sdist/wheel、全新 Python 3.12 隔离 `site-packages`、唯一 Hermes plugin entrypoint、24 个 provenance slices、主 CLI 与 `enable/disable --help` 均通过。
- [x] exact merge SHA、远端 CI、annotated tag、public tag/install 与 Release assets。

### V4.2.12：审批能力与零工具时间线（发布候选）

- [x] 合入 PR #206：approval 按 `smart_denied`、`allow_session`、`allow_permanent` 生成选项，并以 `allow_custom_input` 区分 approval 与 clarify。
- [x] sidecar 拒绝未声明的自定义输入与伪造选项值；callback token、chat/operator binding、expiry 与幂等边界保持不变。
- [x] 合入 PR #205：启用 reasoning timeline 时，零工具卡在运行、完成和失败态保持同一折叠入口，raw thinking 不公开。
- [x] 原贡献 commit 与作者身份保留；`legacy/`、Hermes patch ownership 和本机运行环境不变。
- [x] v4.2.12 候选门禁：docs/package `94 passed`、聚焦矩阵 `830 passed`、完整 pytest `2481 passed, 6 skipped`；sdist/wheel、隔离 `site-packages` provenance、CLI help 与 `git diff --check` 均通过。
- [ ] PR CI、exact merge SHA、annotated tag、public tag/install 与 Release assets。

### V4.2.11：旧交互卡接力快照热修（发布候选）

- [x] Issue #202 旧卡不再永久显示 clarify/tool 运行态；新卡发送成功后旧卡变为绿色“已转入交互卡片”快照。
- [x] 历史快照保留可见正文、thinking、timeline、工具与附件，但移除 pending 按钮、callback token 和临时 Header。
- [x] 新卡发送失败保持原 session/卡片/动画权威；旧卡 PATCH 失败保持 fail-open 并进入既有 metrics/diagnostics。
- [x] 动画取消完成后才 PATCH 快照；连续交互、`turn_id`、per-session card config 与 callback 后续更新完成回归。
- [x] session/render/server/clarify 聚焦矩阵 `450 passed`，`git diff --check` 通过。
- [x] 隔离 v4.2.11 候选完整 pytest `2478 passed, 6 skipped`。
- [x] 本地 sdist/wheel 与全新 venv 候选 wheel `site-packages` provenance/CLI smoke。
- [ ] PR CI、精确 merge-SHA 复验、public tag/install 与 Release assets。

### V4.2.10：sidecar 请求鉴权、交互过期与仓库门禁（发布候选）

- [x] 非回环 sidecar 的 `/card/actions`、`/interactions/{id}` 与 `/messages/{id}/summary` 使用 method/path/body 绑定的独立 HMAC proof。
- [x] 交互使用 sidecar 绝对截止时间；晚到按钮/表单不能完成，周期清理会标记 failed 并刷新原卡。
- [x] Gateway poll 超时只发送一次独立 `interaction.failed`，不重放 `interaction.requested`。
- [x] Ubuntu Python 3.9–3.12、Windows 3.12 与 macOS 3.12 全量 pytest 门禁；保留 Feishu SDK、PowerShell 与 Docker smoke。
- [x] 官方 Actions 固定到 Node 24 版本的不可变 SHA；新增 CodeQL 与每周 Dependabot。
- [ ] 完整 pytest、精确 PR merge、detached merge-SHA 复验、public tag/install 与 Release assets。

### V4.2.9：交互回调与引用摘要热修（已发布）

- [x] Issue #197 已完成卡片的引用摘要使用有界真实回答摘录。
- [x] PR #196 的慢速 slash-confirm 移出飞书 callback deadline，并增加原子 claim 与调度失败回退。
- [x] PR #199 的多选、单选序号、自定义回答、pending freeze 与过期 footer 完成安全整合。
- [x] form submit 使用随机 callback token，并要求非空精确 chat binding；不接受 interaction ID 作为凭据。
- [x] `/events` 单次 POST、Hermes 旧 clarify 签名兼容与日志脱敏完成回归。
- [x] 完整 pytest、sdist/wheel、隔离 `site-packages` provenance 与 `git diff --check`。
- [x] PR CI 五项门禁。
- [x] exact merge `dc332212c14423abb3b42f524dce46ff0ff28479`、public tag/install 与四个 Release assets。

### V4.2.8：安装凭据持久化热修（已发布）

- [x] 公开 v4.2.7 tag 安装复现环境凭据只在当前进程可见，后续 doctor 无法读取。
- [x] macOS/Linux、Docker 与 PowerShell 安装器把进程凭据持久化到选定 `.env`。
- [x] POSIX `.env` 权限为 `0600`，安装日志不包含凭据值。
- [x] 三平台回归覆盖进程凭据、带空格 secret、dotenv 替换与日志脱敏。
- [x] 完整 pytest、sdist/wheel、隔离 `site-packages` provenance 与 `git diff --check`。
- [x] PR CI、exact merge、public tag/install 与 Release assets。

### V4.2.7：Windows 安装与 detached runner 热修（已发布）

- [x] Issue #193 的 SDK/HFC 冷启动探针上限从 8 秒调整为 30 秒。
- [x] 新 manifest/recovery plan 写 POSIX 相对路径，精确兼容旧 Windows 反斜杠路径并拒绝越界输入。
- [x] 合入 PR #180 的 parent `HERMES_HOME` config 查找与 PR #181 的 detached runner PID 严格重绑。
- [x] PowerShell installer 传播 native pip/setup 非零退出，不在失败后打印 `done`。
- [x] 候选完整自动化与五项 GitHub Actions 门禁通过。
- [x] 发布分支聚焦/完整测试、sdist/wheel 与隔离 `site-packages` provenance。
- [x] exact merge、public tag/install 与 Release assets。

### V4.2.6：Issues #187–#190 与更新兼容修复（已发布）

- [x] 重复 `interaction.requested` 创建并提升最新选项卡，失败时精确回滚。
- [x] 保留完整流式答案后的短 terminal postscript，同时维持正常 final 替换语义。
- [x] Hermes 0.20 exact Base awaited ledger patch 可验证、可移除且逐字节恢复。
- [x] 裸 `/update` 保留 venv symlink runtime，放宽慢 fetch 上限，并正确识别 `hermes_cli.__version__`。
- [x] 发布分支版本契约、聚焦矩阵、完整测试、本地包与 `site-packages` provenance。
- [x] PR CI、exact merge、public tag/install 与 Release assets。

### V4.2.5：审查安全热修（已发布）

- [x] canonical `turn_id` 隔离 quoted turn 的 session、sequence、terminal、native handoff 与 delivery policy，同时保留 legacy alias fallback。
- [x] duplicate resume 无副作用；maintenance 命令固定到 confirmed checkout，并按 persisted phase 对齐 external drain。
- [x] doctor 只建议可执行的 integrity action；三端 installer `latest` 固定稳定 tag 或 fail closed。
- [x] 五处版本标记与 exact tested annotated-tag Release Assets gate 纳入自动化。
- [x] 完整候选测试、真实验收、PR CI、exact merge、public tag/install 与 Release assets。

### V4.2.4：话题引用回复独立卡片热修（发布候选）

- [x] Issue #175 与 PR #177 定位连续引用同一消息时 `message.started` 复用 reply anchor，导致 session key 碰撞并覆盖旧卡。
- [x] hook 使用真实入站 message ID，sidecar 对新 turn 跳过 reply alias；同一轮 delta/tool alias 关联保持不变。
- [x] 新增真实 HTTP `/events` 回归，验证两次引用回复各创建一张卡，第二轮 delta 只更新第二张卡。
- [x] 完整 pytest `2311 passed, 5 skipped`、`git diff --check`、sdist/wheel 与干净 `site-packages` provenance。
- [ ] PR CI、exact merge、public tag/install 与 Release assets。

### V4.2.3：更新回调证据转发热修（已发布）

- [x] 真实飞书点击、Gateway 日志与 sidecar metrics 联合定位 WebSocket hook 遗漏 `update_evidence_fingerprint`。
- [x] 新增 executor-facing 回归，先观察缺失字段失败，再以最小修改保留证据指纹。
- [x] hook/runtime/server/Feishu SDK 相关矩阵 `670 passed, 1 skipped`。
- [x] 完整 pytest `2309 passed, 5 skipped`、`git diff --check`、sdist/wheel 与干净 Python 3.12 `site-packages` provenance。
- [x] 本机候选经官方 setup 安装后，真实飞书取消使原卡进入终态；sidecar update 成功，Hermes HEAD 与 update log 不变，无 updater 进程。
- [x] PR CI、exact merge、public tag/install 与 Release assets；正式 tag 真实飞书复验未记录，保留为运行环境验收项。

### V4.2.2：更新确认卡终态写回热修（已发布）

- [x] native card action 保持快速空 ACK，sidecar 在后台 PATCH 原确认卡，不把 Feishu API 延迟带回 callback deadline。
- [x] 取消写入“已取消更新” terminal card 且绝不调度 updater；确认先尝试写入 locking/准备态再调度维护任务。
- [x] 聚焦回归与相关 operations/server/hook-runtime 矩阵 `378 passed`。
- [x] Python 3.9 / 3.12 全量均为 `2307 passed, 5 skipped`；`git diff --check`、wheel/sdist、干净 `site-packages` 与独立 maintenance runtime 通过。
- [x] PR CI、exact merge、public tag/install 与 Release assets；真实飞书复测发现 WebSocket hook 遗漏证据指纹，转入 V4.2.3 热修。

### V4.2.1：Gateway 首个 heartbeat 任务计数热修（已发布）

- [x] startup adapter 在 runtime control 启动前登记 live runner，首个 heartbeat 直接调用同一次 `_active_work_count()` 聚合采样。
- [x] Gateway 重启后的第一条私聊裸 `/update` 不再依赖其他消息预热；缺失计数仍 fail-closed。
- [x] Python 3.9 / 3.12 完整 pytest、构建与隔离安装。
- [x] PR CI、exact merge、public tag/install 与 Release assets；真实飞书确认卡创建通过，并暴露了 V4.2.2 修复的取消终态写回缺口。

### V4.2.0：飞书私聊安全升级（已发布）

- [x] 私聊裸 `/update` 使用 120 秒、发起者/会话/profile/目标证据绑定的确认卡；群聊、非飞书、别名和带参数命令保持 Hermes 原路径。
- [x] 独立维护 runtime 在 Hermes checkout 外运行官方 `hermes update --yes`，从私有缓存重装同一 HFC wheel，并恢复 hook、sidecar 与 Gateway。
- [x] durable job store、journal、锁、重入恢复、卡片阶段更新和 `maintenance provision/status/run/resume` 本机恢复入口均有回归。
- [x] 非 HFC tracked 改动、Git 未完成操作、artifact/版本漂移或最终验证失败均停止；不使用 force flag、自定义 reset/checkout/stash/rollback。
- [x] 完整 pytest、`git diff --check`、wheel/sdist 与隔离 `site-packages` provenance。
- [x] PR CI、exact merge SHA、public tag/install 与 Release assets；真实飞书确认卡流程保留为用户侧验收。

### V4.1.4：Windows 旧版 manifest 迁移热修（已发布）

- [x] 从公开 v4.0.14 `site-packages` 复现 manifestless legacy owned hook 在 v4.1.3 被拒绝。
- [x] gateway lenient removal 与 backup 逐字一致时进入 official install 事务；Cron/Base 使用严格独立校验。
- [x] 无 directory-fd 的 Windows 等价路径、Unicode + CRLF、Hermes v0.19.0 required Base 与 optional Cron 聚焦回归。
- [x] `--no-repair`、Cron/Base 目标缺失、owned marker 外用户改动、并发编辑、backup 不一致与 symlink 继续 fail-closed。
- [x] 完整 pytest `2221 passed, 5 skipped`、`git diff --check`、wheel/sdist 与隔离 Python 3.12 `site-packages` provenance。
- [x] PR CI、exact merge、public tag/install 与 Release assets。

### V4.1.3：升级恢复与 TurnRunner 兼容性热修（已发布）

- [x] 同一 Hermes target 的旧/新 plan binding 只在双重 current-plan、双重 sidecar-stopped 与 snapshot CAS 校验后原子迁移。
- [x] 不同 target、状态漂移、残留进程和不可验证 plan 继续 fail-closed；独立 restart/hash fence 保留。
- [x] doctor 对 integrity migration 与 manual review 输出完整官方命令。
- [x] PR #168 在多个同名 `_stream_delta_cb` 中选择原生 `_stream_consumer.on_delta`，并可迁移旧的受管 hook。
- [x] Issue #169 在 Hermes `1a3a9de` 的 `TurnRunner` seam 中恢复 stable tool、answer、thinking、clarify、approval 与 status hook；doctor 根据真实可注入性 fail-closed。
- [x] 合并候选完整自动化 `2207 passed, 4 skipped` 与 `git diff --check`。
- [x] wheel/sdist 构建与隔离 Python 3.12 `site-packages` 包版本、distribution、CLI entry point provenance。
- [x] CI、exact merge、public tag/install 与 Release assets。

### V4.1.2：Gateway 重启竞态热修（已发布）

- [x] `installed` plan 下 heartbeat stale 只保持 degraded readiness，不写持久化 fence。
- [x] 新 matching `runtime.hello` 一次恢复 ready，generation/package/control-auth 安全边界不变。
- [x] 完整自动化、构建、exact merge、public tag/install、Release assets、本机/远端升级与真实飞书 smoke。

### V4.1.1：升级恢复安全热修（已发布）

- [x] verified `installed` plan 等待首次 heartbeat 时不再写 restart/manual-review fence。
- [x] sidecar package version、隔离 Python identity、Hermes venv 与受管重启决策保持一致。
- [x] legacy `0644` pidfile 仅在 owned `0700` state dir 内收紧；pidfile-less 进程不自动接管或 kill。
- [x] detached sidecar 改为 token 认证的本机自停路径，停止流程不再向可复用数字 PID/PGID 强制发信号。
- [x] `integrity acknowledge-review` 受 installed plan、stopped sidecar、无 pidfile、target binding 与 fence CAS 约束。
- [x] 独立审查通过；候选提交 `20b7b06` 完整 pytest `2194 passed, 4 skipped`，`git diff --check`、wheel/sdist 构建、隔离 `site-packages` provenance 与真实进程测试均通过。
- [ ] CI、exact merge SHA、public tag/install、Linux/Docker、本机与远端升级、真实飞书 smoke 和 Release assets。

### V4.1.0：投递策略与运行安全（已发布）

- [x] exact/profile-scoped `bindings.native_chats`、signed policy query 与 hook/server 双重 enforcement。
- [x] 默认无损 table compact、共享 serializer 限额与 terminal native handoff。
- [x] runtime hello/heartbeat、safe/notify/off integrity 与 strict repair，不自动重启 Gateway。
- [x] `auto` / `systemd-user` / `systemd-system` / `detached` 明确进程管理，Docker 不使用 systemd/privilege。
- [x] 双语 README、安装/迁移/安全/架构/协议/wiki/release notes 与版本元数据准备。
- [x] exact merge SHA、完整自动化、upgrade simulation、Linux/Docker、public tag/install 与 Release assets 已完成；V4.1.1 继续修复真实升级发现的 recovery/process 问题。

### V4.0.21：内容完整性与图片/notice 组合热修（发布候选）

- [x] Issue #155：仅显式 `answer -> tool` 边界归档答案，`tool -> answer -> completed` 保留完整用户可见终态答案。
- [x] Issue #147：图片/notice 自动化组合回归覆盖原生图片投递、一次性媒体文本抑制和 accepted notice 无 uncertain-delivery warning。
- [x] 包元数据、当前安装入口、Docker Compose 默认、双语用户指南和发布门禁统一到 `v4.0.21`，不改变 UI 或配置。
- [x] 真实飞书图片验收已通过（2026-07-28）：图片回合观测到 1 条带标记、非“生成中”的 completion card + 1 条 native image，无 uncertain-delivery warning；正常工具回合两段答案保留在同一卡，bot 原生标记重复为 0。
- [x] sidecar `events_received/events_applied=23/23`、1 次发送成功、16 次更新成功；event/auth rejection、send/update failures、notice uncertain warnings、notice update failures 均为 0，Gateway Feishu WebSocket 已连接，Hermes venv site-packages 为 4.0.21。
- [x] 最终本地发布门禁已通过：完整 pytest `1526 passed, 4 skipped in 53.56s`；`uv build` 生成 `hermes_feishu_streaming_card-4.0.21.tar.gz` 与 `hermes_feishu_streaming_card-4.0.21-py3-none-any.whl`；干净 Python 3.12 venv wheel 安装后的 import 位于 `site-packages`，package/distribution version 为 `4.0.21`，`hermes-feishu-card = hermes_feishu_card.cli:main` 存在，CLI `--help` exit 0。
- [ ] 公开 tagged installer 与 Release assets 的 post-tag 验证仍待完成；验收不宣称截图或桌面/移动端视觉 QA。

### V4.0.20：notice 异步 ACK 语义热修（发布候选）

- [x] 已有卡片的 `system.notice` 在事件应用且 PATCH 任务排队后返回 `accepted`，hook 不再误报投递未知。
- [x] `accepted` 必须同时带有 `applied=true`；独立 notice 首次 create/reply 继续使用 `delivered` / `not_sent` / `unknown`。
- [x] 异步 notice PATCH 内部重试耗尽时增加 `notice_update_failures`，并仅记录脱敏的 `status_code` / `api_code`。
- [x] hook、server、metrics、文档、包与完整发布门禁均有回归覆盖。

### V4.0.19：one-line installer venv 安装热修（发布候选）

- [x] 选中 Hermes venv Python 时默认关闭 `pip --user`；system Python fallback 继续使用 user install。
- [x] pip 安装失败时保留真实退出码并停止，不再继续调用旧版 `hermes_feishu_card.cli setup`。
- [x] fresh venv 不设置 `HFC_PIP_USER` 的真实安装、`site-packages` 来源与回归测试进入发布门禁。

### V4.0.18：Hermes Feishu SDK 兼容门禁（发布候选）

- [x] 仅在 Hermes adapter 实际使用 `extra_ua_tags` 时检查 Gateway venv 的 `lark_oapi.ws.Client` 构造签名。
- [x] `doctor` 输出 `feishu_sdk` 并以 `feishu_sdk_incompatible` 解释“Gateway 存活但飞书不回应”。
- [x] `setup/install` 对不兼容环境安装已验证的 `lark-oapi==1.6.8`，并在能力复检通过后继续。
- [x] 真实 Hermes v0.19.0 Gateway 恢复 Feishu WebSocket；全量自动化、文档、包和发布资产进入门禁。

### V4.0.17：并行同名工具事件关联热修（发布候选）

- [x] 使用 Hermes `tool_start_callback` / `tool_complete_callback` 的真实 `call_id` 关联 started/completed。
- [x] 两个并行 `web_search` 保留各自查询摘要、参数和耗时，不再发生详情串线。
- [x] 工具调用次数按 invocation 计数，不再把 started/completed 各算一次。
- [x] 渲染时清除详情中的全部 `耗时:` 行，只在工具标题保留一个耗时。
- [x] 无稳定回调锚点的旧 Hermes 继续使用既有 fail-open fallback；当前 Hermes patch 可编译、幂等并可还原。

### V4.0.16：加载态去重与真实工具耗时（发布候选）

- [x] 初始 Header 仅显示 `Hermes Agent`，正文保留动画“正在加载上下文…”。
- [x] 工具开始后 subtitle 显示当前动作；没有模型正文时移除加载占位。
- [x] 读取 Hermes `kwargs.duration`，缺失时用 started/completed 事件时间兜底，terminal-only 不伪造耗时。
- [x] 工具完成时保留 started 事件的查询摘要与参数，并继续使用紧凑时间线首行耗时。
- [x] 全量测试、包构建、隔离安装、公开 tagged installer 和本机运行来源进入发布门禁。

### V4.0.15：工具事件视觉与 Hermes 升级防护（发布候选）

- [x] Issue #141：工具事件改为状态/工具/耗时首行加参数/结果/失败详情次行的紧凑时间线。
- [x] 首事件前显示“正在加载上下文…”同卡 spinner，运行中工具复用动画；正文、工具终态或消息终态及时停止。
- [x] 动画走既有串行 PATCH、终态 drain、topic/reply anchor 与失败停止边界，不创建重复卡片。
- [x] `status` / `start` 主动识别经过验证的 Hermes 升级覆盖并给出显式恢复；用户改动继续 fail-closed。
- [x] 真实 Hermes 配置模型飞书验证、升级闭环模拟、全量自动化与包验证均纳入发布门禁。

### V4.0.14：长任务 heartbeat 卡片生命周期热修（发布候选）

- [x] Issue #142：heartbeat 明确为非终态，独立卡不再出现“运行中 / 已完成”矛盾状态。
- [x] 同一原始消息锚点的连续 heartbeat 复用同一 independent card；同 chat 不同锚点保持隔离。
- [x] orphan heartbeat 的 6/9 分钟更新、最终 `message.completed` 收束和 unknown delivery 后恢复均有回归覆盖。
- [x] `not_sent` / `unknown` 安全回退及其他 system notice 行为保持不变。

### V4.0.13：Hermes 全命令反馈卡片化（已发布）

- [x] 任意 Feishu/Lark slash command 的非空文本反馈进入统一 command context，不再维护固定命令 allowlist；built-in、alias、plugin/quick 和 unknown-command 提示自动覆盖。
- [x] 首次反馈创建命令卡，后续反馈串行更新同一卡；长 Markdown 分块，topic/reply anchor 保持不变。
- [x] create/PATCH 成功才抑制原生灰色文本，失败逐条回退 Hermes 原始反馈。
- [x] `/model`、裸 `/resume`、destructive confirmation 与 `/hfc` 专用交互卡保持优先；Agent turn 继续进入普通流式卡。
- [x] 手动 `/compress` 先创建“正在压缩上下文”卡，再以 original handler 的成功、no-op 或 aborted 结果更新同一卡。
- [x] 全量自动化与发布文档整理完成；真实 Feishu 命令矩阵未执行，不写成已通过。

### V4.0.12：上下文压缩、字号与凭据可观测性（已发布）

- [x] Issue #133：从 Hermes 精确 `Compacting context` callback 生成 `context-compaction` 阶段；已有卡继续更新，无卡时只创建一张 primary card。
- [x] `card.text_sizes` 支持 `body`、`reasoning`、`tool`、`notice`、`footer`，以及 `default` / `pc` / `mobile` 映射；物理 width/height 明确由客户端控制。
- [x] Issue #136：runner 与运维诊断读取 selected env 凭据；配置优先级固定且不隐式读取全局 `~/.hermes/.env`。
- [x] Noop 模式显示 `degraded` / `noop_mode`，发送返回 `not_sent` 并记录 `feishu_noop_attempts`，不再制造假 message id/success。
- [x] 自动化覆盖压缩 hook/session/render/server、字号 schema/merge/render/device，以及 selected-env/Noop process 集成。
- [x] 最终全量 gate `1460 passed, 4 skipped`、`git diff --check`、sdist/wheel 与干净 Python 3.12 wheel import `4.0.12` 通过。
- [x] 合并发布 PR；annotated tag `v4.0.12` 指向 `00a48a7`，Release 四个 assets/checksums 与公共 tagged installer fixture 验证通过。

### V4.0.11：system.notice 可靠投递（发布候选）

- [x] Issue #135：Feishu create/reply 使用稳定 `delivery_uuid`，只对可重试 HTTP/网络错误做最多 3 次有界重试。
- [x] sidecar 明确返回 `delivered`、`not_sent`、`unknown`；Hermes hook 分别抑制原生文本、回退原文或只发不含原通知内容的通用警告。
- [x] `/health` 与 card-safe diagnostics 增加重试、未知结果、原生回退、通用警告指标，并排除原始 ID、UUID、响应正文、URL 与凭据。
- [x] 自动化覆盖 503 后成功、永久 400、结果不明、topic 路由、同 UUID 重试与单次 hook `/events` 调用。
- [x] 真实 loopback sidecar + Feishu API 的私聊 create 与 topic reply 均为 `delivered/applied`，2 次发送全部成功；诊断未包含验收正文或 UUID。
- [x] 最终全量 gate `1389 passed, 4 skipped`、`git diff --check`、sdist/wheel 构建与干净 Python 3.12 wheel import `4.0.11` 通过。
- [x] annotated tag `v4.0.11`、GitHub Release、四个 assets/checksums 与公共 tagged installer fixture 验证通过。

### V4.0.10：事件传输安全边界（已发布）

- [x] 本机回环监听保持兼容；非回环监听默认拒绝，只有显式 `server.allow_non_loopback: true` 才允许启动。
- [x] 非回环 `/events` 使用 transport root 对 raw body、timestamp、nonce 做 HMAC-SHA256 鉴权，并拒绝错误、过期和重放 proof。
- [x] `/health`、CLI 与 card-safe diagnostics 增加不含敏感内容的 `event_auth_rejections` 可观测性。
- [x] 中英文架构、安装与安全说明更新；新增 fail-open/必须失败维护矩阵。
- [x] 安全专项 `523 passed`，候选全量 gate 与 `git diff --check` 通过。
- [x] sdist/wheel、干净 Python 3.12 import 与真实 Hermes/Feishu smoke 通过；客户端为 1 张完成卡、0 条匹配原生灰色重复正文，sidecar 发送/更新/鉴权拒绝均无异常。
- [x] 版本文档合入后的最终全量 gate `1362 passed, 4 skipped` 与 `git diff --check` 通过。
- [x] annotated tag `v4.0.10`、GitHub Release、四个 assets/checksums 与公共 tagged installer fixture 验证通过。

### V4.0.9：Feishu WebSocket live handler 稳定性热修（已发布）

- [x] Issue #130：startup hook 不再重建并替换已连接 Lark WS client 的 live `EventDispatcherHandler`。
- [x] 仅更新 `p2.card.action.trigger` processor callback，并通过 `_ws_thread_loop.call_soon_threadsafe(...)` 在 SDK 线程执行。
- [x] 不兼容的 handler 内部结构保持 fail-open，不回退到 live handler 整体替换。
- [x] Python 3.11.15 + `lark-oapi==1.6.8` + `websockets==15.0.1` 精确兼容 smoke 通过；Ubuntu 专用 CI job 已加入。
- [x] 感谢 @Jasonsun77 提供安装 hook 前后 A/B、断连时间线、SDK 版本与上游 #64712/#64741 关联证据。
- [x] 完整 gate `1330 passed, 4 skipped`、`git diff --check` 与真实飞书 420 秒 idle/message、`/model` callback/实际切换 smoke 通过。
- [x] sdist/wheel 构建与干净 Python 3.12 wheel import `4.0.9` 通过。
- [x] annotated tag `v4.0.9`、GitHub Release、四个 assets/checksums 与公共 tagged installer fixture 验证通过。

### V4.0.8：cron 原生附件投递热修（已发布）

- [x] Issue #127：cron 卡片成功后不再于 `extract_media` 前提前返回；有 `media_files` 时继续执行 Hermes 原生附件上传。
- [x] 卡片保留正文与附件摘要，原生 `cleaned_delivery_content` 清空，避免再次发送灰色 cron 文本。
- [x] V4.0.7 旧 cron hook 可安全迁移到媒体提取后的新锚点，保持幂等、可移除与 fail-open。
- [x] `/health` 记录真实 `native_delivery` 策略；感谢 @zyq2552899783-lgtm 报告 Issue #127。
- [x] 完整 gate `1328 passed, 3 skipped`、`git diff --check`、真实飞书 cron 文件 smoke、sdist/wheel 与干净 Python 3.12 import 通过。
- [x] `v4.0.8` tag、GitHub Release、四个 assets 与公共 tagged installer 验证。

### V4.0.7：Linux/systemd sidecar 生命周期热修（已发布）

- [x] Issue #125：Linux 上 sidecar 使用独立、可重启的 systemd user service，不再被 `hermes-gateway` 的 cgroup 重启连带杀死。
- [x] 旧 detached-process sidecar 仅在 PID/token/health 身份一致时迁移；systemd 重启后的 PID 变化继续通过 token + unit 安全管理。
- [x] `install.sh` 优先使用 Hermes venv Python，并保留 `HFC_PYTHON` 显式覆盖。
- [x] PR #124：孤立的 session-scoped 自我改进通知改为独立卡片，不占用下一轮对话卡片。
- [x] 自动化 gate `1324 passed, 3 skipped`、`git diff --check`、sdist/wheel 构建与干净 Python 3.12 venv import `4.0.7` 通过。
- [x] `v4.0.7` tag、GitHub Release、四个 assets 与公共安装验证通过。

### V4.0.6：Hermes 0.18.x 完成态与升级恢复热修（已发布）

- [x] Issue #118：新增显式 `--accept-hermes-upgrade` 恢复，默认 fail-closed，并保留升级后的 Hermes 源码。
- [x] PR #119：background process 与 `/background` 通知进入稳定 `system.notice` 卡片，保留 topic 路由并抑制重复灰色文本。
- [x] Issue #120 / PR #121：Hermes 0.18.x completion hook 在 `already_sent` 提前返回前执行，queued completion hook 可跨新版多行调用安装。
- [x] 本机 Hermes 0.18.2 runtime 从旧版升级到 4.0.6，hook marker、runtime import 与 doctor 完整一致。
- [x] 自动化 gate `1315 passed, 3 skipped`，#118 六条 sandbox 路径、sdist/wheel 与干净 venv import smoke 通过。
- [x] 2026-07-15 真实飞书：私聊 completion、私聊 `/background`、测试群聊 completion、话题 `/background` 全部通过；无灰色原生启动/答案，background 卡片从启动态原位更新到完成态且不残留“生成中”，sidecar 发送/更新零失败。
- [x] `v4.0.6` tag、GitHub Release、四个 assets 与公共安装验证通过。

### V4.0.5：Gateway runtime 版本同步热修（已发布）

- [x] Issue #115：安装器读取 Gateway venv 中的插件版本，不再把旧版可 import 误判为已升级。
- [x] runtime 版本落后时沿用当前安装源自动升级，安装后复核版本与模块路径。
- [x] 同版本保持幂等；metadata 异常或安装后版本不一致时明确失败。
- [x] `v4.0.5` tag、GitHub Release、四个 assets、公开安装与 #115 回复。

### V4.0.4：Markdown 媒体字面量与旧 callback 交互热修（已发布）

- [x] Issue #107：上游只返回一个含义不明确的 primary window 时使用 `limit` 标签，不误标为 `5h`；双窗口格式保持不变。
- [x] Issue #110：inline/fenced Markdown code 中的 `MEDIA:` 和本地路径不再进入附件提取与 native delivery。
- [x] Issue #112：lark SDK 已捕获旧 bound callback 时，`interaction.select` 仍由后台兼容路径转发 sidecar。
- [x] Issue #111：确认是 #106 的重复反馈，关联 V4.0.3 真实飞书验收。
- [x] `v4.0.4` tag、GitHub Release、四个 assets、公开安装与 issues 回复。

### V4.0.3：#106 stale-hook 媒体正文去重热修（已发布）

- [x] 复现仅升级 runtime、仍保留 V4.0.0 completion hook 时的灰色原生正文重复。
- [x] sidecar 已接管媒体完成卡后，仅抑制同 chat、正文精确匹配的下一次 Feishu 原生文本发送。
- [x] 原生图片/文件继续发送；其他正文、其他 chat、后续同文消息和 sidecar 失败保持 fail-open。
- [x] `v4.0.3` tag、GitHub Release、四个 assets、公开安装与 #106 回复。

### V4.0.2：旧 owned hook 安全升级热修（候选）

- [x] manifest/current/backup 哈希均可信且 owned markers 可精确还原 backup 时，允许重应用当前 hook。
- [x] 用户编辑、hash 不符、backup 不符、corrupt markers 和新版 anchors 不支持时继续拒绝修改。
- [x] 本机真实 V4.0.0 hook 升级演练通过，doctor 恢复为完整一致状态。
- [x] Issue #107 / @tianqiii：可选 `subscription_usage` footer，复用 Hermes 原生 Codex account usage；失败静默跳过。
- [ ] `v4.0.2` tag、GitHub Release、四个 assets、公开安装与 #106 回复。

### V4.0.1：原生媒体正文去重补丁（已发布）

- [x] Issue #106：卡片成功后只将媒体指令交给 Hermes 原生通道，不再重复发送回答正文。
- [x] 卡片正文隐藏 `MEDIA:` 与本地文件路径，附件摘要和原生图片/文件投递继续保留。
- [x] 卡片失败、非飞书平台和无显式媒体路径时保持原始 fail-open 行为。
- [x] V4.0.0 completion hook 可直接升级，不误报 corrupt patch markers。
- [x] `v4.0.1` tag、GitHub Release、四个 assets 与公开安装通过；升级恢复热修由 V4.0.2 接续发布。

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
- [x] 当时的 V3.8.3 边界：`/update` 不做交互卡片；该边界已由 V4.2.0 的“仅飞书私聊裸命令”维护确认卡替代。
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
- [x] 当时的 V3.8.5 边界：`/update` 保持 Hermes 后台升级命令，不纳入通用命令结果卡片化；V4.2.0 后私聊裸命令走专用维护卡。
- [x] 移除 card action 后额外调用 direct interactive `message.update` 的路径，改由 Feishu callback response 更新原卡片。
- [x] 补齐 `/new` 直通结果卡片、一次性上下文、V3.8.5 的 `/update` 普通路径和 V3.8.4 hook block 升级兼容测试。

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
- [x] 完全兜住极端 Markdown table 边界：当结构化拆分失败时输出安全折叠提示，避免回退 plain split（V4.4.0）。
- [x] 清理 terminal 后的 closed `FlushController`，并增加 `flush_controllers_collected` / `update_coalesced` 脱敏指标；V4.4.0 进一步把 queue peak 升级为真实 coalesced backlog depth。
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
