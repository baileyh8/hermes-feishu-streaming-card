# Hermes Feishu Streaming Card — 主线任务清单

当前 active runtime 是 `hermes_feishu_card/`。legacy adapter、dual mode、旧 `sidecar/`、旧 `patch/` 和 `installer_v2.py` 不是 active runtime，仅保留作历史参考。

## 下一版计划：V3.6.0 / V3.7.0

详细路线见 [docs/roadmap-v3.6.0.md](docs/roadmap-v3.6.0.md)。

### V3.5.2：安装补丁版

- [x] 更新 CHANGELOG、README 和 Release notes，说明一行安装、Release 包、checksum。
- [x] 发布 tag 后验证 `.github/workflows/release-assets.yml` 能上传 macOS/Linux/Windows 安装包。
- [x] 确认 `install.sh` 在 macOS 临时 Hermes fixture 上完整跑通。
- [x] 补 Windows PowerShell 安装脚本的语法验证路径。

### V3.6.0：安装与运维产品化

- [x] **P0 安装自救**：新增 `doctor --explain` / `doctor --json`，解释 hook strategy、manifest、backup 和 anchor 状态。
- [x] **P0 安装修复**：新增 `setup --repair` 和 `repair` 子命令，处理 manifest/backup 缺失等可验证修复场景，并拒绝用户改动。
- [x] **P0 媒体/文件消息处理**：识别结构化 attachments/files/media_files，在卡片中保留摘要，同时不抑制 Hermes 原生媒体/文件投递路径。
- [x] **P1 多 Profile CLI**：`smoke-feishu-card`、`bots test` 支持 `--profile-id` 和 profile 维度排障。
- [x] **P1 health routing 分组**：`/health.routing` 在多 Profile 下按 profile 分组展示 bot、chat binding、last_route、last_route_error 和 events。
- [x] **P1 E2E 矩阵**：覆盖 Hermes `v2026.4.23`、`v2026.5.7`、`v2026.5.16+`、`v2026.5.29`、`0.13.x`、`0.14.x`。
- [x] **P1 发布矩阵**：CI 验证 Release 打包 dry run、macOS/Linux install dry run、Windows PowerShell parser。
- [x] **P2 Docker 部署 / issue #70**：V3.7.0 提供 `install-docker.sh`、`docker-compose.example.yml`、容器路径/venv Python/权限诊断和发布包文档。

### V3.7.0：体验增强候选

- [ ] 卡片思考过程折叠/展开，默认突出最终答案和关键工具状态。
- [ ] 工具调用详情支持查看参数摘要、耗时、失败原因。
- [ ] 卡片内提供“继续”“重试”“取消”等操作入口。
- [ ] 群聊规则支持 @机器人触发、白名单、chat binding 自动提示。
- [ ] 可观测性补充 update queue length、coalesce count、terminal drain latency、Feishu API latency。

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
