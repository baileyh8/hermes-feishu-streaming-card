# V5.0.5：新 Hermes admission 与配置写入兼容

本版修复 [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451) 涉及的新源码兼容边界，并整合 [PR #448](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/448)、[PR #449](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/449) 与 [PR #452](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/452)。发行区间从 v5.0.4 之后开始；既有提交、标签和贡献署名全部保留。

## 修复范围

- **原生 admission 完成卡**：核验完整 `session_ingress.py` / `session_authority.py` 源码组、实际 loaded code 和 Base 提取接点，允许 Hermes 为同一 admission 合法重建 source。完整路由、owner、generation、epoch 及原 execution handle 仍须匹配，终局 handoff 只能消费一次；重复、迟到、跨身份和终局失败继续走原有边界。
- **接管前兼容性检查**：当前 admission 首次决定卡片策略、且同一 identity 尚未 pin 时，既有 policy 入口检查全部 `SUPPORTED_RUNTIME_EVENTS`，包括 notice / interaction 首事件。未知 admission 来源保留 Hermes 原生路径；detect 单独说明 admission completion 子能力。admission 之前已 pin 的普通上下文、官方 plugin 独立 owner 和已接管后的投递/终局失败规则保持原职责。
- **官方配置 writer**：增加经过调用链与间接 serializer 核验的源码族，保留旧族。官方 enable 前在私有 HOME 规划准确配置字节，未知源码或漂移拒绝写入；PM A→B 重建核对普通 wheel 来源，恢复仍逐字核验原配置与受管源码。
- **完整 CI 的分片与隔离**：包含 #448/#449 的保守范围选择、按耗时分片和完整覆盖核对。上游 worker 使用各自新建的普通 wheel prefix，避免 sensor / `.pth` 竞争及 33 例共享 bundle 过重；耗时权重记录实际 JUnit 与明确标出的估计。保留 Linux Python 3.9–3.14、macOS、Windows、SDK、Docker、CodeQL、全部 required checks 和 25 分钟任务超时。缺 wheel、环境准备失败或缺报告仍阻断。异步 recheck 测试在关闭客户端前等待真实诊断完成与发布，保留指纹及唯一投递断言。

## 固定来源与升级

本次支持绑定以下真实源码，不能从版本字符串推断任意后续 main 也兼容：

| 来源 | 固定 commit |
| --- | --- |
| #451 报告的 cf4 | `cf4f3a7396814ffcaef69d9af09f7cfdade3ddbe` |
| rc.2-v0.22.3 | `f83b4df3ce1ab2ec62f1e0e90f22e0a1db21cb68` |
| v0.22.4 | `4d04303818b61bf3aa09278af48356315515965b` |
| main，2026-10-22 | `4e58c721c8cf9b51c46636ea84ea6a5cb540880a` |

原 `5fc490e` admission 来源、d923 v0.22.2 与 0ab main 的独立安装/writer/rate 基线继续保留。混合来源摘要、未知 loaded topology、缺失 seam 或身份不符不能获得完成卡接管资格；已接管路径仍遵守既有投递 owner 和终局失败规则。该范围不证明所有未知版本或所有 plugin 都不会创建卡片。

在 `v5.0.5` 正式发布后使用官方安装器升级，保留原 config、env、Hermes HOME、profile 和服务归属，按安装输出重启对应进程。详见[升级指南](upgrade-v4-to-v5.md)。显示默认值、卡片布局和 Hermes 执行/授权归属保持原行为。

## 验证与尚未验证的范围

修复候选已具备固定源码安装、重复安装、doctor、逐字恢复、原始 patched ledger 执行、配置 writer 对照及真实 PM 重建的分层证据。普通 wheel 并发探针核对两个独立 prefix/site、sensor/`.pth` 归属、非 editable 来源、archive SHA 与 141 个资源字节，并确认借用环境未修改。它们分别证明对应边界，不代表真实业务回合验收。

v5.0.5 精确提交的完整 CI / CodeQL、真实 Feishu API、普通安装包、公开 tag 安装及资产/checksums 结果，逐项登记在[发布准备说明](release-readiness.md)，实际结果见精确版本 [v5.0.5 GitHub Release 验收记录](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.5)。相邻提交或版本准备前的通过结果不能替代最终 release gate。

真实 Gateway/model 执行、报告者的工具密集现场、桌面及 Android 视觉/实际点击仍未验证。iOS 与新用户试用不纳入本次范围。#451 的合成多工具用例通过不能写成报告者原场景已复测；已有 #344/#361 等未验边界不因本次修复关闭。

## 贡献

本发行区间的修复与 CI 代码由 [baileyh8](https://github.com/baileyh8) 维护，PR #448/#449/#452 的实际作者与 Git 提交记录一致。感谢 [coytus](https://github.com/coytus) 提供 #451 现场报告，以及 [Lite-G](https://github.com/Lite-G) 提供源码分析。这两项是问题证据与分析署名，与本次代码作者分开记录。双 README 和历史发行说明中的早期贡献记录全部保留。
