# 发布准备说明

[中文](release-readiness.md) | [English](release-readiness.en.md)

当前发布候选为 `4.4.1`。本轮处理 Hermes 0.21 facade 拆分兼容、话题后续回复、单进程多 profile 路由、审批完整性和思考展示，并更新 CodeQL。聚焦自动化已通过；当前候选的完整测试、普通 wheel 验证及 GitHub CI 以本轮发布记录为准。真实 Feishu/Lark 客户端复测尚未执行，旧版本的真实 smoke 不代表本轮验收。release PR、exact merge SHA、public tag/install 与 Release assets 只有完成后才会标记通过。自动化不冒充平台验收。

V3.9.0 和 V3.9.1 已于 2026-07-11 发布。V4.0.13 的通用命令链仍保持“重启前反馈进入命令卡”的历史契约；V4.2.0 只把私聊裸 `/update` 收束到更严格的专用维护卡。

## 已具备

- Hermes `v2026.4.23+` 目录检测和 fail-closed 安装。
- 最小 Hermes hook、备份、manifest、restore/uninstall。
- sidecar `/events`、`/health`、进程 start/status/stop。
- Feishu CardKit HTTP client，已用 mock Feishu server 和真实 Feishu 测试应用覆盖 tenant token、发送和更新。
- 手动 `smoke-feishu-card` 命令。
- E2E 预览材料和生成器。
- 真实长卡压力测试：同一张 Feishu 卡片更新到 16k 中文字符成功。
- 真实 Hermes `v2026.4.23` 目录 `restore -> install` 循环验证。
- Hermes `0.13.0+` / `0.14.0` / `0.15.x` / `0.17.x` / `0.18.x` / `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` / `v2026.7.7.2` 使用 `gateway_run_013_plus` hook strategy，旧版 `v2026.4.x` 保持 `legacy_gateway_run`。
- 飞书卡片按钮交互覆盖 `interaction.requested`、`/card/actions`、`/interactions/{interaction_id}` 的本地 mock 验收；localhost/private sidecar 的默认 `auto` 走 WebSocket-native callback，显式 `card.interaction_mode: text` 保留编号文本 fallback。
- 飞书 thread 消息会携带可选 `thread_id`，有 reply anchor 时通过 Feishu reply API 把初始卡片放回原 thread，后续更新继续 PATCH 同一张卡片。
- cron delivery 支持从 `deliver: "feishu:oc_xxx"` 提取 chat id，也支持 `deliver: origin` / `deliver: all` / `origin,all` 先解析到 Feishu origin 或 scheduler targets，避免定时投递退回 plain text；`deliver: local` 仍保持无投递。
- Markdown 长表格/长代码块超过 `MAIN_CONTENT_CHUNK_CHARS` 后按完整结构重复切分，避免 raw markdown。
- thinking/interim assistant 使用 `append_block` 完整块追加，避免 delta 累积导致漏字或截断。
- 同一 message id 的 runtime event 发送、sidecar 更新和终态 PATCH 均有排序/合并保护。
- 新版 Hermes 流如果直接以 `answer.delta`、`thinking.delta`、`tool.updated` 或 `message.completed` 开始，也会创建初始 Feishu/Lark 卡片。
- Hermes 原生 `Working` 心跳、上下文窗口/压缩提示、自动 session reset、skill 加载和自我改进 review 会归一为 `system.notice`，优先进入当前卡片 timeline；任务外提示会发送独立小卡片。
- 飞书/Lark 话题回复里，后续 `answer.delta`、`thinking.delta`、`tool.updated` 和 `system.notice` 即使使用不同内部流式 `message_id`，也会通过 `reply_to_message_id` 回到同一张卡片，避免 topic timeline 停住或灰色原生提示重复外溢。
- 飞书/Lark 话题群如果连续消息复用同一 `message_id`，已完成或失败的旧 session 会被清理并创建新卡片；当前轮仍在 streaming 时，重复 `message.started` 继续 ignored，避免误发第二张卡。
- Gateway runtime 会在 Hermes 进程内合并高频 `thinking.delta` / `answer.delta`，覆盖 V3.8.1 的 issue #74，降低 stream-reader 线程压力。
- terminal event 前会 flush 同一消息 pending delta，避免最终卡片缺少尾部内容。
- 飞书内 `/hfc help/status/doctor/monitor` 提供只读诊断卡片，且只展示 hash 后的上下文 id。
- 已接管的 `/hfc` 诊断命令会快速 ACK Hermes Gateway，真实 Feishu/Lark 卡片发送转入后台，避免 `/hfc status` 卡片和灰色 `Unknown command /hfc` 原生回复双发。
- 完成卡片中的普通附件摘要不再触发原生最终 reply fallback；真实 `MEDIA:`、本地文件路径和 Hermes media/file locals 仍保留原生文件/媒体投递路径。
- 群内 `/hfc status` 会展示 chat binding 状态、fallback/default 路由、建议 `bots bind-chat` 命令和群内 slash command 行为边界；真实 @机器人触发和白名单准入仍由 Hermes Gateway 控制。
- pre-tool answer 会先显示在正文区，并在下一段 answer 或终态到来时归档进辅助 timeline；终态卡片会剥离已归档的中间说明。
- 辅助 timeline 中思考条目和工具详情使用不同字号和灰度层级，raw `thinking.delta` 不进入用户可见 timeline。
- 工具详情可展示参数摘要、耗时和失败原因，并继续按紧凑 timeline 渲染。
- 独立 slash 命令确认继续支持 Feishu command card；此外，built-in、alias、plugin/quick 和 unknown command 的所有非空文本反馈都由独立命令卡片承载，同一命令的后续反馈 PATCH 同一卡片。
- Feishu/Lark WebSocket 长连接部署会动态获得原生 `send_slash_confirm(...)` 和 `send_model_picker(...)` 卡片能力；按钮点击经 `_on_card_action_trigger` 回到 Hermes 原 handler。
- WebSocket 原生卡片可用时跳过 sidecar `interaction.requested` 预交互，避免同一 slash 命令同时出现 sidecar 选项卡和原生按钮卡。
- `/model` 无参数选择可通过 Feishu-only `send_model_picker(...)` 卡片呈现；选择后回调 Hermes 并更新同一张命令卡片。
- V4.2.0 仅接管飞书私聊中的裸 `/update`：只读预检后显示 120 秒维护确认卡，确认后由独立 runtime 运行 `hermes update --yes`、重装同版本 HFC 并恢复 hook/sidecar/Gateway；群聊、非飞书、别名与参数化命令继续使用 Hermes 原路径。使用前运行 `maintenance status`。
- terminal 事件会快速 ACK Hermes，慢 Feishu PATCH 在后台完成，避免中断或更新堆积后触发重复原生答复。
- `load_config()` 会读取 config 同目录 `.env`，真实环境变量仍保持最高优先级。
- `install.sh` 白名单读取 `.env` 中的飞书/sidecar 变量，不会执行带空格路径等无关配置。
- `install.sh` 会在 uv/PEP 668 externally managed Python 场景下重试 `--break-system-packages`。
- Windows sidecar 进程 stop/status 避免使用 POSIX process group signal，并走 Windows 专用 PID/`taskkill` 路径。
- `doctor --json` / `doctor --explain` 会展示 config、sidecar、Hermes、streaming、install_state 和 recommendations。
- `doctor --explain` / `install` 在 `gateway/run.py missing` 且 `hermes -V` 可用时，会提示 Hermes CLI `Project:` 目录作为正确 `--hermes-dir`。
- `setup` / `install` 会检测 Hermes runtime venv Python 并安装同一插件版本；`doctor` 会报告 `runtime_import`。
- `install-docker.sh` 支持既有 Hermes Docker 容器内一键安装/更新，默认使用 `HERMES_DIR=/opt/hermes`、`HFC_CONFIG=/opt/data/config.yaml`、`HFC_ENV_FILE=/opt/data/.env`。
- `docker-compose.example.yml` 覆盖 `/opt/hermes`、`/opt/data` 挂载与非交互安装执行路径，支持 compose 场景验证。
- Docker/source-stripped Hermes 根目录缺少 `VERSION` 和 `.git` 元数据时，`doctor` / `install` / `setup` 会用 `gateway/run.py` anchor 兜底，并显示 `version_source: gateway anchors`；版本 metadata 存在但不可解析时，anchors 可验证即可显示 `VERSION + gateway anchors` 或 `git tag + gateway anchors` 并继续。
- hook import/emit 失败保持 fail-open，但会向 Hermes stderr 写入 `[hermes-feishu-card] hook failed: ...` 诊断 warning。
- `repair --hermes-dir ... --yes` 和 `setup --repair` 能修复可验证的 manifest/backup 状态，无法验证用户改动时拒绝覆盖。
- 结构化附件、媒体和文件对象会在卡片保留摘要，同时不抑制 Hermes 原生媒体/文件投递路径。
- `smoke-feishu-card --profile-id`、`bots test --profile-id`、CLI `status` 和 `/health.routing.profiles` 支持 profile 维度排障。
- Hermes key release matrix 覆盖 `v2026.4.23`、`v2026.5.7`、`v2026.5.16+`、`v2026.5.29`、`v2026.6.19+`、`v2026.7.1+`、`v2026.7.7.2`、`0.13.x`、`0.14.x`、`0.15.x`、`0.17.x`、`0.18.x`，并覆盖语义版本带/不带 `v` 前缀和描述型版本 metadata。
- GitHub Actions 会在 PR/push 上运行 Python 3.9/3.12 的测试矩阵，并在 Windows 上解析验证 `install.ps1`。
- Release assets workflow 会为 tag 生成 macOS/Linux/Windows 安装包和 checksum。
- V3.9.0 运维卡支持诊断、重新检测、两步安全修复和重启确认；私聊不比较操作者，群聊只允许发起者完成 repair/restart 确认。卡片不可用时使用 CLI fallback。
- state-dir transport root 会自动创建权限私有的 transport secret，不需要配置 secret，也不在诊断或卡片中输出。
- setup 的 profile/event URL 优先级为显式参数、进程环境、选定 env file、默认值；仅 `doctor` 输出完整脱敏 identity/profile/event endpoint route chain，`status` 摘要运行时路由/profile 事件，`/health` 报告实际 routing health 字段。
- install/setup 可自动修复已知安全状态，`--no-repair` 可关闭；无法验证的用户编辑继续拒绝覆盖。cleanup history 和 metrics 保持有界且 hash 化。
- 运维按钮 WebSocket 回调会即时 ACK，认证动作进入有界后台队列并有限重试；所有认证后的状态统一由 sidecar PATCH 原卡，慢 PATCH 不阻塞 recheck/repair/restart。
- 自动化 release gate：Python 3.9 / 3.12 均为 `1172 passed, 3 skipped`；运维 semaphore/publish-lock 仅在活跃 event loop 内初始化，保持声明的 Python 3.9 支持。
- 2026-07-11 真实飞书私聊通过：`/hfc doctor` 无灰色原生未知命令；中文摘要/详情、连续两次重新检测（含后台 successor）在 156–201 ms 内 ACK、无目标回调超时提示并更新同一卡；sandbox 两步安全修复、卡片实际重启 Gateway 与普通流式完成卡 footer 均通过，sidecar 发送/更新零失败。
- V3.9.1 完成答案边界、打断任务终态排序、异步模型选择 callback、loopback no-proxy、marker-only 恢复与未知编辑拒绝均有回归测试。
- V3.9.1 自动化 release gate：Python 3.9 / 3.12 均为 `1198 passed, 3 skipped`，`git diff --check` 通过。
- V3.10.0 裸 `/resume` picker 复用 original Hermes handler；群聊发起者、topic metadata、失效/无效 state、fail-open 和即时 ACK 有聚焦回归。
- V3.10.0 模型 footer 仅改变转义后的 model label 颜色，element id、字段顺序、分隔符、字号与非完成态不变。
- V4.0.0 将 Hermes 工具名与 `tool.updated.detail` 整理为非完成态 Header 的确定性动作摘要，将公开 `thinking.delta` 独立流式显示在正文；最终 `answer.delta` 仍保持正文优先级。

