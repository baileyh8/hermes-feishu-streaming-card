# V4.6.6 — 审批、工具与通知收尾

本版集中处理 #337、#340 和 PR #338/#339 的剩余体验问题，沿用 V4.6.x 版本号与阅读默认。

- 已决定审批在整轮 completed/failed 后精简重复审阅区。清理前先显式更新独立回执，确认完整问题、操作范围和选择已投递；唯一回执、仍待审批、回执投递失败或容量不足时保留原记录。连续审批逐条确认，不影响 clarify。展示证明仅在内存中，不恢复授权或执行。
- 过程面板优先保留运行中的工具、其前一步及对应思考段，修复较早的 #24/#25 被最近条目窗口挤掉的问题；编号与正文一致。整轮结束后未收到工具终态的条目显示“已中断”。仍遵守总条目和卡片容量限制。
- 已登记重启、上线、关闭和 draining 提示在 15 秒后撤回；同一 profile/bot/chat/thread 的确认投递可提前唤醒计时。Home 按自己的计时清理，其他话题不会提前清理它。私有账本恢复原始期限，删除失败保留冷却与归属。
- 成功后台任务的一行提示、原生过期审批的一行提示按 15 秒收尾；失败、带输出的结果和完整审批回执保留。实际发送必须来自已知 producer，并验证 adapter、profile 和应用；普通答案引用相同文案不获得撤回权。原生 requester 重启完成提示使用彩色 ♻️，保留原文含义。
- 开启 `hide_completed_tool_activity` 后，仅隐藏成功工具，失败、取消和中断仍显示。`focused` 在成功回合中也保留这些异常工具。默认仍为 `false`；过程默认仍为 `newest_first`、`timeline_tools_per_reasoning: 0`。

详见[通知生命周期](wiki/notice-lifecycle.md)、[审批续答](wiki/interaction-continuation.md)、[阅读设置](wiki/reading-presets.md)和[验收记录](wiki/feishu-acceptance-v4.6.6.md)。平台拒绝撤回、来源不明历史或未知 Hermes 签名会保留消息；本版不承诺跨系统原子投递，不变更 AMD/9Router、模型选择或 Hermes 核心。

感谢 [mouyong](https://github.com/mouyong) 的 [PR #338](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/338)、[PR #339](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/339) 方案与代码，以及 [#337](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/337)、[#340](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/340) 的现场证据。按来源证明和回执确认边界适配，保留贡献署名；历史贡献者仍完整保留于 README。

发布门禁包括全量测试、精确合并 CI、annotated tag、资产/checksum、公开 tag 普通安装、本机官方升级和真实飞书验收。结果随 [Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.6.6) 登记；自动化不等于所有设备体验通过。
