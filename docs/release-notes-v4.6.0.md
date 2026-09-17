# V4.6.0：集中稳定性修复

- #323：瞬态提示撤回携带 profile/chat 路由，sidecar 按 bot binding 选择实际客户端；多 profile 下拒绝猜测默认机器人。
- #319：接入 Hermes 结构化 reasoning，按轮绑定并保留原生 fallback；思考与最终正文分离，流式片段和完整回调去重，不删除合法重复 token。
- #310：终局重试的总时限覆盖锁等待、请求及退避，日志不输出原始标识符和异常正文；排队结束卡携带模型、耗时和用量，成功空答案不误称中断。工具详情与标题分别限长，时间线展示最新条目。
- #320：私有有界检查点恢复原卡展示与投递身份；已知 Gateway 启动恢复入口先有限等待 sidecar policy，避免启动窗口过早固定为原生文本。

## 恢复边界

不恢复执行栈、旧审批 token 或 native admission。待审批任务须重新发起；未结束卡静态显示连接已恢复、等待状态同步，不能假称任务成功。升级前无检查点的旧卡不能追溯修复。检查点最多 128 条、每条 1 MiB、保留 24 小时，含本地对话正文，目录/文件使用私有权限。磁盘失败继续保留原投递并报告诊断，不补发重复消息。详见 [恢复设计](wiki/card-restart-recovery.md)。

## 验证

每项修复有失败复现及结果断言，覆盖本机 HTTP 单卡终局、重复/跨 chat/profile、旧审批失效、磁盘失败、实际生成的回调执行和可逆升级。Hermes `8017dfa4a87aac9b9ae478f6b465508ca904ebb6` 的真实源码验证包含升级、重复安装、回调执行与卸载还原。完整测试、CI、资产与公开安装的最终结果在发布时补充；此文不宣称生产升级或手机真机验收。

## 贡献

感谢 [mouyong](https://github.com/mouyong) 的 [PR #310](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/310)（截至 `f35a4ac`，保留原始作者）及 #320 现场反馈；[zhangzq](https://github.com/zhangzq) 的 [#319](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/319) 环境与 reasoning 证据；[qqqq560204-maker](https://github.com/qqqq560204-maker) 的 [#323](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/323) 路由复现。README 中既有贡献记录全部保留。
