# v5.0.29：按会话类型与单群静默抑制系统通知卡

[English](release-notes-v5.0.29.en.md)

本版新增可选配置 `card.suppressed_notice_kinds`（默认 `[]`），支持按全局 / profile / bot、会话类型（`group` / `p2p`）或单个 `chat_id` 静默抑制「自我改进」等非对话类系统通知卡，避免内部维护通知在多人群聊中形成干扰。

- **按会话抑制系统通知卡（[#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500) · [PR #547](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/547)）**：将 `suppressed_notice_kinds` 纳入 `card` 与 `card.chat_overrides` 白名单及 `card-config --chat-type/--chat-id` 只读诊断（不回显真实 `chat_id`）。支持 `self-improvement`、`skill-loading`、`compression`、`context-compaction`、`context-cap`、`session-reset`、`background-process`、`background-task`、`heartbeat`、`home-channel`、`gateway-restart`；非法类别或非列表在配置加载时报错拒绝。
- **中英文识别与静默不回落（[#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500) · [PR #547](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/547)）**：`hook_runtime` 同步识别英文 `Self-improvement review` 与中文 `自我改进回顾` 并透传 `chat_type`；后台 detached 回调未带 `chat_type` 时在 sidecar 侧回退到同 `(profile, chat_id)` 已观测会话的 `chat_type`。命中抑制时既不发飞书卡片也不回落原生纯文本，不影响 Hermes 后台执行与日志。

请按[安装说明](../README-install.md)使用正常安装流程，升级前如需 park 受管 hook 请使用 `hermes-feishu-card restore` 并保留 manifest 与备份。精确提交 CI、实际发行包与现场验收边界以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.29) 的记录为准。

感谢 [Lite-G](https://github.com/Lite-G) 在 [#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500) 提出按会话类型与单群抑制系统通知卡的场景需求与边界说明。问题证据/建议与代码作者分开记录，原提交与历史署名保留。
