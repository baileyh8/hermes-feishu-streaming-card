# Docker Install Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v3.7.0 with a Docker-friendly install/update path for existing Hermes containers and close issue #70.

**Architecture:** Add a separate `install-docker.sh` so ordinary macOS/Linux `install.sh` remains simple. The Docker script runs inside an existing Hermes container, selects the Hermes venv Python, installs/upgrades the package there, runs `doctor --explain`, then runs `setup`. Documentation and release packaging expose the script plus a non-official `docker-compose.example.yml`.

**Tech Stack:** Bash, Python pytest, GitHub Actions release packaging, Markdown docs, existing `hermes_feishu_card.cli setup/install/doctor`.

## Global Constraints

- Default Docker Hermes root is `/opt/hermes`.
- Default Docker config path is `/opt/data/config.yaml`.
- Default Docker env file is `/opt/data/.env`.
- Docker installer must not silently fall back to system `python`; only `HFC_PYTHON` may override venv discovery.
- Docker installer is for existing Hermes images/containers; this release does not publish an official Docker image.
- Tests must not require Docker daemon access.
- Version for this release is `v3.7.0`.

---

## File Structure

- Create `install-docker.sh`: Docker-specific one-shot install/update script.
- Create `docker-compose.example.yml`: example only, with a sample image name and `/opt/hermes` plus `/opt/data` mounts.
- Modify `.github/workflows/release-assets.yml`: include Docker installer and Compose example in release packages.
- Modify `tests/unit/test_install_scripts.py`: add script behavior tests using fake Hermes venv Python.
- Modify `tests/unit/test_ci_workflow.py`: assert release workflow packages Docker files.
- Modify `tests/unit/test_docs.py`: assert Docker docs and v3.7.0 release notes are linked.
- Modify `tests/unit/test_package_metadata.py`, `pyproject.toml`, `hermes_feishu_card/__init__.py`: bump version to `3.7.0`.
- Modify `README.md`, `README.en.md`, `README-install.md`, `CHANGELOG.md`, `docs/release-readiness.md`, `docs/release-readiness.en.md`, `TODO.md`.
- Create `docs/release-notes-v3.7.0.md`.

---

### Task 1: Docker Installer Behavior Tests

**Files:**
- Modify: `tests/unit/test_install_scripts.py`
- Test: `tests/unit/test_install_scripts.py`

**Interfaces:**
- Consumes: none.
- Produces: failing tests that define `install-docker.sh` behavior.

- [ ] **Step 1: Write failing tests**

Append these helpers and tests to `tests/unit/test_install_scripts.py`:

```python
def make_fake_docker_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_PYTHON_LOG"
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "ensurepip" ]; then
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "hermes_feishu_card.cli" ]; then
  if [ "${FEISHU_APP_ID:-}" != "cli_docker" ]; then
    echo "FEISHU_APP_ID was not loaded" >&2
    exit 5
  fi
  if [ "${FEISHU_APP_SECRET:-}" != "docker_secret" ]; then
    echo "FEISHU_APP_SECRET was not loaded" >&2
    exit 6
  fi
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_install_docker_sh_uses_container_defaults_and_hermes_venv(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
    env_file = data_dir / ".env"
    data_dir.mkdir(parents=True)
    env_file.write_text(
        "FEISHU_APP_ID=cli_docker\\nFEISHU_APP_SECRET=docker_secret\\n",
        encoding="utf-8",
    )
    runtime_python = make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")

    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(env_file),
            "HFC_VERSION": "main",
            "HFC_SKIP_START": "1",
        }
    )
    env.pop("PYTHON", None)
    env.pop("HFC_PYTHON", None)
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert str(runtime_python) in result.stdout
    assert "-m pip install --upgrade git+https://github.com/baileyh8/hermes-feishu-streaming-card.git" in log
    assert f"hermes_feishu_card.cli doctor --config {data_dir / 'config.yaml'} --hermes-dir {hermes_dir} --explain" in log
    assert f"hermes_feishu_card.cli setup --hermes-dir {hermes_dir} --config {data_dir / 'config.yaml'} --yes --skip-start" in log


def test_install_docker_sh_fails_without_hermes_venv_python(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
    data_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "FEISHU_APP_ID": "cli_docker",
            "FEISHU_APP_SECRET": "docker_secret",
            "HFC_VERSION": "main",
        }
    )
    env.pop("HFC_PYTHON", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Hermes venv Python was not found" in result.stderr
    assert "HFC_PYTHON" in result.stderr


def test_install_docker_sh_fails_without_noninteractive_credentials(tmp_path):
    hermes_dir = tmp_path / "opt" / "hermes"
    data_dir = tmp_path / "opt" / "data"
    (hermes_dir / "gateway").mkdir(parents=True)
    (hermes_dir / "gateway" / "run.py").write_text("# gateway\\n", encoding="utf-8")
    make_fake_docker_python(hermes_dir / "venv" / "bin" / "python")
    data_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "HERMES_DIR": str(hermes_dir),
            "HFC_CONFIG": str(data_dir / "config.yaml"),
            "HFC_ENV_FILE": str(data_dir / ".env"),
            "HFC_VERSION": "main",
        }
    )
    env.pop("FEISHU_APP_ID", None)
    env.pop("FEISHU_APP_SECRET", None)

    result = subprocess.run(
        ["bash", "install-docker.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FEISHU_APP_ID/FEISHU_APP_SECRET are missing" in result.stderr
    assert "/opt/data/.env" in result.stderr or str(data_dir / ".env") in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_install_scripts.py::test_install_docker_sh_uses_container_defaults_and_hermes_venv tests/unit/test_install_scripts.py::test_install_docker_sh_fails_without_hermes_venv_python tests/unit/test_install_scripts.py::test_install_docker_sh_fails_without_noninteractive_credentials -q
```

