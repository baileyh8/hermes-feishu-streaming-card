# 真实飞书验收清单

自动化测试不能完全证明 Feishu/Lark 客户端体验。涉及卡片 UX、topic、系统提示、命令卡片的版本，发布前需要真实飞书 smoke。

## V4.4.0 新版 Hermes 原生能力中心验收

- 使用候选 wheel `4.4.0`、官方 Hermes `v2026.8.27` / `0.20.6` 和隔离 CLI Gateway；启动后 `/health` 必须为 `healthy`、integrity 为 `runtime_ready`，不得手工修改用户现有 Hermes 源码。
- 私聊发送 `/commands`：首页必须显示当前 Hermes 原生命令与 skill command 计数；分类选择、`/model` 命令详情、返回分类/首页和安全 `/status` 快捷按钮均需真实点击通过。
- 私聊发送 `/status` 与 `/context`：状态卡必须同时保留 KPI columns 和完整原文；尚无普通会话时 `/context` 显示明确空态，建立最小普通会话后必须显示 model、window、in-use、headroom、自动压缩阈值、累计吞吐和分类估算。
- 群聊以真实 `@bot` 发送 `/commands` 和 `/status`：能力中心、分类/详情/返回导航和快捷动作必须由原发起人操作成功；普通群消息必须生成单张持续更新的完成卡，并显示工具折叠区与 footer。
- 2026-08-31 候选验收结果：上述私聊和群聊路径通过；能力中心读取 63 个 Hermes 原生命令和 74 个 skill commands。普通私聊与群聊流式卡完成后，sidecar 为 `healthy / runtime_ready`，`events_received/events_applied=14/14`、发送 `3/3`、更新 `43/43`，event rejection、发送/更新失败和 profile mismatch 均为 0。
- 测试群只有一位真人操作者，changed-operator rejection 不冒充真实双人验收，继续由自动化攻击回归覆盖。验收记录不得包含真实 chat/user/message id、凭据、callback token、回答正文或私人截图。

## 首回复建 thread 候选验收

- 在测试群顶层发送一条触发消息，让 Hermes 在尚无 `thread_id` 时请求 `reply_in_thread`；首张 schema 2.0 流式卡必须出现在该消息新建的 thread 中，主群不得出现 top-level fallback 卡。
- 在同一 turn 依次触发普通 approval/clarify 和 runtime-admission interaction；每张辅助 legacy 交互卡都必须留在同一 thread，原 schema 2.0 卡继续作为唯一 stream PATCH owner。
- 连续触发两轮 interaction，并受控让第一轮交互卡发送失败后重试；session 的 reply anchor 与 thread placement 不得被后续 event message identity 覆盖，重试也不得降级为主群新卡。
- 显式 `reply_in_thread=true` 但缺少 reply anchor 时，确认 card 或 completion notification 不发送 top-level fallback；只保留脱敏拒绝分类，不记录真实标识符或正文。
- 记录前后脱敏 counters，要求 `feishu_send_failures`、`feishu_update_failures` 和 `last_route_error` 不新增；不得保存真实 chat/message/user id、callback token、回答正文或私人截图。

## V4.3.6 话题 create 与 requester mention 验收

- 在带 `thread_id` 但刻意没有 reply anchor 的 system notice 或 cron 场景触发一次卡片 create；请求必须以 `receive_id_type=chat_id` 成功落到父群，`last_send_error.api_code` 不得出现 `99992402`，且不得把 `omt_*`/`om_*` 作为 create receiver。
- 再以真实 `reply_to_message_id` 触发同类场景；卡片必须通过 reply API 留在原 thread，不能因前一条 fallback 永久丢失 anchored placement。
- 默认配置下分别触发 approval 与 clarify，合法发起人 `open_id` 只渲染一次 `<at>`。设置 `card.interaction_mentions.clarify: false` 后仅 clarify 不提及；设置 `card.mentions_in_cards: false` 后 approval、clarify 与 completion 全部不提及。
- 设置 `completion_notify: {enabled: true, mention: false}`，用无 sender 的系统/后台 turn 完成任务；必须发送一次不带 `@` 的普通完成通知。恢复 mention 默认值并提供非法 `open_id` 后不得发送通知，也不得把原始身份写入日志。
- 每轮都确认原 schema 2.0 卡仍是唯一 streaming PATCH owner，legacy interaction card 只是 auxiliary；`230099`、`200800` 与 `200673` 不新增。

## V4.3.2 Issue #227 卡片方言双轨验收

- 在同一 turn 记录脱敏前后 counters，发起一次 direct choice clarify 和一次 custom-input form clarify；每轮只发送一张辅助 legacy 交互卡，原 schema 2.0 streaming card 必须保持为后续 PATCH owner。
- 点击后 callback toast 显示“已选择”，辅助 legacy 卡原位变为无 button/form 的完成态；原 schema 2.0 卡从“已转入交互卡片”继续显示 answer/thinking/tool 更新并正常终结。
- `/health.metrics.feishu_update_failures` 不新增，`last_update_error` 不出现 `230099`/`200800`；Gateway/客户端不出现 `200673`。不得用普通任务卡成功替代 clarify 交互卡本身的验证。
- direct choice 与 custom-input form 都要覆盖；另复验 expired callback 的 legacy failed 卡，且 original schema 2.0 owner 只收到 schema 2.0 PATCH。
- 若发布前没有可用的真实测试会话，只记录自动化、CI 与公开安装证据，Issue #227 保持 Open 并请报告者在 Hermes 0.20.0 + lark-oapi 1.6.8 + 飞书 WebSocket 环境复测。
- 只记录计数、状态和脱敏版本信息；不保存真实 chat/message/user id、callback token、App Secret、用户答案、原始响应或私人截图。

## V4.3.1 Hermes 0.20 交互恢复验收

