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
- 未改动 `tests/unit/test_install_scripts.py`，因为当前实现已覆盖其断言目标。
