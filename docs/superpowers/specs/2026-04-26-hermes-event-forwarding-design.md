# Hermes 事件转发第二阶段设计

## 背景

第一阶段已经完成 `hermes_feishu_card/` sidecar-only 主线、事件协议、sidecar HTTP 服务、会话状态、卡片渲染骨架，以及安全安装/恢复/卸载闭环。当前安装器写入 Hermes `gateway/run.py` 的 hook block 仍是 `try/pass` 安全占位，不会向 sidecar 发送真实事件。

第二阶段选择 **真实 Hermes 事件转发优先**。本阶段只打通 Hermes hook 到 sidecar `/events` 的本机 HTTP 事件链路，飞书 CardKit 真实发送/更新继续使用 fake client 或后续阶段实现。

## 目标

- 将 patcher 写入的 hook block 从安全占位升级为真实事件转发入口。
- 在不阻塞 Hermes 原生流程的前提下，把 Hermes 消息生命周期事件发送到本机 sidecar。
- 事件格式继续使用第一阶段的 `SidecarEvent` schema。
- sidecar 不可用、超时、异常、配置缺失时全部 fail-open：只放弃卡片流式更新，不影响 Hermes 原生文本回复。
- 使用 fixture 和本地 mock sidecar 测试转发行为，不依赖真实飞书机器人、不读取或提交真实 App Secret。

## 非目标

- 不实现真实 Feishu CardKit HTTP API。
- 不运行真实飞书机器人端到端联调。
- 不实现 sidecar 进程管理 `start/stop/status`。
- 不扩大 Hermes 原生代码侵入面；仍只通过安装器拥有的 marked hook block 接入。
- 不支持旧 `adapter/`、旧 `sidecar/`、dual mode 或 legacy installer 作为 active runtime。

## 设计概览

新增一个小型运行时模块，例如 `hermes_feishu_card/hook_runtime.py`。patcher 写入 Hermes 的 hook block 只负责：

1. 尝试导入 hook runtime。
2. 从当前 handler 的局部变量中提取 Hermes 上下文。
3. 调用 runtime 的非阻塞转发函数。
4. 捕获所有异常并吞掉，确保 Hermes 主流程继续。

hook block 本身保持短小、可审计、可恢复，不直接包含复杂 HTTP 逻辑。复杂逻辑放在本仓库模块中测试。

## 事件提取

由于 Hermes fixture 目前只保证 `_handle_message_with_agent` 和 `hooks.emit("agent:end", ...)` anchor，真实 Hermes 运行时变量可能随版本变化。本阶段采用保守提取策略：

- 从 handler `locals()` 中读取候选对象，而不是依赖单一固定参数签名。
- `chat_id` 从以下候选字段中按顺序提取：`chat_id`、`open_chat_id`、`receive_id`、`message.chat_id`、`message.open_chat_id`。
- `message_id` 从以下候选字段中按顺序提取：`message_id`、`msg_id`、`message.message_id`、`message.msg_id`；若缺失，使用稳定 fallback，例如 `conversation_id + created_at` 的短 hash。
- `conversation_id` 优先使用 Hermes 已有 conversation/thread 字段；缺失时退化为 `chat_id`。
- 文本增量从显式 delta/text 变量中提取；无法安全识别时不发送 delta。

提取不到必要字段时不发送事件，并记录可选 debug 信息；不能抛出异常影响 Hermes。

## 事件映射

本阶段 hook runtime 输出以下事件：

- `message.started`：在 handler 首次进入且能拿到 `chat_id` 时发送。
- `thinking.delta`：当能识别 Hermes 流式思考增量时发送，文本进入 `data.text`。
- `answer.delta`：当能识别 Hermes 最终答案流式增量时发送，文本进入 `data.text`。
- `tool.updated`：当能识别工具调用状态时发送，字段为 `tool_id`、`name`、`status`、`detail`。
- `message.completed`：在 handler 成功完成时发送，`data.answer` 包含可公开最终答案，`duration` 和 `tokens` 尽量填充。
- `message.failed`：在 handler 捕获到可见异常路径时发送，`data.error` 只包含可公开摘要。

如果真实 Hermes 当前版本无法稳定区分 thinking 与 answer，本阶段宁可只发送 `message.started`、`answer.delta`、`message.completed`，也不猜测内部思考内容。不得向 sidecar 暴露 `</think>` 标签；文本仍由 sidecar 的 normalizer 做第二道过滤。

## 非阻塞发送

