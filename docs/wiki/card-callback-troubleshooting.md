# 按钮显示但点击无响应

先记录 HFC、Hermes、Gateway Python 与 `lark-oapi` 版本、连接方式和点击时间，日志必须脱敏。消息能收到不等于卡片回调能收到。

1. 飞书应用应订阅 `card.action.trigger` 回调，确认正在运行的 app 与配置一致，避免多台实例使用同一应用分流事件。
2. HFC 会替换 Hermes 的卡片回调。只在原 `_on_card_action_trigger` 中加日志，不能证明 WebSocket 没收到帧；应检查 SDK 帧分发、实际 processor 和 HFC 回调三层。不要开启会输出完整消息/凭据的 SDK DEBUG 日志。
3. 已核验的 `lark-oapi==1.6.8` 把 `CARD` 帧直接丢弃。V4.7.2 在该实现的语义哈希匹配时，仅修复当前 WS 实例。日志 `Feishu SDK CARD-frame dispatch repaired for this WebSocket client` 证明修复安装，不证明一次真实点击完成。
4. 升级 HFC 后重新 setup/install，按原 owner 重启 Gateway 与 sidecar，重新发 `/new`，点击新卡。旧审批过期后不能恢复授权。若仍失败，提供版本、点击时间与脱敏 HFC 日志，不要提供 token、App Secret 或原始用户/群标识。
5. 若原始帧已到达但回调未执行，检查 SDK 分发；若 HFC 已收到，检查原请求是否仍有效、操作者是否被 Hermes 接纳；若确认已完成而原卡未更新，检查消息 ID 与 PATCH 返回。不得为了恢复显示跳过原生权限检查。

SDK 原始缺陷见 [官方 SDK issue #126](https://github.com/larksuite/oapi-sdk-python/issues/126)。未知或已经修复的 SDK 实现保持原样。个人版、Termux 和移动客户端支持情况必须以该环境真实点击结果为准，不能用本地合成帧测试代替。

English: HFC replaces the original Hermes callback, so missing logs there do not prove transport non-delivery. V4.7.2 repairs only the verified SDK CARD-drop implementation on the live client. Upgrade and restart through existing owners, send a fresh confirmation, and distinguish frame arrival, dispatcher execution, authorization and result-card PATCH. Real Personal Edition / mobile acceptance remains a separate check.