## 发布前必须验证

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

真实飞书联调只能使用本机配置或环境变量提供 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。不要把 App Secret、tenant token 或真实 chat_id 提交到仓库。公开演示截图入库前需要确认不包含敏感凭据和不可公开的会话内容。

## V4.4.1 发布门禁

- 聚焦 render/config/hook-runtime：`726 passed`；renderer 与 mention 兼容复测：`221 passed`；server reasoning 配置接线：`2 passed`。
- 文档与版本元数据：`101 passed`；普通 wheel、完整 pytest、GitHub CI 和发布资产结果由本轮最终验证补充。
- 真实 Feishu/Lark 私聊、群聊、话题重启恢复、多 profile、审批手机/桌面阅读：待复测；不复用 V4.4.0 的 smoke 作为当前候选证据。

## V4.4.0 发布门禁

- Hermes `v2026.8.27` / `0.20.6` 与独立 `main@4f225435` 的动态 `COMMAND_REGISTRY` 加载：**已通过**，最新 main 识别 66 个 Gateway 命令，包括 `/bg`、`/btw`、`/plan` 与 Gateway `/busy`。
- 最新 main 的 Gateway/Base/Cron patch apply、幂等重装、remove 后逐字还原：**已通过**。
- 能力中心三层导航、安全 copied-event dispatch、群聊发起人/原 chat 绑定、状态变更命令拒绝、KPI 原文保留、真实 backlog depth 与极端表格 fallback：**聚焦自动化通过**。
- 完整 pytest **`3356 passed, 5 skipped`**、`git diff --check`、sdist/wheel 与干净 Python 3.12 `site-packages` package/distribution/CLI provenance：**通过**。
- 真实飞书私聊/群聊 `/commands`、分类/详情/返回导航、安全快捷 `/status`、私聊 `/context` 空态与真实用量视图、普通流式完成 footer：**已通过（2026-08-31）**。候选 wheel `4.4.0` 运行于官方 Hermes `v2026.8.27` / `0.20.6` 隔离 CLI 环境；sidecar 最终为 `healthy / runtime_ready`，事件 `14/14`、发送 `3/3`、更新 `43/43`，拒绝、发送/更新失败与 profile mismatch 均为 0。changed-operator rejection 因测试群仅一位真人，继续由自动化覆盖。
- Release PR、exact merge SHA、annotated tag、public install 与 Release assets/checksums：**待显式发布审批**。

## V3.9.0 人工验收进度

- existing-container Docker：fresh install、pinned upgrade、已知安全 corrupt-marker auto-repair、用户编辑拒绝、main/child profile endpoint mapping、最终 `doctor`。**待验收**。
- 真实飞书私聊：`/hfc doctor`、中文详情、recheck、后台 successor 再次点击、同卡 PATCH、sandbox 两步安全修复、卡片实际重启 Gateway、普通 footer snapshot。**已通过（2026-07-11）**。
- 真实 Feishu cron：no-agent 一次性任务的结果正文已成功进入普通完成卡，sidecar 记录事件接收、应用和卡片发送均成功且无 fallback。**已通过（2026-07-11）**。
- profile route mismatch：用临时错误 `HERMES_FEISHU_CARD_PROFILE_ID` 复现 `profile_unknown`，诊断只显示脱敏 route chain；移除临时环境后恢复默认 profile，未修改持久配置。**已通过（2026-07-11）**。
- V3.10.0 真实飞书 `/resume`：私聊、群聊发起者、topic 原线程选择与同卡 PATCH 已通过；changed-operator rejection 因测试群仅一位真人，保留自动化回归证据。

验收时发现 Hermes 上游 `cron run` 对成功后自动删除的一次性任务仍可能显示 `Ran now: failed`：它在任务记录删除后再次读取 `last_status`，因此把缺失记录误判为失败。该提示不代表插件投递失败；本次以 Feishu 卡片、sidecar metrics 和保存的 cron 输出三方一致作为验收依据。插件不为此额外 patch Hermes `tools/cronjob_tools.py`，避免扩大安装修改面。

## V3.9.1 发布门禁

- Python 3.9 / 3.12 全量自动化：**已通过（`1198 passed, 3 skipped`）**。
- `git diff --check`：**已通过**。
- 真实飞书重点复测：模型选择 callback、打断任务终态和完成答案保留按 [真实飞书验收清单](wiki/feishu-acceptance.md) 执行；公开记录仅保留脱敏结果。
- Release assets：tag 后验证 macOS、Linux、Windows 与 checksums 四个文件。

## V3.10.0 发布门禁