runtime 使用标准库优先，避免把 Hermes hook 绑定到额外依赖：

- 在已有 event loop 中使用 `asyncio.create_task` 调度异步 POST。
- 在无法安全获取 loop 时，放弃发送并 fail-open。
- HTTP POST 超时默认 0.5 秒到 1 秒，可由环境变量覆盖。
- sidecar URL 默认 `http://127.0.0.1:8765/events`，可由环境变量覆盖。
- 任何网络错误、JSON 序列化错误、sidecar 非 2xx 响应都不向 Hermes 抛出。

如果使用标准库实现异步 HTTP 过于笨重，可以在 runtime 内把阻塞 `urllib.request` 放到 executor，但必须限制超时并保持异常吞掉。实现计划应优先选择最少依赖、最容易 fixture 测试的方案。

## 配置

本阶段只需要 hook runtime 读取环境变量：

- `HERMES_FEISHU_CARD_ENABLED`：默认启用；值为 `0`、`false`、`no` 时禁用。
- `HERMES_FEISHU_CARD_EVENT_URL`：默认 `http://127.0.0.1:8765/events`。
- `HERMES_FEISHU_CARD_TIMEOUT_MS`：默认 `800`，范围 50 到 5000。

不在 hook block 中读取飞书 App ID 或 App Secret。飞书凭据仍只属于 sidecar/FeishuClient 后续阶段。

## Patcher 变更

`hermes_feishu_card/install/patcher.py` 需要把 `_render_hook_block()` 输出从 `try/pass` 改为调用 runtime 的真实 hook：

```python
# HERMES_FEISHU_CARD_PATCH_BEGIN
try:
    from hermes_feishu_card.hook_runtime import emit_from_hermes_locals as _hfc_emit
    _hfc_emit(locals())
except Exception:
    pass
# HERMES_FEISHU_CARD_PATCH_END
```

具体函数名可以在实施计划中调整，但要求：

- block 仍完全由本插件拥有，`remove_patch()` 可精确恢复。
- 旧占位 block 不再被视为当前 expected block；restore 仍需能识别第一阶段已安装的占位 block，或通过一次迁移测试证明重复 install 可以安全升级。
- hook block 不包含 App Secret、网络细节或长逻辑。

## 测试策略

测试必须先覆盖 runtime，再覆盖 patcher，再覆盖安装后 fixture 行为。

核心测试：

- runtime 在 disabled 环境下不发送。
- runtime 缺少 `chat_id` 时不发送且不抛异常。
- runtime 能从 dict/object locals 中提取 `chat_id`、`message_id`、文本字段。
- runtime 对 mock sidecar 发送合法 `SidecarEvent` JSON。
- runtime 遇到 sidecar 断开、超时、HTTP 500 时不抛异常。
- patcher 写入真实 hook block，重复 apply 仍幂等，remove 仍恢复。
- 安装 fixture 后执行 handler，mock sidecar 至少收到 `message.started` 和一个完成类事件。
- 没安装 sidecar 或 sidecar 失败时，fixture handler 原返回值不变。

测试不得访问真实飞书，不得读取用户给过的 App Secret。

## 文档更新

README 和 docs 需要从“第一阶段 hook 是安全占位”更新为：

- 第二阶段实现了 Hermes 到 sidecar 的最小事件转发链路。
- Feishu CardKit 真实发送/更新仍未完成，卡片侧仍可通过 fake client 或 mock server 验证。
- 安装安全模型不变：备份、manifest、restore/uninstall、fail-closed。

## 风险与缓解

- **Hermes 局部变量不稳定**：使用候选字段和 fail-open，不猜测不能证明的字段。
- **事件重复或乱序**：sidecar 已有 sequence 和 session 幂等保护；runtime 需要为每条消息维护轻量 sequence。
- **网络慢影响 Hermes**：短超时、异步调度、吞异常。
- **hook block 变复杂**：hook block 只调用 runtime；复杂逻辑在仓库模块测试。
- **升级旧占位 hook**：计划中必须包含占位 block 到真实 block 的升级路径测试。

## 成功标准

- `python3 -m pytest -q` 全部通过。
- fixture 安装后，执行测试 handler 能向 mock sidecar 发送符合 schema 的事件。
- sidecar 不可用时，handler 不抛异常，原生返回值保持不变。
- restore/uninstall 仍能删除真实 hook block 并恢复原文件。
- 文档明确：真实 Hermes 事件转发已完成，真实 Feishu CardKit 联调仍是后续阶段。