- 在非 default profile 发起一次 clarify 和一次 approval；卡片按钮必须携带 exact profile，Hermes WebSocket action 转发后 sidecar runtime callback attempts/successes 各增加，failures 不增加。
- 点击后不要再发送消息；同一 turn 必须继续显示 answer/thinking 流式 PATCH 并正常终结。不能只看到“已选择”后长时间无更新、最后才一次刷新。
- 显式切换 `card.interaction_mode: text`，发起一轮 interaction 后只回复一次编号或文本；Hermes 必须立即继续，Sidecar 不建立 runtime callback admission，旧卡不得一直显示“交互已过期”。
- callback 404/409/timeout/expired 与 profile mismatch 用脱敏 health 分类定位；不得记录真实 choice、token、chat/user/profile id 或回答正文。
- 若应用长连接确实收到零个 `card.action.trigger`，仍属于平台/应用订阅边界；先核对订阅、发布版本、应用身份和开放平台原始事件，再与“事件已到但本地 profile/callback 失败”区分。

## V4.3.0 Hermes 0.20 Hybrid 验收

- 在固定 Hermes `v2026.8.3` 安装后，`doctor --explain` 与 installer 显示 `integration.mode: hybrid`；manifest 为 V3，列出 17 个 patch group 与 7 个 target。重复 install 不改 manifest，restore 后 checkout Git clean。
- 普通回答只出现一张 streaming card；完成后没有第二条灰色原生正文。failed/interrupted 不得因为 `applied=true` 错误抑制 Hermes 原生失败信息。
- approval 只出现一张卡，点击 once/session/always/deny 必须唤醒当前 exact tool call；连续 approval、clarify、slash confirm 不串 choice、pending handle 或上一轮 hover 内容。
- subagent start/running/terminal 使用独立 timeline，`interrupted` 属于终态且迟到 running 不得重开；工具调用数不包含 subagent item。
- 断开 callback、过期 descriptor、session replacement、重复同选项和冲突选项都要保持原 pending/终态契约；不得出现第二套 poll/wait UI。
- Linux persistent smoke：确认 `loginctl show-user "$USER" -p Linger --value` 为 `yes`，执行 `enable` 后重启 user manager/主机，`systemctl --user is-enabled/is-active hermes-feishu-card-sidecar.service` 与 `status` 均正确；`disable` 后 unit 与私有 manifest 都消失。
- V4.3.0 原验收只覆盖 Issue #216 的平台零事件边界；V4.3.1 增加“事件已到、runtime 已继续但流式卡片未恢复”和 text fallback 首次回复的独立验收。
- 真实飞书消息、截图、chat/user/app identity 必须脱敏；本地 fixed-tag、loopback 与自动化通过不等同于真实客户端验收。

## V4.2.12 审批能力与零工具时间线验收

- approval 默认不显示或接受“其他”自定义输入；选项严格跟随 `smart_denied`、`allow_session`、`allow_permanent`，伪造固定值或 form input 返回拒绝且原 interaction 保持 pending。
- clarify 单选、多选与“其他”自定义答案继续可用；旧事件缺少 `allow_custom_input` 时仅 `kind=clarify` 兼容启用自定义输入，truthy string 不提升权限。
- 启用 reasoning timeline 时，无工具的初始加载态显示“等待工具事件…”，完成/失败态显示“暂无可展示的思考或工具记录。”，折叠 Header 始终为 0 次工具调用。
- `show_reasoning=false` 时不显示折叠 timeline，继续使用普通 tool summary；raw `thinking.delta` 不进入任何可见空状态。
- 本轮维护者不额外发送真实飞书测试消息。PR #205 描述中的真实飞书安装验收作为贡献者证据保留；PR #206 与两项组合结果由本地独立攻击测试、完整 pytest 和 GitHub 多平台 CI 覆盖，不把这些自动化写成维护者真实客户端复测。

## V4.2.11 旧交互卡接力快照验收

- 在一张仍显示 clarify 或运行中工具 Header 的流式卡上触发 `interaction.requested`；新卡必须出现在聊天底部并保持按钮可用，旧卡必须变为绿色“已转入交互卡片”。
- 旧卡正文、thinking、timeline、工具记录和附件保持可读，但 Header 不再显示临时工具/clarify 文案，卡内不存在 pending 按钮、interaction id 或 callback token。
- 连续触发两轮 interaction；前两张卡各只出现一次接力终态 PATCH，只有第三张最新卡可操作。完成选择和继续生成时，后续 PATCH 全部落在第三张卡。
- 受控让旧卡 PATCH 的全部有界重试失败；新卡仍保持可点击并可完成 interaction，`/health` 只出现脱敏的 update failure 证据，不回退到旧卡或重复发送交互。
- 受控延迟旧 animation frame；必须先观察 animation 取消完成，再出现“已转入交互卡片”最终 PATCH，之后旧卡不被运行态覆盖。
- 复验私聊、群聊/topic、`turn_id`、per-session card title、交互过期、form submit 与 native gray-text suppression；不在记录中保留真实 chat/message/user id、callback token、回答正文或截图中的私人信息。

## Windows installer follow-up 候选验收

- Windows 10/11 + Python 3.11 的 Hermes venv 首次导入 `lark_oapi.ws.Client` 或 HFC 超过 8 秒、但少于 30 秒时，官方 PowerShell `install/setup` 应继续完成；超过 30 秒或子进程非零退出仍必须失败。
- 新 ownership manifest 的 run/Cron/Base target 与 backup 路径均使用 `/`；由旧 Windows 版本写出的精确 `gateway\\platforms\\base.py` 与对应 backup 可安全重装/恢复，路径穿越、绝对路径和多余后缀继续拒绝。
- venv launcher 写入 parent PID、runner 使用 child PID 时，只有精确 `detached` manager、token 与 parent 关系可重绑；错误 token/manager/parent、非 Windows 和写后复核失败均拒绝。
- 人为令 PowerShell installer 的 `pip` 或 `hermes_feishu_card.cli setup` 返回非零，脚本必须返回失败且不打印 `done`。

## V4.1.4 Windows 旧版安装迁移验收

