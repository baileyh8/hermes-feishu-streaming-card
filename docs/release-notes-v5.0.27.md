# v5.0.27：兼容 Hermes v0.23.3 与有意静默卡片收尾修复

[English](release-notes-v5.0.27.en.md)

本版适配 Hermes v0.23.3 官方源码（`caafe5389ae585ef77387a962aee05b1bcf17029`），并修复后台完成回执等有意静默回合在飞书卡片显示 `NO_REPLY` 且停留在「执行中」的问题。

- **Hermes v0.23.3 兼容（[#537](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/537) · [PR #538](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/538)）**：登记 `pm-0233` writer 与 launch 家族（含 `hermes_scope.py` 与 `hermes_cli/main_resume_args.py`）及 schema 54 配置写入契约；适配 `persistence_assembly.Persistence` 句柄与 `SQLiteAdmissionRepository` / `SQLiteSessionRuntimeRepository` 仓库接口（由 `current_epoch()` 读取持久 epoch，实例 ID 来自进程内 `SessionAuthority`），校验底层 `SQLiteStore`、`StoreHolder`、作用域与文件替换状态，保留未知来源拒绝与逐字恢复。
- **有意静默卡片收尾修复（[#535](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/535)）**：在 `answer.delta` 阶段有界暂存潜在静默标记前缀，仅在确认允许静默时传递 `intentional_silence` 并在原卡就地收尾（保留已有正文、附件与审批回执，不触发完成通知或补发空卡）；覆盖普通、queued-first（含 `notification_no_reply` 空正文）与递归排队回合的静默判定及回合归属；人类回合被 Hermes 拒绝静默时以独立的 `native_fallback` 终局状态保留原生 fallback 投递并隔离重复/迟到终局事件。
- **CI 门禁扩展**：增加固定的 v0.23.3 源码绑定、writer 家族与官方镜像（`preserved` / `cleared`）验收。
- **未纳入范围**：[#536](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/536)（`~silent`）目前仅有截图、缺少版本与日志证据，本版不宣称修复。

请按[安装说明](../README-install.md)使用正常安装流程，对已有安装保留 manifest 和备份。`doctor` 的兼容结果只证明检查器能识别当前源码和安装状态。精确提交 CI、实际发行包、Gateway/model 和飞书客户端的验收结果以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.27) 的记录为准。

感谢 [0x3st](https://github.com/0x3st) 在 [#537](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/537) 提供 119 文件漂移与 cohort 分析，并在 [#538](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/538) 提交 `pm-0233` 兼容实现；感谢 [mouyong](https://github.com/mouyong) 在 [#535](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/535) 提供后台回执 `NO_REPLY` 截图与时间线证据。问题证据与代码作者分开记录，原提交与历史署名保留。
