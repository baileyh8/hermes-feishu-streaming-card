# V4.0.16

V4.0.16 是 V4.0.15 工具时间线的体验与兼容性热修：去除初始加载文案重复，并让真实 Hermes 工具耗时稳定进入飞书卡片。

## 修复内容

- 初始加载时，Header 只显示 `Hermes Agent`；正文继续显示带动画的“正在加载上下文…”。
- 工具开始后，Header subtitle 显示“正在执行终端…”或“正在搜索…”等当前动作；模型正文尚未出现时，不再保留加载占位。
- Hermes progress callback 的真实耗时位于 `kwargs.duration`。hook 现在会提取该字段并转换为 `duration_ms`，时间线显示为 `✓ web_search · 1.75s`。
- 完成事件只携带耗时时，started 事件里的查询摘要与参数会保留，不再被单独的耗时行覆盖。

## 可靠性边界

- 优先使用 Hermes 明确提供的 `kwargs.duration` / `duration_ms`。
- 上游未提供耗时时，sidecar 使用同一 `tool_id` 的 started/completed 事件时间差兜底。
- terminal-only 兼容事件没有可信开始时间时不伪造耗时。
- Hermes 当前 progress callback 仍以工具名作为关联键；顺序同名调用可正确复用，严格并行的同名调用仍属于上游关联能力边界。

## 验证

- 真实 Hermes callback 结构 smoke：完成事件的 `kwargs.duration=1.75` 渲染为 `✓ web_search · 1.75s`，查询摘要和参数保留，工具开始后的空正文不包含加载占位。
- 完整自动化通过：`1504 passed, 4 skipped`；`git diff --check` 通过。
- sdist/wheel、隔离 `site-packages` 导入、公开 tagged installer 与本机运行来源在发布流程中复核。

## Release assets

- `hermes-feishu-card-v4.0.16-macos.tar.gz`
- `hermes-feishu-card-v4.0.16-linux.tar.gz`
- `hermes-feishu-card-v4.0.16-windows.zip`
- `hermes-feishu-card-v4.0.16-checksums.txt`