Expected: FAIL because `install-docker.sh` does not exist.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/unit/test_install_scripts.py
git commit -m "test: cover Docker installer behavior"
```

---

### Task 2: Docker Installer Script

**Files:**
- Create: `install-docker.sh`
- Test: `tests/unit/test_install_scripts.py`

**Interfaces:**
- Consumes: tests from Task 1.
- Produces: executable `install-docker.sh`.

- [ ] **Step 1: Implement `install-docker.sh`**

Create `install-docker.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="${HFC_REPO:-baileyh8/hermes-feishu-streaming-card}"
VERSION="${HFC_VERSION:-latest}"
HERMES_DIR="${HERMES_DIR:-/opt/hermes}"
CONFIG_PATH="${HFC_CONFIG:-/opt/data/config.yaml}"
ENV_FILE="${HFC_ENV_FILE:-/opt/data/.env}"
NO_PROMPT="${HFC_NO_PROMPT:-1}"
SKIP_START="${HFC_SKIP_START:-0}"

log() {
  printf '[hermes-feishu-card:docker] %s\n' "$*"
}

fail() {
  printf '[hermes-feishu-card:docker] error: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

resolve_version() {
  if [ "$VERSION" != "latest" ]; then
    printf '%s\n' "$VERSION"
    return
  fi
  if have curl; then
    local tag
    tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
      | head -n 1 || true)"
    if [ -n "$tag" ]; then
      printf '%s\n' "$tag"
      return
    fi
  fi
  printf 'main\n'
}

load_env_file() {
  [ -f "$ENV_FILE" ] || return 0
  log "loading credentials from $ENV_FILE"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
      export\ *) line="${line#export }" ;;
    esac
    case "$line" in
      FEISHU_APP_ID=*|FEISHU_APP_SECRET=*|FEISHU_CONNECTION_MODE=*|FEISHU_HOME_CHANNEL=*|HERMES_FEISHU_CARD_HOST=*|HERMES_FEISHU_CARD_PORT=*)
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        case "$value" in
          \"*\") value="${value#\"}"; value="${value%\"}" ;;
          \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        export "$key=$value"
        ;;
    esac
  done < "$ENV_FILE"
}

