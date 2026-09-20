# V4.6.5 — 通知生命周期与故障反馈收尾

本版继续 V4.6.x 的增量体验计划。已有阅读默认不变；AMD/9Router 运维、默认模型切换和 Hermes 核心升级仍为独立任务。

## 修复与新增

- 新续答确认发送成功后收尾上方旧卡：保留新输出到来前的正文、历史与选择回执，状态改为“本段已转入续答”，不宣称整轮成功。失败/不确定的新发送不收尾；唯一 legacy 回执不接收 schema 2.0 PATCH，旧卡更新失败不阻断新卡。此项由实际桌面复现，并参考 PR #339；无条件隐藏已决定审批的部分尚未吸收。文本选择回执在决定后变为静态记录；连续两题不会把较早回执改回执行中或换成后一题内容。

- 重启通知的精确 profile/bot/chat/thread 归属写入私有账本，sidecar 重启后可在同一作用域成功投递时清理。只有已知原生 startup/home/shutdown producer、实际 adapter 和应用身份相符的消息可登记；普通答案即使引用相同文案也不登记。删除失败保留重试，来源不明的历史提示保留。
- 工具事件使用上游明确的 `call_id` / `tool_call_id`：同一次调用重复终态不增加计数或条目，迟到的 start 不把终态改回运行；持续时间与 ordinal 保留。只有工具名、没有调用 ID 的旧事件继续原语义，不能可靠推断为同一调用。
- 可选 `card.timeline_order: chronological` 让过程面板按时间正序；默认仍为 `newest_first`。`card.timeline_tools_per_reasoning: 2` 在每段思考之后显示最近两个成功工具步骤；默认 `0` 不额外限制。失败、运行步骤不被此规则裁掉，仍受 `max_timeline_items` 和卡片容量限制。正文 code 思考始终正序，历史与工具总数不变。
- 已知模型重试耗尽提示仅在 sidecar 明确确认后抑制重复原生灰色提示；超时、错误、未知格式或不适用的回调保留原生反馈。完整失败原因仍由终态结果卡承载，不把原始 provider 错误复制进通知。
- Hermes 更新器将已知旧 hook 重新应用到新源码、但留下旧 ownership 时，安装器可在显式 `--accept-hermes-upgrade` 下迁移。要求严格移除已知模板后逐字匹配当前 Git blob，并绑定 HEAD；未知改动、损坏 backup、无 Git 来源及中途漂移均拒绝。保留 Git index 与无关本地定制，重复安装无变化，移除恢复新源码。
- preflight 仅在测试子进程中清除代理环境，避免本机代理污染回环 HTTP 验证；父进程与全局设置不变。

## 数据与回退边界

通知账本最多保存 500 个作用域、每个 8 条、7 天；只含删除所需的私有标识、应用身份摘要、时间和代次，不含答案、密钥或审批 token。校验失败拒绝覆写，应用/路由策略改变不恢复旧归属；过期只忘记归属，不主动删平台消息。平台发送与本地登记并非跨系统原子事务，不宣称永久 exactly-once。

带新 `call_id` 的展示检查点需要新版读取；回退旧版本可能跳过这些检查点，不影响执行历史。未改变授权/任务恢复语义，旧按钮不会因检查点复活。详见[阅读设置](wiki/reading-presets.md)和[通知归属](wiki/notice-ownership.md)。

## 验证与贡献

自动化覆盖生成 callback 的实际执行、真实回环 HTTP 的明确 ACK/拒绝/缺字段、通知两次重启与跨话题隔离、应用身份不符、持久化失败、严格迁移与还原、工具重复/迟到事件及显示默认兼容。完整 pytest、精确合并 CI、annotated tag、资产/checksum 与公开 tag 普通安装是发布门禁，最终证据随 [Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.6.5) 登记。

4.6.4 本机生产升级已通过。初次 clarify 被 DeepSeek HTTP 503 阻塞，随后 macOS 飞书真实选择 A 成功，回执下方收到“Acceptance complete: choice A”，同时复现旧卡仍显示执行中。严格首击时序与手机体验没有单独证明，4.6.5 升级后结果另记。本版自动化不替代真实桌面/手机验收，也不据此关闭 #335 等现场问题。参见[本版验收](wiki/feishu-acceptance-v4.6.5.md)。

继续感谢 [mouyong](https://github.com/mouyong) 在 [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331) 的通知、工具和时间线方案及 [#330](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/330) 的贡献者验证需求。本版按子项适配，保留旧默认与原有贡献署名，不代表整 PR 合并。
