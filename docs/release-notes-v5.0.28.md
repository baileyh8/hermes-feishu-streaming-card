# v5.0.28：`uninstall` PM 依赖前置校验与显式释放 adopted-selection member

[English](release-notes-v5.0.28.en.md)

本版修复自管理 PM 布局下 `hermes-feishu-card uninstall` 遇到 `selection_added: false` 成员时先还原受管源码再失败退出的问题，并为经完整归属与摘要校验的 schema 2 `hfc-runtime-dependency` member 提供显式释放入口，同时同步开发工具 Ruff 至 `0.17.6`。

- **卸载前置校验（[#540](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/540) · [PR #543](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/543)）**：新增 `pm_dependency.preflight_remove()`，在 `cli._run_uninstall()` 调用 `_restore()` 之前先校验 PM runtime 可用性、member 归属与 `selection_added` 策略；拒绝时直接退出，避免出现源码已恢复而依赖残留的半卸载状态。
- **保留保守默认 + 显式释放入口（[#540](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/540) · [PR #543](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/543)）**：默认 `uninstall` 继续保守拒绝 `selection_added: false`，并在报错中提示升级前应改用 `hermes-feishu-card restore`（恢复受管 hook 并保留已归属 PM dependency）；确需彻底移除经 `_owned()` 与外部 `wheel-store` 摘要完整校验的 schema 2 `hfc-runtime-dependency` member 时，可显式执行 `uninstall --remove-adopted-pm-dependency`，经官方 PM admission API 移除选择项并删除 member 目录。未归属目录、文件漂移、schema 1 以及 schema 3（`legacy_9b904`）仍保持 fail-closed 拒绝。
- **开发工具与文档同步（[PR #542](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/542)）**：更新 `ruff` 开发依赖至 `0.17.6`，并同步 `docs/wiki/pm-recovery.md`、`README-install.md` 与 `docs/wiki/development-rules.md`。

请按[安装说明](../README-install.md)使用正常安装流程，升级前如需 park 受管 hook 请使用 `hermes-feishu-card restore` 并保留 manifest 与备份。精确提交 CI、实际发行包与现场验收边界以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.28) 的记录为准。

感谢 [tidytorch](https://github.com/tidytorch) 在 [#540](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/540) 提供跨多节点自管理 PM 升级窗口的 `selection_added: false` 诊断与复现证据，感谢 [Dependabot](https://github.com/apps/dependabot) 提交 [PR #542](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/542)。问题证据与代码作者分开记录，原提交与历史署名保留。
