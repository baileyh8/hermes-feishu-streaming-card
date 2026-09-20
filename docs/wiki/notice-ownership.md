# 临时通知归属与清理

4.6.4 只维护进程内的明确归属；4.6.5 在私有 state 下增加 `restart-notices-v1/owned.json`。登记包含 exact profile/bot/chat/thread、message ID、当前应用身份摘要、代次、创建时间和重试时间。后续成功发送/更新仅清理发送前的同作用域快照；空 thread 不通配话题，迟到清理不处理新代次。

原生通知仅从已知 `_send_home_channel_message` / `_send_shutdown_notice` 签名与精确在线/关闭/重启模板取得 task-local 来源。实际 adapter 必须唯一属于当前 profile，发送应用摘要须匹配 sidecar 对该路由选择的客户端。普通 send、转述相同文案、来源不明历史、relay 与未知签名 fail-open 保留原生消息，不登记删除权。

账本使用 0700 目录、0600 文件、原子写入、摘要和严格有界校验；500 个作用域、每个 8 条、文件 4 MiB、保留 7 天。持久化成功才确认新登记；损坏文件不覆盖，诊断 `restart_notice_store_state=unavailable`。不同应用、路由策略不再允许 card、过期记录不恢复；过期只清理本地归属，不扫平台历史。删除失败保留冷却与重试，重启使用 wall-clock 转换新的 monotonic 冷却。

不得将普通回答、审批决定、失败解释或未知 home 提示当临时通知撤回。账本不含答案/密钥/token，不恢复执行或审批。平台发送与本地登记之间仍可能中断，因此不承诺跨系统原子发送或永久 exactly-once。
