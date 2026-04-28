# 端到端可视化验证材料

本项目当前提供可重复生成的本地验证材料，用于检查 sidecar-only 主线的流式卡片渲染结果。

## 已生成材料

- [`docs/assets/e2e-card-preview.svg`](assets/e2e-card-preview.svg)：思考中和已完成两种卡片状态的可视化预览。
- [`docs/assets/e2e-card-preview.json`](assets/e2e-card-preview.json)：由真实 `CardSession`、`SidecarEvent` 和 `render_card()` 生成的 Feishu CardKit JSON。

预览覆盖：

- `思考中` 和 `已完成` 两个正常状态。
- thinking 内容累积显示，并过滤 `<think>` / `</think>` 标签。
- 工具调用实时计数，示例为 `工具调用 2 次`。
- 完成后思考内容被最终答案覆盖，同时保留工具调用摘要和耗时/token 统计。

## 重新生成

```bash
python3 tools/generate_e2e_preview.py --output-dir docs/assets
```

生成器只使用本仓库代码和标准库，不访问真实飞书、不读取 App Secret，也不会发送网络请求。

## 真实飞书 smoke

真实飞书应用验证仍使用：

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

不要把 App Secret、tenant token、真实 chat_id 或真实截图中的敏感聊天内容提交到仓库。

## 已完成真实验收

当前主线已在真实 Hermes Gateway + 真实 Feishu 测试应用中完成以下验收：

- 新消息创建新卡片，不复用上一张未关闭卡片。
- `thinking.delta`、`answer.delta`、`tool.updated` 和 `message.completed` 能进入 sidecar 生命周期。
- 答案内容流式更新在卡片内；sidecar 接受完成事件后，Hermes 不再额外发送灰色原生文本。
- 工具调用在卡片中显示实时计数，完成后保留总次数。
- footer 显示耗时、模型、输入/输出 token 和上下文使用量，异常累计 token 会被过滤。
- 真实长卡压力测试中，同一张飞书卡片更新到 16k 中文字符成功。
- 安装器在真实 Hermes `v2026.4.23` 目录完成 `restore -> install` 循环验证，最终保持已安装状态。

最近一次全量自动化回归：`348 passed`。