- 聚焦 interaction/installer/render 矩阵：**已通过（`416 passed`）**。
- Python 3.9 / 3.12 全量自动化：**已通过（`1216 passed, 3 skipped`）**。
- 真实 Feishu：私聊、群聊发起者、topic 原线程更新和 footer 已通过；换人拒绝由自动化覆盖。
- `v4.0.0`：**已发布（2026-07-12）**。release-assets workflow 成功；macOS、Linux、Windows 与 checksums 四个 assets 完整且 checksum 通过；从公开 tag 安装后版本为 `4.0.0`，CLI 可启动。
- `v3.10.0`：**已发布（2026-07-11）**，四个 assets 验证通过。

## V4.0.1 发布门禁

- Issue #106 数据流回归、普通/queued completion 和 V4.0.0 hook 升级测试：**已通过**。
- hook/patcher/install/server 热区矩阵：**已通过（`509 passed`）**。
- Hermes `extract_media()` 验证：**已通过**，媒体路径保留且原生可见正文为空。
- 全量自动化：**已通过（`1257 passed, 3 skipped`）**；`git diff --check` 通过。
- 本地发布包 smoke：**已通过**。sdist/wheel 构建成功，干净 venv 安装后导入版本为 `4.0.1`。
- `v4.0.1` 公开安装与 Release assets：**已通过**；四个 assets 齐全且 checksum 通过。

## V4.0.3 发布门禁

- stale-hook 媒体正文精确去重、一次性消费、媒体保留与 sidecar fail-open 回归：**已通过**。
- hook/patcher/install/server 热区矩阵：**已通过（`513 passed`）**。
- 全量自动化：**已通过（`1269 passed, 3 skipped`）**；`git diff --check` 通过。
- 本地发布包：**已通过**。sdist/wheel 构建成功，干净 venv 从 `site-packages` 导入版本 `4.0.3`。
- 公开安装与 Release assets：**待 tag 后验证**。

## V4.0.2 发布门禁

- recovery/install 回归矩阵：**已通过（`121 passed`）**。
- 本机真实旧 owned hook 升级：**已通过**。自动执行 `run.py: reapplied current hook`，doctor install state 完整一致，Gateway 与 sidecar 恢复运行。
- Issue #107 可选配额 footer：**已通过**。server/render/subscription usage 聚焦矩阵 `237 passed`；本机 Hermes 原生接口只读返回并格式化 Session/Weekly 两个窗口。
- 全量自动化：**已通过（`1266 passed, 3 skipped`）**；`git diff --check` 通过。
- 本地发布包：**已通过**。sdist/wheel 构建成功，干净 venv 从 `site-packages` 导入版本 `4.0.2`。
- 公开安装与 Release assets：**待 tag 后验证**。

## V4.0.0 发布门禁

- 会话、渲染、状态聚焦测试：**已通过（`139 passed`）**。
- server/hook/model picker 热区矩阵：**已通过（`341 passed`）**。
- 真实飞书私聊/群聊四状态验收：**已通过（2026-07-12）**。运行、等待、失败、完成态均原位更新同一卡；运行态动作摘要与公开阶段输出相互独立；非完成态 footer 仅显示状态；完成态保留原生回复引用且不叠加 Card JSON Header；没有灰色原生重复消息或回调超时。
- 真实飞书 `/model`：**已通过（2026-07-12）**。Provider 与模型数据直接复用 Hermes CLI picker 的同源列表；进入 Provider、返回上一级、切换模型和结果回写同一卡均成功。
- 四张公开截图：**已通过隐私与视觉检查**，仅保留脱敏后的真实飞书卡片区域。
- 全量自动化：**已通过（`1252 passed, 3 skipped`）**；`git diff --check` 通过。
- 本地发布包 smoke：**已通过**。sdist/wheel 构建成功，干净 Python 3.12 venv 安装后导入版本为 `4.0.0`；Hermes `v2026.7.7.2` doctor 确认 runtime import、streaming 和 install state 正常。
- tag 后验证 macOS、Linux、Windows 与 checksums 四个 assets。

`v3.9.0` tag 的 release-assets workflow 会发布 4 个 assets：macOS tarball、Linux tarball、Windows zip 和 checksums 文件，分别为 `hermes-feishu-card-v3.9.0-macos.tar.gz`、`hermes-feishu-card-v3.9.0-linux.tar.gz`、`hermes-feishu-card-v3.9.0-windows.zip`、`hermes-feishu-card-v3.9.0-checksums.txt`。

## V4.3.8 发布门禁

- Issue #244：`setup` 仅在 `service.manager=auto|systemd-user`、user manager 可用且 linger 已开启时默认启用 owned persistent service；其他环境明确 warning、transient fallback 与精确 `enable` 命令，`--transient` 显式 opt-out。
- Issue #245：认证 out-of-band card action completion 不推进 `/events` transport `last_sequence`；第二个 batch clarify 请求仍需通过严格 identity 与单调 sequence 校验，第一题 callback card 在 session lock 内快照。
- PR #242：远程 Feishu/Lark HTTP 遵循 proxy 环境变量，loopback/private/link-local/unspecified 地址继续 bypass；原作者 commit `c25e2c4c36d5bc795b3c92df6796e7c971e9dba4` 独立保留。
- focused proxy client **`81 passed`**；session/hook/server **`937 passed`**；persistent/process/install **`649 passed, 5 skipped`**；fresh normal-wheel process lifecycle **`8 passed`**。
- fresh Python 3.12 normal-wheel 完整 pytest **`3343 passed, 6 skipped in 690.84s`**；`git diff --check` **已通过**。release candidate CI、exact merge SHA、annotated tag、public tagged install 与 Release assets/checksums：**待最终门禁完成**。
- 真实 Feishu/Lark 客户端 smoke 与真实 Linux systemd user + linger 主机 smoke：**未执行**。自动化、mock 与 CI 不冒充真实平台验收。

## V4.3.7 发布门禁（历史记录）

- Issue #240 / PR #241：Base `filter_media_delivery_paths` / `filter_local_delivery_paths` exact matcher 必须同时接受旧版单位置参数调用与新版唯一 `session_key=session_key` 关键字调用，避免 `exact_delivery_contract: missing_or_unsupported`。
- extra/wrong/unpacked keyword、错误值以及缺少/增加位置参数必须全部 fail-closed；apply/remove/restore 保持幂等和逐字恢复。
- PR 精确 head `5e75650b0f147a24e65d5f0e499fe8b5a3f8f22f` 定向回归 **`460 passed, 1 skipped`**；6 种对抗调用形态全部拒绝；真实 Hermes `82b32f32ef` source apply/idempotent/remove roundtrip 通过。
- fresh Python 3.12 normal-wheel 环境完整 pytest **`3330 passed, 5 skipped in 569.93s`**；`git diff --check` **已通过**。
- PR #241 的 12 项 GitHub checks 全绿；exact merge `7fcf3cbd67d3a5100739e9e3d3d7cdcce080cb62`。release candidate CI、exact release merge、annotated tag、public tagged install 与 Release assets/checksums：**待最终门禁完成**。
- 真实飞书客户端 smoke：**未执行**。本修复只改变 installer AST contract 识别，不改变 Feishu API/card runtime；自动化不冒充平台验收。

## V4.3.6 发布门禁（历史记录）

- Issue #237 / PR #238：无 reply anchor 的 topic 路径不得再把 `thread_id` 作为 create API 的 `receive_id_type` 或 `receive_id`；实际请求必须使用父 `chat_id`。有 `reply_to_message_id` 时继续使用 reply API 与 `reply_in_thread`。
- PR #228：approval/clarify 与 opt-in completion notification 可 `@` 发起人；`card.mentions_in_cards: false` 必须覆盖 per-kind 与 completion 配置。`completion_notify.mention: false` 在无 sender 的系统/后台场景发送普通完成通知，mention 开启时仍拒绝缺失或非法 `open_id`。
- schema 2.0 streaming card 保持唯一 PATCH owner，legacy interaction card 继续作为 auxiliary message；native-handoff route/UUID 继续绑定逻辑 topic identity，即使实际无锚点 create 回落父群。
- exact feature/fix merges：PR #238 `199d0390269693e74d1ff130cb7b4ecc4570dcfe`；PR #228 `69f47123611bb1639e74d9a076212ce621322805`。
- 已有回归证据：#237 一次性普通 wheel 全量 pytest **`3283 passed, 5 skipped`**；#228 最终组合相关 unit **`225 passed`**、server integration **`324 passed`**、两条新增 completion 回归单独 **`2 passed`**，最终 rebased head 的 12 项 CI 全绿。
- v4.3.6 release candidate：`git diff --check` **已通过**；fresh Python 3.12 normal-wheel 环境完整 pytest **`3325 passed, 5 skipped in 560.94s`**；PEP 517 sdist/wheel、隔离 `site-packages` 中的 package/distribution `4.3.6`、唯一 plugin entrypoint、24 slices，以及主 CLI 与 `enable/disable --help` 均已验证。
- release PR CI、exact release merge `a2a244659f198ecd57c862455d3f4d658a827b66`、annotated tag、public tagged install 与 Release assets/checksums：**已完成**。
- 真实飞书：Issue #237 报告者已验证非法 `thread_id` create 返回 `99992402`、`chat_id` create 与 reply API 成功，并报告本地热修后 create 恢复；维护者本轮独立客户端 smoke：**未执行**。warning 节流不在本版本范围。

