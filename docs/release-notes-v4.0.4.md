# V4.0.4

V4.0.4 处理本轮 issue：兼容 #107 的单限额窗口变化，修复 #110 的 Markdown `MEDIA:` 字面量误解析，补齐 #112 在 lark SDK 已保存旧 bound callback 时的 `interaction.select` 后台兼容路径，并将 #111 归并到已由 V4.0.3 修复和真实验收的 #106。

## Codex 限额窗口

- OpenAI 暂时取消 Plus 的 5 小时窗口后，Hermes 仍可能把唯一 `primary_window` 标成 `Session`。
- 插件无法从该标准化结果可靠判断窗口周期，因此单个 `Session/Primary` 使用中性 `limit 94%`，不再误显示 `5h 94%`。
- 同时存在 Session 与 Weekly 时，继续显示 `5h 26% · weekly 89%`；显式 Weekly 单窗口仍显示 `weekly`。

## Markdown 媒体解析

- inline code 与 fenced code 中的 `MEDIA:` 或本地路径保持普通 Markdown 内容，不生成附件摘要，也不触发原生媒体投递。
- 卡片正文清理、附件提取、`native_delivery` 判定和 native-media-only response 改写共享同一代码区边界。
- 代码区外的真实 `MEDIA:/path`、本地文件路径和 Hermes 结构化媒体字段保持原有行为。

## Card action 兼容

- lark SDK 可能在插件安装前保存原始 `_on_card_action_trigger` bound method；仅修改 class attribute 不能改变该引用。
- 当前主路径仍会重建 event handler；新增后台兼容路径确保旧 callback 调用动态解析到 `_handle_card_action_event` 时，`interaction.select` 仍直接转发 `/card/actions`。
- 转发在 worker thread 中执行，不阻塞 Feishu adapter event loop；重复 action token 继续被拦截。
- 未识别的 action 仍交还 Hermes 原生 synthetic-command 路径，保持 fail-open。

## Issue 整理与贡献

- 感谢 @tianqiii 在 #107 及时反馈 OpenAI 上游单窗口返回变化和真实 payload。
- 感谢 @sthnow 在 #110 提供精确复现、正则根因和期望解析边界。
- 感谢 @zkyken 在 #112 提供完整日志和 bound-method 分析，帮助定位缺失的后台兼容分支。
- #111 是 #106 的重复后续反馈；感谢 @ShakuOvO 与 @blakejia 的原始报告、独立复测和截图。V4.0.3 已通过真实飞书“完成卡 + 原生图片 + 额外机器人 text 为 0”验收。

## 验证

- hook runtime / interaction / server / subscription usage 热区矩阵：`404 passed`。
- 全量测试：`1275 passed, 3 skipped`；`git diff --check` 通过。
- 回归覆盖真实媒体指令、inline/fenced code 字面量、native response 保留和 SDK 预绑定旧 callback。

## Release assets

- `hermes-feishu-card-v4.0.4-macos.tar.gz`
- `hermes-feishu-card-v4.0.4-linux.tar.gz`
- `hermes-feishu-card-v4.0.4-windows.zip`
- `hermes-feishu-card-v4.0.4-checksums.txt`