- 在 Windows 10 / Hermes v0.19.0 上保留 Issue #171 的原始 `gateway/run.py`、`.hermes_feishu_card.bak` 与 optional Cron，不再手工创建 manifest 或调用内部 `apply_patch()`。
- 通过官方 PowerShell `install.ps1` / `setup` 安装候选；必须看到 `manifest: rebuilt` 与 `install ok`，required exact Base backup/hook 和 `manifest_version: 2` 同步建立。
- 重启 Hermes Gateway 后运行 doctor，install state 必须为 `installed`；sidecar/Gateway runtime package 必须来自 Hermes venv 的 `site-packages` 且版本为 4.1.4。
- 发送一轮普通流式回答和一轮工具调用，确认卡片进入完成态、工具只出现一次、无匹配原生灰字重复。该真实飞书复测由 Issue #171 报告者确认前，不标记为已通过。
- 另用 owned marker 外增加无敏感信息测试行的隔离副本确认安装拒绝且文件不变；不要在真实 Hermes 工作树制造用户改动。

## V4.1.3 升级恢复、TurnRunner 与 V4.1 安全控制验收

- heartbeat fence：verified `installed` plan 在 startup grace 内外等待首次 heartbeat，或 Gateway 正常重启造成 heartbeat stale，均不得出现持久化 fence 或 repair mutation；新 matching `runtime.hello` 到达后应一次恢复 ready。
- review acknowledgement：只有双重 installed/integrity plan、sidecar health 不可达、无 pidfile、snapshot CAS 未变化且旧新 binding 指向同一 target identity 才允许迁移 plan fingerprint；不同 target 拒绝。V4.1.0 unbound empty hash 只迁移精确已知形态，unbound non-empty 拒绝，bound non-empty 保留 restart/hash。命令后人工重启 sidecar 与 Gateway。
- legacy process：私有 owned `0700` state dir 内的精确 `0644` legacy pidfile 可经同 inode/fd identity 收紧到 `0600`；非私有目录、symlink、未知 shape、identity race 全部拒绝。detached child 必须在读取配置/监听前验证父进程写入的精确 PID/token 管理记录；detached stop 需 loopback token 自停并确认 health 消失，不得向数字 PID/PGID 发 TERM/KILL。具体 non-loopback 地址需同时验证同地址族 loopback 管理监听，wildcard 不重复绑定；pidfile-less、旧接口或超时进程保持运行，先人工停旧服务再重跑。
- setup identity：以 `python -I` 复检 Hermes venv `site-packages`；构造 package version/Python identity 匹配与不匹配两条 `/health` 分支，确认仅后者触发 sidecar 受管自停/重启；start/status/stop 的 selected env 必须一致，之后人工重启 Gateway。
- TurnRunner：在包含 Hermes `1a3a9de` 或更新重构的源码上走官方 install/setup，doctor 必须为 `supported/full`；一轮真实工具调用只出现一个 tool summary/terminal/duration，流式 answer/thinking、clarify、approval、status 均进入同一卡片且无原生灰字重复。回滚/卸载必须逐字恢复 Hermes 源码。
- Issue #158 还需在 Ubuntu 24.04 / Hermes 实际 upstream update 后走完整官方命令复测；Issue #169 报告者还需在最新 Hermes 上复测上述 TurnRunner 路径。不得手工修改 fence、`gateway/run.py` 或调用内部函数。以上为 V4.1.3 发布前待验收项，未实际完成自动化、候选安装与真实环境复测前不得标记通过。

## V4.1.0 安全控制验收

- card → native → card：先让普通会话完成一张卡，执行 `chats use-native` 后从下一条新消息观察 Hermes 原生投递，再执行 `chats use-card` 恢复；正在运行的 turn 不能中途变轨，三步均不得重复正文。
- native 工具与反馈：在 native chat 中完成一轮带工具回答，并分别验证普通命令、system notice、approval/clarify 与 picker 按 Hermes 原生路径可见；`/hfc status` / `/hfc doctor` 仍必须是卡片。
- 表格：发送包含 7 张真实 Markdown 表格和一段 fenced fake table 的回答；默认 `compact` 只把前 5 张渲染为 table，其余字段列表保留全部行、单元格和后续正文。显式 `truncate` 单独做自动化兼容，不把数据丢失模式作为默认真实验收。
- 预算 handoff：在默认 profile 让无附件/媒体的最终序列化 card JSON 超过 28,000 byte。运行中只保留小型等待卡；完成后已有卡显示短 handoff，完整答案走 Hermes 原生消息。重复 terminal、Gateway ledger recovery、terminal 响应丢失后的 recovery lookup 与 ACK retry 在一小时窗口内复用相同分片 UUID；窗口外 exact descriptor 必须失效、sidecar 记录 `uncertain`。若 Hermes 随后执行有界 native recovery，消息必须带可见 `RECOVERED_MARKER`、使用普通随机 UUID，不能伪装成 exact retry；不宣称永久 exactly-once。再用 secondary profile 与带真实附件/媒体的回合确认它们继续走原生 best-effort，不广告 ACK capability。
- integrity：先验证 `notify` 只报告；在严格 Git provenance fixture 中执行 `integrity migrate-safe`，确认 `sidecar.restart_required: true`、`gateway.restart_required: false`。safe repair 后确认 readiness 显示 `gateway.restart_required: true`，Gateway 未被自动重启；重启 sidecar 后该 fence 仍在，旧 runtime heartbeat/hello 和新 runtime heartbeat 均不能清除；手动重启 Gateway 后，只有不同且 generation/package 匹配的新 `runtime.hello` 恢复 ready。持久化文件不得包含 runtime id 原文。
- service/Docker：Linux 覆盖 `auto`、`systemd-user`、`systemd-system`、`detached`，确认 `auto` 不调用 sudo/系统总线；Docker Compose 以普通 setup/sidecar/Gateway 容器运行，不使用 privileged 或 systemd。
- 全程只记录脱敏计数、状态和 release version，不记录真实 chat/message/user id、secret、transport proof、本机路径、回答正文或 recovery fingerprint。真实验收、Linux/Docker smoke 和 public tag/install 未完成前，不得写成已经通过。