## V4.3.5 发布门禁（历史记录）

- PR #235：Hermes v2026.8.3 Feishu adapter 的原始 `edit_message(chat_id, message_id, content, *, finalize=False)` 不接收 `metadata`；HFC wrapper 在 card 路由未接管、回退原方法时只能移除这一项 wrapper-owned 内部参数。
- 原方法显式接收 `metadata` 或 `**kwargs` 时必须原样透传；无关未知关键字不得被吞掉，仍由原方法抛出 `TypeError`。
- 独立直接回归：**已通过（`4 passed`）**；hook/server 热区：**已通过（`841 passed`）**；精确 PR HEAD 完整 pytest：**已通过（`3279 passed, 6 skipped in 599.42s`）**。
- v4.3.5 docs/package/native provenance 聚焦门禁：**已通过（`99 passed`）**；一次性 wheel 环境完整 pytest：**已通过（`3280 passed, 5 skipped in 555.86s`）**；`git diff --check`：**已通过**。
- PEP 517 sdist/wheel 与 fresh Python 3.12 wheel-only provenance：**已通过**。package/distribution `4.3.5`、隔离 `site-packages` import、唯一 Hermes plugin entrypoint、24 个 provenance slices、主 CLI 与 `enable/disable --help` 均已验证。
- PR #235 HEAD `5b3bf428eb688df4b95607cba1a4ce50e2eeb8d0`：Tests run `32719244038` attempt 3 与 CodeQL run `32719244032` **已通过**；attempt 1/2 仅 fixed Hermes fixture 因 GitHub HTTP 429 克隆失败，第三次 fixture 与所有平台 job 均通过。
- exact PR merge `d56555bf9e716de67ed14f8ed992df1ec55cea21`、release merge `7829e51c4c7851aa09347e56bb8c2a7136c4b0cb`、annotated tag、public install 与 Release assets/checksums：**已完成**。
- 本轮不改 card ownership、thread placement、callback authentication、飞书 API payload、Hermes patch ownership 或 `legacy/` runtime。

## V4.3.4 发布门禁（历史记录）

- PR #229：runtime interaction listener 的 bind 路径不得调用 reverse DNS；`serve_forever` thread 必须是 daemon，未显式 `close()` 的短命令进程仍可退出。
- Issue #233：有效的 `manifest_version: 3` Hybrid 安装必须由 V3 runtime binding、plugin entrypoint 与 fixed-tag inspector 校验，并报告 `installed`；不得调用 Legacy install diagnosis、recovery 或 integrity repair planner。
- V3 phase/config/target/backup/runtime identity 漂移必须 fail-closed，输出 V3-specific finding、禁止 Legacy 自动 repair，并引导使用官方 V3 restore/reinstall 流程。
- hosted macOS 的 blocked-delivery close 回归以 Future deadline 验证有界完成，不再把 runner 调度开销混入 `<0.25s` 原始 wall-clock 断言；生产超时不放宽。
- #229/#233/diagnostics/CLI/macOS timing 联合回归：**已通过（`191 passed`）**；一次性 4.3.4 venv 完整 pytest：**已通过（`3275 passed, 6 skipped in 634.95s`）**；`git diff --check`：**已通过**。
- PEP 517 sdist/wheel 与 fresh Python 3.12 wheel-only provenance：**已通过**。package/distribution `4.3.4`、隔离 `site-packages` import、唯一 Hermes plugin entrypoint、24 个 provenance slices、主 CLI 与 `enable/disable --help` 均已验证。
- PR #234 candidate HEAD `435ea4e355719e0f2d904cf1bac986ff18f70876`：Tests run `32710110323`（10 jobs）与 CodeQL run `32710110375` **已通过**；exact merge `2f1abcfcad50997c615103e3cdf1302c61f94c91`、tag 与 Release assets/checksums：**已完成**。
- 本轮不改 Feishu card/API delivery semantics，因此不发送额外真实飞书测试消息；这不替代 V4.3.3 尚未完成的 first-reply thread 客户端验收。

## V4.3.3 发布门禁（历史记录）

- 首回复在没有 concrete `thread_id` 时携带显式 `reply_in_thread=true` 和真实 `om_` anchor：streaming card、普通/重复/runtime-admission interaction 及 opt-in completion notification 必须保留在同一 thread。
- `send_text_message()` 收到 `reply_in_thread=true` 或非空 `thread_id`、但缺少 `reply_to_message_id` 时，必须在 token/API 调用前拒绝，不能发送 top-level fallback；没有 thread placement intent 的默认路径继续兼容。
- 本地回归与完整 pytest：**已通过（`3267 passed, 6 skipped`）**；`git diff --check`、sdist/wheel、fresh Python 3.12 wheel-only provenance、唯一 Hermes plugin entrypoint、24 个 provenance slices 与 CLI help smoke：**已通过**。
- PR #232 candidate HEAD `f7de533d67f9e50afcd2c4d80fad89b572054605` 的 Tests run `32657674121`（10 个 job）与 CodeQL run `32657674120`：**已通过**。
- exact merge SHA、public tag/install 与 Release assets/checksums 按发布流程继续记录；真实 Feishu/Lark 客户端验收当前未验证。

## V4.3.2 发布门禁（历史记录）

- Issue #227：原 schema 2.0 streaming message 必须始终保留为 `FEISHU_MESSAGE_IDS_KEY` owner；新发 legacy 交互卡只能接收 callback，绝不能成为 schema 2.0 PATCH 目标。
- direct-select、custom-input form、runtime admission、连续 interaction 和过期路径都必须返回同方言 legacy 终态卡；Gateway 若收到 schema 2.0 callback card 必须降级为 success toast，不能生成 raw callback card。
- 方言感知 fake 必须像飞书一样拒绝 cross-dialect PATCH；全部后续 answer/thinking/tool/terminal 更新只落在原 schema 2.0 message。
- renderer/hook/server/Feishu SDK compatibility 联合回归：**已通过（`932 passed, 1 skipped`）**；`git diff --check`：**已通过**。
- 完整 pytest：**已通过（`3253 passed, 5 skipped in 413.97s`）**。PEP 517 sdist/wheel、fresh Python 3.12 + `lark-oapi 1.6.8` wheel-only `site-packages` provenance、package/distribution `4.3.2`、唯一 Hermes plugin entrypoint、24 slices 与主 CLI/`enable`/`disable` help：**已通过**。
- exact merge SHA、远端 CI、annotated tag、public install、Release assets/checksums 与真实飞书 direct choice/custom-input form：**发布流程中逐项记录**。

## V4.3.1 发布门禁

- Issue #216：真实飞书点击必须先到 Hermes WebSocket card-action channel，再带 exact profile 转发 sidecar，并由签名 listener 直接唤醒原 pending handle；同一 turn 后续 answer/thinking delta 必须继续 PATCH 最新卡片，不得要求再发一句话。
- 显式 `card.interaction_mode: text` 必须在 session mutation 前拒绝 runtime callback ownership；第一条编号/文本回复由 Hermes 原生 interceptor 消费，不产生第二套 waiter 或过期卡片。
- PR #226：persistent enable 接受 exact `python-sha256:` identity；systemd `WorkingDirectory` 拒绝 relative/control-character 并安全处理 `%`/反斜杠；tokenless health 明确返回空 hash，有 token 时只返回 SHA-256。
- 真实 fixed-tag Hermes `v2026.8.3` + 飞书 WebSocket 两次物理点击已到达 listener 并 resolved，交互后卡片继续显示新结果；验收记录不保存真实标识符、token、正文或截图。
- README 中英文贡献者名单必须与历史 release、merged/absorbed PR、accepted issue evidence、commit author/co-author 对账；GitHub Contributors 图只使用真实提交归属，禁止为改图伪造 authorship。
- 完整 pytest：**`3245 passed, 6 skipped in 425.58s`**；sdist/wheel 与 fresh Python 3.12 wheel-only venv 的版本、`site-packages` origin、唯一 plugin entrypoint、24 slices 和 CLI help：**通过**。
- diff-check、exact merge SHA、远端 CI、annotated tag、public install 与 Release assets：**发布流程中逐项记录**。

