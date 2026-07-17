# V4.0.10

V4.0.10 收紧 Hermes hook 到 HFC sidecar 的事件传输边界，同时保持默认本机安装兼容。

## 安全改进

- 默认 `127.0.0.1` / `localhost` / `::1` 继续使用本机进程互信，升级后无需新增凭据或修改 hook URL。
- `0.0.0.0`、私网地址和命名主机等非回环监听默认拒绝启动；必须显式设置 `server.allow_non_loopback: true`。
- 显式非回环模式下，每个 `/events` 请求都必须携带 HMAC-SHA256 proof。签名绑定原始请求体、timestamp 和 nonce，30 秒时间窗内防伪并拒绝重放。
- 签名复用 sidecar state directory 中权限受限的 operations transport root，不把密钥写入 config、env、日志、卡片、health 或诊断输出。
- HMAC 只提供鉴权和完整性，不提供加密。跨主机部署仍应位于可信私网，并在公开或不可信链路前配置 TLS 或 mTLS。

## 可观测性与维护边界

- `/health` 显示布尔值 `event_auth_required`；鉴权失败增加 `events_rejected` 和 `event_auth_rejections`。
- CLI `status` 与 card-safe diagnostics 允许输出拒绝计数，但不会输出 timestamp、nonce、signature 或 transport root。
- 中英文架构已更新为当前 V4 事件流；新增 fail-open 维护矩阵，区分 Hermes 原生路径可继续的异常与网络暴露、鉴权、安装所有权必须失败的异常。

## 兼容性

- loopback sidecar 仍接受旧 hook 的 unsigned event，避免升级窗口中 Gateway 与 sidecar 版本不一致导致中断。
- 当前 hook 在读取到安全 transport root 时会签名 `/events`；读取或签名异常保持 Hermes fail-open，不影响非 HFC 原生路径。
- `/commands`、卡片 callback 与现有 operations proof 域保持独立，不复用 event proof。

## 验证

- 安全专项矩阵：`523 passed`。
- 最终完整自动化：`1362 passed, 4 skipped`，并通过 `git diff --check`。
- `uv build` 成功生成 sdist/wheel；干净 Python 3.12 venv 从 wheel 导入 `hermes_feishu_card==4.0.10` 与 `event_auth`，console entry point metadata 正确。
- 真实 Hermes `v2026.7.7.2` 通过官方 `install` 路径把 Gateway venv 从 4.0.9 升级到 4.0.10；`doctor` 的 runtime/import/install/recovery state 全部一致。
- 已认证 Feishu user 发起唯一 transport smoke；sidecar 收到并应用 3/3 个事件，1 次 send 与 2 次 update 全部成功，`events_rejected=0`、`event_auth_rejections=0`、投递失败为 0。客户端侧存在 1 张完成 interactive card，匹配的原生 app text duplicate 为 0。
- GitHub `tests` 与 `release-assets` workflow 全部成功；annotated tag 正确指向 release commit，四个公开资产上传完成并逐项通过 SHA-256 checksums。
- 公共 `v4.0.10` tagged installer fixture 从 Git tag `e464316` 安装到独立 `site-packages`，runtime/import/install/recovery state 一致。真实 Gateway 与 sidecar 随后也强制切换到同一公共 tag；最终 Feishu smoke 为 1 张完成卡、0 张目标运行卡、0 条原生重复文本，3/3 事件应用且发送/更新/鉴权拒绝均无异常。

## Release assets

- `hermes-feishu-card-v4.0.10-macos.tar.gz`
- `hermes-feishu-card-v4.0.10-linux.tar.gz`
- `hermes-feishu-card-v4.0.10-windows.zip`
- `hermes-feishu-card-v4.0.10-checksums.txt`
