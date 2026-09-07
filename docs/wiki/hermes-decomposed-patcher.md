# Hermes facade 拆分适配

本页记录针对 Hermes `79445a496c` 源码布局的 Legacy patcher 适配。
源码拆分本身不构成 native hook 能力证明；`MIN_SUPPORTED_VERSION`、固定 tag
的 Hybrid/V3 安装入口与既有 native-hooks/hybrid/legacy-patch 选择规则保持不变。

## 锚点与目标文件

| 文件 | Hook |
| --- | --- |
| `gateway/run_turn.py` | message.started、completion、调用方耗时传递；校验 post-turn `agent:end` 调用 |
| `gateway/run_turn_runner.py` | stable tool lifecycle、answer/thinking delta、status、clarify、approval |
| `gateway/run_inbound.py` | command adapter 与 `_quick_key` 前的 HFC command |
| `gateway/run_busy.py` | slash confirm |
| `gateway/run_startup.py` | startup adapter 与 ledger redelivery |
| `gateway/run_notifications.py` | platform notice |
| `cron/scheduler_delivery.py` | 媒体提取和过滤之后的 cron completion |
| `gateway/platforms/base.py` | exact final delivery 与无独立正文的 terminal |

`gateway/run.py` 和 `cron/scheduler.py` facade 也参与 ownership 证据。
检测只把实际可注入的定义视为 hook 锚点；cron 定义仍在 `scheduler.py` 时路由到
该文件，重复定义拒绝安装。doctor 的 `anchor_locations` 显示实际命中文件。
`status_callback` 仍是 optional，缺失时支持安装并显示 partial。

TurnRunner 的 `stream_delta_cb` / `interim_assistant_cb` 使用 `ctx` 传递本轮 source、
message ID、Gateway loop 和 session；approval 保留空选择时的原生回退。
completion 从调用方复制 `_turn_seconds`，不修改原始 `agent_result` 对象。

## Exact Base 边界

必须同时验证 `_process_message_background`、`_extract_response_content`、
`_send_final_text`、`_record_delivery_obligation` 与 `_finalize_delivery_obligation`
的调用参数、提取/过滤顺序和 ledger 契约。不能仅按函数名接受新的上游实现。

提取后的 images/local_files/media_files 通过 task-local completion stage 传给
final-send helper；ledger 仍由 Hermes 在 terminal hook 前记录并标记 attempting。
sidecar 明确接受后才用成功代理抑制原生正文，原生 ledger finalize 和附件路径继续执行。
带附件和纯媒体 terminal 不授予 text-only native ACK。缺少提取证据时清理 stage，
回退原生发送；不猜测附件为空。

## Ownership 与恢复

拆分布局使用 `manifest_version=4`、`layout=gateway-decomposed-v1`，每个受管源码文件
独立记录原始/注入 SHA-256 与同目录 backup。安装前先完成整个集合的内存渲染、编译和
逐字可逆检查，再复用 recovery 的目录绑定、原子替换与失败回滚事务。
源码集合、backup、manifest、版本文件和 Git HEAD/packed refs 证据参与 fingerprint。

重复安装不改源码。restore/uninstall 验证全部 ownership 后逐字还原源码并清理
backup/manifest；恢复锁文件遵循原有 recovery 行为保留。任意受管文件的用户编辑、
损坏 marker、缺失 backup、路径越界或 symlink 均拒绝覆盖。
已知原始文件替换了 owned hook 时可 repair，`--no-repair` 阻止自动修复。
兼容的无 marker 源码更新需要显式 `--accept-hermes-upgrade`。

Hermes 升级到拆分布局后，旧 V1/V2 manifest 与 backup 可能保留，而源码中的 hook
已被 updater/autostash 移走。doctor/status 会提示需要显式执行：

```bash
hermes-feishu-card install --hermes-dir /path/to/hermes-agent --accept-hermes-upgrade --yes
hermes gateway start
```

迁移逐一验证旧路径、backup hash、已有 hook 的 patched hash 和可逆性。只有当前受管
文件完全匹配旧 ownership，或是不含 HFC marker 的新源码，且新布局整体可编译、可逆时
才允许迁移；新 manifest/backup 记录升级后的源码，卸载不会把旧 monolithic Gateway
覆盖回新版 facade。`--no-repair` 仍会拒绝迁移。未知 backup、用户修改的 hook、缺失或
损坏 ownership 均拒绝。仅凭没有 marker 不足以自动修复，仍需显式接受源码升级。

HFC renderer 升级时，已安装文件先以 manifest 的 patched hash 和 backup 的 original
hash 确认 ownership，再更新 hook 与 manifest；restore 不依赖新 renderer 能重建旧 hook。
Windows 显式 install 复用既有 portable writer，验证目录、文件 identity 与内容 hash，
失败时保留无法安全删除的新建证据。自动 recovery 与删除 ownership backup 的 restore
仍要求目录句柄能力，在缺少该能力的平台会明确拒绝。

固定 tag V3 还拥有 Hermes plugin 配置，必须先用其专用 installer 验证/还原，不能用
V1/V2 的源码迁移方式跳过配置 ownership。
`integrity.mode=safe` 的 Git provenance 自动修复仍要求原有严格证据；当前 V4 没有
这份 provenance，必须拒绝自动升级修复，不能把普通多文件 fingerprint 当成授权。

## 验证与后续维护

`tests/fixtures/hermes_decomposed/` 包含 8 个注入目标和两个 facade，无凭据、无运行环境。
`tests/unit/test_decomposed_patcher.py` 验证 CLI install/uninstall、重复安装、LF/CRLF
及无末尾换行的逐字往返、事务回滚、拒绝漂移、版本复核与生成 hook 的实际执行。
这些测试不代替真实 Feishu/Lark smoke；模拟 CLI 测试仅替换 runtime package/SDK provisioning。

```bash
python -m pytest tests/unit/test_patcher.py tests/unit/test_decomposed_patcher.py tests/unit/test_installer_detection.py tests/integration/test_cli_install.py -q
python -m pytest -q
git diff --check
```

此适配吸收 PR #257 的拆分布局实现；维护时应继续比较上游锚点、renderer 和 manifest
迁移行为。上游或用户的独立 timeout 设置不属于该补丁的 ownership。
