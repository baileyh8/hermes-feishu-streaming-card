# v5.0.13 — 非飞书投递隔离与模型选择修复

本版本处理 [#479](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/479) 与 [#478](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/478)。是否发布以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.13) 为准。

## 修复范围

完成 hook 仅允许 Feishu 进入 HFC 的 admission、排队终局和卡片交接逻辑。Telegram、LOCAL 等其他平台继续执行 Hermes 原有 already_sent、媒体、页脚与最终回复处理，不在 HFC 复制上游去重规则。飞书既有交接和失败回退语义保留。

`/model` 的 Provider 选择首屏不再预选当前项，使用户可以主动选择当前 Provider 进入模型列表；当前 Provider 仍有文字标记。模型选择卡作为共享卡更新。缺失聊天标识的单聊回调必须通过原卡消息和已准入操作者等绑定检查；未知或冲突的身份不能借此放行。具体安全边界与验收证据在正式 Release 中登记。

## 安装与恢复

仅替换 wheel 不会刷新已经插入的 Hermes hook。请通过现有正常安装/修复入口升级，再由原服务管理方重启 Gateway；不要手改 Hermes 文件或删除 manifest、备份和保护标记。旧 completion block 继续按精确字节识别，保留重复升级及逐字还原。原有受支持 Hermes 源码和 PM 安装前态限制不变。

发行归档保留独立顶层目录：解压后进入 `hermes-feishu-card-v5.0.13-<platform>/` 再调用安装器。

## 验收与限制

候选验收进行中，最终完整 CI、普通 wheel、旧安装升级恢复、真实飞书 API、发行资产与公开安装结果绑定正式 Release 的精确提交。源码测试、受控原生方法对照和真实 Gateway/model/客户端点击分开记录，不能相互替代。报告者原现场复测不作已完成声明。

#476 的 B 方向动图仅为已确认的设计稿，不包含于本版本；#477 暂不考虑主分支图标。

## 贡献

感谢 [Lite-G](https://github.com/Lite-G) 的 #479 报告和分析，以及 [Kevinzz224](https://github.com/Kevinzz224) 的 #478 报告。#479 初始补丁来自 Bailey 提供的 ChatGPT 修复会话，后续集成与验收单独记录；问题证据与代码作者分开，既有贡献、标签和发行记录全部保留。
