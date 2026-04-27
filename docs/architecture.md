# 架构说明

目标架构是 sidecar-only：Hermes Agent 内只保留最小 hook，把消息生命周期事件转发到本机 HTTP sidecar；飞书卡片创建、更新、最终态渲染和状态累积都在 `hermes_feishu_card/` 内完成。

第二阶段已实现最小事件转发：安装器、备份/恢复/卸载闭环、事件协议、sidecar HTTP 接口、渲染、会话状态骨架，以及 Hermes hook 到 sidecar `/events` 的 fail-open 转发链路已经落地。Feishu CardKit HTTP client 已通过 mock server 验证，真实飞书应用联调仍未完成。

## 组件

### 最小 Hermes hook

安装器只修改 Hermes 的 `gateway/run.py`，插入受标记包围的 hook block。第二阶段 hook block 已升级为真实 runtime 调用，复杂提取和发送逻辑在 `hermes_feishu_card.hook_runtime` 中测试。hook 仍保持 fail-open，不直接包含飞书凭据或长逻辑。

### HTTP sidecar

`hermes_feishu_card.server` 提供本机 HTTP 接口，接收 Hermes hook 发送的事件。sidecar 独立于 Hermes 进程运行；卡片故障不应拖垮 Agent 主流程。

`hermes_feishu_card.cli start/status/stop` 管理本机 sidecar 进程。进程状态保存在用户态 pidfile 中，`status` 以 `/health` 作为真实探活来源，`stop` 只有在 pidfile 的 PID/token 与 `/health` 返回的 process_pid/process_token 同时匹配时才停止进程，避免陈旧 pidfile 或 PID 复用误杀无关进程。当前进程管理面向 macOS/Linux 这类 POSIX 环境。未配置飞书凭据时，CLI runner 使用 no-op client 接收事件并维护会话状态；配置凭据后，runner 使用真实 Feishu HTTP client。

`/health` 还暴露 process-local `metrics`，用于观察当前进程生命周期内的事件流和飞书交付结果。指标包括 `events_received`、`events_applied`、`events_ignored`、`events_rejected`、`feishu_send_successes`、`feishu_update_successes`、`feishu_update_failures` 和 `feishu_update_retries` 等；CLI `status` 会同步打印这些指标。为避免重复创建飞书卡片，`send_card` 不自动重试；`update_card_message` 针对已有 message_id 做一次有限重试。

### 会话状态

`hermes_feishu_card.session` 维护每个会话的流式状态，包括思考文本、答案文本、工具调用次数、消息是否完成以及错误状态。事件按会话聚合后再交给渲染层生成卡片内容。

### Feishu client

`hermes_feishu_card.feishu_client` 定义飞书/Lark CardKit 调用边界。凭据来自本机配置或环境变量，不进入仓库。client 会获取 tenant access token，调用发送消息接口创建 interactive card，并通过消息更新接口增量更新卡片内容；真实飞书应用联调仍是后续阶段。

## 旧代码边界

`adapter/`、`sidecar/`、`patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等目录或脚本是历史 legacy/dual/patch 实现，不是 active runtime。新主线只以 `hermes_feishu_card/`、当前 CLI 和当前安装器安全模型为准。
