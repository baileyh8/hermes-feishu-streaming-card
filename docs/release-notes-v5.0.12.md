# v5.0.12 — 隔离 Feishu 消息锚点与完成交接来源

本补丁针对 [#474](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/474) 和 [#469](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/469)。是否已发布以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.12) 为准。

## 修复与边界

HFC 的消息锚点发布限定为 Feishu，避免改写 LOCAL 等其他平台的 source.message_id。报告者指出该写入会让 LOCAL 现存 source 与恢复 receipt 的来源字典不同，第二轮被 Hermes 拒绝。这个修复不代表所有原生 Hermes storage_unavailable 故障都由 HFC 引起。

Feishu 的完成交接需区别已核验的当前轮消息锚点与原生执行身份。仅当 Feishu 的 admission source 缺少 message_id，而 live source 的 message_id 和 HFC turn marker 都精确等于当前 canonical message ID 时允许这一个差异；反向缺失、不同非空锚点与未知轮次仍拒绝。除此之外，保留其余原生字段的严格比较，以及原 Task、claim、durable row、profile、路由、sender、thread、generation/epoch 与单次终局确认。不能把报告者的身份字段白名单直接当作安全证明。

## 安装与恢复

仅安装新 wheel 不保证运行中的 Gateway 已加载新 hook。旧安装必须通过正常安装/修复入口刷新受管补丁，并由原服务管理方重启 Gateway 和相关 sidecar。保留 manifest、marker、备份与 journal，不手改 Hermes 源码或删除保护记录。旧补丁升级、重复安装与逐字恢复需单独验收。

若旧安装升级时 PM generation 已变化，`setup` 可能先以 `legacy observer runtime binding changed` 安全拒绝。这时用同一实际 checkout 和 HOME 执行官方 `integrity rebind-legacy --hermes-dir <checkout> --hermes-home <HOME>` 预览；核对后加 `--yes`，再重试原来的 `setup --repair`。该路径核验已安装源码与备份的归属，不把新版 renderer 的变化误判为旧源码未安装；它不接受源码漂移、部分恢复或未知备份。不要删 marker 绕过重绑。

本补丁不扩大 v5.0.11 的精确 Hermes 9b904 安装前态支持范围；全新 PM、旧 receipt、未核验 SDK 和来源漂移仍按原合同拒绝。解压后进入 hermes-feishu-card-v5.0.12-<platform>/ 再执行安装器。

## 验收状态

固定 v0.22.5 源码下已独立复现两条故障；修复后 LOCAL 连续三轮的恢复检查通过（receipt/policy 为测试端口）。v0.22.5 与固定 9b904 的普通/内部事件均通过实际 rendered hook 到完成事件的受控链路。受影响的完成交接回归 434 项通过，本机未提供的两个 9b904 checkout 检查单列跳过，须由对应 CI 门禁补齐，不能视为通过。普通 wheel 升级/恢复、精确完整 CI、tag、公开安装和实际发行包验收分别记录，最终结果绑定正式 Release 中的精确提交。真实 Gateway/model、报告者现场与 Feishu 客户端视觉/实际交互不能由模拟或 CI 代替；最终结果以正式 Release 的证据为准。

#459 已依据报告者提供的原 macOS/POSIX PM 环境成功复测回复并关闭，与本补丁的独立验收分开记录。

## 贡献

感谢 [Alex-HJY](https://github.com/Alex-HJY) 的 #474 报告、源码分析及现场补丁证据，感谢 [leavrcn](https://github.com/leavrcn) 的 #469 复测与差异分析。代码作者以 Git 提交为准，问题证据不冒作代码署名；既有贡献和历史发行记录保留。