## V4.3.0 发布门禁

- 固定 Hermes `v2026.8.3` / commit `3c27eb6234bf91b8ceee9e9071591b31e9b148cb` 的 probe 必须同时通过源码哈希/call-site slices、runtime Python、entrypoint/distribution origin 与真实 PluginManager subprocess evidence；不能用版本字符串或 hook 名列表代替能力证明。
- Hybrid 必须精确检测 17 个 patch group、7 个 target，并逐文件 compile；重复 install 的 manifest 哈希必须不变，restore 后 Hermes checkout 必须 Git clean，配置 SHA-256 与安装前一致，ownership evidence 必须清空。
- interaction callback 必须直接唤醒 Hermes 原 pending handle/future，Sidecar listener POST 和 Feishu create/PATCH 均不持 session/message lock；event-id fence、terminal/native handoff、expiry、session replacement 与 caller cancellation 攻击测试必须通过。
- persistent `enable` 必须要求 `Linger=yes`、安全迁移 verified transient owner、用 SHA-256 绑定 mode `0600` unit/manifest，并在停服失败时保留 ownership；`disable` 对 drift fail-closed。
- 已完成聚焦证据：V3 installer/restore/script `340 passed, 5 skipped`；persistent process/CLI loopback `302 passed`；真实 fixed-tag install/idempotence/restore 全链路通过。
- 完整 pytest：**`3227 passed, 6 skipped in 378.84s`**；sdist/wheel、全新 Python 3.12 venv 的隔离 `site-packages` provenance、唯一 Hermes plugin entrypoint、24 个 provenance slices、主 CLI 与 `enable/disable --help`：**本地通过**。最终提交后仍需再跑 `git diff --check` 与 docs/package 快速门禁。
- V4.3.0 发布时把 Issue #216 限定为平台零事件边界；后续真实复测证明还存在本地 profile/callback/streaming 恢复缺口，已由 V4.3.1 单独修复。PR #203 仅改归档 `legacy/`，不纳入 active runtime。
- exact merge SHA、远端 CI、annotated tag、public tag/install 与 Release assets：**发布流程中逐项记录**。

## V4.2.12 发布门禁

- PR #206 的 approval capability matrix 覆盖默认、`allow_permanent=false`、`allow_session=false` 与 `smart_denied=true`；approval 默认只接受当前卡声明的协议选项，clarify 继续支持自定义输入。
- sidecar 拒绝伪造固定选项、approval 自定义 form 与 truthy-string capability；拒绝后 interaction 保持 pending，token/chat/operator/expiry/idempotency 不变。
- PR #205 在 reasoning timeline 启用时为零工具运行、完成和失败卡保留同款折叠条与明确空状态；`show_reasoning=false` 保留普通 tool summary，raw thinking 不公开。
- 两个 PR 更新到同一 main 后的 GitHub 多平台 CI：**已通过**；合并后 runtime 基线完整 pytest：**`2481 passed, 6 skipped`**。
- v4.2.12 发布候选门禁：docs/package **`94 passed`**、聚焦矩阵 **`830 passed`**、完整 pytest **`2481 passed, 6 skipped`**；sdist/wheel、干净 venv `site-packages` provenance、CLI help 与 `git diff --check`：**已通过**。
- 本轮不额外发送真实飞书测试消息；PR #205 的真实飞书结果属于贡献者证据，PR #206 与组合结果以自动化、独立攻击测试和多平台 CI 为门禁，不写成维护者真实客户端复测。
- exact merge SHA、annotated tag、public tag/install 与 Release assets：**发布流程中逐项记录**。

## V4.2.11 发布门禁

- Issue #202 回归先在旧实现观察到 predecessor 没有终态 PATCH，再验证绿色“已转入交互卡片” Header/summary、内容与工具保留、临时运行态和 pending 控件清除。
- 连续 interaction 每张 predecessor 只 final PATCH 一次；旧 pending token/按钮不会残留，只有最新卡继续接收 callback 与后续更新。
- replacement send 失败恢复请求前 session；predecessor PATCH 全部失败仍提升新卡并记录既有脱敏 update metrics/diagnostics。
- animation cancellation 在 predecessor PATCH 前完成；canonical `turn_id` session 使用原 per-session card config。
- session/render/server/clarify 聚焦矩阵：**`450 passed`**；`git diff --check`：**通过**。
- 隔离 v4.2.11 候选完整 pytest：**`2478 passed, 6 skipped`**。
- 本地 sdist/wheel 与全新 venv 候选 wheel `site-packages` provenance/CLI smoke：**通过**。
- PR CI、exact merge SHA、public tag/install 与 Release assets：**发布流程中逐项记录**。

## V4.2.10 发布门禁

- sidecar request proof 绑定 HTTP method、规范 path 与 raw body，使用独立 `hfc-sidecar-request-v1` 域；缺失、过期、跨 method/path/body 与 replay 均 fail-closed，认证失败响应与指标不包含签名、标识符、正文或选择。
- 默认 loopback 部署保持兼容；启用非回环事件认证时，`/card/actions`、`/interactions/{id}` 与 `/messages/{id}/summary` 在解析/返回前必须验签。
- interaction deadline 由 sidecar 接收时刻决定；晚到直连按钮与 form submit 返回过期状态，周期任务刷新原卡，过期 pending 不再永久阻塞清理；Gateway poll 超时只发送一次独立 `interaction.failed`。
- session/lifecycle/render/hook 单元回归：**`556 passed`**；完整 server/clarify 集成回归：**`297 passed`**；CI workflow 契约：**`15 passed`**。
- GitHub Actions 覆盖 Ubuntu Python 3.9/3.10/3.11/3.12、Windows 3.12、macOS 3.12 全量 pytest，并保留 Feishu SDK、PowerShell installer 与 Docker Compose smoke；官方 Action 固定到已核验 Node 24 版本的不可变 SHA。
- 新增 CodeQL Python push/PR/weekly 扫描与 pip/GitHub Actions weekly Dependabot 配置。
- 隔离 v4.2.10 runtime 完整 pytest：**`2473 passed, 6 skipped`**；精确 PR merge、detached merge-SHA 复验、public tag/install 与 Release assets：**发布流程中逐项记录**。

## V4.2.9 发布门禁

- PR #196/#199 原作者提交已保留；额外回归锁定调度失败、并发重复、token/chat 鉴权、旧 Hermes callback 签名、单次 `/events` 与日志脱敏。
- hook/runtime/render/server/patcher/install 聚焦矩阵：**`1161 passed, 2 skipped`**。
- 隔离 v4.2.9 runtime 完整 pytest：**`2452 passed, 6 skipped`**；`git diff --check`：**通过**。
- 本地 sdist/wheel 构建成功，metadata 均为 `4.2.9`；全新 venv 安装 wheel 与公开依赖后，package/distribution 版本均为 `4.2.9`，import 来自 venv `site-packages`，console entrypoint 与 CLI help exit 0。
- GitHub Actions（Python 3.9/3.12、Feishu SDK、PowerShell、Docker）：**通过**（[run 31318602152](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/31318602152)）。
- exact merge SHA `dc332212c14423abb3b42f524dce46ff0ff28479`；annotated tag `v4.2.9` 与 [Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.2.9)：**已发布（2026-08-09）**。
- release-assets [run 31319394583](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/31319394583)：**通过**；macOS、Linux、Windows 与 checksums 四个 assets 均已上传并带 GitHub SHA256 digest。

## V4.2.8 发布门禁

