# V4.4.6 — 安装兼容、终局恢复与交互状态修复

## 修复

- #288/#294/#296：接受 Hermes 附件投递有无 `record_delivery=_record_delivery` 的两种已验证契约，仍拒绝未知参数和重复调用。修正拆分账本 hook 的运行时识别，保证最终投递顺序。兼容测试固定在 Hermes 0.21.3 源码 `2179a279ae04bfadf8efbc49a01ca0abfb738000`，不以版本号代替源码验证。
- #298：卡片 PATCH 和终局重试耗尽后，使用稳定 UUID 在原会话/话题补发一次完整终局卡片；保留 Gateway ACK 所有权，避免原生正文重复。`terminal_delivery_state` 与 `last_terminal_delivery` 提供恢复结果。飞书整体不可用时补发仍可能失败，诊断会保留该状态。
- #301/#285：识别明确的迭代/预算耗尽原因，覆盖 legacy 与 native incomplete 路径，不把未完成任务标成成功。#289：失败时保留可见的部分回答。不会修复 Hermes/provider 停止执行本身。
- #280：交互结束或过期后显示原问题、编号选项和结果；#282：拒绝或不确定的回调显示明确反馈，不引导重复授权。未宣称真实手机端首次点击问题已经复现或全部解决。
- #276：完整性诊断给出沿用当前路径、正确 shell 引用的只读 doctor 命令，不放松安全 fence。
- CodeQL init/analyze 同步更新到 4.38.0，并分组后续依赖更新。

## 验证与升级

修复候选在 Python 3.11/3.12 与 macOS 全量 CI 各通过 3605 项，Python 3.9/3.10 各通过 3594 项；另覆盖 Windows、PowerShell、Docker、SDK 和当前 Hermes source-only 安装/重复安装/恢复。发布提交仍须通过同一跨平台门禁、精确合并提交全量测试、资产校验和公开安装验证。

使用官方安装方式将 HFC 更新至 `v4.4.6`。Hermes 已升级的环境应使用安装器的 `--accept-hermes-upgrade` 流程，经 doctor 确认后重启 Gateway 使新 hook 生效；仅重启 sidecar 不会重新加载 Gateway hook。不要手动删除完整性标记。

## 仍在跟踪

#293 CardKit streaming_mode 和 #295 持久暂停/恢复尚未实现。#282 移动端首次点击、#283 长时间空卡、部分 Topic/@/Docker 现场需继续定位；见 [逐项处理记录](issue-triage-2026-09-15.md)。本版没有执行真实移动端验收或生产升级。

## 致谢

感谢 tidytorch（#286/#291）、Jentlezhi（#292）的实现与测试，保留原始作者提交；感谢 sp960817、Cyber-Yichen、shichenshuo-star、ywarmy 的安装证据，7360403-coder 的终局投递分析，以及 mouyong 的状态、交互与诊断反馈。历史贡献者继续保留在中英文 README。
