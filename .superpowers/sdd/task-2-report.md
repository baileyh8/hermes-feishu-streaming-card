# Task 2 Report — Docker Installer Script (GREEN)

## Scope
- Files modified:
  - `install-docker.sh`
  - `.superpowers/sdd/task-2-report.md`

## Changes made
- Added `install-docker.sh` for Docker/container runtime installation/update in an existing Hermes instance.
- Script defaults now match the Docker container contract:
  - `HERMES_DIR` 默认 `/opt/hermes`
  - `CONFIG_PATH` 默认 `/opt/data/config.yaml`
  - `ENV_FILE` 默认 `/opt/data/.env`
  - `NO_PROMPT` 默认 `1`
  - `SKIP_START` 默认 `0`
- `detect_python` 仅使用 `HFC_PYTHON` 或 Hermes venv 候选路径，不会回退到系统 `python`/`python3` 或 `PATH`。
  - `"$HERMES_DIR/venv/bin/python"`
  - `"$HERMES_DIR/.venv/bin/python"`
  - `"$HERMES_DIR/gateway/.venv/bin/python"`
  - `"$HERMES_DIR/gateway/venv/bin/python"`
- `.env` 加载支持 `KEY=value` 与引号字符串，仅加载白名单键并忽略注释/空行，不执行 `source`。
- 安装 spec 规则：`VERSION=latest` 不加 tag，其他版本加 `@${VERSION}`；v3.7.0 场景会使用 `@v3.7.0`。
- 执行顺序保证 `doctor --explain` 在 `setup` 前执行。

## Test commands
```bash
.venv/bin/python -m pytest tests/unit/test_install_scripts.py::test_install_docker_sh_declares_container_defaults tests/unit/test_install_scripts.py::test_install_docker_sh_uses_container_defaults_and_hermes_venv tests/unit/test_install_scripts.py::test_install_docker_sh_fails_without_hermes_venv_python tests/unit/test_install_scripts.py::test_install_docker_sh_fails_without_noninteractive_credentials -q
```

```bash
.venv/bin/python -m pytest tests/unit/test_install_scripts.py -q
```

## Notes
- 已补充 `tests/unit/test_install_scripts.py` 回归用例，覆盖 `.env` 中 `FEISHU_APP_SECRET` 含反斜杠字面值的场景（如 `docker\\secret`、`abc\ndef`），确保与 `printf '%b'` 语义分离。

## Follow-up Update (Task 2 Review Fix)
- 已修复 reviewer 指出的问题：`install-docker.sh` 在 `HFC_VERSION=latest` 时不再请求 GitHub releases，也不再拼接 `@tag`。
- 新规则改为：
  - `latest` => `git+https://github.com/baileyh8/hermes-feishu-streaming-card.git`
  - 非 `latest` => 按 `@${VERSION}` 拼装（包含 `main`, `v3.7.0` 等）
- 已补充回归测试：`tests/unit/test_install_scripts.py::test_install_docker_sh_uses_latest_without_pin`，使用 fake Hermes venv python，断言 `pip install` spec 无 tag。
- 已执行测试命令：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_install_scripts.py -q
  ```
- `v3.7.0` 场景测试继续保留对 `@v3.7.0` 的断言，行为未变。

## Follow-up Update (Task 2 Review Finding #2)
- 已修复 reviewer 指出的重要问题：`install-docker.sh` 的 `detect_python` 只使用 `python` 而未覆盖 Hermes venv 场景中的 `python3`。  
- 已补齐候选项顺序如下：
  - `$HERMES_DIR/venv/bin/python`
  - `$HERMES_DIR/venv/bin/python3`
  - `$HERMES_DIR/.venv/bin/python`
  - `$HERMES_DIR/.venv/bin/python3`
  - 保留 gateway 变体：`$HERMES_DIR/gateway/.venv/bin/python`、`$HERMES_DIR/gateway/venv/bin/python`
- 已删除脚本中未使用的 `have()` 死代码函数。
- 新增测试：`tests/unit/test_install_scripts.py::test_install_docker_sh_prefers_hermes_venv_python3`  
  - 场景为 Hermes 下仅存在 `venv/bin/python3`，不提供 `venv/bin/python`；
  - 同时注入 `PYTHON` 与 `PATH` 级别的系统 python 哨兵（`fake-system`）；
  - 断言脚本成功运行并未调用系统 python 哨兵（系统日志文件不存在）。
- 已执行测试命令并通过：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_install_scripts.py -q
  ```