## V4.0.21 内容完整性与媒体/notice 组合验收

- Issue #155：按 `tool -> answer -> completed` 走一轮，完成卡必须保留完整用户可见答案；只有在明确 `answer -> tool` 边界后，前一段答案才能进入时间线。
- Issue #147：同一图片回合须有一张完成卡和一条 native image；匹配的原生媒体文本只能抑制一次，后续无关原生文本不得被抑制。
- 让已接管卡片的 `system.notice` 返回 accepted，确认不出现 uncertain-delivery warning；保留既有 `MEDIA:` 清理、附件和 fail-open 分支。
- 不改变卡片 UI 或配置；不要在验收记录中写入真实 chat/message/user 标识符、凭据、token、图片内容或本机路径。

2026-07-28 真实验收结果：图片回合产生 1 条带标记、非“生成中”的 interactive completion card 与 1 条 native image，没有 uncertain-delivery warning。正常工具回合的两段答案保留在同一张卡，bot 原生标记重复为 0。

sidecar 最终 metrics：`events_received/events_applied=23/23`，1 次发送成功、16 次更新成功；event rejected、auth rejection、send/update failures、notice uncertain warnings、notice update failures 均为 0。Gateway 日志确认 Feishu WebSocket 已连接，Hermes venv site-packages 中的候选 runtime 为 4.0.21。

本记录不宣称截图或桌面/移动端视觉 QA，也不宣称真实故障注入。公开 tagged installer 与 Release assets 仍待 post-tag 验证；验收记录不包含真实标识符、marker 文本、本机路径、凭据或测试图片内容。

## V4.0.20 notice 异步 ACK 语义

- 已有卡片收到 `system.notice` 时，`/events` 只在事件已应用且 PATCH 任务已排队后返回 `delivery.outcome=accepted`；hook 不再发送“投递结果无法确认”的灰色误报。
- 独立 notice 的首次 create/reply 仍需确认 `delivered` / `not_sent` / `unknown`，不得用 `accepted` 替代实际初始投递结果。
- 故障注入让 notice PATCH 的内部重试耗尽后，`notice_update_failures` 增加一次，`last_update_error` 只包含异常类型与白名单 `status_code` / `api_code`，不包含响应正文、token、URL 或凭据。

2026-07-22 发布候选证据：本地真实 aiohttp `/events` 链路与 Hermes hook 回归确认 `accepted + applied=true` 会被接管，缺少 `applied` 的 accepted ACK 会 fail-open；模拟 Feishu 503/API 9499 的三次 PATCH 均失败后，异步失败指标增加一次且诊断未泄露注入的私密 token。该协议修复不改变卡片视觉结构，不把自动化写成飞书客户端视觉复验；公开版安装后将复核两台 Mac 的 runtime 来源与 Gateway/sidecar 状态。

## V4.0.18 Hermes Feishu SDK 兼容门禁

- Hermes Gateway 进程存活不能单独证明 Feishu 在线；日志必须出现 `✓ feishu connected`，且不能持续出现 `extra_ua_tags` 构造错误。
- Hermes adapter 使用 `extra_ua_tags` 时，`doctor --json/--explain` 必须显示 `feishu_sdk`；不兼容时给出 `feishu_sdk_incompatible`。
- `setup/install` 修复后必须复检 `lark_oapi.ws.Client` 构造签名，并重启 Gateway 才视为恢复。

2026-07-22 验收结果：真实 Hermes v0.19.0 Gateway 进程仍在运行，但 Feishu connector 因 `lark-oapi 1.5.3` 不接受 `extra_ua_tags` 而持续失败。将 Gateway venv 修复到 `lark-oapi 1.6.8` 后，构造签名检查通过，`uv pip check` 确认 214 个包兼容，Gateway 与 sidecar 保持运行，日志恢复 `✓ feishu connected` 且未再出现新的参数错误。自动化使用同一失败条件完成红灯/绿灯回归。

## V4.0.17 并行同名工具事件关联

- 两个并行同名工具必须渲染为两条独立事件，不能重复显示任一查询摘要或参数。
- “思考与工具”次数按真实调用计数；两个工具显示 `2 次工具调用`，不能把四个生命周期事件显示为 `4 次`。
- 每条完成事件标题只显示自己的耗时，详情末尾不能再出现第二个 `耗时:` 行。
- 最终答案、工具时间线顺序、topic/reply anchor 和 footer 保持既有行为。

2026-07-22 发布候选证据：真实 Hermes 日志确认问题样本实际执行了两个不同 `web_search`，分别耗时 `2.12s` 和 `2.47s`；旧卡片因工具名关联重复了第二条查询并混入两个耗时。候选版使用 Hermes 稳定 `call_id` 重放同样的并行生命周期，得到两条独立详情、`2 次工具调用` 和各自耗时；全量自动化与真实 Gateway source patch 编译/还原验证通过。公开版安装后的本机 runtime/hook/sidecar 来源另行复核。

## V4.0.16 加载态去重与真实工具耗时

- 初始加载卡的 Header 只显示 `Hermes Agent`，正文显示动画“正在加载上下文…”，两处不能重复。
- 工具开始后，subtitle 显示当前动作；没有模型正文时，不保留加载占位。
- 工具完成行显示真实耗时；上游显式 duration 优先，兼容兜底只能使用可配对的 started/completed 事件，terminal-only 不得伪造。
- 查询摘要、参数、最终答案、工具时间线结构与 footer 保持既有行为。

2026-07-22 发布候选证据：本机真实 Hermes `progress_callback` 源码确认 `tool.completed` 通过 `kwargs.duration` 提供耗时；按该 callback 结构执行候选 hook/session/render smoke，得到 `✓ web_search · 1.75s`，查询摘要和参数保留，工具开始后无空正文加载占位。renderer/session/server 与全量自动化通过。本补丁不重复宣称飞书客户端视觉复验；公开版安装后的本机 runtime/hook/sidecar 来源另行复核。

## V4.0.15 工具事件视觉与加载动画

