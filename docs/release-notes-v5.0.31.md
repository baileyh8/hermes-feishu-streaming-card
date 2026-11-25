# v5.0.31：兼容 Hermes v0.23.4 与修复流式卡片重复原生中间回复

[English](release-notes-v5.0.31.en.md)

## Hermes v0.23.4 兼容

- 适配固定的 Hermes `v0.23.4` 源码（`8fc0374447313e276f6b9a2764bfc750190c966a`，配置 schema 54）：登记 `pm-0234` 官方配置写入与启动家族（117 个 writer 文件，含新增 `hermes_cli/profiles_run_elsewhere.py`，以及 5 个 launch 文件），解决 `v5.0.30` 在 `v0.23.4` 上因文件漂移触发 `official config writer is unknown; PM admission refused` 的拒装问题。 [PR #554](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/554) · [#553](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/553)
- 补齐 `v0.23.4` 的 45 模块 `Persistence` 准入源码与渲染摘要校验，显式绑定新增的 `persistence.authority.vocabulary.retry_input` 并在 `doctor` 中核验 `admission_completion=True`。
- 保留 fail-closed 边界：未知或漂移源码继续拒绝；存在 `profiles_run_elsewhere/<profile>.json` 标记的目标 profile 继续保守拒绝（`unbound_read`），同时不阻断同一 `HERMES_HOME` 下未被标记的本地 profile。
- CI 新增固定的 `v0.23.4` 源码绑定、writer 家族与官方镜像（`nousresearch/hermes-agent@sha256:ef0650d523ee1b5ab32c779acabc446d9936869a9932591553688a62bcf0c42f`，`preserved` / `cleared` 双模式）门禁。

## 飞书流式卡片重复原生中间回复去重

- 修复开启 `display.platforms.feishu.interim_assistant_messages: true` 时，因默认 `display.show_reasoning=True` 或全局 `streaming.enabled=False` 导致 `stream_deltas_enabled=False`，HFC 已通过 `ANSWER_DELTA_PATCH` 将正文流式写入卡片后，`THINKING_DELTA_PATCH` 因跳过 `already_streamed=True` 而回落到 Hermes 原生 `on_commentary`，在卡片下方再发一条引用用户原消息的重复原生回复的问题。 [PR #554](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/554) · [#552](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/552)
- 渲染契约升级为 `completion-native-fallthrough-v3`（同步更新 `v0.23.0`–`v0.23.4` 固定渲染摘要）。当 `already_streamed=True`、当前回合由卡片策略接管且纯预览事件校验通过时，抑制重复原生中间回复且不再重复发射 `thinking.delta`。
- 保留 `v5.0.30` 历史 `thinking.delta` 补丁模板识别，既有 `v5.0.30` 安装可逐字节还原并升级；非飞书路由、`policy=native` 或无法确认回合身份时继续 fail-open 交回 Hermes 原生投递。

## CI 与发行证据

- 修复 macOS CI 分片中 `Restore exact Hermes fixed-tag source` 使用 `PATH` 解析到的 Homebrew `git` 而非 `native_hooks.py` 要求的 `/usr/bin/git`，导致冷启动首次 `_git_head` 探测偶发触发 5 秒超时的问题；未放宽 `_GIT_HEAD_TIMEOUT_SECONDS` 或任何来源校验。
- PR #554 最终提交 `4bfaeb6f2ffd0fa71c74ee30fa81522a5bc0f6bc` 首轮一次性通过全部 70 项 CI（含 Linux Python 3.9–3.14、macOS 3.12、Windows 3.12/3.14、上游兼容矩阵、全部官方镜像含 `v0.23.4` `preserved`/`cleared` 与 CodeQL）。 [PR #554 CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/45572354976)
- 独立验证覆盖隔离普通 wheel 在官方 `v0.23.4` 上的 `install` / `reinstall` / `doctor` / `restore` / `uninstall`、真实 PM Gen A → Gen B 切代存留与移除、119/119 writer 文件与 45/45 admission 模块逐文件漂移拒绝、`profiles_run_elsewhere` 矩阵，以及模拟多工具回合的 `#552` 六场景复现与去重对照。
- `v5.0.31` 发布提交、主分支门禁、注解 tag、三平台发行包与公开 tag 普通安装另行核验，最终记录见 GitHub Release。真实 Hermes Gateway/model 执行及真实飞书 API、桌面、Android、iOS 现场验收本版未验证。 [发布准备](release-readiness.md) · [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases)

## 贡献者

[baileyh8](https://github.com/baileyh8) 提交 Hermes `v0.23.4` 兼容、重复原生中间回复修复及 macOS CI 夹具校验修复（[PR #554](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/554)）。感谢 [github-liu](https://github.com/github-liu) 在 [#553](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/553) 报告 Hermes `v0.23.4` 兼容性问题，感谢 [Lite-G](https://github.com/Lite-G) 在 [#552](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/552) 提供重复原生中间回复的配置、截图与复现证据。问题证据与代码作者分开记录，原提交与历史署名保留。
