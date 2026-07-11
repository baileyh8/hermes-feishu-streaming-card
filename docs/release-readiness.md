# 发布准备说明

[中文](release-readiness.md) | [English](release-readiness.en.md)

当前候选包版本为 `3.10.0`。它加入裸 `/resume` 原生会话选择器和转义后的模型 footer 语义色，同时复用 Hermes 原生安全恢复路径并保持 footer/layout 不变。V3.9.1 已于 2026-07-11 发布；V3.9.0 同样已于 2026-07-11 发布，并建立在 sidecar-only、V3.8.2 timeline、群聊诊断、话题/cron 路由和 WebSocket 交互基础上。

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
- 飞书卡片按钮交互覆盖 `interaction.requested`、`/card/actions`、`/interactions/{interaction_id}` 的本地 mock 验收；localhost/private sidecar 覆盖 `card.interaction_mode: text` fallback。
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
- 独立 slash 命令确认支持 Feishu command card：`/new`、`/reset`、`/undo` 和高成本 `/model <model>` 确认会优先渲染为独立命令卡片。
- Feishu/Lark WebSocket 长连接部署会动态获得原生 `send_slash_confirm(...)` 和 `send_model_picker(...)` 卡片能力；按钮点击经 `_on_card_action_trigger` 回到 Hermes 原 handler。
- WebSocket 原生卡片可用时跳过 sidecar `interaction.requested` 预交互，避免同一 slash 命令同时出现 sidecar 选项卡和原生按钮卡。
- `/model` 无参数选择可通过 Feishu-only `send_model_picker(...)` 卡片呈现；选择后回调 Hermes 并更新同一张命令卡片。
- `/update` 保持 Hermes 后台升级命令语义，不渲染交互命令卡片；sidecar 不可用或卡片完成态更新失败时退回 Hermes 原生文本路径。
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

## 发布前必须验证

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

真实飞书联调只能使用本机配置或环境变量提供 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。不要把 App Secret、tenant token 或真实 chat_id 提交到仓库。公开演示截图入库前需要确认不包含敏感凭据和不可公开的会话内容。

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
- tag 后验证 macOS、Linux、Windows 与 checksums 四个 assets。

`v3.9.0` tag 的 release-assets workflow 会发布 4 个 assets：macOS tarball、Linux tarball、Windows zip 和 checksums 文件，分别为 `hermes-feishu-card-v3.9.0-macos.tar.gz`、`hermes-feishu-card-v3.9.0-linux.tar.gz`、`hermes-feishu-card-v3.9.0-windows.zip`、`hermes-feishu-card-v3.9.0-checksums.txt`。

## 当前边界

自动化测试不会访问真实飞书，也不会启动真实 Hermes Gateway。真实联调仍是人工/本机验收流程，成功后只记录脱敏结果，不提交凭据、真实 chat_id 或敏感截图。