- 首个模型/工具事件前，同一张卡显示“正在加载上下文…”及可见 spinner；不能发送额外原生灰色消息或第二张卡。
- 运行中工具的首行显示语义状态、工具名与耗时，次行显示参数摘要；完成、失败、取消、等待的颜色和详情保持可区分。
- spinner 通过同一 card message PATCH 推进；正文、工具终态或消息终态到达即停止，最终只留下单张完成卡。
- topic/reply anchor、工具失败详情、终态 drain 和 create/PATCH 失败回退按既有规则保持。

2026-07-22 验收结果：通过正式 patcher 接入真实 Hermes `v2026.7.20`，使用配置模型 `deepseek-v4-flash` 在飞书中观察到初始“正在加载上下文…”spinner、同卡切换到运行中终端工具、再收束为单张完成卡；候选 sidecar 共处理 10/10 个相关事件，2 次发送、35 次更新全部成功，发送/更新失败和事件拒绝均为 0。Hermes 升级覆盖 hook 的用户侧防护由临时 fixture 完整闭环验证；不把它写成飞书客户端视觉验证。

## V4.0.14 长任务 heartbeat 热修

- 对同一个真实用户消息 reply anchor 连续观察或等价重放 6 分钟、9 分钟 `Working` heartbeat：只能创建一张独立卡，后续为同卡更新。
- heartbeat 卡 Header 保持“运行中”，不能出现“已完成”副标题；最终 `message.completed` 到达后才转为完成态。
- 同一 chat 的两个不同原始消息锚点必须生成不同 independent card identity，不能互相覆盖。
- 受控 unknown delivery 后的下一次 heartbeat 仍回到同一独立生命周期；既有通用警告不得重复原始通知正文。

2026-07-20 发布边界：真实 Feishu 已在公开版 `v4.0.13` 复现两张矛盾状态卡并确认根因；候选修复通过等价 6/9 分钟事件重放、最终完成和 unknown delivery 回归。本次不再次等待真实长任务窗口，不把自动化重放写成客户端视觉复验。

## V4.0.13 全命令反馈卡片

- `/status`、`/usage`、`/commands`、`/reasoning` 与一个 unknown command：每条反馈使用独立命令卡，无灰色原生文本；`/commands` 长 Markdown 不缺段、不破坏代码块。
- 同一命令产生两条以上反馈时只 create 一次，后续更新 same card；topic 内 reply anchor 不掉出原话题。
- 手动 `/compress`：先显示蓝色“正在压缩上下文”，完成后原位显示 Hermes 的 messages/tokens 统计；no-op 和 aborted warning 也更新原卡。
- `/model`、裸 `/resume`、`/new` confirmation：继续使用已有交互卡，选择/确认后原卡更新，不出现第二张结果卡。
- `/learn` 或 `/blueprint`：即时确认使用命令卡，后续 Agent reasoning/answer 使用普通流式卡，不串到命令卡。
- V4.0.13 当时的 `/update`：重启前反馈使用通用命令卡；该边界已由 V4.2.0 的私聊裸命令专用维护卡替代，群聊和参数化命令仍保留 Hermes 原路径。
- 受控让 command card create/PATCH 失败：对应 Hermes 原始反馈必须通过 native fail-open 可见，不能静默丢失。
- 媒体/附件命令继续投递原生文件；文本卡片不能吞掉附件。

## V4.0.12 上下文压缩与字号（发布候选验收）

- 使用官方 installer/patcher 更新真实 Hermes；确认 `doctor` 的 `status_callback=true`，不得手工编辑 `gateway/run.py`。
- 真实长会话触发 Hermes 原生 `Compacting context`：压缩开始时同一张 primary card 的 Header 显示“正在压缩上下文”，后续 answer/tool 清除阶段并继续更新原卡；完成后仅剩一张终态卡，无灰色原生压缩提示。
- 以压缩为首个可见事件时，确认 `create_session=true` 只创建一张卡，私聊与 topic reply anchor 不变。
- 不接受静默 watchdog 或百分比作为验收证据；必须观察真实 callback 和后续事件。
- 桌面端与移动端分别检查 `body`、`reasoning`、`tool`、`notice`、`footer` 的 scalar 和 `pc`/`mobile` 映射，覆盖长中文、代码块、表格、深色模式与 streaming-to-terminal；不得出现 `hfc_*` 可见文本或跨 bot 字号污染。
- 卡片物理 width/height 不在验收范围，由 Feishu/Lark 客户端控制。

2026-07-18 发布候选验收边界：已通过官方 setup/patcher 将候选 runtime 装入真实 Hermes，`doctor` 确认 `status_callback` capability 与 install state 一致；真实 Feishu 字号演示卡完成 create + update，五类 alias 均进入 Card JSON。手动 `/compress` 不经过 `_status_callback_sync`，因此不作为自动压缩 callback 证据；按发布决定不再执行自动压缩长会话 smoke，也不宣称桌面/移动端视觉验收完成。Issue #136 的 selected-env live/noop 分支由真实子进程集成测试覆盖；报告者的 Linux/systemd 环境仍邀请发布后复验。发布后进一步确认 annotated tag `v4.0.12` 指向 `00a48a7`，四个 assets/checksums 与公共 tagged installer 均通过；这些发布验证不扩大上述真实客户端验收结论。

## V4.0.11 system.notice 可靠投递（发布候选验收）

- 私聊触发一条独立 `system.notice`，确认 create 请求携带稳定 `delivery_uuid`，正常路径只有一张卡和 0 条灰色原文。
- topic 内触发 notice，确认 reply 保留原 `reply_to_message_id` / `thread_id`，卡片与任何降级提示都留在 topic。
- 受控测试让前两次 Feishu create/reply 返回 503、第三次成功：三次使用同一 UUID，最终只创建一张卡，`feishu_send_retries=2`。
- 受控永久 400 返回 `not_sent`：只出现一次原始通知文本，`notice_native_fallbacks` 加一。
- 受控 503 耗尽或连接结果不明返回 `unknown`：只尝试一次 `⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。`，不重复原始通知文本，`notice_uncertain_warnings` 加一；飞书完全不可用时不要求该提示必达。
- 检查 `/health`：`feishu_send_retries`、`feishu_send_unknown_outcomes`、`notice_native_fallbacks`、`notice_uncertain_warnings` 与脱敏 `last_send_error` 符合分支，且没有原始 chat/message id、UUID、通知正文、URL、token 或 secret。

