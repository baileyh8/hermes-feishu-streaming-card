# 临时通知归属与清理

4.6.4 只维护进程内的明确归属；4.6.5 在私有 state 下增加 `restart-notices-v1/owned.json`。登记包含 exact profile/bot/chat/thread、message ID、当前应用身份摘要、代次、创建时间和重试时间。后续成功发送/更新仅清理发送前的同作用域快照；空 thread 不通配话题，迟到清理不处理新代次。

原生通知仅从已知 `_send_home_channel_message` / `_send_shutdown_notice` 签名与精确在线/关闭/重启模板取得 task-local 来源。实际 adapter 必须唯一属于当前 profile，发送应用摘要须匹配 sidecar 对该路由选择的客户端。普通 send、转述相同文案、来源不明历史、relay 与未知签名 fail-open 保留原生消息，不登记删除权。

账本使用 0700 目录、0600 文件、原子写入、摘要和严格有界校验；500 个作用域、每个 8 条、文件 4 MiB、保留 7 天。持久化成功才确认新登记；损坏文件不覆盖，诊断 `restart_notice_store_state=unavailable`。不同应用、路由策略不再允许 card、过期记录不恢复；过期只清理本地归属，不扫平台历史。删除失败保留冷却与重试，重启使用 wall-clock 转换新的 monotonic 冷却。

不得将普通回答、审批决定、失败解释或未知 home 提示当临时通知撤回。账本不含答案/密钥/token，不恢复执行或审批。平台发送与本地登记之间仍可能中断，因此不承诺跨系统原子发送或永久 exactly-once。

V4.6.6 增加 15 秒到期与同 scope 确认投递提前唤醒；使用已有创建时间恢复计时，不重置重复登记期限。原生 requester、drain、后台成功一行有独立 producer 证明；原生过期审批纠正提示可能是唯一未执行记录，保持留存；普通短提示仍为进程内计时，完整回执、失败和输出不撤回。详见[生命周期](notice-lifecycle.md)。

## 群聊通知抑制

`card.chat_overrides` 的 `suppressed_notice_kinds` 作用于来源已确认的 `system.notice`。普通正文即使逐字引用自我改进回顾、后台任务或进程通知，也会经过原生 `send` 或 `edit_message`。`Base._send_with_retry`、会话上下文和 `_interim_send` 都只提供投递信息。压缩、会话重置及技能加载继续使用已知的 Hermes 通知出口，混合工具进度保留原生更新。

Hermes v0.23.3 的会话内回顾在 `post_delivery` 释放，冷会话的 `/refine` 通过同一调度器投递。HFC 核对这些回调及旧版内联回调的实际发送调用，识别英文、简中和繁中正式模板。已确认的通知按来源的 profile、群或私聊配置处理，指定群的 `[]` 继续覆盖群默认值，`native` 沿用原生发送。[会话回调](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/gateway/run_turn_runner.py#L453) · [冷会话 `/refine`](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/gateway/session_cold_review.py#L102) · [繁中模板](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/locales/zh-hant.yaml#L3576)

后台任务的启动回执仅在原 `/bg` 处理调用和同一 Base 投递任务之间传递来源。任务结果和旧版进程通知分别从已知后台任务和 watcher 的实际发送调用确认来源，再核对完整模板。新版 watcher 的简短成功通知沿用原生文本和到期撤回，完整输出仍保留。[启动回执](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/gateway/slash_commands.py#L849) · [任务结果](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/gateway/run_background.py#L131) · [watcher 发送](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/gateway/run_notifications.py#L1737)

Gateway 的平台通知等待侧车回执。已送达或已接受的通知由侧车处理，其中包括按配置抑制的通知。明确 `not_sent` 时由 Hermes 按原路由补发正文。结果为 `unknown` 时，Hermes 只尝试发送一次固定提示，避免重复正文。旧版安装中已有的同步 hook 在运行中的 Gateway loop 内直接交还原生入口。[平台通知原生入口](https://github.com/NousResearch/hermes-agent/blob/caafe5389ae585ef77387a962aee05b1bcf17029/gateway/run_notifications.py#L195)
