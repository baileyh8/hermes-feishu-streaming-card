# V4.0.13

V4.0.13 把 Hermes 所有带非空文本反馈的 slash command 统一收进飞书/Lark 命令卡片：不再只覆盖固定命令列表，built-in、alias、plugin/quick 和 unknown command 都自动适用。

## 全命令反馈卡片

- 任意已通过 Hermes 准入的 Feishu/Lark slash command 都会建立 task-local command context，不再维护固定 allowlist。
- 第一条反馈创建一张 interactive card；同一命令后续反馈在上下文锁内串行 PATCH 同一卡，避免多卡和并发重复 create。
- 命令名优先复用 Hermes `resolve_command(...)` 规范化 alias；无法解析时安全保留原命令名，plugin/quick/unknown command 无需 HFC 预注册。
- 长 Markdown 复用普通卡片的结构化 splitter，topic 的 reply anchor 和 `thread_id` 保持不变。

## 手动 `/compress`

- 手动 `/compress`（含 `/compact` alias）先创建蓝色“正在压缩上下文”卡，再且只调用一次 Hermes original handler。
- 成功统计、no-op 和 aborted/warning 都使用 Hermes 原始返回文本原位更新同一卡，不自行改写 messages/tokens 数据。
- 起始卡创建失败时返回 original handler 结果；终态 PATCH 失败时也返回原始结果，让 Hermes 原生文本可见。

## 兼容与 fail-open

- `/model`、裸 `/resume`、destructive confirmation 和 `/hfc` 继续使用既有专用交互卡，不产生第二张通用命令卡。
- Agent turn 继续使用普通 streaming card；真实媒体/文件继续走 Hermes 原生投递。
- `/update` 仍是 Hermes 后台升级命令：重启前反馈进入命令卡，重启后状态继续由独立 `system.notice` 承载。
- 只有 card create/PATCH 明确成功才抑制对应灰色文本；失败逐条把完全未修改的 Hermes feedback 交回 original Feishu adapter。

## 验证边界

- 自动化覆盖 built-in、alias、plugin/quick、unknown command、空文本/过期/跨 chat 上下文、同卡多反馈、并发只 create 一次、长 Markdown、create/PATCH 失败回退，以及 `/compress` 成功/no-op/aborted/异常/非飞书分支。
- 发布门禁为 `1482 passed, 4 skipped`，并通过 `git diff --check`。
- sdist/wheel 与隔离 Python 3.12 安装/import/CLI smoke 在发布前完成。
- 本版本未执行真实 Feishu 客户端命令矩阵，不把桌面/移动端视觉或真实故障注入写成已通过；自动化与 SDK 兼容测试覆盖协议分支。

## Release assets

- `hermes-feishu-card-v4.0.13-macos.tar.gz`
- `hermes-feishu-card-v4.0.13-linux.tar.gz`
- `hermes-feishu-card-v4.0.13-windows.zip`
- `hermes-feishu-card-v4.0.13-checksums.txt`