2026-07-18 发布验收结果：真实 Hermes `v2026.7.7.2` 通过项目 CLI 重载当前工作树的 sidecar，并使用 Gateway 官方 CLI 重启 Gateway；`doctor` 显示 runtime/import/install state 一致。通过 loopback `/events` 向已认证 Feishu 会话执行私聊 create 与 topic reply，两条事件均返回 `delivered/applied`，sidecar 指标增加 2 次接收、2 次应用和 2 次成功发送，失败、重试及 unknown 均未增加；card-safe diagnostics 未包含验收正文或 `delivery_uuid`。受控 503/400/unknown 与 hook 原生回退分支由自动化集成测试覆盖；本次直接 `/events` smoke 不宣称已完成客户端原生灰字去重视觉验收或真实 Feishu 故障注入。发布后进一步确认 annotated tag 指向 `2a806d3`、四个 assets/checksums 全部通过，公共 `v4.0.11` tag 可安装到独立 Python 3.12 `site-packages`，临时 Hermes fixture 的 hook install state 完整一致。

## V4.0.10 事件传输安全边界

- 使用官方 `install` 把真实 Hermes Gateway venv 升级到 4.0.10；不得手工编辑 `gateway/run.py`。
- `doctor --json` 必须显示 runtime import、install state 与 recovery state 一致。
- 默认 loopback 配置发起一条唯一普通消息，确认 hook 事件能创建、更新并完成一张卡；sidecar 的 `events_rejected`、`event_auth_rejections` 和 Feishu send/update failures 保持为 0。
- 通过 Feishu API 结构检查完成卡只有一张，匹配答案不存在 app text duplicate；不在记录中暴露 chat/message/user id。
- 非回环默认拒绝、显式 opt-in 后强制 event proof、错误/过期/重放 proof 的 401 由自动化矩阵验证；真实 smoke 不临时把生产 sidecar 暴露到非回环地址。

2026-07-17 发布验收结果：真实 Hermes `v2026.7.7.2` 通过官方安装路径将 Gateway venv 从 4.0.9 升级到 4.0.10，`doctor` 的 runtime/import/install/recovery state 一致。已认证 Feishu user 发起唯一 transport smoke，sidecar 收到并应用 3/3 个事件，1 次发送和 2 次更新全部成功，事件/鉴权拒绝与投递失败均为 0；客户端为 1 张完成 interactive card、0 条匹配原生 app text duplicate。发布后进一步验证 annotated tag、Release、四个 assets/checksums 与 public tagged installer fixture；真实 Gateway/sidecar 强制切换到 `v4.0.10@e464316` 的 `site-packages` 后再次完成 3/3 事件 smoke，目标卡完成态 1、运行态 0、原生重复 0。Gateway 与 sidecar 在 smoke 前后保持运行。

## 准备

- 本机 Hermes Gateway 正常运行。
- sidecar 已启动：`python -m hermes_feishu_card.cli status --config ~/.hermes_feishu_card/config.yaml`
- `doctor --explain` 通过，且 `Runtime import` 指向当前版本。
- 飞书 bot 已在目标会话可用。
- 不在仓库、issue 或日志中暴露 App Secret、tenant token、真实 chat id。

## V4.0.9 WebSocket live handler 稳定性

- 使用官方 `restore` / `install` 路径把真实 Hermes Gateway 更新到 V4.0.9，不手工编辑 `gateway/run.py`；`doctor --explain` 必须显示 runtime/import/install state 一致。
- 启动后先发送一条普通消息并确认收到流式完成卡，再保持 WebSocket 空闲超过 Issue #130 报告的 3–6 分钟故障窗口。
- 空闲窗口后再次发送普通消息，确认仍能收到回复；期间 Gateway 不得出现 Lark `ConnectionClosedOK` 后的 restart loop，进程 identity 和 restart count 保持稳定。
- 发送 `/model` 或 `/new` 并完成一次卡片交互，确认 `p2.card.action.trigger` callback 仍可用，且没有 callback timeout 或灰色 fallback。
- 自动化门禁在 Python 3.11 上使用 `lark-oapi==1.6.8`、`websockets==15.0.1`，断言 startup hook 前后 live `EventDispatcherHandler` identity 不变，并且卡片 callback 已通过 `_ws_thread_loop.call_soon_threadsafe(...)` 更新。

2026-07-16 验收结果：真实 Hermes v2026.7.7.2 通过官方 `restore` / `install` 路径加载 V4.0.9，`doctor --explain` 的 runtime/import/install state 一致。私聊先后完成 pre-idle、超过故障窗口的 420 秒空闲、post-idle 与额外 liveness 消息；Gateway/sidecar PID 全程不变，sidecar 10/10 个事件全部应用，3 次发送与 7 次更新成功，发送/更新失败均为 0。`/model` 卡片成功打开，Provider 选择回调原位进入模型列表，随后由 Bailey 手动切换模型并确认成功，没有 callback timeout。完整自动化为 `1330 passed, 4 skipped`，精确 SDK smoke 另行通过；四个发布资产 checksums 和公共 tagged installer fixture smoke 均通过。

## V4.0.8 cron 原生附件投递

- 使用 no-agent 一次性 cron，让脚本返回一段安全测试正文和一个 `MEDIA:` 本地文本文件。
- 验收飞书同时出现一张完成卡和一条独立文件消息；卡片正文不再额外生成灰色原生文本。
- 从飞书下载文件并与源文件逐字节比较；检查 sidecar 的 cron send 成功且 `cron_fallbacks=0`。
- 检查真实 Hermes scheduler 的 HFC cron hook 位于 `media_files` 安全过滤之后，`doctor --explain` 显示 runtime/import/install state 完整一致。

