# V4.6.7：心跳稳定与紧凑审批

Working 心跳由 Hermes 原地编辑。HFC 不再在 15 秒后删除它，避免下一轮编辑失败并反复创建新消息；终轮清理仍由 Hermes 控制，一次性压缩、等待审批和重试提示保持原策略。

审批单选按钮使用每行最多四个自动宽度列，完整操作、选项说明和原 callback 身份保留。clarify、阅读默认、时间线硬预算、通知 producer 来源、Home 隔离和审批过期纠正回执不变。

感谢 [mouyong 的 PR #345](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/345)，本版提取心跳与审批布局修复，不整体合并 fork 的产品偏好。[#344](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/344) 截图中的旧卡正文问题未在当前版本复现；当前中性交接路径可保留正文/思考，仍需反馈者版本和步骤，不因此关闭问题。

## 验证边界

核心修复自动化：4170 passed、12 skipped；三次心跳仅发送一次并编辑两次，审批回执保留完整脱敏正文。最终提交、精确合并、平台和公开安装证据由 GitHub Release 交付记录补充。真实桌面按钮回调与手机布局分开记录；未经验证不宣称通过。
