# Hermes Feishu Streaming Card — 主线任务清单

当前 active runtime 是 `hermes_feishu_card/`。legacy adapter、dual mode、旧 `sidecar/`、旧 `patch/` 和 `installer_v2.py` 不是 active runtime，仅保留作历史参考。

## V3.8 系列路线：V3.8.0 / V3.8.1 / V3.8.2

详细路线见 [docs/superpowers/specs/2026-06-30-v3-8-design.md](docs/superpowers/specs/2026-06-30-v3-8-design.md) 和 [docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md](docs/superpowers/plans/2026-06-30-v3-8-card-ux-stability.md)。

### V3.8.0：卡片体验与流式稳定性（已完成）

- [x] 主回答与 reasoning / tool timeline 分离，默认突出最终答案。
- [x] burst update coalescing 收敛高频 PATCH，减少快速 thinking / tool burst 下的重复更新。
- [x] terminal completion 前 drain pending updates，避免终态卡片被陈旧中间态覆盖。
- [x] 长 Markdown 表格和 fenced code block 跨卡片分块时保持结构安全。
- [x] 可观测性补充 update queue length、coalesce count、terminal drain latency、Feishu API latency。

### V3.8.1：高频流式修复与只读诊断（已完成）

- [x] issue #74：Gateway runtime 内合并高频 `thinking.delta` / `answer.delta`，降低 Hermes stream-reader 热路径压力。
- [x] terminal event 前 flush 同一消息 pending delta，避免最终卡片缺少尾部内容。
- [x] 飞书内提供 `/hfc help`、`/hfc status`、`/hfc doctor`、`/hfc monitor` 只读诊断命令。
- [x] 安全清理：`/messages/{message_id}/summary` 返回中的 `chat_id` / Feishu `message_id` 改为 hash。
- [x] patcher 兼容 V3.8.0 及更早无命令 hook block 的升级和卸载。

### V3.8.2：卡片 timeline 阅读体验补丁（已完成）

- [x] pre-tool answer 先停留在正文区，下一段 answer 或终态到来时再归档进“思考与工具”。
- [x] 完成态正文剥离已归档的中间说明，只保留最终答案。
- [x] raw `thinking.delta` 继续隐藏，不混入正文区或用户可见 timeline。
- [x] 折叠区中思考和工具使用不同字号与灰度层级，工具详情更紧凑。
- [x] README 增加 V3.8.2 折叠态和展开态真实截图。

### V3.8.x 后续维护与扩展面（待办）

- [ ] 卡片内提供“继续”“重试”“取消”等写操作入口，需要单独做权限、幂等和误触发设计。
- [ ] 工具调用详情支持查看参数摘要、耗时、失败原因。
- [ ] 群聊规则支持 @机器人触发、白名单、chat binding 自动提示。
- [ ] 补齐 E2E / fixture 覆盖，验证 V3.8.x 卡片体验和终态 drain 主链路。
- [ ] 完成 agent guide、维护手册和开放扩展面的文档整理。
- [ ] 评估卡片 timeline/metrics 的长期兼容边界，并补发布回归清单。
- [ ] 完全兜住极端 Markdown table 边界：当结构化拆分失败时输出安全折叠提示，避免回退 plain split。
- [ ] 清理 terminal 后的 closed `FlushController`，并评估更有诊断价值的 queue depth / coalesced backlog 指标。

## V3.3.0 (已完成)

