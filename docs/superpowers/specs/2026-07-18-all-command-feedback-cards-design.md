# Hermes 全命令反馈卡片化设计

## 目标

所有从 Feishu/Lark 进入 Hermes Gateway 的 slash command，只要产生用户可见的文本反馈，就由 HFC 卡片承载。卡片成功接管后不再发送原生灰色文本；卡片创建或更新失败时，必须把 Hermes 原始反馈交回原生发送路径。

本设计不是逐命令增加特例。Hermes 新增命令、alias、plugin command、用户 quick command 或 unknown-command 提示，只要仍通过 Gateway 的正常命令反馈发送边界，就自动获得卡片承载。

## 当前状态

`hook_runtime.install_feishu_command_card_adapter_methods(...)` 已在每个 Gateway message task 开始时包装 Feishu adapter 的 `send`，但 `_HFC_COMMAND_RESULT_CARD_COMMANDS` 只允许：

- `/new`
- `/reset`
- `/clear`
- `/undo`
- `/stop`
- `/model`

Hermes 当前 Gateway registry 还包含 `/compress`、`/status`、`/usage`、`/reasoning`、`/update`、`/resume`、`/sessions`、`/branch` 等大量命令和 alias。它们返回的反馈仍会走原生文本。

## 方案比较

### A. Adapter 边界的统一命令反馈生命周期（采用）

在现有 `send` wrapper 上扩展：从 Feishu inbound event 建立命令上下文，首次反馈创建命令卡，后续反馈更新同一张卡。现有交互卡和 Agent streaming card 继续走各自路径。

优点：覆盖 handler 返回值、handler 内部直接 `adapter.send(...)`、alias、quick command、plugin command 和 unknown-command；不依赖 Hermes 每个 handler 的函数名。

### B. 在 Gateway dispatcher 捕获 handler 返回值（不采用）

只能捕获 `return await self._handle_*_command(...)`，会漏掉 `/learn`、`/blueprint`、后台 update 等 handler 内部直接发送或异步产生的反馈；同时需要侵入 Hermes 的中央分发结构。

### C. 逐命令适配（不采用）

行为最精确，但维护面随 Hermes 命令数量线性增长，新命令默认漏卡，违背“所有命令反馈”的目标。

## 命令范围

### 自动纳入

- Hermes registry 中的 built-in command 和 alias。
- plugin 注册命令。
- 用户 quick command。
- unknown slash command 的用户提示。
- access hook 的 deny/handled 反馈。
- `/update` 在重启前产生的命令反馈。

判断依据是“Feishu inbound event 是 slash command 且 Hermes 发出非空用户可见文本”，不维护固定 allowlist。

### 保持专用路径

- `/model`、裸 `/resume` 和 destructive slash confirmation 继续优先使用现有交互卡；成功时不再创建第二张结果卡，专用路径失败回退的文本才进入统一命令卡。
- `/hfc help/status/doctor/monitor` 继续使用 sidecar 运维卡；只有该路径 fail-open 返回文本时才进入统一命令卡。
- `/learn`、`/blueprint`、`/steer`、`/queue`、`/moa` 等会转入 Agent turn 的命令，只把即时确认、usage 或错误视为命令反馈；Agent 的 reasoning/answer 继续由普通流式卡承载。
- 文件、图片、音频等附件仍走 Hermes 原生媒体发送；与附件伴随的文本反馈可以卡片化。
- `/start` 等返回空字符串的无反馈命令不创建空卡。

## 数据与生命周期

### 命令上下文

每个 inbound message task 用 `ContextVar` 保存一个可变且有期限的命令上下文：

```text
raw_command
canonical_command
chat_id
reply_to_message_id
thread_id
card_message_id
created_at / expires_at
async lock
```

canonical command 优先调用 Hermes `resolve_command(...)`；无法解析时保留规范化 raw command，从而覆盖 quick/plugin/unknown command。

上下文只在 Feishu/Lark slash event 中建立，并校验 chat。新 task 不共享 ContextVar 值；由命令派生的异步 task 可以沿用同一上下文，因此同一命令的连续反馈能原位更新。上下文过期后 fail-open，避免旧 background task 污染后续消息。

### 首次反馈

