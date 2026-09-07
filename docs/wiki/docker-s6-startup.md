# s6 容器：以 Gateway 用户启动 HFC

## #263 中实际可见的启动方式

[#263](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/263) 的启动脚本截图
显示 `cont-init.d/03-hfc-setup` 使用 `/opt/hermes/.venv/bin/hermes-feishu-card`，
声明了 `/opt/data/.env`，但启动时只传 `--config`：

```sh
"$HFC_BIN" start --config "$CONFIG_PATH" >/dev/null 2>&1 &
```

紧接着脚本对 `.env` 执行 `chown hermes:hermes`，注释说明 Gateway 以 `hermes`
运行。这是应核对 sidecar/Gateway 运行身份的具体线索；截图本身没有给出两个实际
进程的 UID、环境或 transport key，所以还不能直接认定远端认证错配。

该片段还有两个可确认的问题：CLI 的启动失败和诊断输出全部丢弃；输出的 `$!`
是后台启动命令的 PID，不是经过验证的 sidecar PID。只修 `.env` 所有者也不会
自动解决 HFC state directory 中 `operations.transport.key` 的访问问题。

## 可替换的启动样例

此样例用于**已通过 HFC 安装器完成安装**、Gateway 用户名为 `hermes`、Hermes
checkout 为 `/opt/hermes` 的 s6 容器。先将路径对应到实际部署；不要在每次启动时
重新 patch Hermes。Gateway 与下面的启动器应继承相同的部署环境：

```yaml
environment:
  HERMES_HOME: /opt/data
  HERMES_FEISHU_CARD_EVENT_URL: http://127.0.0.1:8765/events
  HERMES_FEISHU_CARD_STATE_DIR: /opt/data/hfc-state
  HERMES_FEISHU_CARD_SERVICE_MANAGER: detached
```

这四项应来自容器共同环境或 Gateway 的服务环境，不要仅在 `cont-init` 子进程里
`export` 后假定另一个 s6 服务会继承。`HERMES_HOME` 是 Hermes 运行数据路径；
`--hermes-dir` 是源码 checkout，两个参数不能互换。

以下内容可以替换截图中的“启动 sidecar”段。要求镜像已经提供 `s6-setuidgid`，
并且配置与显式 env file 对 `hermes` 可读。state directory 应在部署/安装时以
`hermes` 所有者及 `0700` 权限创建；现有目录必须先核对归属，不要递归改权限或
删除旧 key。

```sh
#!/bin/sh
set -eu

# Fail visibly if the image lacks the expected account or s6 helper.
id hermes >/dev/null
command -v s6-setuidgid >/dev/null
: "${HERMES_FEISHU_CARD_STATE_DIR:?set the shared HFC state directory}"
: "${HERMES_FEISHU_CARD_EVENT_URL:?set the shared HFC event URL}"
: "${HERMES_HOME:?set the Gateway runtime home}"

s6-setuidgid hermes /bin/sh -eu <<'HFC_START'
HFC_PYTHON=/opt/hermes/.venv/bin/python
HFC_CONFIG=/opt/data/config.yaml
HFC_ENV_FILE=/opt/data/.env
HFC_HERMES_DIR=/opt/hermes

[ -x "$HFC_PYTHON" ]
[ -r "$HFC_CONFIG" ]
[ -r "$HFC_ENV_FILE" ]
export HERMES_FEISHU_CARD_SERVICE_MANAGER=detached

# Check ownership/access without printing, copying, or weakening the key.
"$HFC_PYTHON" - <<'HFC_CHECK'
import os
import stat
from pathlib import Path
from hermes_feishu_card.operations_transport import read_transport_root_secret
root = Path(os.environ["HERMES_FEISHU_CARD_STATE_DIR"])
entry = root.lstat()
if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
    raise SystemExit("HFC state directory must be a private directory owned by the Gateway user")
if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
    raise SystemExit("Gateway user cannot access the HFC state directory")
key = root / "operations.transport.key"
if (key.exists() or key.is_symlink()) and read_transport_root_secret(root) is None:
    raise SystemExit("Existing HFC transport key is not valid/readable for the Gateway user")
HFC_CHECK

# start itself creates the detached runner. Wait for its actual exit code;
# keep stdout/stderr in the container logs instead of reporting a shell $! PID.
"$HFC_PYTHON" -m hermes_feishu_card.cli start \
  --config "$HFC_CONFIG" \
  --env-file "$HFC_ENV_FILE" \
  --hermes-dir "$HFC_HERMES_DIR" \
  --hermes-home "$HERMES_HOME"
"$HFC_PYTHON" -m hermes_feishu_card.cli status \
  --config "$HFC_CONFIG" \
  --env-file "$HFC_ENV_FILE" \
  --hermes-dir "$HFC_HERMES_DIR" \
  --hermes-home "$HERMES_HOME"
HFC_START
```

`start` 会等待自身启动检查后返回，并不在这里前台永久运行 sidecar。该样例选择
启动失败即非零退出，以便 s6/容器日志保留错误；若部署要求 Gateway 在 HFC 启动
失败时仍上线，应由服务编排显式实现这项策略，并保留错误及告警，不能把全部输出
重定向到 `/dev/null` 后报告成功。s6 不同版本对 `cont-init` 失败的处理存在区别，
应按实际镜像的启动策略接入。

若旧 sidecar 已在其他 UID/state directory 下占用端口，应先通过其原所有者和原
state directory 的 `status` 核对 PID/token/health，再按受管 `stop` 流程停服。
不要只换到一个新目录后继续启动第二个实例，也不要删 key 来躲过认证失败。

## 验收边界

1. 在容器中确认 HFC 与 Gateway 实际 UID 一致，使用同一 state directory；
   启动器成功并不代替 Gateway 进程环境核对。
2. Gateway 启动后，确认 `runtime_seen=true` 且 generation 匹配；如果只有 sidecar
   `healthy` 而缺 heartbeat，不能算通过。
3. 向 named profile bot 发送一条消息，按照[分阶段诊断](shared-profile-routing.md#不出卡片时按实际请求阶段定位)
   对比 policy、事件及认证拒绝计数，验证卡片真实创建和更新。

本样例已完成 shell 语法及 HFC CLI 参数校验；本仓库尚未用 #263 的实际 s6 镜像
执行启动，不能把文档样例视为远端部署修复已经完成。
