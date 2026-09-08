# Hermes Feishu Streaming Card V4.4.3

V4.4.3 修复真实本机 Hermes 0.21.0 升级时发现的旧 hook 迁移与本机源码定制完整性问题，并按 Issue #269 隐藏没有内容的思考/工具 timeline。

## 修复

- 显式 `--accept-hermes-upgrade` 现在可以识别 manifest/backup 可验证、但仍携带旧版 HFC primary marker block 的 Hermes 源码。安装器先验证旧 ownership，再用限定的 lenient remover 清除 HFC 自有块；未知块外改动仍拒绝覆盖。
- `integrity migrate-safe --yes` 在 Git checkout 含本机源码定制时，可把健康且逐字可逆的 hook 绑定为 `verified_owned_snapshot`。该证据只证明当前安装，不允许自动接受后续 Hermes 升级。
- 当 reasoning/tool/subagent/notice timeline 没有任何条目时，不再显示“思考与工具 · 0 次工具调用”空面板，也不显示重复的“工具调用 0 次”摘要。出现第一条真实记录后 timeline 保持原行为。
- 固定兼容门禁增加本机生产 Hermes 0.21.0 提交 `180291162ff4df0d42b5dc4fecd08005cf7cebf9` 的源码摘要。

## 生产验证

- 候选 wheel 由实际 Gateway venv 加载；Hermes hook recovery 为 `installed`，完整性迁移后 sidecar 为 `healthy / runtime_ready / integrity=safe`。
- 真实飞书私聊 smoke 成功；随后实际 Hermes 入站会话的事件全部进入并应用到 sidecar，发送计数没有失败。
- 生产 Gateway 只有 `default` profile，因此不把本轮结果写成真实 multiplex 验收。双 named bot/profile/topic 的 Hermes handler 回归证明 profile、conversation 与 thread identity 不会在 HFC 内降级到 default；Issue #268 仍等待报告者环境复测。

## 贡献者

- [mouyong](https://github.com/mouyong)：[#268](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/268) 的 multiplex 生产报告与 [#269](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/269) 的空 timeline 建议。