- [x] 多 Profile 进程内支持（一个 sidecar 服务多个 Hermes profile，`profile_id:message_id` 复合键）
- [x] 多 Bot 独立凭据路由（`_resolve_route` 注入 profile prefix，`_client_for_bot` 按 profile 分发）
- [x] DeepSeek `<thinking>`/`</thinking>` 标签过滤
- [x] 卡片表格超限保护（`MAX_CARD_TABLES=5`，自动截断）
- [x] Footer braille spinner 旋转动画
- [x] COMPLETE_PATCH 平台判断修复（非飞书平台不再吞掉响应）
- [x] 工具次数改为累计调用次数（`_tool_call_count`）
- [x] 锁优化：飞书 API 调用移出事件锁，更新间隔 2.0→0.5s
- [x] 跨 Profile 数据泄漏修复（feishu_message_ids 等改用 session key）
- [x] README 全面重写（安装→功能→配置→FAQ 结构，214 行）
- [x] CHANGELOG、LICENSE、config.yaml.example、AGENTS.md 更新
- [x] 真实环境 E2E 测试（3 bot × 3 profile，飞书卡片发送验证）
- [x] 425 个测试，0 失败

## V3.0-V3.2 (已完成，归档)

- [x] Sidecar-only 架构、流式卡片、健康端点、安装向导（V3.0）
- [x] 多 Bot 注册与路由、群聊绑定、Bot CLI、路由诊断（V3.2）
- [x] Accept-Encoding 修复 brotli 兼容（V3.2.1）
- [x] 真实 Feishu E2E 主链路验收（Hermes hook 到 sidecar `/events` 的 fail-open 转发链路）
- [x] 实现 Feishu CardKit HTTP client，并用 mock server 验证 tenant token、发送和更新。
- [x] 提供 `smoke-feishu-card` 手动命令用于真实飞书卡片发送/更新验证。
- [x] 使用真实飞书应用做人工 CardKit smoke test，凭据仅使用本机配置或环境变量。
- [x] 完成真实飞书长卡片压力测试，同一张卡片更新到 16k 中文字符。
- [x] 将 sidecar 进程管理从占位 `status` 扩展为可启动、可停止、可探活。
- [x] 增加 sidecar 健康检查和重试指标。
- [x] 增加安装前 Hermes 版本展示和更友好的错误提示。
- [x] 补齐官方 Hermes `v2026.4.23` Git tag 源码的安装/恢复 smoke test。
- [x] 补齐基于 Hermes fixture 和 mock sidecar 的最小 hook 事件转发验证。
- [x] 在真实 Hermes Gateway 进程中做人工 smoke test。
- [x] 编写从 legacy/dual（installer_v2.py、gateway_run_patch.py、patch_feishu.py）安装迁移到 sidecar-only 的安全迁移说明。
- [x] 端到端截图与验证材料（e2e-card-preview.svg、e2e-card-preview.json、generate_e2e_preview.py）。

## V3.5.0 (已完成)

- [x] Hermes 授权/选项请求在飞书卡片中渲染按钮，用户点击后原任务继续并更新原卡片
- [x] issue #41：多条回复/新版 Hermes 流式链路第二条开始不再退回 text 模式
- [x] PR #42：cron deliver 与 scheduler resolved targets 优先于陈旧 `origin.platform`
- [x] 超过 `MAIN_CONTENT_CHUNK_CHARS` 的长表格/代码块按完整 Markdown 结构切分，避免飞书 raw markdown
- [x] thinking/interim assistant 使用 `append_block` 完整块追加，减少句子截断、漏字和粘连

## V3.4 (计划)

- [x] issue #39：修复 DeepSeek V4 Pro 工具调用后 blank completed answer 清空流式答案（V3.4.3）
- [x] PR #38 核心能力：Markdown 长内容按表格/代码块结构边界切分（V3.4.3）
- [x] Hermes `v0.14.0` / `v2026.5.16+`：确认使用 `gateway_run_013_plus`，`v2026.4.x` 保持 legacy（V3.4.3）
- [x] issue #31：修复并发 PATCH / sequence 竞争导致的流式卡片内容回退与漏字（V3.4.2）
- [x] issue #25：修复 Hermes v2026.5.7 fallback `message_id` 生命周期一致性（V3.4.1）
- [ ] 旧 V3.4 未完成项已迁移到 V3.6.0 / V3.7.0 下一版计划。
