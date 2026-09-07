# 单进程多 profile 与共享 sidecar

V4.4.1 支持 Hermes Gateway 的 `multiplex_profiles` 模式。一个 sidecar 监听一个 `/events` 地址，`profiles:` 的 key 必须与 Hermes 实际 profile 名称相同，不要求包含 `default`。

```yaml
server:
  host: 127.0.0.1
  port: 8765
profiles:
  ai-secretary:
    feishu:
      app_id: "cli_example_secretary"
      app_secret: "replace-locally"
  engineering:
    feishu:
      app_id: "cli_example_engineering"
      app_secret: "replace-locally"
```

在本地配置中填入对应应用凭据，不要把凭据提交到 GitHub。多 profile 配置不会从顶层 `feishu` 或全局 `FEISHU_APP_SECRET` 补充凭据。

```bash
python -m hermes_feishu_card.cli setup \
  --hermes-dir ~/.hermes/hermes-agent \
  --config ~/.hermes/feishu-card.yaml \
  --event-url http://127.0.0.1:8765/events --yes
```

省略 `--profile-id` 时，setup 可以选择首个配置项完成安装诊断，而不会把这个选择写成所有消息的固定身份。旧配置残留的 `default` 若不在 `profiles:` 中，也按该方式处理。Hermes multiplex 运行时使用每条消息的 profile 或该任务的 Hermes home 上下文；不会让进程全局的 profile 覆盖多个并发任务。

多个独立 Hermes 进程也可以共享这一 sidecar。此时可以为各进程显式设置 `--profile-id ai-secretary` 和独立 `--env-file`，已有明确环境绑定继续保留。所有进程使用同一个 sidecar `/events` 地址，不需要各启动一个占用相同端口的 sidecar。

HFC 的 `/events` 端口与飞书事件订阅、卡片回调端口是不同职责。共享 HFC 不会自动合并多个独立 Hermes HTTP webhook server；若选择多进程 Hermes，仍需在 Hermes 配置中安排各自监听端口，或使用其支持的 WebSocket 接收方式。

启动后检查 `status` 和 `doctor --explain` 的 profile 路由信息。未知 profile 会拒绝路由，不会投递到另一项配置。排查时提供 profile 名、脱敏配置和诊断输出即可；不要提供 App Secret、token 或真实 chat id。

本轮已验证并发任务身份、无 `default` 配置、显式多进程绑定和未知 profile 拒绝；真实飞书 multiplex 消息验收仍需在部署环境执行。

## 不出卡片时按实际请求阶段定位

`events_received: 0` 与 `runtime_heartbeat_missing` **不能证明 hook 没有执行**。
消息 hook 在发送 `/events` 前先查询 `/delivery/policy`；查询认证失败、超时或返回
`native` 都会保留 Hermes 原生回复。runtime heartbeat 是独立的认证请求：worker
已经启动但读不到 transport key 时，仍然不会发送 `runtime.hello`。

V4.4.1 的逐消息 profile 修复与实际 Gateway 进程的安装、环境和认证状态是不同证据。
不能只凭之前反馈中的版本号断言用户仍运行 V4.4.0，也不能凭本地回归通过认定其
远端 V4.4.1 已恢复。V4.4.2 修复 named-only adapter 漏装和选择错误的缺口；真实
multiplex 飞书环境仍须验证。最新 Hermes 将 named bot 放在 `_profile_adapters`，
HFC 同时安装这些 adapter，并通过 Hermes 的 `_adapter_for_source` 保留经过验证的
transport ownership；未知或连接失败的 named profile 不借用 default bot。

### 1. 对准实际 Gateway 解释器和 checkout

以下 `python` 必须替换为 **运行 Gateway 的同一虚拟环境解释器**；配置、Hermes
路径替换为实际路径。在容器内安装过多个 Python 包时，宿主机或另一个 shell 的
`pip show` 不足以证明 Gateway 加载了哪一份包。

```bash
python -c 'import hermes_feishu_card; print(hermes_feishu_card.__version__); print(hermes_feishu_card.__file__)'
python -m hermes_feishu_card.cli doctor --config /actual/feishu-card.yaml --hermes-dir /actual/hermes-agent --explain
python -m hermes_feishu_card.cli status --config /actual/feishu-card.yaml
```

这些检查只读，不要为了诊断先重装、删 key 或手改 Hermes 文件。`doctor` 的源码
hook 完整性不能证明旧 Gateway 进程已重启或已加载当前文件。