- 公开 v4.2.7 tag 的全新 macOS 安装复现：包从目标 venv `site-packages` 导入且 hook 状态完整，但环境凭据未写入所选 `.env`，后续隔离 doctor 报告凭据缺失。
- TDD 回归覆盖 `install.sh`、`install-docker.sh` 与 `install.ps1` 的进程凭据持久化、带空格 secret、POSIX `0600` 权限和日志不泄露。
- 安装脚本与 CLI install/setup/restore 聚焦矩阵：**`285 passed, 5 skipped`**；版本/文档契约：**`92 passed`**；PowerShell 动态用例由 Windows GitHub Actions 执行。
- 隔离 v4.2.8 测试 venv 完整 pytest：**`2431 passed, 6 skipped`**；`git diff --check`：**通过**。
- 本地 sdist/wheel 构建成功，metadata 均为 `4.2.8`；第二个全新 venv 安装 wheel 与公开依赖后，package/distribution 版本均为 `4.2.8`，import 来自 venv `site-packages`，CLI help exit 0。
- PR CI、exact merge、public tag/install 与四个 Release assets：**发布流程中逐项记录**。

## V4.2.7 发布门禁

Accepted candidate SHA: `18d0346bd041a7c7b2c049ace116b78c720bad98`

- 候选 GitHub Actions [run 30966895426](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/30966895426) 成功，Python 3.9/3.12、PowerShell installer、Docker Compose runtime smoke 与 Feishu SDK compat 五个 jobs 全部通过。
- 候选聚焦回归：**`632 passed, 4 skipped`**；候选完整 pytest：**`2429 passed, 5 skipped`**；`git diff --check`：**通过**。
- Windows 专项覆盖 30 秒 SDK/HFC 探针、POSIX manifest 写入、精确 legacy 反斜杠读取、越界拒绝、parent `HERMES_HOME`、detached runner PID 重绑和 PowerShell 非零退出传播。
- 发布分支版本/文档契约：**`92 passed`**；Windows/installer/CLI 聚焦矩阵：**`628 passed, 4 skipped`**；完整 pytest：**`2429 passed, 5 skipped`**；`git diff --check`：**通过**。
- 本地 sdist/wheel 构建成功，metadata 均为 `4.2.7`；全新 venv 安装 wheel 与公开依赖后，package/distribution 版本均为 `4.2.7`，import 来自 venv `site-packages`，CLI help exit 0。
- exact merge SHA、public tag/install 与四个 Release assets：**发布流程中逐项记录**。

## V4.2.6 发布门禁

Accepted candidate SHA: `a03838da5f012aae112ca549cbe03727a91b578a`

- 截止 2026-08-04 15:00 CST，Issue #187、#188、#189 与 PR #190 没有包含 Hermes/HFC 版本、真实场景和明确结果的有效候选复测反馈。
- 候选 GitHub Actions [run 30880188189](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/30880188189) 成功，五个 jobs 全部通过。
- 候选完整 pytest：**`2412 passed, 5 skipped`**。
- 本机 Hermes `0.20.0` 飞书私聊裸 `/update`：预检、确认卡与独立更新流程通过；sidecar ready，版本显示为 `0.20.0`。公开记录不含本机路径、凭据或 chat id。
- 发布分支版本/文档契约：**`103 passed`**；发布聚焦矩阵：**`1201 passed, 1 skipped`**；完整 pytest：**`2413 passed, 5 skipped`**；`git diff --check`：**通过**。
- 本地 sdist/wheel 构建成功；wheel 与 sdist metadata 均为 `4.2.6`，隔离 `-I` import 来自 venv `site-packages`，package/distribution 版本均为 `4.2.6`。
- PR CI、exact merge、public tag/install 与四个 Release assets：**发布流程中逐项记录**。

## V4.2.5 发布门禁

Accepted runtime SHA: `7f87beed8a37a365c10483f3d638092fd422782e`

- 候选验收记录：**2026-08-02 11:31:18 CST（Asia/Shanghai）**；平台为 **macOS arm64**；Hermes checkout 版本为 `v2026.7.30-15-gce6dd1a65-dirty`（仅只读用于验收，未由本次流程修改）；隔离运行的 HFC package/runtime 均为 `4.2.5`。
- 真实飞书 topic 验收：**通过**。在最近仍有效的既有测试群 topic 中严格只创建两张 A/B 卡；sidecar 报告 `events_applied=4/4`、`feishu_send_successes=2/2`、`events_rejected=0`、send/update failure 均为 `0`。首次 A 已成功创建后，验收夹具因错误依赖 hook 布尔返回提前停止；恢复流程复用同一张 A，没有重发第三张卡。A 的首段与 B started 后的迟到标记均 PATCH 成功，B 的首段与 terminal 由候选 hook/sidecar 完成，B summary 已索引且不含 A/late 标记，两张卡 ID 不同。
- 飞书历史消息接口对已 PATCH 卡片返回初始正文快照，因此不把该正文快照当作 A 的当前内容证据；A 以两次 PATCH 成功、`updated=true`、`update_time` 前进和零 update failure 证明状态转换。B 以 sidecar summary 与事件/发送/更新计数证明。该限制不影响“两张卡、无跨写”的通过结论，但保留为验收证据边界。
- accepted runtime 自动化：runtime focused `938 passed`；maintenance focused `223 passed`；installer/release focused `159 passed, 3 skipped`；disposable maintenance smoke `6 passed`；完整 pytest **`2400 passed, 5 skipped`**。沙箱内首次 runtime focused 运行因禁止绑定 `127.0.0.1` 临时端口失败，按项目授权在沙箱外原样重跑后全部通过。
- 九个审查 ID 均有命名回归，覆盖 quoted turn、maintenance ownership/binding/drain、doctor action、installer pin 与 config marker。
- `latest` 解析失败必须在 pip/setup/doctor 和 Docker state mutation 前退出；显式 `main` 是唯一 moving ref。
- Release Assets 必须按 `resolve-release -> reusable exact-commit tests -> package` 执行，并在 build 前和 upload 前 full reverify annotated tag。
- 候选完整 pytest、compileall、package provenance、disposable maintenance smoke、真实验收、PR CI、exact merge、public tag/install 与四个 assets：**发布流程中逐项记录**。

## V4.2.4 发布门禁

- `message.started` 必须使用真实入站 message ID，每条引用回复创建独立卡片；仅在 ID 缺失时回退 reply anchor：**patcher 单元回归通过**。
- sidecar 必须只对新 turn 跳过 reply alias；后续 `answer.delta` 等流式事件仍更新本轮新卡：**真实 HTTP `/events` 集成回归通过**。
- 完整 pytest：**`2311 passed, 5 skipped`**；`git diff --check`、sdist/wheel 与干净隔离 Python `site-packages` 包/distribution/CLI provenance：**本地候选门禁通过**。PR CI、exact merge SHA、public tag/install 与 Release assets：**发布流程中验证**。
- PR #177 贡献者报告真实飞书话题连续引用场景通过；正式 tag 后的运行环境复测保留为用户侧验收，不用它替代自动化与精确 SHA 门禁。

## V4.2.3 发布门禁

- WebSocket hook 必须将 card value 中的 `update_evidence_fingerprint` 原样转发给 sidecar；缺失字段的回归测试先红后绿：**通过**。
- 相关 hook/runtime/server/Feishu SDK 矩阵：**`670 passed, 1 skipped`**。完整 pytest：**`2309 passed, 5 skipped`**；`git diff --check`、sdist/wheel、干净 Python 3.12 `site-packages` provenance、PR CI、exact merge SHA、public tag/install 与 Release assets：**发布链路通过**。
- 真实验收必须观察 sidecar update attempt、原卡状态转换，并证明取消不会启动 updater；不得只以按钮被点击或 Gateway 收到 action 作为通过依据。
- 本机候选真实飞书取消验收：**通过（2026-08-01）**。新卡显示 HFC 4.2.3，原卡进入“已取消更新 / 未执行 Hermes 更新”；sidecar 为 `feishu_update_attempts=1`、`successes=1`、`failures=0`，Hermes HEAD 未变化，`update.log` 仍停在 2026-07-31 15:01:52，且无 updater/maintenance run 进程。正式 tag 安装后仍需复验。

## V4.2.2 发布门禁

- native card action 必须先快速空 ACK，再由 sidecar 异步 PATCH 原确认卡；Feishu API 延迟不得阻塞 callback deadline：**聚焦回归通过**。
- 取消必须写入 durable `cancelled` 后显示“已取消更新”终态，且不得调度 updater；确认必须先尝试发布 locking/准备态，再调度独立维护任务：**相关 operations/server/hook-runtime 矩阵 `378 passed`**。
- Python 3.9 / 3.12 全量均为 **`2307 passed, 5 skipped`**；`git diff --check`、wheel/sdist、干净 Python 3.12 `site-packages` package/distribution/CLI provenance、PR CI、exact merge SHA、public tag/install 与 Release assets：**发布链路通过**。真实飞书点击随后暴露 WebSocket hook 遗漏证据指纹，取消终态未完成，已由 V4.2.3 候选接续修复。

