# 架构说明

当前主线是 sidecar-only 架构。Hermes Agent 内只保留最小 hook，把消息生命周期事件转发到本机 HTTP sidecar；飞书卡片创建、更新、最终态渲染和状态累积都在 `hermes_feishu_card/` 内完成。

## 组件

### 最小 Hermes hook

安装器只修改 Hermes 的 `gateway/run.py`，插入受标记包围的事件转发代码。hook 的职责是把 Hermes 运行时产生的事件发送到 sidecar，并在 sidecar 不可用时让 Hermes 回到原生文本回复路径。

### HTTP sidecar

`hermes_feishu_card.server` 提供本机 HTTP 接口，接收 Hermes hook 发送的事件。sidecar 独立于 Hermes 进程运行；卡片故障不应拖垮 Agent 主流程。

### 会话状态

`hermes_feishu_card.session` 维护每个会话的流式状态，包括思考文本、答案文本、工具调用次数、消息是否完成以及错误状态。事件按会话聚合后再交给渲染层生成卡片内容。

### Feishu client

`hermes_feishu_card.feishu_client` 封装飞书/Lark CardKit 调用。凭据来自本机配置或环境变量，不进入仓库。sidecar 通过 client 创建卡片、增量更新卡片，并在消息完成时写入最终答案。

## 旧代码边界

`adapter/`、`sidecar/`、`patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等目录或脚本是历史 legacy/dual/patch 实现，不是 active runtime。新主线只以 `hermes_feishu_card/`、当前 CLI 和当前安装器安全模型为准。
