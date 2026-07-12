# V4.0.0

V4.0.0 将普通 Hermes 飞书卡片升级为更自然的实时 Agent 界面，同时保持 sidecar-only 架构、现有交互安全边界和完成态布局。

## 实时双轨卡片

- 运行态 Header title 保留用户自定义标题（默认 `Hermes Agent`）；subtitle 将 Hermes 工具名与 `progress_callback.preview`（经 `tool.updated.detail` 传递）整理成“正在搜索 / 读取 / 编辑 / 执行终端”等动作摘要。
- 工具间隙保留最后一个非空 preview，不显示插件生成的占位文案。
- 正文独立流式显示 Hermes 公开的 `thinking.delta` 阶段输出；`answer.delta` 开始后主回答优先。
- 等待态 Header 显示 Hermes 原始交互问题，正文保留说明与结构化选项。
- 失败态保留最后一个工具 preview，帮助定位失败发生在哪一步。
- 普通聊天完成态移除 Card JSON Header，只保留飞书原生回复引用作为 Header；没有有效 reply anchor 的兼容路径仍使用配置标题 fallback。

## Footer 与布局

- 运行、等待和失败 Footer 只显示状态，不提前展示未结算的模型、token、时长或 context 数据。
- 普通聊天完成态 Footer 显示“已完成”并接续最终统计，不在 Header 重复状态。
- timeline、附件、按钮布局、话题锚点和同卡更新顺序保持兼容。

## `/model` 与 CLI 同源

- 飞书 `/model` 与 Hermes CLI 使用同一 Provider/模型列表，直接消费 Hermes 已过滤的 picker 数据，不读取或猜测本地凭据与辅助模型配置。
- 卡片先显示 Provider，再在同一张卡片中进入该 Provider 的模型列表；Provider → Model、返回和取消都不会触发模型切换。
- Provider 数量、模型数量和当前项沿用 Hermes 的 `total_models` / `is_current`；最终选择继续调用 Hermes 原生 `on_model_selected`，切换与持久化语义不由插件重写。

## 真实飞书状态

| 运行中 | 等待用户 |
|---|---|
| ![运行态动态工具 Header](assets/feishu-v4-runtime-running.png) | ![等待态原生交互按钮](assets/feishu-v4-runtime-waiting.png) |
| 失败 | 已完成 |
| ![失败态保留最后工具预览](assets/feishu-v4-runtime-failed.png) | ![完成态仅保留原生回复 Header](assets/feishu-v4-runtime-completed.png) |

## 兼容性与安全

- 不扩展 Hermes patch 协议；没有 `preview` 的 Hermes 版本继续使用现有卡片标题和布局。
- `card.interaction_mode: auto` 在 localhost/private sidecar 上也默认使用 WebSocket-native 按钮；显式 `text` 仍保留编号文本 fallback。该路径建立在 @colinaaa 的 [PR #87](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/87) / [issue #86](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/86) 贡献之上，V4 补齐了默认模式的配置衔接与真实群聊回归。
- 动作摘要不调用 LLM，也不推断结果；URL 只保留 host/path、搜索操作符和私有路径会收束，随后执行单行折叠、120 字符限制、Markdown fence 清理和敏感参数脱敏。完整命令仍在 timeline。
- `thinking.delta` 只承载 Hermes 已公开的 interim assistant 输出；插件不接入或展示隐藏 reasoning / chain-of-thought。
- 完成态不在卡片内生成第二份回复引用，也不叠加 `Hermes Agent` Card JSON Header。
- 继续复用现有更新队列、重试、终态屏障、群聊点击归属和原生灰色消息抑制。

## 验证状态

- 单元与集成测试已覆盖 preview 替换、空值保留、交互覆盖/恢复、失败保留、完成清除、脱敏、队列合并和迟到事件拒绝。
- 真实飞书群聊已验证运行态动态 Header、原生等待按钮、交互回调、失败态和仅保留原生回复 Header 的完成态；公共安装包 smoke 将在发布前完成并记录。

## Release assets

- `hermes-feishu-card-v4.0.0-macos.tar.gz`
- `hermes-feishu-card-v4.0.0-linux.tar.gz`
- `hermes-feishu-card-v4.0.0-windows.zip`
- `hermes-feishu-card-v4.0.0-checksums.txt`