detect_python() {
  if [ -n "${HFC_PYTHON:-}" ]; then
    [ -x "$HFC_PYTHON" ] || fail "HFC_PYTHON is not executable: $HFC_PYTHON"
    printf '%s\n' "$HFC_PYTHON"
    return
  fi
  local candidates=(
    "$HERMES_DIR/venv/bin/python"
    "$HERMES_DIR/venv/bin/python3"
    "$HERMES_DIR/.venv/bin/python"
    "$HERMES_DIR/.venv/bin/python3"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  fail "Hermes venv Python was not found. Checked: ${candidates[*]}. Set HFC_PYTHON to the Python used by Hermes inside the container."
}

validate_paths() {
  [ -d "$HERMES_DIR" ] || fail "Hermes root does not exist: $HERMES_DIR. Mount Hermes at /opt/hermes or set HERMES_DIR."
  [ -f "$HERMES_DIR/gateway/run.py" ] || fail "gateway/run.py missing under $HERMES_DIR. Verify the Docker mount or set HERMES_DIR."
  mkdir -p "$(dirname "$CONFIG_PATH")"
  [ -w "$(dirname "$CONFIG_PATH")" ] || fail "$(dirname "$CONFIG_PATH") is not writable. Check Docker volume ownership/root permissions for /opt/data."
}

require_credentials() {
  if [ -n "${FEISHU_APP_ID:-}" ] && [ -n "${FEISHU_APP_SECRET:-}" ]; then
    return 0
  fi
  if [ "$NO_PROMPT" = "1" ] || [ ! -t 0 ]; then
    fail "FEISHU_APP_ID/FEISHU_APP_SECRET are missing. Set them as environment variables or write them to $ENV_FILE."
  fi
  fail "Interactive credential prompts are not supported by install-docker.sh. Set FEISHU_APP_ID and FEISHU_APP_SECRET."
}

install_package() {
  local python_bin="$1"
  "$python_bin" -m pip --version >/dev/null 2>&1 || "$python_bin" -m ensurepip --upgrade >/dev/null
  local tag
  tag="$(resolve_version)"
  local spec="git+https://github.com/$REPO.git"
  if [ -n "$tag" ] && [ "$tag" != "main" ]; then
    spec="$spec@$tag"
  fi
  export HFC_INSTALL_SPEC="$spec"
  log "installing $REPO@$tag into $python_bin"
  "$python_bin" -m pip install --upgrade "$spec"
}

run_doctor() {
  local python_bin="$1"
  log "running doctor"
  "$python_bin" -m hermes_feishu_card.cli doctor \
    --config "$CONFIG_PATH" \
    --hermes-dir "$HERMES_DIR" \
    --explain
}

run_setup() {
  local python_bin="$1"
  local setup_args=(
    -m hermes_feishu_card.cli setup
    --hermes-dir "$HERMES_DIR"
    --config "$CONFIG_PATH"
    --yes
  )
  if [ "$SKIP_START" = "1" ]; then
    setup_args+=(--skip-start)
  fi
  log "running setup"
  "$python_bin" "${setup_args[@]}"
}

main() {
  HERMES_DIR="$(expand_path "$HERMES_DIR")"
  CONFIG_PATH="$(expand_path "$CONFIG_PATH")"
  ENV_FILE="$(expand_path "$ENV_FILE")"

  validate_paths
  load_env_file
  require_credentials
  local python_bin
  python_bin="$(detect_python)"
  log "using Hermes Python: $python_bin"
  install_package "$python_bin"
  run_doctor "$python_bin"
  run_setup "$python_bin"
  log "done"
  log "status: $python_bin -m hermes_feishu_card.cli status --config \"$CONFIG_PATH\""
  log "doctor: $python_bin -m hermes_feishu_card.cli doctor --config \"$CONFIG_PATH\" --hermes-dir \"$HERMES_DIR\" --explain"
}

main "$@"
```

- [ ] **Step 2: Make the script executable**

Run:

```bash
chmod +x install-docker.sh
```

- [ ] **Step 3: Run installer tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_install_scripts.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit script**

```bash
git add install-docker.sh tests/unit/test_install_scripts.py
git commit -m "feat: add Docker installer script"
```

---

### Task 3: Compose Example and Release Packaging

**Files:**
- Create: `docker-compose.example.yml`
- Modify: `.github/workflows/release-assets.yml`
- Modify: `tests/unit/test_ci_workflow.py`

**Interfaces:**
- Consumes: `install-docker.sh`.
- Produces: Docker files included in release packages.

- [ ] **Step 1: Write failing release workflow test**

Extend `test_release_assets_workflow_supports_manual_package_dry_run()` in `tests/unit/test_ci_workflow.py`:

```python
    assert "install-docker.sh" in text
    assert "docker-compose.example.yml" in text
```

Add a Compose file test:

```python
def test_docker_compose_example_documents_container_paths():
    compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")

    assert "image: your-hermes-image:latest" in compose
    assert "/opt/hermes" in compose
    assert "/opt/data" in compose
    assert "FEISHU_APP_ID" in compose
    assert "FEISHU_APP_SECRET" in compose
    assert "install-docker.sh" in compose
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_ci_workflow.py::test_release_assets_workflow_supports_manual_package_dry_run tests/unit/test_ci_workflow.py::test_docker_compose_example_documents_container_paths -q
```

Expected: FAIL because release workflow and Compose example do not mention Docker files yet.

- [ ] **Step 3: Create `docker-compose.example.yml`**

```yaml
# Example only: this project does not publish an official Hermes Docker image.
services:
  hermes:
    image: your-hermes-image:latest
    container_name: hermes-feishu-card-example
    working_dir: /opt/hermes
    environment:
      HERMES_DIR: /opt/hermes
      HFC_CONFIG: /opt/data/config.yaml
      HFC_ENV_FILE: /opt/data/.env
      HFC_NO_PROMPT: "1"
      HFC_SKIP_START: "1"
      FEISHU_APP_ID: "${FEISHU_APP_ID}"
      FEISHU_APP_SECRET: "${FEISHU_APP_SECRET}"
      FEISHU_CONNECTION_MODE: "${FEISHU_CONNECTION_MODE:-websocket}"
      FEISHU_HOME_CHANNEL: "${FEISHU_HOME_CHANNEL:-}"
    volumes:
      - hermes-runtime:/opt/hermes
      - hermes-data:/opt/data
      - ./install-docker.sh:/tmp/install-docker.sh:ro
    command: ["bash", "/tmp/install-docker.sh"]

volumes:
  hermes-runtime:
  hermes-data:
```

- [ ] **Step 4: Update release assets workflow**

In `.github/workflows/release-assets.yml`, change package copy lines to:

```yaml
          cp install.sh install-docker.sh docker-compose.example.yml "${COMMON_FILES[@]}" pkg/macos/
          cp install.sh install-docker.sh docker-compose.example.yml "${COMMON_FILES[@]}" pkg/linux/
          cp install.ps1 install-docker.sh docker-compose.example.yml "${COMMON_FILES[@]}" pkg/windows/
```

- [ ] **Step 5: Run packaging tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_ci_workflow.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Compose and packaging**

```bash
git add docker-compose.example.yml .github/workflows/release-assets.yml tests/unit/test_ci_workflow.py
git commit -m "feat: package Docker install examples"
```

---

### Task 4: Documentation, Version, and Release Notes

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `README-install.md`
- Modify: `CHANGELOG.md`
- Modify: `TODO.md`
- Modify: `docs/release-readiness.md`
- Modify: `docs/release-readiness.en.md`
- Create: `docs/release-notes-v3.7.0.md`
- Modify: `hermes_feishu_card/__init__.py`
- Modify: `pyproject.toml`
- Modify: `tests/unit/test_docs.py`
- Modify: `tests/unit/test_package_metadata.py`

**Interfaces:**
- Consumes: Docker files from Tasks 2 and 3.
- Produces: v3.7.0 metadata and docs.

- [ ] **Step 1: Write failing docs/version tests**

Update `tests/unit/test_package_metadata.py`:

```python
def test_package_has_version():
    assert __version__ == "3.7.0"
```

and:

```python
    assert 'version = "3.7.0"' in pyproject
```

Extend `test_readme_documents_one_line_install_and_release_packages()` in `tests/unit/test_docs.py`:

```python
    assert "install-docker.sh" in readme
    assert "docker-compose.example.yml" in readme
    assert "Docker" in install_doc
    assert "v3.7.0" in install_doc
    assert (ROOT / "install-docker.sh").exists()
    assert (ROOT / "docker-compose.example.yml").exists()
    assert (ROOT / "docs/release-notes-v3.7.0.md").exists()
```

Add:

```python
def test_changelog_documents_v370_release_notes():
    changelog = read_doc("CHANGELOG.md")
    release_notes = read_doc("docs/release-notes-v3.7.0.md")

    assert "## V3.7.0 — 2026-06-29" in changelog
    assert "issue #70" in changelog
    assert "install-docker.sh" in release_notes
    assert "docker-compose.example.yml" in release_notes
    assert "hermes-feishu-card-v3.7.0-linux.tar.gz" in release_notes
```

Update `test_docs_describe_release_readiness_boundaries()`:

```python
    assert "3.7.0" in release_readiness
    assert "install-docker.sh" in release_readiness
```

- [ ] **Step 2: Run docs/version tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_docs.py tests/unit/test_package_metadata.py -q
```

Expected: FAIL because metadata and docs are still v3.6.6 and v3.7.0 notes do not exist.

- [ ] **Step 3: Bump version metadata**

Set `hermes_feishu_card/__init__.py`:

```python
__version__ = "3.7.0"
```

Set `pyproject.toml`:

```toml
version = "3.7.0"
```

- [ ] **Step 4: Add release notes**

Create `docs/release-notes-v3.7.0.md`:

```markdown
# V3.7.0 Release Notes

[中文](release-notes-v3.7.0.md)

V3.7.0 adds Docker deployment adaptation for issue #70.

## What Changed

- Added `install-docker.sh` for running install/update inside existing Hermes containers.
- Added `docker-compose.example.yml` showing `/opt/hermes` and `/opt/data` mounts, Feishu environment variables, and one-shot installer execution.
- The Docker installer defaults to `HERMES_DIR=/opt/hermes`, `HFC_CONFIG=/opt/data/config.yaml`, and `HFC_ENV_FILE=/opt/data/.env`.
- The Docker installer uses Hermes venv Python and does not silently fall back to system `python` / `pip`.
- Release packages now include the Docker installer and Compose example.

## Upgrade

Inside an existing Hermes container:

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v3.7.0
bash install-docker.sh
```

After install:

```bash
/opt/hermes/venv/bin/python -m hermes_feishu_card.cli doctor \
  --config /opt/data/config.yaml \
  --hermes-dir /opt/hermes \
  --explain
```

## Release Assets

GitHub Releases include:

- `hermes-feishu-card-v3.7.0-macos.tar.gz`
- `hermes-feishu-card-v3.7.0-linux.tar.gz`
- `hermes-feishu-card-v3.7.0-windows.zip`
- `hermes-feishu-card-v3.7.0-checksums.txt`

## Verification

- `tests/unit/test_install_scripts.py`
- `tests/unit/test_docs.py`
- `tests/unit/test_ci_workflow.py`
- full pytest suite
```

- [ ] **Step 5: Update docs**

Add a Docker section to `README.md` under install:

```markdown
## Docker 容器内安装 / 更新

如果 Hermes 运行在已有 Docker 容器里，优先使用 `install-docker.sh`。它默认读取：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HERMES_DIR` | `/opt/hermes` | 容器内 Hermes Gateway 目录 |
| `HFC_CONFIG` | `/opt/data/config.yaml` | sidecar 配置路径 |
| `HFC_ENV_FILE` | `/opt/data/.env` | 飞书凭据文件 |
| `HFC_PYTHON` | 自动检测 Hermes venv | 显式指定容器内 Python |

示例：

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v3.7.0
bash install-docker.sh
```

`docker-compose.example.yml` 只是适配示例，不是官方镜像。它展示 `/opt/hermes`、`/opt/data` 挂载和非交互安装方式。
```

Add equivalent English text to `README.en.md`.

Add Docker section to `README-install.md`:

```markdown
## Docker Containers

Use `install-docker.sh` inside an existing Hermes container. It defaults to
`/opt/hermes` for Hermes and `/opt/data/config.yaml` for sidecar config. The
script selects Hermes venv Python and does not fall back to system Python unless
`HFC_PYTHON` is set.
```

Update `CHANGELOG.md`:

```markdown
## V3.7.0 — 2026-06-29

### Added
- issue #70: added `install-docker.sh` for existing Hermes Docker containers with `/opt/hermes`, `/opt/data`, root-owned volume, and Hermes venv Python assumptions.
- Added `docker-compose.example.yml` as a non-official Compose example for bind/volume layout and non-interactive installer execution.
- Release packages now include Docker install assets.

### Tests
- Added Docker installer script tests, Compose example checks, release packaging checks, and docs assertions.
```

Mark the Docker item in `TODO.md` as complete:

```markdown
- [x] **P2 Docker 部署 / issue #70**：V3.7.0 提供 `install-docker.sh`、`docker-compose.example.yml`、容器路径/venv Python/权限诊断和发布包文档。
```

Update release readiness current version and Ready list to mention v3.7.0 and Docker installer assets in Chinese and English.

- [ ] **Step 6: Run docs/version tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_docs.py tests/unit/test_package_metadata.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit docs and version**

```bash
git add README.md README.en.md README-install.md CHANGELOG.md TODO.md docs/release-readiness.md docs/release-readiness.en.md docs/release-notes-v3.7.0.md hermes_feishu_card/__init__.py pyproject.toml tests/unit/test_docs.py tests/unit/test_package_metadata.py
git commit -m "docs: release v3.7.0 Docker install adapter"
```

---

### Task 5: Final Verification, PR, Release, and Issue Reply

**Files:**
- No new source files.
- Uses: all files changed in earlier tasks.

**Interfaces:**
- Consumes: completed v3.7.0 implementation.
- Produces: merged PR, tag, release, issue #70 reply.

- [ ] **Step 1: Run targeted tests**

```bash
.venv/bin/python -m pytest tests/unit/test_install_scripts.py tests/unit/test_docs.py tests/unit/test_ci_workflow.py tests/unit/test_package_metadata.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run diff checks**

```bash
git diff --check
git status --short --branch
```

Expected: no diff-check output; branch contains only intended committed changes.

- [ ] **Step 4: Push branch and create PR**

```bash
git push -u origin codex/v3.7.0-docker-install-adapter
gh pr create \
  --base main \
  --head codex/v3.7.0-docker-install-adapter \
  --title "Add Docker install adapter" \
  --body $'## Summary\n- add install-docker.sh for existing Hermes containers\n- add docker-compose.example.yml and Docker docs\n- include Docker assets in release packages\n- release v3.7.0 metadata and notes\n\nCloses #70\n\n## Tests\n- .venv/bin/python -m pytest -q'
```

Expected: PR URL.

- [ ] **Step 5: Wait for CI and merge**

```bash
gh pr checks --watch --interval 10
gh pr merge --squash --delete-branch
```

Expected: CI passes and PR merges to `main`.

- [ ] **Step 6: Tag and release**

```bash
git switch main
git pull --ff-only
git tag -a v3.7.0 -m "Release v3.7.0"
git push origin v3.7.0
gh release create v3.7.0 --verify-tag --title "v3.7.0" --notes-file docs/release-notes-v3.7.0.md
```

Expected: release URL.

- [ ] **Step 7: Confirm release assets**

```bash
gh run list --workflow release-assets.yml --limit 3
gh release view v3.7.0 --json assets,url
```

Expected assets include:

- `hermes-feishu-card-v3.7.0-macos.tar.gz`
- `hermes-feishu-card-v3.7.0-linux.tar.gz`
- `hermes-feishu-card-v3.7.0-windows.zip`
- `hermes-feishu-card-v3.7.0-checksums.txt`

Download or inspect package listing if needed to confirm `install-docker.sh` and `docker-compose.example.yml` are inside the packages.

- [ ] **Step 8: Reply to issue #70**

```bash
gh issue comment 70 --body $'已在 v3.7.0 发布 Docker 适配版本：\n\n- Release: https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.7.0\n- 新增 `install-docker.sh`：适配容器内 `/opt/hermes`、`/opt/data`、Hermes venv Python、非交互安装/更新。\n- 新增 `docker-compose.example.yml`：展示已有 Hermes 镜像的 volume 挂载、环境变量和一键安装方式；这不是官方镜像。\n\n容器内最小用法：\n\n```bash\nexport FEISHU_APP_ID=cli_xxx\nexport FEISHU_APP_SECRET=xxx\nexport HFC_VERSION=v3.7.0\nbash install-docker.sh\n```\n\n如果目录不是默认值，可以设置 `HERMES_DIR`、`HFC_CONFIG`、`HFC_ENV_FILE` 或 `HFC_PYTHON`。发布包里也包含 Docker 安装脚本和 Compose 示例。'\ngh issue close 70 --comment "v3.7.0 已发布 Docker 适配脚本和 Compose 示例，先关闭；如你的容器路径或权限还有特殊情况，可以重新打开并补充日志。"
```

Expected: issue #70 closed with release guidance.

---

## Self-Review

- Spec coverage: script, Compose example, docs, tests, packaging, v3.7.0 release, and issue #70 reply are covered.
- Incomplete requirement scan: file names, release tasks, commands, and expected outputs are concrete.
- Type consistency: shell environment names are consistent across tasks: `HERMES_DIR`, `HFC_CONFIG`, `HFC_ENV_FILE`, `HFC_PYTHON`, `HFC_VERSION`, `HFC_SKIP_START`, `HFC_NO_PROMPT`.
