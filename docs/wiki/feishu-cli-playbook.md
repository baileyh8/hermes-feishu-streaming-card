# 飞书 CLI 验收与诊断

`lark-cli` 是 HFC 的可选验收/诊断工具，不是 sidecar 运行时依赖。所有本机命令使用 `LARK_CLI_NO_PROXY=1`，避免本机代理影响飞书 OpenAPI 和事件长连接。

## 前检

```bash
LARK_CLI_NO_PROXY=1 lark-cli doctor
LARK_CLI_NO_PROXY=1 lark-cli auth status
LARK_CLI_NO_PROXY=1 lark-cli auth check --scope 'im:message im:message:readonly im:chat:read im:chat.members:read'
LARK_CLI_NO_PROXY=1 lark-cli whoami
```

user 与 bot 是两套身份和 scope。user 前检通过不代表 bot 已申请或发布同一组权限；CLI 授权也不能证明 Hermes 应用拥有相同 scope。某个 bot 只读命令返回 `app_scope_not_applied` 时，记录缺失 scope 名称并改用已授权的 user 身份或飞书客户端完成验收，不要修改 HFC 运行配置来迁就 CLI。

## 真实卡片

- `im +chat-search`：以 user 身份定位测试群，结果只用于本轮 shell 环境。
- `im +chat-members-list`：确认测试用户和 Hermes bot 在群内；bot scope 不足时可改用 user 身份。
- `event consume card.action.trigger`：旁路观察一次按钮或下拉回调，核对 action tag/value 和 operator/message/chat 路由字段。
- `im +messages-mget`：卡片更新到错误会话时核对消息。
- `im +threads-messages-list`：卡片更新到错误位置时核对 topic/thread 锚点。

示例：

```bash
export HFC_TEST_CHAT_ID='仅在当前 shell 中设置'
LARK_CLI_NO_PROXY=1 lark-cli im +chat-members-list \
  --chat-id "$HFC_TEST_CHAT_ID" --as user
LARK_CLI_NO_PROXY=1 lark-cli event consume card.action.trigger \
  --as bot --max-events 1 --timeout 60s
```

使用不熟悉的 endpoint 前先运行 `lark-cli schema` 查看真实参数，不猜 payload 字段。

## 安全边界

- 禁止把 token、callback token、chat/open/message id 或原始事件输出提交到 Git。
- 不把真实群 ID 写入脚本、文档、截图文件名、issue 或 release notes；只放在临时环境变量。
- 原始 `card.action.trigger` 可能包含 callback token 和用户标识，只在终端即时核对，不重定向到文件或日志。
- CLI 结果只能证明 CLI 应用身份的能力。Hermes 应用与 HFC sidecar 仍通过各自的 `doctor`、`/health` 和真实卡片行为验收。
