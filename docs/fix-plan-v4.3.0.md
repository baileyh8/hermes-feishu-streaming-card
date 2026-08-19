# v4.3.0 已知问题与修复方案

> 记录 v4.3.0 在 `enable`（persistent systemd service）路径暴露的 3 个 bug，以及对应修复方案。
> 发现场景：Hermes Agent v0.20.4（v2026.8.18）上从 4.0.20 升级到 4.3.0，`enable` 反复失败后定位。
> 补丁与单测已落地（2026-08-19），下方"修复后验证清单"记录了实际验证状态。

## Bug 1 — Python 身份格式校验与产出不一致

- **位置**：`hermes_feishu_card/persistent_service.py:295-305`（校验）↔ `hermes_feishu_card/server.py:7100-7111`（产出）
- **现象**：任何走 `enable` / `persistent_sidecar_matches` 的路径抛 `ValueError: Python identity is invalid`
- **根因**：`python_executable_identity()` 有意返回域分隔前缀 `python-sha256:`（14 字符 + 64 hex = 78 字符），但 `_normalize_inputs` 的格式校验从 `_valid_digest`（persistent_service.py:595-596，用于 unit/manifest 哈希，格式正确勿改）复制而来，写成 `sha256:` + 71 字符
- **修复**（persistent_service.py:295-301，校验改为接受实际产出格式）：

```python
    _hfc_identity_prefix = "python-sha256:"
    if (
        type(expected_python_identity) is not str
        or not expected_python_identity.startswith(_hfc_identity_prefix)
        or len(expected_python_identity) != len(_hfc_identity_prefix) + 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_python_identity[len(_hfc_identity_prefix):]
        )
    ):
        raise ValueError("Python identity is invalid")
```

- **依据**：身份是"域分隔"的（docstring 写明 path-free, domain-separated identity），应改校验而非产出；`_health_matches_expected_identity`（process.py:853）只做等值比较，两侧同函数计算，前缀不影响正确性

## Bug 2 — `WorkingDirectory=` 被错误地加引号

- **位置**：`hermes_feishu_card/persistent_service.py:337`（`_render_unit`）
- **现象**：生成的 unit `Loaded: bad-setting`，`systemd-analyze verify` 报 `WorkingDirectory= path is not absolute`，`enable --now` 失败
- **根因**：用 `_systemd_quote`（persistent_service.py:581，为 ExecStart 参数设计：加引号 + 转义 `\` `"` `%`）渲染 `WorkingDirectory=`，但 systemd 的 `WorkingDirectory=` **不做 shell 词法解析、不接受引号**，引号被当作路径的一部分
- **修复**（persistent_service.py:337，去掉引号；`%` 是 systemd specifier，转义为 `%%` 防展开）：

```python
        f"WorkingDirectory={inputs['state_dir'].replace('%', '%%')}\n"
```

- **依据**：`WorkingDirectory=` 取值是字面路径（仅做 `%` specifier 展开）；`Environment=` / `ExecStart=` 行保留 `_systemd_quote` 不变（那两处支持引号）

## Bug 3 — 健康检查对无 token 的 sidecar 永远失败

- **位置**：`hermes_feishu_card/persistent_service.py:267`（检查）↔ `hermes_feishu_card/server.py:812`（产出）
- **现象**：`enable` 创建单元并启动后，健康检查 5 秒超时，触发 rollback 回滚
- **根因**：`_health_matches` 要求 `health.get("process_token_hash") == ""`，但 `server.py:812` 只在 `process_token` 非空时写入该字段；enable 生成的单元**不带 `--token`** → 字段缺失 → `.get()` 返回 `None` → `None == ""` 恒 False。而 `process_token_hash(None)`（process.py:34）的语义正是返回 `""`（无 token）
- **修复**（server.py:811-813，健康端点始终发出该字段）：

```python
    process_token = request.app[PROCESS_TOKEN_KEY]
    response["process_token_hash"] = _full_diagnostic_hash(process_token)
```

- **依据**：`_full_diagnostic_hash("")` / `(None)` 均返回 `""`，无 token 时端点报 `process_token_hash: ""`，与 `_health_matches` 匹配，也与 `_record_matches_health` / `_health_matches_token`（process.py:829/842）对无 token 记录的语义对齐
- **备选（最小改法，不推荐）**：persistent_service.py:267 改为 `in ("", None)`；能解锁 enable 但健康端点契约仍不一致

## 部署侧注意（非代码 bug）

- 符号链接 home（如 `/home/<user>` → `/data00/home/<user>`）会触发 v4.3.0 的 state 目录安全检查（`_state_dir_security_error`）拒绝 enable——属刻意的安全设计，需设 `HERMES_FEISHU_CARD_STATE_DIR` 指向无符号链接路径
- 旧版 sidecar 留下的过期 `sidecar.pid` 会挡 `enable`（`invalid pidfile exists; stop refused`），确认进程死亡后移除即可

## 修复后验证清单

- [x] `python3 -m py_compile hermes_feishu_card/persistent_service.py hermes_feishu_card/server.py`
- [ ] `python3 -m hermes_feishu_card.cli enable --config <config> --hermes-dir <hermes> --yes` → `enable ok`（需 Linux systemd 环境）
- [ ] `systemctl --user is-active hermes-feishu-card-sidecar.service` → `active`（需 Linux systemd 环境）
- [ ] `python3 -m hermes_feishu_card.cli doctor --config <config> --hermes-dir <hermes> --explain` → `compatibility full`、`Install state: installed`（需 Linux systemd 环境）
- [x] 补单测：`_normalize_inputs` 接受 `python-sha256:` 前缀（同步改现有 fixture）；`_render_unit` 输出 `WorkingDirectory=` 不带引号；token-less 健康响应含 `process_token_hash: ""`
- [x] 回归：`tests/unit/test_persistent_service.py`、`tests/integration/test_server.py`、`tests/unit/test_process.py`、`tests/integration/test_cli_process.py`、`tests/integration/test_cli_install.py`、`tests/unit/test_manifest.py` 全绿（`test_private_repair_is_exactly_once_under_concurrent_confirmation` 为既有并发 flaky，与本次改动无关）

