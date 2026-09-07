# Hermes Feishu Streaming Card V4.4.1

[中文](release-notes-v4.4.1.md) | [English](release-notes-v4.4.1.en.md)

V4.4.1 修复 Hermes 0.21 源码拆分后的安装兼容性、话题后续投递和单进程多 profile 配置，并改善审批与思考内容的阅读方式。

## 安装与话题投递

- 吸收 PR #257 的多文件 facade/mixin 适配，按已验证契约定位 hook，保留 ownership、备份和可逆恢复；不因找到同名函数就放宽未知结构检查。
- Hermes 更新带走旧补丁时，诊断与恢复继续依赖当前源码、manifest 和 ownership 证据，不覆盖无法确认的用户修改。
- 吸收 PR #251 的后续回复、queue/redirect 和 cron 话题锚点处理；显式 turn 身份保持隔离，已有回复锚点传到最终投递。

## 多 profile 与页脚

- Issues #83 / #259：单进程 multiplex 消息按当前消息的可信 profile 身份路由，配置不再一律假定存在名为 `default` 的 profile；多进程固定 profile 的场景保持支持。
- Issue #250：当 runtime 提供实际 provider 时，页脚显示 `provider/model`，避免重复前缀。不能证明 provider 时不从初始配置猜测 fallback 归属。

## 审批和思考阅读

- Issue #258：审批命令不再在 3,000 字符处静默截断；使用转义后的普通 Markdown 展示完整范围，减少长代码行横向滚动问题。
- 转义正文的 JSON 编码超过 12,000 UTF-8 字节时，创建卡片前交还 Hermes 原生审批；不会针对截断的命令提供授权按钮。
- Issue #253：新增 `card.reasoning_format: code`，思考片段以代码块直接展示，工具记录保留在折叠面板。默认 `panel`、`show_reasoning`、长度限制和最终卡片大小门禁继续生效。配置示例见 [阅读方式](wiki/card-readability.md)。

## CI 与验证边界

CodeQL init/analyze 一起升级，相关测试校验同步更新。聚焦 renderer/config/runtime 测试和 server 接线集成已通过；完整 pytest、普通 wheel、GitHub CI 和发布资产的结果以本轮最终发布记录为准。

本轮尚未执行真实 Feishu/Lark 客户端验收；Issue #258 原截图未成功读取，阅读改动基于已确认的代码截断和展示结构，需报告者复测。历史 Issue #73 没有当前环境复现，继续等待新版诊断证据，不能仅凭自动化认定解决。

## 贡献者

- [liooil](https://github.com/liooil)：[PR #257](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/257) 提供 Hermes facade 拆分适配实现；[Clarence-G](https://github.com/Clarence-G)：[PR #251](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/251) 提供话题后续投递、queue/redirect 与 cron 相关修复。原始代码提交和作者身份予以保留。
- [mouyong](https://github.com/mouyong)：[#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83)、[#252](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/252)、[#253](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/253)、[#258](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/258)、[#259](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/259) 的单进程 profile、话题和阅读体验反馈；[shiboyumm](https://github.com/shiboyumm)：[#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83) 最初的配置问题；[Boer2333](https://github.com/Boer2333)：[#250](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/250) 的 provider 展示需求。
- [sp960817](https://github.com/sp960817)：[#254](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/254)、[Kevin32623](https://github.com/Kevin32623)：[#255](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/255)、[shichenshuo-star](https://github.com/shichenshuo-star)：[#256](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/256) 的 Hermes 0.21 兼容性报告；[hnzwx](https://github.com/hnzwx) 与 [leavrcn](https://github.com/leavrcn)：[#254 的复现与兼容性审查](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/254)；[micah928](https://github.com/micah928)：[#73](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/73) 的历史无卡片诊断证据，该环境仍待新版复测。
- Dependabot 提供 [PR #247](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/247) 和 [PR #248](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/248) 的 CodeQL 依赖更新。
- 历史署名补全：[lanx214](https://github.com/lanx214) 在 [Issue #240](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/240) 提供 Linux 复现（[V4.3.7](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.7)）；[Lite-G](https://github.com/Lite-G) 报告、复现、测试并实现 [PR #235](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/235) 的 Feishu edit fallback 修复（[V4.3.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.5)）；[lyp88997](https://github.com/lyp88997) 提供 toast-only `200673` 修复方向及跨环境更新观察（[V4.3.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.2)）。这些是此前版本的贡献，本轮恢复遗漏的历史署名。

历史版本的贡献者继续完整保留在 [README](../README.md#贡献者) 中。
