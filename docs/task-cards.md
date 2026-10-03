# 任务卡片与离线预览（4.7.0 发布候选）

`task` 把卡片分为状态、正文、过程和统计四个阅读区域。它沿用已有投递与权限流程，适合希望先看答案、再查看执行细节的用户。4.7.0 候选正在最终验收，尚未发布；已发布的 V4.6.13 尚不包含本页新增选项。

## 启用与回退

在已经连接 Hermes/飞书的配置中选择预设：

```yaml
card:
  reading_preset: task
```

全局、profile 和 bot 均可设置，顺序仍是全局 → profile → bot。同层显式开关优先；已有 `text_sizes` 按角色保留。如果复制了完整示例配置，检查哪些旧显示开关覆盖了预设。保存后通过原服务入口重启 sidecar，随后核实配置来源。切回 `classic`、`focused` 或 `detailed` 并重启即可恢复对应布局；不会改写已结束的历史卡片。

## 卡片变化

| 场景 | 任务布局 |
| --- | --- |
| 开始与执行 | 顶部只说明任务状态；正文集中显示当前动作；参数和历史在实际包含该工具的“执行过程”中查看 |
| 没有新事件 | 超过 60 秒显示“等待新进展”，写明执行情况尚待确认；旧动作标为“上次动作” |
| 审批与选择 | 统一状态标题，完整问题留在正文，范围和原有操作保留；选择后显示回执 |
| 完成 | 省去重复副标题，正文优先；成功工具行默认收起，失败和中断证据保留 |
| 失败 | 明确显示“已停止”，保留已收到的答案及错误 |
| 展示恢复 | 显示“等待状态同步”，不会恢复旧授权或宣称任务已续跑 |
| 页脚 | 只保留有依据的耗时、模型和用量，不重复动作与工具数；无标题的完成回复保留唯一完成标识；非 Codex GPT 回合不显示订阅额度 |

任务布局默认字号为正文/通知 `normal`、思考/工具 `small`、页脚 `notation`。设备字号映射继续可用。卡片宽度和最终排版由飞书客户端控制，JSON 1.0 交互卡仍遵守其组件限制。

模型与统计使用中性文字，状态颜色承担主要提示。过程关闭或未包含某个工具时，该工具参数仍保留在正文，避免为整洁丢失内容。短答不额外堆叠状态横幅和空区块；长内容保留完整正文。布局、字号、颜色、间距与信息密度按[视觉发布门槛](wiki/card-visual-guidelines.md)逐状态验收。

任务正文中可安全识别的顶层代码块使用飞书原生 `plain_text` 显示，原语言在块外标注，改善浅色主题下部分语法颜色偏淡的问题。代码体和保存的原回答不改；其他预设、过程、审批范围及完整答案回退沿用原文。未知结构或会因显示变化而拆开长单行的代码块保留原显示。新候选的实际外观与交互结论以验收记录为准。

短审批选项直接显示动作名称，长选项保留完整说明与编号。新样式若让原本可发送的交互卡超过整卡容量，自动保留原布局，不截断问题或操作范围。标题颜色沿用[飞书官方标题枚举](https://open.feishu.cn/document/common-capabilities/message-card/message-cards-content/card-header)。

静默提醒复用原卡的刷新控制器：原有短动画之后等待观测窗口，最多再更新一次；收到新事件后可重新触发。每个任务最多等待检查 60 次，不增加独立消息，不轮询 Hermes 或调用模型。审批待输入、展示分段待切换、已完成或失败时停止刷新。它不是存活检测，也不会推算 ETA。

## 查看配置和预览

```bash
hermes-feishu-card card-config --config ~/.hermes_feishu_card/config.yaml --json
hermes-feishu-card card-config --config ~/.hermes_feishu_card/config.yaml --preview-dir ./card-preview
# 多 profile / bot 时追加 --profile-id work --bot-id support
```

打开输出目录中的 `index.html`，可比较经典与任务布局，切换九种状态、桌面/手机模拟宽度和浅色/深色背景。`cards.json` 保存相同的 renderer 输出，便于检查组件与容量。对比保留相同的阅读设置，只切换展示布局；不是把整个配置替换成 `classic`。

预览只使用内置示例，不读取聊天记录、不调用网络、不发送飞书消息，卡片按钮不会执行。配置解释采用字段白名单，不导出标题、账号、路径、路由或凭据。目标文件已存在时拒绝覆盖；换一个输出目录即可。

配置报告包含 `width_mode`、逐角色 `text_sizes` 来源、`footer_fields` 和交互/流式模式。`effective_for: next_load` 与 `running_config: not_checked` 表示该文件下次加载的结果；不能据此确认运行中的服务已应用设置。

额度显示要求本轮模型为 GPT，且实际 provider 为 `openai-codex`。仅有本机 Codex 登录、模型名以 `gpt-` 开头、或 API key provider 都不足以显示订阅额度；来源未知时隐藏。异步查询期间模型切换后，旧查询结果也会丢弃。

## 验证边界

离线 HTML 模拟排版，真实 renderer 证明 Card JSON 内容与容量。它不能证明 Feishu/Lark 原生字体、按钮触达、移动端视觉或真实 Gateway 操作。桌面、Android、iOS 的真实验收与公开发布另行记录；当前实现不升级本机 Hermes、不迁移配置、不提高 Python 最低版本。

## English quick reference

This is an **unreleased 4.7.0 candidate feature**, pending final acceptance and not part of published V4.6.13. Set `card.reading_preset: task` to opt into the task layout. Explicit display values retain precedence; restart through the existing service manager after editing. Switch to `classic`, `focused` or `detailed` to revert.

Task cards emphasize observed state and the complete answer, keep reasoning in a collapsible process panel, retain permission scope and callback ownership, and omit unreported footer metrics. After 60 seconds without an event, one bounded update explains that execution status is unknown. Pending input and terminal states stop refreshes. Restored display does not restore execution or authorization.

Recognized top-level code fences in task answers use native `plain_text` display with the original language labeled outside the code. The code body, canonical answer, other presets, permission scope and full-answer fallback remain unchanged. Unknown structures and blocks whose new fence would split a long line retain their original display. Actual client validation is recorded separately.

Run `hermes-feishu-card card-config --config <path> --preview-dir ./card-preview` and open `index.html`. Nine synthetic scenarios compare both layouts with simulated desktop/mobile widths and light/dark backgrounds. `cards.json` contains actual renderer payloads. The preview is offline, sends nothing, uses a configuration allowlist and refuses to overwrite existing output files.

The report explains width, per-role text sizes, footer fields and their global/profile/bot sources. `effective_for: next_load` and `running_config: not_checked` distinguish file interpretation from the running process. Codex subscription quota is shown only for a GPT turn attributed to `openai-codex`; unrelated, unknown or changed routes omit it. Real Feishu/Lark clients and Gateway execution require separate acceptance.
