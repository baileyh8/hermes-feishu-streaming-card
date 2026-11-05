# V5.0.15 — 进行中工具信息、按会话控制与 Hermes v0.22.7 兼容

[English](release-notes-v5.0.15.en.md)

本版交付 #489、#491、#492 与 #494 的修复，并保留 #493 为待补信息的调查项。升级、全新安装和未写出新字段的配置保持原有默认行为。

## 变更范围

- **#489 进行中工具信息**：新增 `card.tool_activity: full | summary | hidden`，默认 `full`。`summary` 只显示通用步骤与耗时，`hidden` 去掉标题副行、正文工具行、底部工具计数与动作短语；两者也从进行中的思考时间线移除工具条目。终态卡片（completed/failed）保持原有行为，仍由 `hide_completed_tool_activity` 与阅读预设决定。报告者已确认三态方案与面板不保留工具名的口径。
- **#492 按会话控制**：`bindings.default_delivery` 支持标量 `card`/`native` 或 `{group: native, p2p: card}`；`bindings.card_chats` 列出类型默认下的例外群；`card.chat_overrides` 只允许覆盖 `tool_activity`、`show_reasoning`、`stream_thinking_to_body` 和 `hide_completed_tool_activity`。优先级为单 chat → 会话类型 → 现有 global/profile/bot 合并结果；覆盖在会话创建时固定。chat_type 缺失或无法识别时不套用类型设置，单 chat 规则仍生效；同一 chat 同时出现在 `native_chats` 与 `card_chats`、非法枚举或白名单外字段在加载配置时报错。`card-config --chat-type/--chat-id` 显示生效值和来源，不回显 chat ID。
- **#491 Hermes v0.22.7**：在精确 commit `b200f5bc60e31588ba2cb60e288029c4a54ba5fa` 上独立复现 `partial`（仅 `admission_completion` 为 `unsupported-source`），新增该版本的整组源码 cohort 与运行时拓扑校验；ingress、post-delivery 与 follow-up ACK 文件逐字不变，authority、producer lifecycle 和 Base 有变化，Base 的 extraction seam 仍逐字一致。未知上游源码继续 fail-closed，CI 增加固定检出、文件摘要清单与逐文件漂移的负向对照。
- **#495 v0.22.7 安装事务**：在 #491 的检测 cohort 之外，为 v0.22.7 新增整组 writer family `pm-0227`：以 `pm-0226` 的 100 个键为基础，仅替换 7 个变化文件（`agent/skill_utils.py`、`hermes_cli/config_defaults.py`、`pm/environments.py`、`pm/plugin_eviction.py`、`pm/publication.py`、`pm/workspace.py`、`toolsets.py`），两个 launch 文件逐字复用。安装前置、config backend proof、PM admission 与 Windows/ACL 审计均识别该 family；未知或漂移源码仍拒绝。
- **#494 受管 PM 升级收敛**：pip 从 Git commit 安装的 CLI 现在从 `direct_url.json` 推导安装 spec，不再要求手工设置 `HFC_INSTALL_SPEC`；索引 wheel 没有可固定的源码，仍需显式 spec。重复安装时若已验证的 PM 代际、版本、spec 摘要与普通 wheel 资源全部一致，则复用现有代际，不再新建 PM 环境，从而避免 observer runtime binding 反复漂移。版本升级本身产生一个新代际仍属预期；失败事务仍保留原有 fail-closed 与回滚证据。
- **#493**：报告截图中的旧卡正文只剩「本轮已被新对话替代」提示。v4.4.5 起 `server.py` 就是在 `answer_text` 后追加该提示；v5.0.13..v5.0.14 的 `server.py` 无差异，当前没有证据表明代码被覆盖。已向报告者请求精确版本、脱敏事件序列与布局信息，本版不改动该渲染路径。

## 配置与验收

`card-config` 新增 `--chat-type` 与 `--chat-id`；`--running` 可带选择器核对已加载值，无选择器的 scope hash 不变。`--minimal-yaml` 与 chat 选择器互斥。`chats use-native/use-card` 仍只管理 `native_chats`；`card_chats` 需直接编辑对应 profile 的 `bindings`。

自动化证据：三态 × 各渲染表面、终态不变、非法值拒绝、`card-config` 有效值与来源；v0.22.7 精确检出上的 detect 与逐文件漂移负向对照；投递策略的类型/单 chat/未知类型/冲突与 profile 边界；显示覆盖的分层合并与 selector 不回显；PM spec 推导与重复安装跳过新代际的正负向对照；完整 focused 回归、ruff、mypy 与 `git diff --check`。完整 CI、tag 门禁、资产校验和、公开匿名下载与普通包安装验收结果见本版 GitHub Release。

限制：报告者现场、真实 Hermes Gateway/model 执行、真实飞书群聊/私聊的视觉与实际点击、以及受管 macOS launchd + PM + legacy observer 现场升级仍未由本次自动化覆盖；#495 的 v0.22.7 安装由固定源码 family 与负向对照覆盖，真实 macOS 现场升级仍需报告者复测。#489 报告者将在发布后按 `summary` 与 `hidden` 各复测一次；#494 报告者需在隔离环境复测升级收敛；#493 待补信息后再定位。归档仍保持单一顶层目录，解压前后目标目录属主与权限不变，Linux root/非 root 与 macOS 实际解压由 CI 覆盖。

## 贡献

感谢 [Lite-G](https://github.com/Lite-G) 提供 #489、#491、#492 的定位与 [PR #490](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/490) 的参考实现，[ywarmy](https://github.com/ywarmy) 提供 #495 的 writer family 字节级数据，[WenhuaXia](https://github.com/WenhuaXia) 提供 #494 的受管部署复现记录，[mouyong](https://github.com/mouyong) 提供 #493 截图。问题证据与代码作者分开记录，#486 原始提交作者与历史贡献全部保留。
