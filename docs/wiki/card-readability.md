# 思考与审批卡片的阅读方式

`card.reasoning_format` 支持 `panel` 与 `code`，默认 `panel` 保留原来的折叠时间线。
需要直接阅读思考片段时可以配置：

```yaml
card:
  reasoning_format: code
  max_reasoning_chars: 1200
  timeline_expanded: false
```

`code` 把已记录的思考片段作为 fenced text block 放在卡片正文，工具记录仍保留在可折叠面板。
同一项可以配置在 profile 或 bot 的 `card` 映射中。`show_reasoning: false` 继续隐藏思考与工具时间线，
`max_reasoning_chars` 与 `max_timeline_items` 继续限制每次展示内容；卡片仍经过统一大小检查。

审批卡片展示完整命令和操作说明。命令使用经过转义的普通 Markdown，避免 fenced code 长行必须横向滚动，
并防止命令中的链接、标签和格式符号隐藏授权范围。文本交互模式的审批卡省略此前的回答和工具历史，
让完整授权范围与选择靠在一起；按钮回调模式原本就使用独立审批布局。

审批内容不再在第 3,000 个字符处静默截断。若转义后审批正文的 JSON 编码超过 12,000 UTF-8 字节，
hook 不发送交互卡并交还 Hermes 原生审批流程，给卡片头部、按钮和回调字段保留容量。
返回原生流程不是批准；不生成针对不完整命令的授权按钮。

自动测试覆盖内容尾部保留、Markdown 转义、超限回退、配置继承以及思考代码块与卡片大小限制。
手机和桌面端的实际换行、滚动行为仍需真实 Feishu/Lark 客户端复测。