2026-07-16 发布候选验收结果：真实 Hermes 0.18.2 从 V4.0.7 旧 hook 通过官方 restore/install 路径迁移到 V4.0.8；一次性 cron 在测试群产生完成卡和独立文件消息，下载文件与源文件字节一致，sidecar send 成功且无 fallback。Hermes 上游 `cron run` 在成功的一次性任务自动删除后仍会显示已知的 `Ran now: failed` 状态误报，不影响三方证据一致的验收结论。感谢 @zyq2552899783-lgtm 报告 Issue #127。

## V4.0.6 Hermes 0.18.x 完成态与 background 通知

- 私聊 completion：发送一个带工具调用的普通任务，确认卡片从运行态进入完成态，`diagnostics.last_terminal_event` 有记录，且没有重复灰色最终回复。
- 私聊 `/background`：启动一个短后台任务，确认启动确认与 running/final 通知使用卡片；同一任务保持稳定卡片 identity，完成后无额外灰色正文。
- 测试群聊：@bot 执行普通任务和 `/background`，确认完成态、群聊路由和原生重复抑制与私聊一致。
- topic/thread：在测试群的话题回复中执行 `/background`，确认 running/final 更新留在原 thread，不外溢到主群。
- installer：真实 Hermes 0.18.2 runtime 版本、`COMPLETE` / `QUEUED_COMPLETE` marker、`doctor --explain` 均通过；`--accept-hermes-upgrade` 的源码替换路径只在临时 sandbox 验证，不手工编辑真实 `gateway/run.py`。

2026-07-15 发布候选验收结果：runtime/hook 安装、`1315 passed, 3 skipped`、#118 sandbox 与干净 wheel import 通过；私聊 completion、私聊 `/background`、测试群聊 @bot completion、群话题 `/background` 全部通过。background 启动与终态原位更新同一张卡片，终态无“生成中”，没有灰色原生启动/答案；topic 回复留在原 thread，sidecar 发送/更新失败均为 0。

## V4.0.0 实时双轨卡片

使用刻意准备、适合公开展示的任务文案验证以下状态；截图只保留真实飞书卡片区域，不包含群名、头像、真实 chat/open id、无关会话或桌面内容。

- 运行中：Header title 保留用户配置；Hermes 每次发送非空 `progress_callback.preview` 时，subtitle 根据工具名原地更新为动作摘要，不直接暴露完整命令、URL query 或私有路径；正文独立累积公开 `thinking.delta`。
- 等待用户：clarify/approval 问题只在 Header 出现一次，正文保留必要说明和原生按钮/选项；点击后继续 PATCH 同一张卡。群聊必须由任务发起者点击，其他成员点击应被拒绝且不消耗交互。
- 失败：Header 保留最后一条有效工具预览，正文明确显示 Hermes 的失败原因；footer 只有失败状态。
- 已完成：飞书原生回复引用作为唯一 Header 显示用户原指令，Card JSON 不再叠加配置标题；正文只显示最终答案，完成态 footer 才显示时长、模型、token 和 context 数据。没有有效 reply anchor 的兼容路径仍使用配置标题 fallback。
- 回归：工具 timeline、附件顺序、topic 锚点、command/operations 卡片、原 footer 布局和原生灰色消息抑制保持不变。

推荐展示提示词：

```text
请查询广州未来两小时的天气变化，并给我一份简洁的通勤建议。请先核对天气数据，再整理结论。
```

```text
请把广州周末出行建议整理到演示文件中。覆盖现有演示内容前，请先让我确认。
```

```text
请读取演示天气数据并生成摘要；如果数据源不可用，请明确报告失败原因。
```

## V3.10.0 `/resume` 与 footer

- 私聊：发送裸 `/resume`，确认只出现一张原生下拉卡、当前会话有标记；选择其他会话后先显示恢复中，再在原卡显示结果，无灰色文本列表和 callback timeout。
- 当前会话：再次打开 picker 并选择当前项，确认 original Hermes handler 返回 already-on 结果，不执行第二套 switch 逻辑。
- 过期/无效：使用过期 state 或无效 option，确认原卡显示失效提示且不切换会话。
- 群聊：发起者可选择；另一位用户点击被拒绝且 state 保留，随后发起者仍可完成。
- topic：picker 与结果留在原 topic；reply anchor/thread metadata 不丢失。
- fallback：session DB 空、adapter/card 不可用、群聊发起者 `open_id` 无法验证时，Hermes 原生文本列表仍可用。
- footer：常见模型名只有文本颜色变化；divider、字段顺序、分隔符、字号和普通卡 footer/layout 不变，未知/特殊字符模型名无 markup 注入。

## V3.9.1 可靠性热修

- 完成答案：构造 completed event 带较长 suffix 的任务，确认卡片保留完整正文且没有灰色重复 reply。
- 打断任务：旧任务仍在更新时发起新任务，确认旧卡收束为中断终态，迟到更新不再恢复运行态。
- 模型选择：打开 `/model`，选择模型后 callback 不出现超时 toast；先显示切换中，随后原卡显示成功或失败终态，不额外重复发送结果卡。
- 安装恢复：在临时 Hermes sandbox 验证 marker-only 安全恢复；不要在真实运行目录手工编辑 `gateway/run.py`。
- 回归：普通流式卡 footer/layout 不变。

## V3.9.0 运维卡（部分通过）

以下项目必须在真实飞书完成后才可标记通过；当前自动化证据不替代这些 smoke。

- 私聊：`/hfc doctor` 打开运维卡，执行重新检测、两步安全修复、重启确认；确认普通流式卡 footer/layout 快照不变。
- 群聊（group）：发起者能够完成 repair/restart；第二位操作者确认时被拒绝；再次由发起者确认后完成，并检查没有泄漏 chat id、token 或 transport secret。
- topic：在话题内打开运维卡后，普通 topic 流仍更新原卡，运维卡不改写普通 footer/layout。
- cron：cron 投递和普通定时完成卡不被运维操作阻断。
- profile route mismatch：以 main/child profile 或错误 `HERMES_FEISHU_CARD_PROFILE_ID` / endpoint 配置复现 mismatch，确认 `status`/`doctor` 仅显示脱敏 route chain，并修正后恢复。

