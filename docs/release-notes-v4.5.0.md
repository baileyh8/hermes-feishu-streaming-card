# V4.5.0 — 话题交互、CardKit 流式更新与审批暂停

- #275/#278：识别默认投递位置提示与中文“上下文压缩已推迟”，沿用当前入站消息的话题锚点；私有通知继续遵守 Hermes 的私有投递设置。
- #258/#282：长问题完整展示在正文；选择已记录后不再等待慢速飞书 PATCH 才响应回调，Gateway 回调网络预算缩至两秒。
- #262/#279：正文中的已知提问者 `@姓名` 转为真实提及，代码、链接与未知用户保持原文；`completion_notify.placement: card` 可将提及放在答案前，默认 `message` 保留独立通知。
- #274：完成状态使用“本轮回复结束”，不把模型响应结束表达成业务目标已完成。模型是否继续调用工具仍由 Hermes/provider 决定。
- #293：新增 `card.streaming_mode: true`，使用 CardKit 实体及累计文本更新，包含并发顺序、限速、投递重试复用与结束收尾。需要应用开通 `cardkit:card:write`；默认关闭。
- #295：存活的同步 Gateway 审批回调到期后暂停等待，不自动以拒绝继续任务。旧按钮撤销；“查看并继续审批”重新展示完整范围，新的明确选择才恢复原调用。此功能不恢复重启后的执行栈，不延长 native admission 的授权证明。
- #73/#263/#264/#265/#266/#277：增加报告所用 Hermes 0.17、单文件 0.21 与当前拆分源码的固定 SHA256 安装、迁移及恢复回归，并增加真实容器 UID 对照。

升级请使用官方安装器并重启 Gateway 以加载新 hook。容器安装身份、配置与 state directory 的处理见 [s6 启动说明](wiki/docker-s6-startup.md)。CardKit 配置见 [流式更新说明](wiki/cardkit-streaming.md)，审批和提及详见 [卡片阅读方式](wiki/card-readability.md)。

发布门禁包括完整 pytest、跨平台 CI、精确合并提交、annotated tag、资产 checksum 和公开 tag 普通安装。真实飞书客户端验收与自动化结果分别记录，不把模拟 HTTP 等同于真实客户端通过。
