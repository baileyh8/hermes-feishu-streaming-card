# 架构说明

目标架构是 sidecar-only：Hermes Agent 内只保留最小 hook，把消息生命周期事件转发到本机 HTTP sidecar；飞书卡片创建、更新、最终态渲染和状态累积都在 `hermes_feishu_card/` 内完成。

第一阶段已实现边界更窄：安装器、备份/恢复/卸载闭环、事件协议、sidecar HTTP 接口、渲染和会话状态骨架已经落地。当前 patcher 写入的 hook block 是带校验的安全占位，还不是已验证的真实 Hermes 事件转发链路；真实 Feishu CardKit 发送和更新也尚未完成联调。

## 组件

### 最小 Hermes hook

安装器只修改 Hermes 的 `gateway/run.py`，插入受标记包围的 hook block。目标职责是把 Hermes 运行时产生的事件发送到 sidecar，并在 sidecar 不可用时让 Hermes 回到原生文本回复路径；第一阶段的 hook block 仍是安全占位，真实运行时事件转发验证在 TODO 中继续推进。

### HTTP sidecar

`hermes_feishu_card.server` 提供本机 HTTP 接口，接收 Hermes hook 发送的事件。sidecar 独立于 Hermes 进程运行；卡片故障不应拖垮 Agent 主流程。

### 会话状态

`hermes_feishu_card.session` 维护每个会话的流式状态，包括思考文本、答案文本、工具调用次数、消息是否完成以及错误状态。事件按会话聚合后再交给渲染层生成卡片内容。

### Feishu client

`hermes_feishu_card.feishu_client` 定义飞书/Lark CardKit 调用边界。凭据来自本机配置或环境变量，不进入仓库。sidecar 目标上会通过 client 创建卡片、增量更新卡片，并在消息完成时写入最终答案；真实发送和更新联调还未作为第一阶段完成项。

## 旧代码边界

`adapter/`、`sidecar/`、`patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等目录或脚本是历史 legacy/dual/patch 实现，不是 active runtime。新主线只以 `hermes_feishu_card/`、当前 CLI 和当前安装器安全模型为准。
