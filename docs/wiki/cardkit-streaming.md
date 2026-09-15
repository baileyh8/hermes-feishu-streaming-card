# CardKit 流式更新

`card.streaming_mode: true` 使用飞书 CardKit v1 创建卡片实体，通过原有 IM reply/create
路径发送实体引用，再用组件接口更新累计全文。默认关闭，普通卡片与 legacy 交互卡保持原有接口。

```yaml
card:
  streaming_mode: true
```

启用前，为机器人应用开通 `cardkit:card:write`（创建与更新卡片）并发布权限变更。
原有消息发送权限仍然需要。权限不足会报告发送失败，不在投递结果不明时额外发送第二张卡。
官方契约：[创建卡片实体](https://open.feishu.cn/document/cardkit-v1/card/create)、
[流式更新文本](https://open.feishu.cn/document/cardkit-v1/card-element/content)、
[流式更新说明](https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview)。

每张实体的操作串行执行并使用严格递增的 sequence，最多约 8 次/秒；组件内容传累计全文。
布局变化时更新整个实体。footer 与摘要定期刷新，终局强制刷新完整内容。
结束、失败或转入交互卡时先关闭 streaming_mode，再提交完整卡片；超过九分钟也关闭流式模式，
后续内容通过实体全量更新，避免继续调用已经关闭的文本流式接口。

投递 UUID 与目标共同绑定实体。相同投递重试时即使加载动画已变化，也复用原实体，
避免 IM UUID 返回原消息后却更新一张不可见的新实体。状态保存在 sidecar 进程内；
不将进程重启后的旧卡恢复描述为已支持的能力。

自动化覆盖真实本地 HTTP 请求、鉴权、话题 reply 参数、累计文本、终局收尾、并发序号、
失败重试、超时关闭、长组件 ID 与空文本的全量更新。它不代替真实飞书客户端的视觉验收。