1. 使用原 user message 或 topic anchor reply 创建 interactive card。
2. Header 默认显示 `/<canonical-command>`；对常用命令保留更清晰的语义标题。
3. 内容按现有 Markdown block splitter 切成多个元素，避免 `/help`、`/commands`、`/debug` 等长反馈挤入单个 element。
4. Feishu 返回成功后记录 `card_message_id` 并抑制原生文本。
5. 创建失败则调用 original adapter `send` 发送完全相同的 Hermes 反馈。

### 后续反馈

1. 以同一 `card_message_id` PATCH 更新卡片。
2. 用 context-local `asyncio.Lock` 串行 create/update，避免并发反馈创建多张卡。
3. PATCH 成功后抑制对应原生文本。
4. PATCH 失败时把本次原始反馈交回 native send；不伪报成功。

### `/compress` 开始态

`install_feishu_command_card_adapter_methods(...)` 运行时包装 `GatewayRunner._handle_compress_command`，不增加新的 Hermes patch block：

1. 仅 Feishu/Lark `/compress` 或其 canonical alias 进入包装。
2. 调用 original handler 前创建“正在压缩上下文”命令卡。
3. original handler 返回后，用完整 Hermes 结果更新同一卡，包括成功、no-op、aux model fallback 和 aborted warning。
4. 终态 PATCH 成功则返回 `None`，避免 Base adapter 再发灰色回复。
5. 开始卡创建失败或终态 PATCH 失败则返回 original result，让正常 adapter 路径继续尝试统一卡片并最终 fail-open 到原生文本。
6. wrapper 必须幂等，并始终保留 original Hermes handler；异常不改变 Hermes 压缩语义。

## 卡片表现

- 运行态：蓝色 Header，内容以 `⏳` 开始。
- 成功态：绿色 Header。
- warning/cancel/no-op：橙色 Header。
- error/failed/失败：红色 Header。
- 标题不展示内部 `hfc_*` 标识，不输出 config、token、secret 或真实路由诊断。
- topic/thread 的 reply anchor 和 metadata 原样保留。

## Fail-open 与兼容性

- 只有 Feishu create/PATCH 明确成功才抑制该条原生反馈。
- 无 client、SDK helper 不兼容、异常、失败 response、过期 context 或 chat mismatch 都回到 original adapter `send`。
- 非 Feishu adapter、普通聊天、Agent streaming、system.notice、媒体投递和 legacy archive 行为不变。
- runtime wrapper 不替换已连接 Lark WebSocket handler identity。
- installed Hermes `gateway/run.py` 仍只由现有 patcher 管理；本功能不手工修改它。

## 测试与验收

### Unit

- built-in、alias、plugin/quick/unknown slash 都建立 command context；普通文本和非 Feishu 不建立。
- `/update` 不再被排除。
- 首次反馈创建卡，第二/第三次反馈 PATCH 同一卡。
- create/PATCH 失败逐条回退原始文本。
- chat mismatch、空反馈、过期 context 不接管。
- 并发反馈只创建一张卡。
- 长 Markdown 拆分后内容顺序与文本完整。
- `/compress` 先创建运行卡，再以 original handler 的完整结果更新；失败时返回原文。
- `/model`、`/resume`、confirmation、`/hfc` 专用路径成功时无重复卡。

### Integration / patch compatibility

- command adapter startup/event hook install、remove、restore、idempotence 保持通过。
- Feishu SDK compatibility 保持 live event handler identity 不变。
- topic reply anchor 与 direct-message reply 均正确。
- 聚焦矩阵：`test_hook_runtime.py`、`test_patcher.py`、`test_feishu_sdk_compat.py`、`test_cli_install.py`。
- 最终运行全量 `python -m pytest -q` 和 `git diff --check`。

### 真实 Feishu 后续验收

- `/compress`：运行卡原位更新为压缩统计，无灰色文本。
- `/status`、`/usage`、`/commands`：各一张结果卡，无灰色文本。
- `/model`、`/resume`：仍使用原交互卡，不出现第二张结果卡。
- `/update`：重启前反馈为卡片；重启后通知继续由现有 system.notice 卡承载。
- 受控卡片失败：Hermes 原始文本仍可见。
