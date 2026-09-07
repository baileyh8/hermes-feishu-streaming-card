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
