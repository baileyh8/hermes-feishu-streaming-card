# V4.7.0：任务卡片与阅读体验

**发布候选，待最终验收，尚未发布。** 最新稳定版仍为 V4.6.13。本页准备发行说明，不代表已通过全部客户端与发布门禁；实际记录见[本轮验收](reviews/2026-10-03-task-cards-acceptance.md)。

## 主要变化

- 新增可选 `card.reading_preset: task`。卡片集中显示状态和当前动作，正文优先，过程可折叠，页脚保留有依据的统计；统一正文、过程和统计的字号层级，减少重复标签。已有预设和显式配置保持有效。
- `card-config` 展示外观有效值及来源，并可生成九种状态的离线 HTML/Card JSON 预览。报告说明“下次加载”的配置，不冒充运行中已生效；预览使用内置样例，不访问聊天或发送飞书消息。
- 订阅周额度只在实际归属于 `openai-codex` 的 GPT 回合显示。其他模型、来源不明或模型切换后晚到的查询结果均隐藏。
- 保留完整工具参数、问题和授权范围，修复长详情裁剪后参数消失、暂停卡跨 JSON 方言以及任务结束后仍显示待操作入口的问题。只有完整独立回执确认送达后，才精简旧卡重复范围。
- Hermes 明确返回中断结果，或受控 `/stop` 确认取消原处理任务后，原任务卡收尾。显示投递不阻塞后续消息。辅助交互卡保存有界、无 token 的展示记录，供过期和重启后更新为失效回执；不会恢复执行、waiter 或旧授权。旧检查点缺少辅助卡身份时不猜测历史消息。
- task 模式在精确任务卡已接管后抑制重复的原生 Working 心跳；classic、未知身份或查询失败继续使用 Hermes 原行为。
- task 正文中可安全识别的代码块使用 `plain_text` 显示，保留原语言标识和完整代码，改善浅色下的语法高亮可读性；canonical 内容与 classic 原行为保留。分块与容量回退已通过定向回归；V16 的指定桌面显示与正文行复制通过，复制末尾换行未纳入实测。

60 秒没有新事件时，任务布局只做一次有界更新，说明“等待新进展、执行情况尚待确认”。展示恢复同样不代表任务仍在执行。

## 升级与启用

版本公开后，通过已有安装器更新，并对实际 Hermes 目录重新运行 `setup` 或 `install`，使包版本与受管 hook 一致。随后通过原服务管理入口重启 sidecar 和 Hermes Gateway。不要手改已安装的 Hermes 源码，也不要用示例覆盖已有配置。

新布局需要主动选择：

```yaml
card:
  reading_preset: task
```

同层显式字段继续优先；全局、profile 和 bot 的覆盖规则不变。切回 `classic`、`focused` 或 `detailed` 并重启 sidecar 即可回退布局，历史卡片不会因此重写。Python 最低版本仍为 3.9。本次发布不自动升级 Hermes 或迁移用户配置。

```bash
hermes-feishu-card card-config --config <配置路径>
hermes-feishu-card card-config --config <配置路径> --preview-dir ./card-preview
```

更多说明见[任务卡片](task-cards.md)与[阅读预设](wiki/reading-presets.md)。离线手机宽度和主题模拟不代替真实客户端验收。

## 验收进度

`455b788` 普通 wheel 在升级后的 Hermes `3d0a61ac` 上通过纯审批 STOP 复验；V14 又补齐旧答案保留、空续答 STOP、超过 180 秒 Working 去重和待审批实际重启。原 owner 与辅助审批卡按实际状态收尾，禁止执行标记为 0；旧授权没有恢复，Hermes 的新恢复答复未重试命令，也没有发出新审批。浅色、深色 100% 的纯审批终态没有重复通用中断正文，工具参数可展开。该源码本地完整回归为 **4,742 passed、22 skipped、0 failed**，947.66 秒；`f07c24f` 仅改文档，运行代码和安装来源相同。

V14 在约 414 逻辑像素的实际桌面聊天区完成选择、长等待、停止与重启检查；宽度按同屏基准和 2 倍 backing scale 校准，截图像素不能当作聊天区逻辑宽度或屏幕物理像素。V11 的两项多选加自定义、深色 125% 完成态及代码行尾实测可作为未变路径的版本绑定证据；旧 Hermes 的安装、重复安装、doctor、完整性迁移与逐字恢复整链也通过。早期失败及影响范围保留在[验收记录](reviews/2026-10-03-task-cards-acceptance.md)，纯文档提交不要求整套客户端重跑。

V15 改用 `d9a0033` 新投影包后，代码 body 与预期逐字一致，canonical 保留原语言 fence，但真实完成卡出现语言标识并入前方表格的布局失败。模型少了代码块外的空行，暴露 renderer 未建立 caption 前置块边界的问题；当时的新包仍须复验 caption 与复制，结果见下方 V16。该提交全量回归主动中断，已完成 **547 passed、1 skipped**，不能记为完整通过；其 CI 在本阶段仍运行，后续源码须核对自己的检查。

V16 的 `b3d1f04` 普通 wheel 在真实“表格后直接接 fence、无空行”输入下关闭 caption 入表问题。DB、canonical 与预期 Markdown 的 421 字符逐字相同；浅色 100% 与深色 125% 的窄区及常宽画面通过独立视觉复核，表格与代码末端实际可达。真实复制到未发送输入框的三行正文一致，没有语言标签、fence 或行号；末尾 LF 未选中，只证明正文行一致。飞书原外观与日常 Hermes 已恢复，候选进程退出、测试端口关闭；当前提交完整回归 **4,854 passed、22 skipped、0 failed**，927.32 秒；前后源码与普通 wheel 一致，GitHub 检查 14/14 通过。pytest/preflight exit 0；收尾脚本因临时文件尚有打开引用返回 exit 4，占用确认是 Spotlight，待其自然释放后临时树已回收，原异常和补验分别保留。

**仍未发布。** V14B 自然暂停后 STOP 正常，但未触发超过 5 秒的取消分支；该真实分支及手机仍未验证，发布范围没有获准缩为桌面。`b3d1f04` 的 14 项 GitHub 检查全部通过；最终合并 SHA 的回归与 CI、annotated tag、资产/checksums 与公开安装来源仍须分别核对。V16 收尾已恢复日常 Hermes 和飞书原外观，生产 HEAD 与四份基线文件保持匹配；退役候选源码和轻量 venv 已回收，删除前分配占用约 280.4 MiB；保留共享测试环境和必要证据。

## 贡献记录

本轮代码提交作者为 [baileyh8](https://github.com/baileyh8)。阅读体验继续回应 [jackwude](https://github.com/jackwude) 的 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 和 [leavrcn](https://github.com/leavrcn) 的 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333)；这些署名对应需求与现场证据。保留全部历史代码作者、方案和问题报告记录，不把未合并的其他 PR 计入本版。
