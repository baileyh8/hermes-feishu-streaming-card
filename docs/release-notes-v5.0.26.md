# v5.0.26：兼容 Hermes v0.23.2

本版解决 Hermes v0.23.2 被 HFC `doctor` 判为 `unsupported`、无法继续正常安装或升级的问题。兼容目标为官方源码 `ae2a81ccc00e9b7c06301523e5bb09608397149c`。 [#532](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/532)

- 识别新版本拆出的回合来源绑定、审批通知 transport、终局交付账本和停止取消路径。前台审批仍校验回合代际，跨回合的后台审批由 Hermes 原生通道投递。
- 支持 schema 53 下的官方 PM 配置写入。恢复计划绑定只读契约文件的内容和存在状态。受管源码仍可逐字恢复。
- CI 增加固定的 v0.23.2 官方镜像。Linux 的 Python 3.9 回归和解包检查使用 Ubuntu 24.04。开发工具 Ruff 升至 0.17.5。 [#531](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/531)

请按[安装说明](../README-install.md)使用正常安装流程，对已有安装保留 manifest 和备份。`doctor` 的兼容结果只证明检查器能识别当前源码和安装状态。精确提交 CI、实际发行包、Gateway/model 和飞书客户端的验收结果以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.26) 的记录为准。

感谢 [Lite-G](https://github.com/Lite-G) 提供可复现报告和兼容实现。 [#532](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/532) · [#533](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/533)
