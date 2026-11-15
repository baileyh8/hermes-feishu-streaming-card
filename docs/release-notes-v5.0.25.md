# v5.0.25：终局确认恢复与 Windows 安装提示

本版修复终局确认恢复、Windows 安装探针和脱敏安装说明。公开安装包与最终门禁记录见 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.25)。

## 变更范围

- **#529 终局确认丢失**：针对 sidecar 已接受终局、HTTP 回执却丢失后又发送原生正文的路径，复用同一 `event_id` 和完整事件内容做一次有界对账。保留原首次请求预算、单次 `AppliedAttempt`、发送锁和严格 `applied` 判定；重放与原生 handoff 恢复共享原有失败恢复预算。未确认、明确拒绝、绑定变化和取消仍按各自既有契约处理，不凭检查点或文本相似度设置 `content_delivered`，不新增执行队列或 owner。
- **#526 Windows PowerShell 5.1**：凭据探针通过临时 Python 文件执行，避免 `-c` 内嵌双引号被 native 参数解析剥离。保留凭据/profile 判定、安装失败检查和临时文件收尾；PowerShell 5.1 与 7 分别验收。
- **#526 脱敏诊断**：保留已知安装状态的固定安全说明，明确 `clean` 表示没有现有 hook 安装状态。源码 anchors、streaming 配置与运行时激活分别解释；不修改 `repair` 的归属、保护标记或自动修复规则。

## 复现与现场证据

维护者在当前源码的隔离链路中复现了“终局已应用、卡片更新一次、确认断连或超时、原生正文再次发送”。对照覆盖正常确认和未接受请求。实验使用固定 Hermes 原生函数、真实 sidecar `/events` 与回环 HTTP，数据库和飞书端为测试替身；它证明共同故障路径，不等同报告者 Linux / Hermes v0.23.1 原机复现。

#526 报告者提供的五个源码 SHA256 与官方 `3b421578` 原生字节一致，随后确认此前没有源码 hook；执行 `setup` 并重启 Gateway 后，现场出现 52 个已应用事件、一次发卡和 41 次成功更新。这是报告者的真实恢复证据，独立于本轮 Windows 安装器和诊断改动。先前缺少的现场证据已经收到，不再重复索取。

PR #530 的 Windows 3.14.9 普通 wheel、原生入口与 Hybrid 激活对照仍保留其边界：合成 Feishu 入站与回环模型/sidecar，不证明既有 PM/observer 进程或真实客户端状态。

## 验收状态

本地独立 QA 的 11 项故障与取消回归、60 项既有 observer/原生恢复回归、12 项身份单元检查和 104 项文档/元数据检查通过。普通非 editable wheel 的 165 个资源逐字匹配；独立子进程中全部 HFC 模块来自普通安装目录，断连、真实十秒超时、未接受和正常确认四个对照通过。

真实飞书 API 的三组验收通过：首个回执丢失后只更新一次终局且不发原生正文；未接受时保留一次完整原生正文；正常确认无重复。验收使用实际 sidecar、固定 Hermes 原生函数和真实平台 I/O；DB/模型仍是测试端口，不等于真实 Gateway/model 或报告者原机验收。

发布门禁包括精确提交的完整 Python/平台/upstream CI、Windows PowerShell 5.1/7、merge/tag 来源、发行资产与公开普通安装；实际提交、运行链接及客户端验收范围分别记录在 [PR #530](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/530) 和 GitHub Release，任何未通过项均不视为通过。全部终局回执持续不可达或 sidecar 去重状态丢失时，仍优先保留原生答案，不能承诺该故障下绝无重复。

## 使用与恢复

`doctor` 的源码兼容检查不替代安装或新回合验收。没有受管 hook 安装状态时，审核选定的 Hermes 根目录、HOME、HFC 配置和环境文件，再使用正常 `setup` 流程；`repair` 维护已有、可验证的安装，不替代首次安装。保留 manifest、备份和所有保护记录，遵循 [PM 恢复说明](wiki/pm-recovery.md)。

## 贡献记录

感谢 [leavrcn](https://github.com/leavrcn) 提供 [#529](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/529) 的终局重复投递时序，[nmtlcxb](https://github.com/nmtlcxb) 提供 [#526](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/526) 的 Windows 恢复与 PowerShell 5.1 证据。问题证据与代码作者分开记录，全部历史贡献、提交与 tag 保留。
