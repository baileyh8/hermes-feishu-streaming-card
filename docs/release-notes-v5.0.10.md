# v5.0.10 — 异步完成交接与配置归属恢复

本补丁处理 [#469](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/469)、[#470](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/470)，并为 [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451) 的重复投递反馈补充诊断。正式发布状态和精确验收证据以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.10) 为准。

## 修复范围

- **异步通知交接**：核验固定 Hermes 源码中的原生 automation admission，在完整 claim、持久化 row 和路由一致时借用原执行归属。标准 v0.22.5 会将复合消息 ID 编码成 JSON 字符串；不接受任意 list/tuple 拼接，也不从展示字段推断执行身份。
- **终局确认竞态**：提交前仍检查当前 owner、generation、epoch 和 superseded 状态。sidecar 明确接受终局后，仅确认并消费本次提交，避免等待期间任务代次变化后又发送同一份原生正文。`applied=true` 代表 sidecar 接管展示投递，不代表 Feishu PATCH 已完成；后续发送和重试仍由 sidecar 负责。
- **配置归属与恢复**：检查和撤销可证明属于 HFC 的配置改动，保留用户无关编辑。受管内容冲突、不可核验证据和并发文件变化继续拒绝。运行环境身份变化仍通过明确的恢复流程处理，不自动删除 marker 或历史证据。
- **失败诊断**：区分 admission 交接的固定拒绝类别，避免不同原因都显示为身份不明；不记录原始用户、会话或配置内容。

## 验收与限制

固定上游源码的失败复现、正反路径、重复与迟到事件、独立 QA/review、普通 wheel 的真实子进程与包来源、跨平台完整 CI，以及受控真实 Feishu API 分别验收。公开 tag 安装、匿名下载、checksums 和实际解压另作发行门禁；未完成的阶段不写成通过。

#451 的最新现场包含自定义 profile 路由和其他热修，本补丁不宣称已经独立复现或解决该部署的全部现象。报告者真实环境、真实 Gateway/model 对话，以及桌面/Android 视觉和点击仍需分别验证；iOS 和新用户试用不在本轮范围。

升级时沿用实际 Hermes checkout、HOME 和配置，通过正式安装器刷新受管 hook，再由原服务管理器重启对应 sidecar/Gateway。保留安装清单、marker、preimage 和 journal。安装包采用版本化顶层目录，解压后进入 `hermes-feishu-card-v5.0.10-<platform>/` 再运行安装器。

## 贡献

感谢 [leavrcn](https://github.com/leavrcn) 的 #469/#470 报告和排查建议，以及 [kludgezol](https://github.com/kludgezol) 在 #451 补充的多 profile 现场证据。问题证据与代码作者分别记录；既有提交作者、tag、发行资产和历史署名全部保留。