## V4.2.1 发布门禁

- startup adapter 安装必须先登记 live Gateway runner，再启动 runtime control；首个 heartbeat 的 `_active_work_count()` 聚合证据必须完整：**聚焦回归通过**。
- 缺失、异常、负数或非整数聚合仍拒绝，不回退为“零任务”：**安全边界保持**。
- Python 3.9 与 3.12 完整 pytest 均为 **`2306 passed, 4 skipped`**；`git diff --check`、wheel/sdist、干净 `site-packages` 与 maintenance runtime：**本地候选门禁通过**。PR CI、exact merge SHA、public tag/install 与 Release assets：**发布流程中验证**。

## V4.2.0 发布门禁

- 私聊裸 `/update` 的只读预检、120 秒确认绑定、取消/过期/重复/跨用户拒绝和专用维护卡：**自动化通过**。
- 独立 runtime 的 exact-wheel provision、durable job/journal/lock、官方 `hermes update --yes`、同版本 HFC 重装、hook 与服务恢复、`maintenance status/resume`：**自动化通过**。
- 非 HFC tracked 改动、Git 未完成状态、artifact/version 漂移和最终验证失败均停止；untracked 文件保留，不执行自定义 Git 回滚：**自动化通过**。
- 完整 pytest：Python 3.9 为 **`2304 passed, 4 skipped`**，Python 3.12 为 **`2303 passed, 5 skipped`**；`git diff --check`、wheel/sdist、干净 Python 3.12 `site-packages` package/distribution/CLI provenance，以及真实 `maintenance provision/status` 独立 runtime 与 runner import：**本地候选门禁通过**。
- PR CI、真实飞书私聊卡片、exact merge SHA、public tag/install 与 Release assets：**发布流程中验证**。

## V4.1.4 发布门禁

- 从公开 v4.0.14 `site-packages` 生成的普通 Gateway、Hermes v0.19.0 required exact Base 与 optional Cron 三类状态中移走 manifest，V4.1.4 官方 install 必须输出 `manifest: rebuilt` / `install ok`，doctor 回到 `installed`：**本地隔离复现通过**。
- Unicode 注释 + 全 CRLF 源码、Windows 原生相对路径与无 directory-fd 的 portable install 路径必须通过；不把这些因素误写为根因：**隔离旧包样本与等价分支通过，真实 Windows 待报告者确认**。
- 只有 legacy owned blocks lenient removal 与干净 Gateway backup 逐字一致、Cron/Base 严格 removal 分别与 backup 一致时才能迁移；`--no-repair`、目标缺失、owned block 外用户改动和写入/rollback 前并发编辑必须保持现场并拒绝：**安全边界回归通过**。
- 完整 pytest **`2221 passed, 5 skipped`**、`git diff --check`、wheel/sdist、隔离 Python 3.12 `site-packages` package/distribution/CLI provenance：**本地通过**；PR CI、Issue #171 Windows 官方流程复测、exact merge SHA、public tag/install 与 Release assets：**待完成**。

## V4.1.3 发布门禁

- 同一 target 的旧 plan binding 只有在当前 recovery/integrity plan 连续两次为 installed、sidecar 连续两次确认停止且 fence CAS 未漂移时才能迁移：**聚焦回归通过**。
- 不同 target、状态漂移、残留 pidfile/health、unknown legacy fence 与不可验证 plan 继续 fail-closed；非空 restart/hash fence 必须保留：**安全边界回归通过**。
- `doctor --explain` 必须分别给出完整 `integrity migrate-safe` 与 `integrity acknowledge-review` 命令，且不泄露路径、fingerprint 或私密状态证据：**诊断回归通过**。
- PR #168 必须只选择调用 `_stream_consumer.on_delta` 的原生文本流 callback，并可迁移旧 hook：**独立审查、完整自动化与真实 Hermes 源码注入验证通过，已保留贡献者署名合并**。
- Hermes `1a3a9de` 的 TurnRunner 源码必须恢复 14 个受管 hook block，6 个迁移 hook 各一次，status 在 ctx 绑定后执行，重复 patch 幂等、卸载逐字还原、doctor 为 `supported/full`；不可识别结构必须 `not safely patchable`：**回归、真实源码与 PR #170 CI 通过**。
- 合并候选完整 pytest **`2207 passed, 4 skipped`**、`git diff --check`、wheel/sdist 与隔离 Python 3.12 `site-packages` 包版本/distribution/CLI entry point provenance：**本地通过**；Issue #158 Ubuntu 官方流程复测、Issue #169 最新 Hermes 真实 Feishu 复测、候选 CI、exact merge SHA、public tagged install 与 Release assets：**待完成**。

## V4.1.2 发布门禁

- 已安装 plan、旧 runtime hello、heartbeat stale、coordinator check、新 matching hello 的竞态必须不产生 fence，并一次恢复 ready：**聚焦回归通过**。
- generation/package mismatch、control auth unavailable、manual-review/restart fence 和实际 strict repair 继续 fail-closed，且不自动重启 Gateway：**安全边界回归通过**。
- stable tool wrapper 检测与显式 fail-open fallback 必须避免同一 call 同时走稳定 callback 和 legacy progress path；本机与远端 MacBook Pro 的真实 Hermes 配置模型均验证 terminal 只出现一个带耗时条目：**自动化与双机真实飞书通过**。
- 候选完整 pytest **`2197 passed, 4 skipped`**，`git diff --check`、wheel/sdist 与隔离 `site-packages`/CLI provenance：**通过**；exact merge SHA、public tagged install 与 Release assets：**待合并后完成**。

## V4.1.1 发布门禁

- 已验证 `installed` plan 在首次 heartbeat 等待/缺失时不执行 repair、不写 restart/manual-review fence；收到 matching `runtime.hello` 后恢复正常评估：**候选提交聚焦与全量回归通过**。
- `integrity acknowledge-review` 只接受 installed + sidecar health 不可达 + 无 pidfile；empty hash 可解除不可自清 fence，non-empty hash 保留 restart fence 直到不同 runtime id 的 matching hello：**CLI、持久化与重启模拟通过**。
- legacy `0644` pidfile 只在私有 owned `0700` state dir 中通过 fd identity 绑定收紧；pidfile-less 进程不自动接管/kill，要求人工停旧服务后重跑：**macOS 真实进程测试通过，Linux CI 待完成**。
- setup 通过 Hermes runtime venv 安装/复检，并按 `/health` package version 与 Python identity 决定是否重启 sidecar；随后人工重启 sidecar 与 Gateway：**待本机、远端升级验收**。
- 候选提交 `20b7b06`：完整 pytest **`2194 passed, 4 skipped`**，`git diff --check`、wheel/sdist 构建、隔离 `site-packages` provenance 与 wheel 真实进程测试 **`8 passed`**；**CI、exact merge SHA、public tagged install、Release assets、Linux/Docker 与真实飞书仍待发布流程完成**。

## V4.1.0 发布门禁

- `bindings.native_chats` exact/profile-scoped，hook 与 sidecar 双重 enforcement，所有 direct card path fail-open，`/hfc` 保持卡片：**待完成聚焦矩阵与真实 card → native → card 验收**。
- 默认 `table_overflow_mode=compact` 保留第 6 张及后续表格，fenced fake table 不计数；无附件 terminal 超过 28,000 byte 时使用 V2 descriptor、稳定 UUID、Hermes ledger 与 delivered 后 ACK 交还完整原生答案；窗口外 exact descriptor 失效，带可见 recovered marker 的上游有界恢复仍保持普通 fail-open：**待完成真实 7-table 与 oversized handoff 验收**。
- `integrity.mode` 的 safe/notify/off、认证 `runtime.hello` / `runtime.heartbeat`、strict repair、`sidecar.restart_required` 与不自动重启 Gateway：**待完成升级 simulation**。
- `service.manager` 四模式、`auto` 不提权、Docker 普通容器：**待 Linux manager 与 Docker Compose smoke**。
- 完整 pytest、`git diff --check`、build/isolated `site-packages`、exact merge SHA、public tagged install 和 Release assets：**发布流程待完成**。

## V4.0.21 发布门禁

