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
