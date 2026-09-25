# V4.7.0：CardKit 重启恢复与 230011 死循环修复

- CardKit 实体重启恢复：sidecar 重启后内存中的 entity 映射丢失，update 回退到 plain-JSON PATCH 被飞书拒绝（400/230011 "not a plain JSON card"）。现在从消息体中的 card 引用（`{"type":"card","data":{"card_id":...}}`）重建 entity，仅在 `schema == "2.0"` 的 CardKit 卡上触发，legacy plain-JSON 卡不付出额外 lookup 代价。
- 230011 死循环终止：飞书返回 230011 "The message was withdrawn" 时目标消息已被撤回，继续重试只会消耗更新预算。现在立即终止重试，调用方按终局失败清理 session 状态。
- Patcher 容忍 Hermes 0.21.x 新增的 `_release_turn_marker` if 块：该块是幂等的，不打乱 ledger 契约，升级时 AST 校验不再拒绝。
- 版本标记文件（`pyproject.toml`、`config.yaml.example`、`docker-compose.example.yml`、CI workflow、文档）全部对齐 4.7.0。

## 恢复边界

恢复仅重建 in-memory entity（card_id + 空 card），不恢复流式状态或执行栈。已撤回消息（230011）标记为 unrecoverable，后续 update 跳过 lookup。Entity 上限 128 条不变。

## 验证

4 项回归测试（`test_cardkit`、`test_feishu_client`、`test_package_metadata`、`test_docs`）全部通过；全量 unit suite 无新增失败（60 个预存环境失败与 HEAD 一致，另修复 1 个 `test_persistent_service` 预存失败）。