- Issue #155：只有明确 `answer -> tool` 边界才能归档答案；`tool -> answer -> completed` 必须保留完整的用户可见终态答案：**已通过聚焦顺序回归（`74 passed`）**。
- Issue #147：完成卡接管后，匹配原生媒体文本只抑制一次、native image 继续投递，accepted queued notice 不出现 uncertain-delivery warning：**已通过 hook runtime 组合回归（`277 passed`）**。
- 当前 README、安装说明、Docker Compose 和双语用户指南均 pin 到 `v4.0.21`；UI 与配置保持不变：**已通过文档门禁**。
- 真实飞书图片验收：**已通过（2026-07-28）**。观测到 1 条带标记、非“生成中”的 completion card + 1 条 native image，无 uncertain-delivery warning；正常工具回合的两段答案保留在同一卡，bot 原生标记重复为 0。
- sidecar 最终 metrics 为 `events_received/events_applied=23/23`、1 次发送成功、16 次更新成功，event/auth rejection、send/update failures、notice uncertain warnings、notice update failures 均为 0；Gateway Feishu WebSocket 已连接，Hermes venv site-packages 为 4.0.21。
- 最终本地发布门禁：完整 pytest 为 `1526 passed, 4 skipped in 53.56s`；`uv build` 生成 `hermes_feishu_streaming_card-4.0.21.tar.gz` 与 `hermes_feishu_streaming_card-4.0.21-py3-none-any.whl`。干净 Python 3.12 venv 从 wheel 安装后，import 位于 `site-packages`，package/distribution version 均为 `4.0.21`，`hermes-feishu-card = hermes_feishu_card.cli:main` 存在，CLI --help exit 0。
- 上述验收不宣称截图或桌面/移动端视觉 QA，也不替代真实故障注入。公开 tagged installer 与 Release assets 的 post-tag 验证仍待完成。

## V4.0.20 发布门禁

- 已有卡片 notice 必须只在 `applied=true` 且异步 PATCH 已排队时返回 `accepted`；hook 据此接管并抑制误报：**已通过 hook/server 回归**。
- 独立 notice 初始 create/reply 继续使用三态投递语义；不把排队等同于送达，也不等待每次 PATCH：**已通过既有投递回归**。
- PATCH 内部重试耗尽后 `notice_update_failures` 增加一次，`last_update_error` 只保留异常类型和白名单 `status_code` / `api_code`：**已通过故障注入与脱敏断言**。
- 最终全量自动化：**已通过（`1517 passed, 4 skipped`）**；sdist/wheel、隔离 `site-packages` import `4.0.20`、公开 tagged installer 与 Release assets 在发布流程中复核。

## V4.0.19 发布门禁

- Hermes venv Python 默认不带 `--user`，system Python fallback 保持 user install：**已通过 installer 回归**。
- pip 安装失败保留真实退出码并阻止 setup：**已通过红灯/绿灯回归**。
- fresh Hermes venv 不设置 `HFC_PIP_USER` 完成安装，并从 venv `site-packages` 导入目标版本：**已通过真实安装 smoke**。
- 最终全量自动化、sdist/wheel、公开 tagged installer 与 Release assets 在发布流程中复核。

## V4.0.18 发布门禁

- Hermes adapter 使用 `extra_ua_tags` 时检查真实 SDK 构造签名；旧 adapter 不触发安装，兼容的新 SDK 不强制降级：**已通过 CLI/diagnostics 回归**。
- `doctor` 只读输出 `feishu_sdk_incompatible`；`setup/install` 安装 `lark-oapi==1.6.8` 后必须复检通过：**已通过红灯/绿灯集成测试**。
- 真实 Hermes v0.19.0 Gateway 从 `lark-oapi 1.5.3` 修复到 `1.6.8` 后恢复 `✓ feishu connected`，214 个 runtime 包依赖兼容：**已通过**。
- 最终全量自动化：**已通过（`1511 passed, 4 skipped`）**；sdist/wheel、隔离 `site-packages` import `4.0.18`、公开 tagged installer 与 Release assets 在发布流程中复核。

## V4.0.17 发布门禁

- 两个并行同名工具使用不同 `call_id`，查询详情和 completed 事件保持独立：**已通过 session/patcher 回归**。
- started/completed 只计一次真实调用，详情中的全部 `耗时:` 元数据被清除且标题只保留一个耗时：**已通过 session/renderer 回归**。
- 本机当前 Hermes 原始 Gateway source 的 patch 编译、幂等与精确 restore：**已通过**；无稳定 callback anchor 的兼容 fallback 保持不变。
- 最终全量自动化：**已通过（`1508 passed, 4 skipped`）**；sdist/wheel、隔离 `site-packages` import `4.0.17`、公开 tagged installer 与本机运行来源在发布流程中复核。

## V4.0.16 发布门禁

- 初始 Header/正文职责、工具开始后的空正文占位移除，以及最终答案/footer 保持：**已通过 renderer/session/server 回归**。
- Hermes `kwargs.duration` 提取、`duration_ms` 传递、started/completed 兜底、terminal-only 不伪造及查询参数保留：**已通过真实 callback 结构 smoke 与自动化**。
- 最终全量自动化：**已通过（`1504 passed, 4 skipped`）**；sdist/wheel、隔离 `site-packages` import `4.0.16`、公开 tagged installer 与本机运行来源在发布流程中复核。
- 本次不重复宣称飞书客户端视觉复验；V4.0.15 已覆盖真实 Hermes/飞书加载与工具状态路径，本补丁的差异由真实 callback 结构和卡片 JSON smoke 验证。

## V4.0.15 发布门禁

- Issue #141 紧凑工具时间线、加载/运行 spinner、同卡 PATCH、停止条件、终态 drain 与 topic/reply anchor：**已通过聚焦自动化和真实 Hermes/飞书模型验证**。
- Hermes 升级覆盖后的只读发现、`start` 拒绝、显式恢复、恢复后 installed，以及用户编辑 fail-closed：**已通过临时 fixture 升级闭环与本机实际升级排障验证**。
- 最终全量自动化：**已通过（`1498 passed, 4 skipped`）**；sdist/wheel、隔离 `site-packages` import `4.0.15` 与 CLI smoke：**已通过**；tag 前再执行 `git diff --check`。

## V4.0.14 发布门禁

- heartbeat 非终态、同锚点复用、不同锚点隔离、orphan 6/9 分钟更新与最终完成收束：**已通过聚焦自动化**。
- unknown delivery 后的稳定独立卡恢复与既有 fail-open 分支：**已通过回归测试**。
- Issue #142 的真实 Feishu `v4.0.13` 复现证据已记录；候选版不重复等待真实 6/9 分钟，不把自动化等价重放写成客户端视觉复验。
- 最终全量自动化：**已通过（`1488 passed, 3 skipped`）**；sdist/wheel、隔离 Python 3.12 `site-packages` import `4.0.14` 与 CLI smoke：**已通过**；tag 前再执行 `git diff --check`。

## V4.0.13 发布门禁

- 全命令上下文、同卡多反馈、并发单 create、长 Markdown、create/PATCH 原文回退与 `/compress` 全分支矩阵：**已通过**。
- 专用 `/model`、裸 `/resume`、confirmation、`/hfc`、Agent turn、媒体和 `/update` 重启边界回归：**已通过**。
- 真实 Feishu 客户端命令矩阵和桌面/移动端视觉确认：**本次未执行，不写成已通过**。
- 最终全量自动化：**已通过（`1482 passed, 4 skipped`）**；`git diff --check`、sdist/wheel 和隔离 Python 3.12 import/CLI smoke 均在 tag 前验证。

## V4.0.12 发布门禁

- compaction hook/session/render/server 与字号 schema/merge/render/device 聚焦矩阵：**已通过**。
- selected env 真实子进程启动为 `healthy/live`；缺凭据子进程为 `degraded/noop`，发送 `not_sent` 且 success 不增加：**已通过**。
- 自动压缩长会话 smoke 与桌面/移动端最终视觉确认：**按发布决定未执行，不写成已通过**。
- 最终全量自动化：**已通过（`1460 passed, 4 skipped`）**；`git diff --check`、sdist/wheel 和干净 Python 3.12 import `4.0.12` 均通过。
- annotated tag `v4.0.12` 指向合并提交 `00a48a7`；release-assets workflow `29632908140` 成功，四个 assets/checksums 与公共 tagged installer：**已通过**。

## 当前边界

自动化测试不会访问真实飞书，也不会启动真实 Hermes Gateway。真实联调仍是人工/本机验收流程，成功后只记录脱敏结果，不提交凭据、真实 chat_id 或敏感截图。
