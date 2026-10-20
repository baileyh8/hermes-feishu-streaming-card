# V5.0.4：legacy 生成速率、压缩提示与跨 Task 完成卡

本版保留原作者提交，整合 [PR #442](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/442)、[PR #444](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/444) 和 [PR #445](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/445)。

## 修复范围

- **Legacy 生成速率**：在满足精确源码合同的安装中，通过 observe-only 官方插件补齐 `output_rate`。Legacy 仍是唯一事件 producer，不增加 listener、hello 或 heartbeat。使用逐次模型调用的实际 usage/timing，不用累计 footer token 猜分子。预览不消费样本，正式终局消费一次，跨 Task 和传输重试复用冻结 payload。缺失、混合、过期或无法归属当前轮的指标隐藏；默认仍关闭。
- **安装与恢复**：只回滚能证明归属官方 enable 的配置 postimage；未知结果及并发用户编辑保留原字节和恢复证据。只将确认缺失的 observer marker 视为缺失；PermissionError/EIO 等不可检查状态禁用准入，不能误进入 Hybrid。
- **精确压缩提示**：将完整原生 `📦 Preflight compression: ~…` 与 `✓ Context compaction complete — continuing turn...` 模板接入已有 15 秒撤回 owner。保留原生返回值、路由、Working/HEARTBEAT、失败、附加正文和未知文案。持续可编辑状态不按这两种一次性提示处理。
- **Session Authority 完成卡**：修复原生 admission 在 drain Task 执行、随后 waiter Task 投递时完成卡未收尾的问题，也覆盖没有 waiter 的 drain-owned 交付。借用原 admission/event 的有界单用 handoff，保持原 execution handle、Task gate、Base 提取后的媒体/TTS 与 delivery ledger。群内每轮 sender、epoch、route、取消和迟到检查保持独立，不新建执行 claim registry。

## 兼容性与失败行为

Session Authority 新桥接只接管经过精确源码及 loaded-code 校验的 Hermes `5fc490e2b34123212d73ef316086f939567a725a` 拓扑。未知版本、源码/运行时漂移、缺失提取接点、身份或 epoch 不符时保留原生交付，并输出有界脱敏诊断。它不表示所有 Hermes 0.22.x/main 均已覆盖。已有非 Authority 路径保持原兼容合同。

使用官方安装器升级到 `v5.0.4`，保留原 config、env、Hermes HOME 和服务归属；见[升级指南](upgrade-v4-to-v5.md)。生成速率通过已有 `card.footer_fields` 中的 `output_rate` 显式启用，测量口径见[observed timing](wiki/observed-timing.md)。

## 验证范围

本版门禁包括组合源码回归、真实 Python 3.9 floor、Ruff/mypy、固定 clean Git fixture 的完整 preflight、独立 pinned upstream CI、普通非 editable wheel/child、原生 SQLite admission/ledger、回环 HTTP、安装/重装/逐字恢复，以及精确 merge/tag 的 CI、资产校验和公开安装。各项结果以精确候选及 Release 验收记录为准，不累加三个修复各自的测试数作为组合验证。

真实 Feishu API smoke 与自动化分开记录。未声明真实 Gateway/model 执行；本轮桌面及 Android 视觉/实际点击未验证，iOS 和新用户试用不纳入本次范围。#434/#443 报告者的原现场未复测；#344/#361 的既有未验事项不因相邻修复自动关闭。

## 贡献

感谢 [Lite-G](https://github.com/Lite-G) 提供 #441 证据和 #442 原始代码；感谢 [Timeral](https://github.com/Timeral) 提供 #434 复现与现场复测；感谢 [JohnZhong505](https://github.com/JohnZhong505) 提供 #443 现场证据，以及 Lite-G 的补充源码分析。问题证据、源码分析与修复代码作者分别记录，全部历史提交和双 README 既有署名保留。