### 2. 对比发送一条测试消息前后的计数

从 Gateway 所在容器/网络空间访问实际 sidecar 地址。下面只输出不含消息内容、
chat id、路径和认证材料的状态字段；若使用了其他地址，请相应替换。

```bash
python - <<'PY'
import json
from urllib.request import ProxyHandler, build_opener
opener = build_opener(ProxyHandler({}))
with opener.open("http://127.0.0.1:8765/health", timeout=5) as response:
    health = json.load(response)
metrics = health.get("metrics", {})
keys = (
    "events_received", "events_applied", "events_ignored",
    "policy_queries", "policy_auth_rejections", "policy_invalid_requests",
    "runtime_control_events_received", "runtime_control_events_accepted",
    "runtime_control_auth_rejections", "feishu_send_attempts",
)
print(json.dumps({
    "readiness": {k: health.get("readiness", {}).get(k) for k in
                  ("status", "reason", "runtime_seen", "generation_match")},
    "metrics": {k: metrics.get(k) for k in keys},
}, indent=2))
PY
```

| 消息前后的变化 | 能证明什么 | 下一步 |
|---|---|---|
| `policy_auth_rejections` 增加 | 请求已到 sidecar 的 policy 入口，尚未通过认证；不能再称“hook 从未请求” | 检查共享 state directory、运行用户与 transport key 可读性 |
| `policy_queries` 增加，`events_received` 不增加 | 至少一个 policy 查询已认证；它可能被路由为 native，或后续构建/发送未完成 | 对照 profile 名、native chat policy 和 Gateway hook 日志；此计数本身不能证明返回 card |
| `runtime_control_auth_rejections` 增加 | heartbeat 请求已到达，但认证不匹配 | 检查 Gateway 和 sidecar 是否读同一份受保护的 transport key |
| policy 和 runtime 计数都不增加 | 当前 sidecar 没观察到请求；不能区分未执行、被禁用、地址错误或无法连接 | 检查实际 Gateway 日志、PID、包导入路径、启用开关与 event URL |
| `events_received` 增加而 `events_applied` 不增加 | 已越过“无请求”阶段 | 检查事件 profile、turn/session 身份及 ignored/rejected 原因，不再修改入站端口 |

计数可能来自其他并发消息，应记录前后差值并用单条测试消息控制干扰。

### 3. 核对进程实际环境和共享认证目录

`HERMES_FEISHU_CARD_STATE_DIR` 是 HFC 的进程级认证目录；未设置时默认
`Path.home() / ".hermes_feishu_card"`。它不跟随 Hermes 的 task-local
`get_hermes_home()`，也不是 `HERMES_HOME/profiles/<name>`。Gateway 与 sidecar
使用不同运行用户、不同 HOME 或不同 state directory，可能让 sidecar 健康但
Gateway 读不到其 `operations.transport.key`，同时出现零业务事件和缺失 heartbeat。
这是已用真实回环 HTTP 回归重现的可能原因，**不是对 #83/#266 远端根因的确认**。

在服务管理器或容器配置中核对 Gateway **实际 PID** 与 sidecar 的运行用户、
`HERMES_FEISHU_CARD_STATE_DIR`、`HERMES_FEISHU_CARD_EVENT_URL`、
`HERMES_FEISHU_CARD_ENABLED` 是否符合部署安排。`docker exec env` 只能证明新 shell
的环境，不能代替 s6/wrapper 启动的 Gateway 进程环境；如果 `/proc/<PID>/environ`
不可读，就应记录为“尚未核实”，不要据此推断变量存在或缺失。

只在部署机器本地检查目录/文件所有者、权限和是否可读，不打印 key、不提交 `.env`，
也不要通过放宽目录权限或复制到日志来排障。缺 key 的 control emitter 不会 POST，
所以 `runtime_control_auth_rejections` 也可能为零；错误 key 则会触发认证拒绝。

提交问题时提供上述脱敏前后计数、实际加载版本、doctor 结论、部署方式和已核实的
配置一致性即可。不要粘贴完整进程环境或含凭据的启动命令。

#263 的 `cont-init` 截图还显示后台启动输出被完全丢弃、之后仅修复 `.env` 所有者。
具体可见证据、同 Gateway 用户启动以及显式传递 state/config/env/Hermes 参数的
可执行替代段见 [s6 容器启动样例](docker-s6-startup.md)。