2026-07-11 已通过的私聊基线：

- `/hfc doctor` 只生成一张运维卡，没有灰色原生未知命令。
- 中文诊断摘要与详情可见，footer 保持不变。
- 连续两次重新检测均快速返回，后台 successor 按钮仍可点击，最终 PATCH 同一张卡片。
- 本轮回调可靠性复测中，“查看诊断”和连续两次“重新检测”均在 156–201 ms 内 ACK；没有新增“目标回调服务超时未响应”，过渡态与终态继续 PATCH 原卡。
- 临时 Hermes sandbox 中两步安全修复成功；卡片实际重启 Gateway，先显示进行态，随后同卡显示完成态。
- 普通流式卡从生成中到完成态保持一张卡，完成 footer/layout 不变，没有灰色重复答案。
- 本轮 sidecar 发送与更新均成功，Gateway 日志没有新的 operations forward timeout。
- no-agent 一次性 cron 的结果正文进入普通完成卡；sidecar 的 event receive/apply/card-send 指标均成功且没有 native fallback。
- Hermes 上游 `cron run` 会在成功的一次性任务自动删除后再次读取 `last_status`，因此本次终端显示 `Ran now: failed`。这属于上游 CLI 状态误报；以飞书卡片、sidecar metrics 和保存的 cron 输出三方一致判定 cron 卡片验收通过。
- 临时设置错误 `HERMES_FEISHU_CARD_PROFILE_ID` 后，`doctor --explain` 显示 `profile_unknown` 与缺失 route，不暴露 chat id、token 或 secret；移除临时环境后恢复默认 profile，持久配置未变。

仍待真实验收：群聊发起者与换操作者拒绝、topic。existing-container Docker 见 release-readiness 单独门禁。

真实验收状态：**部分通过**。

## 普通会话

提示词：

```text
查一下广州明天天气
```

验收：

- 首张卡片出现。
- 正文持续更新。
- 工具调用进入“思考与工具”。
- 完成后只有一张最终卡片，没有额外灰色最终答案。

## Feishu topic / thread

在飞书会话中创建或打开话题，在话题回复框里发送：

```text
请验证当前 Hermes 飞书卡片插件是否已经支持话题内卡片连续更新。不要直接回答，先说明你会从哪些证据判断；然后依次检查本地版本、CHANGELOG、测试用例和运行状态；每次工具调用前先给一句阶段性判断。
```

验收：

- 右侧话题面板中出现卡片。
- 后续工具和答案持续更新同一张卡片。
- `思考与工具` 折叠区可展开，并显示工具 timeline。
- 完成态仍在话题面板内。
- 没有重复外溢的灰色 `system.notice`。

## 系统提示 suppression

提示词：

```text
V3.8.9 notice suppress smoke: please run terminal command date, then reply exactly topic smoke ok
```

验收：

- 卡片完成并回复 `topic smoke ok`。
- 如果触发 `Codex gpt-5.5 caps context...` 等上下文提示，不应额外出现在卡片外灰色消息里。
- Gateway 日志允许出现 `system notice native fallback suppressed`，表示已识别并抑制原生 fallback。

## Slash command cards

发送：

```text
/new
```

验收：

- 出现独立确认卡片。
- 点击“允许一次”或“始终允许”后有状态反馈。
- 允许后的 reset 结果以卡片反馈，不退回灰色文本。

发送：

```text
/model
```

验收：

- 出现模型选择卡片，第一层 Provider 数量、名称和当前项与本机 Hermes CLI `/model` 一致。
- 进入 DeepSeek 等已知 Provider，确认第二层模型数量和模型名称与 CLI 一致，不出现其他 Provider 的模型。
- 点击“返回”后恢复 Provider 列表；再次进入 Provider 后选择模型。
- 结果卡片显示模型已更新。
- 再问“现在是什么模型”，模型应与选择一致。
- 全程没有 callback timeout toast，也没有灰色重复消息。

## 长内容和 Markdown

提示词：

```text
生成一个包含 20 行、4 列的 Markdown 表格，并在后面附一个 80 行 Python 代码块。要求保持表格和代码块结构完整。
```

验收：

- 长表格没有被飞书渲染成 raw markdown。
- code fence 完整，没有半截围栏。
- 卡片完成后没有重复灰色全文。

## 记录方式

验收完成后可记录到 release notes 或 issue comment：

```text
真实飞书验收：
- 普通会话：通过
- 话题回复：通过
- system.notice suppression：通过
- /new：通过
- /model：通过
- 长 Markdown：通过
```

截图入库前需要遮挡私人头像、姓名、chat id、群名和不适合公开的上下文。
# Private `/update` acceptance

- Verify `maintenance status` is ready before opening Feishu.
- A private bare `/update` produces one confirmation card; a group command or
  `/update --yes` remains native Hermes behavior.
- A different operator cannot confirm, an expired or repeated click is
  rejected, and cancel performs no mutation.
- With active work, the card shows draining before mutation. The same card then
  shows hook restoration, Hermes update, exact HFC reinstall, service startup,
  verification, and success.
- After success, `doctor --explain` must report the expected HFC version,
  `site-packages` import origin, installed hooks, and ready sidecar/Gateway.
- Any tracked non-HFC edit or incomplete Git operation must show unavailable
  and leave Hermes unchanged. Untracked user files must remain intact.

## 稳定性回归补充

按 [稳定性测试规则](stability-test-policy.md) 验证失败任务不会显示成功或发送成功通知；工具执行中发新消息后，旧卡保留内容并明确未确认完成，新卡继续独立更新。授权须记录首次点击到实际恢复执行的结果，不能仅凭按钮变绿判定成功。Topic 命令须同时核对回复位置与重复消息数。
