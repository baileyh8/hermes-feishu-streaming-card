#!/usr/bin/env bash
set -euo pipefail

REPO="${HFC_REPO:-baileyh8/hermes-feishu-streaming-card}"
VERSION="${HFC_VERSION:-}"
HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"
HERMES_HOME_DIR="${HERMES_HOME:-}"
CONFIG_PATH="${HFC_CONFIG:-}"
ENV_FILE="${HFC_ENV_FILE:-}"
PROFILE_ID="${HERMES_FEISHU_CARD_PROFILE_ID:-}"
PROFILE_ID_EXPLICIT=0
EVENT_URL="${HERMES_FEISHU_CARD_EVENT_URL:-}"
NO_REPAIR="${HFC_NO_REPAIR:-}"
PYTHON_BIN="${HFC_PYTHON:-}"
PIP_USER_FLAG="${HFC_PIP_USER-}"
PM_MANAGEMENT_DIR=""
PM_MANAGEMENT_INSPECTOR=""

log() {
  printf '[hermes-feishu-card] %s\n' "$*"
}

fail() {
  printf '[hermes-feishu-card] error: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --config|--env-file|--version|--profile-id|--event-url|--hermes-home)
        [ "$#" -ge 2 ] || fail "$1 requires a value"
        case "$1" in
          --config) CONFIG_PATH="$2" ;;
          --env-file) ENV_FILE="$2" ;;
          --version) VERSION="$2" ;;
          --profile-id) PROFILE_ID="$2"; PROFILE_ID_EXPLICIT=1 ;;
          --event-url) EVENT_URL="$2" ;;
          --hermes-home) HERMES_HOME_DIR="$2" ;;
        esac
        shift 2
        ;;
      --no-repair)
        NO_REPAIR="1"
        shift
        ;;
      *) fail "unknown argument: $1" ;;
    esac
  done
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

quote_env_value() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

validate_explicit_ref() {
  case "$1" in
    ""|/*|*..*|*[!A-Za-z0-9._/-]*)
      fail "invalid explicit version ref; no package was installed"
      ;;
  esac
}

resolve_version() {
  local python_bin="$1"
  if [ "$VERSION" != "latest" ]; then
    validate_explicit_ref "$VERSION"
    printf '%s\n' "$VERSION"
    return
  fi
  have curl || fail "latest release lookup requires curl; no package was installed; retry later or set an explicit vX.Y.Z"

  local response_file
  local http_status
  local tag
  response_file="$(mktemp)"
  if ! http_status="$(curl \
      --silent --show-error --location \
      --connect-timeout 5 --max-time 15 \
      --output "$response_file" \
      --write-out '%{http_code}' \
      "https://api.github.com/repos/$REPO/releases/latest")"; then
    rm -f "$response_file"
    fail "latest release lookup failed; no package was installed; retry later or set an explicit vX.Y.Z"
  fi
  if [ "$http_status" != "200" ]; then
    rm -f "$response_file"
    fail "latest release lookup returned HTTP $http_status; no package was installed; retry later or set an explicit vX.Y.Z"
  fi
  if ! tag="$("$python_bin" -I -c '
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
tag = payload.get("tag_name") if isinstance(payload, dict) else None
if not isinstance(tag, str):
    raise SystemExit(2)
sys.stdout.write(tag)
' "$response_file" 2>/dev/null)"; then
    rm -f "$response_file"
    fail "latest release response was invalid; no package was installed; retry later or set an explicit vX.Y.Z"
  fi
  rm -f "$response_file"
  case "$tag" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *) fail "latest release tag was invalid; no package was installed; retry later or set an explicit vX.Y.Z" ;;
  esac
  "$python_bin" -I -c \
    'import re,sys; raise SystemExit(0 if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]) else 2)' \
    "$tag" 2>/dev/null \
    || fail "latest release tag was invalid; no package was installed; retry later or set an explicit vX.Y.Z"
  printf '%s\n' "$tag"
}

build_install_spec() {
  local resolved="$1"
  printf 'git+https://github.com/%s.git@%s\n' "$REPO" "$resolved"
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
      FEISHU_APP_ID=*|FEISHU_APP_SECRET=*|FEISHU_CONNECTION_MODE=*|FEISHU_HOME_CHANNEL=*|HERMES_FEISHU_CARD_HOST=*|HERMES_FEISHU_CARD_PORT=*|HERMES_FEISHU_CARD_PROFILE_ID=*|HERMES_FEISHU_CARD_EVENT_URL=*|HFC_CONFIG=*|HFC_VERSION=*|HFC_NO_REPAIR=*)
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        case "$value" in
          \"*\") value="${value#\"}"; value="${value%\"}" ;;
          \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        case "$key" in
          HFC_CONFIG) [ -n "$CONFIG_PATH" ] || CONFIG_PATH="$value" ;;
          HFC_VERSION) [ -n "$VERSION" ] || VERSION="$value" ;;
          HFC_NO_REPAIR) [ -n "$NO_REPAIR" ] || NO_REPAIR="$value" ;;
          HERMES_FEISHU_CARD_PROFILE_ID) [ -n "$PROFILE_ID" ] || PROFILE_ID="$value" ;;
          HERMES_FEISHU_CARD_EVENT_URL) [ -n "$EVENT_URL" ] || EVENT_URL="$value" ;;
          *)
            if [ -z "${!key:-}" ]; then
              export "$key=$value"
            fi
            ;;
        esac
        ;;
    esac
  done < "$ENV_FILE"
}

upsert_env() {
  local key="$1"
  local value="$2"
  local quoted
  quoted="$(quote_env_value "$value")"
  mkdir -p "$(dirname "$ENV_FILE")"
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  if grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE"; then
    local tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$quoted" '
      {
        normalized = $0
        sub(/^[[:space:]]*export[[:space:]]+/, "", normalized)
      }
      normalized ~ "^[[:space:]]*" key "[[:space:]]*=" { print key "=" value; next }
      { print }
    ' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$quoted" >> "$ENV_FILE"
  fi
  export "$key=$value"
}

prompt_credentials() {
  if [ -f "$CONFIG_PATH" ] && run_python -c 'import sys; from hermes_feishu_card.config import load_config; from hermes_feishu_card.cli import _has_feishu_credentials; c=load_config(sys.argv[1],env_file=sys.argv[2]); p=sys.argv[3] or "default"; ps=c.get("profiles"); p=next(iter(ps)) if ps and p=="default" and p not in ps and sys.argv[4]!="1" else p; sys.exit(0 if _has_feishu_credentials(c,p) else 1)' "$CONFIG_PATH" "$ENV_FILE" "$PROFILE_ID" "$PROFILE_ID_EXPLICIT" >/dev/null 2>&1; then
    return 0
  fi
  if { [ -z "${FEISHU_APP_ID:-}" ] || [ -z "${FEISHU_APP_SECRET:-}" ]; } && \
      { [ "${HFC_NO_PROMPT:-0}" = "1" ] || [ ! -t 0 ]; }; then
    fail "FEISHU_APP_ID/FEISHU_APP_SECRET are missing. Set them or write them to $ENV_FILE."
  fi

  if [ -z "${FEISHU_APP_ID:-}" ] || [ -z "${FEISHU_APP_SECRET:-}" ]; then
    log "Feishu credentials were not found. They will be saved to $ENV_FILE."
  fi
  if [ -z "${FEISHU_APP_ID:-}" ]; then
    printf 'FEISHU_APP_ID: '
    IFS= read -r app_id
    [ -n "$app_id" ] || fail "FEISHU_APP_ID is required"
    export FEISHU_APP_ID="$app_id"
  fi
  if [ -z "${FEISHU_APP_SECRET:-}" ]; then
    printf 'FEISHU_APP_SECRET: '
    stty -echo 2>/dev/null || true
    IFS= read -r app_secret
    stty echo 2>/dev/null || true
    printf '\n'
    [ -n "$app_secret" ] || fail "FEISHU_APP_SECRET is required"
    export FEISHU_APP_SECRET="$app_secret"
  fi
  upsert_env "FEISHU_APP_ID" "$FEISHU_APP_ID"
  upsert_env "FEISHU_APP_SECRET" "$FEISHU_APP_SECRET"
}

prepare_pm_management() {
  # Setup validates the old observer's owned wheel before PM admits its replacement.
  # Keep the CLI usable independently of PM, including when a generation is missing.
  PM_MANAGEMENT_INSPECTOR="$1"
  shift
  PM_MANAGEMENT_DIR="$("$PM_MANAGEMENT_INSPECTOR" -I -B -S -c '
import os, stat, sys
from pathlib import Path

state = Path(sys.argv[1]).resolve(strict=True) / ".hermes_feishu_card"
management = state / "management"
for directory in (state, management):
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077):
        raise SystemExit("The HFC management directory must be private and owned by the current user.")
print(management)
' "$HERMES_HOME_DIR")" || fail "could not prepare the PM management directory"

  # Bash keeps the locked directory descriptor open through setup.
  exec 9<"$PM_MANAGEMENT_DIR"
  pm_management_venv lock || fail "could not lock the PM management directory"
  trap 'rollback_pm_management "$?"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  pm_management_venv prepare "$@" || fail "could not prepare the PM management Python"
  PYTHON_BIN="$PM_MANAGEMENT_DIR/venv/bin/python"
}

pm_management_venv() {
  "$PM_MANAGEMENT_INSPECTOR" -I -B -S -c '
import errno, fcntl, os, stat, subprocess, sys, tempfile
from pathlib import Path

directory, action = Path(sys.argv[1]), sys.argv[2]
target = directory / "venv"
previous = directory / ".venv-before-rebuild"


def owned_directory(path, mask=0o077):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & mask):
        raise SystemExit("The HFC management venv must be private and owned by the current user.")
    return metadata


def owned_venv(path):
    if owned_directory(path) is None:
        return False
    config = path / "pyvenv.cfg"
    try:
        metadata = config.lstat()
    except FileNotFoundError:
        # A failed older installer can leave an empty or partly provisioned venv.
        if set(os.listdir(path)) - {"include", "lib", "lib64", "bin", ".gitignore"}:
            raise SystemExit("The HFC management venv ownership could not be verified.")
    else:
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022 or metadata.st_nlink != 1):
            raise SystemExit("The HFC management venv ownership could not be verified.")
    if owned_directory(path / "bin", mask=0o022) is not None:
        try:
            launcher = (path / "bin" / "python").lstat()
        except FileNotFoundError:
            pass
        else:
            if (launcher.st_uid != os.getuid() or not (
                    stat.S_ISLNK(launcher.st_mode) or
                    stat.S_ISREG(launcher.st_mode) and not launcher.st_mode & 0o022)):
                raise SystemExit("The HFC management Python ownership could not be verified.")
    return True


def move(source, destination):
    os.rename(source.relative_to(directory), destination.relative_to(directory),
              src_dir_fd=9, dst_dir_fd=9)


def discard(path, restore=False):
    # Move the directory out of the live names before any recursive cleanup.
    with tempfile.TemporaryDirectory(prefix=".venv-cleanup-", dir=directory) as scratch:
        move(path, Path(scratch) / "venv")
        if restore:
            move(previous, target)


def rollback():
    if owned_venv(previous):
        if owned_venv(target):
            discard(target, restore=True)
        else:
            move(previous, target)


def probe(python, code, *, without_site=False):
    options = ["-I", "-B"]
    if without_site:
        options.append("-S")
    try:
        result = subprocess.run([python, *options, "-c", code],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=10)
    except FileNotFoundError:
        return None
    except (OSError, subprocess.TimeoutExpired) as exc:
        if without_site and (isinstance(exc, subprocess.TimeoutExpired)
                             or exc.errno in (errno.EACCES, errno.ENOEXEC)):
            return None
        raise
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def runnable(path):
    code = """import sys
if sys.version_info >= (3, 9) and sys.prefix != sys.base_prefix:
    print(sys.prefix)
"""
    return probe(path / "bin/python", code) == str(path)


def select_base(candidates):
    code = """import sys, venv
if sys.version_info >= (3, 9):
    print(sys._base_executable)
"""
    for candidate in dict.fromkeys(filter(None, candidates)):
        base = probe(candidate, code, without_site=True)
        if (base and Path(base).is_absolute() and target not in Path(base).parents
                and probe(base, code, without_site=True) == base):
            return base
    raise SystemExit("A working Python 3.9+ is required to build the HFC management venv.")


opened = os.fstat(9)
metadata = owned_directory(directory)
if (owned_directory(directory.parent) is None or metadata is None
        or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)):
    raise SystemExit("The HFC management directory changed.")
if action == "lock":
    try:
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another HFC installer is using this Hermes HOME.") from None
elif action == "rollback":
    rollback()
elif action == "commit":
    if owned_venv(previous):
        discard(previous)
elif action == "prepare":
    # A previous installer may have exited before its rollback could run.
    rollback()
    existing = owned_venv(target)
    if not existing or not runnable(target):
        base = select_base(sys.argv[3:])
        if existing:
            move(target, previous)
        os.umask(0o077)
        target.mkdir(mode=0o700)
        try:
            # Use native POSIX venv symlinks, including for dynamically linked macOS Python.
            subprocess.run([base, "-I", "-B", "-S", "-m", "venv", "--without-pip", target], check=True)
            if not runnable(target):
                raise SystemExit("The replacement HFC management Python is not usable.")
        except (OSError, subprocess.SubprocessError, SystemExit):
            if not existing:
                discard(target)
            raise
' "$PM_MANAGEMENT_DIR" "$@"
}

rollback_pm_management() {
  local previous_exit="$1"
  trap - EXIT
  pm_management_venv rollback || fail "could not restore the previous PM management venv"
  exit "$previous_exit"
}

detect_legacy_python() {
  if [ -n "$PYTHON_BIN" ]; then
    [ -x "$PYTHON_BIN" ] || fail "HFC_PYTHON is not executable: $PYTHON_BIN"
    return
  fi
  local candidate
  for candidate in "$@"; do
    if [ -x "$candidate" ]; then
      PYTHON_BIN="$candidate"
      return
    fi
  done
  PYTHON_BIN="${PYTHON:-python3}"
}

inspect_hermes_layout() {
  "$1" -I -B -S -c '
import stat, sys
from pathlib import Path

try:
    metadata = (Path(sys.argv[1]) / "pm" / "environments.py").lstat()
except FileNotFoundError:
    print("legacy")
except OSError:
    print("blocked")
else:
    print("pm" if stat.S_ISREG(metadata.st_mode) else "blocked")
' "$HERMES_DIR"
}

detect_python() {
  local candidates=(
    "$HERMES_DIR/venv/bin/python"
    "$HERMES_DIR/venv/bin/python3"
    "$HERMES_DIR/.venv/bin/python"
    "$HERMES_DIR/.venv/bin/python3"
    "$HERMES_DIR/gateway/.venv/bin/python"
    "$HERMES_DIR/gateway/venv/bin/python"
  )
  local inspector kind alternative
  local fallbacks=("$PYTHON_BIN" "${candidates[@]}" "${PYTHON:-}" python)
  # A launcher on PATH can exist even when the Python it calls is unavailable.
  while IFS= read -r alternative; do
    fallbacks+=("$alternative")
  done < <(type -ap python3)
  for inspector in python3 "${fallbacks[@]}"; do
    [ -n "$inspector" ] && have "$inspector" || continue
    kind="$(inspect_hermes_layout "$inspector" 2>/dev/null)" || continue
    case "$kind" in
      pm)
        prepare_pm_management "$inspector" "$PYTHON_BIN" "${PYTHON:-}" "$inspector" "${fallbacks[@]}"
        return
        ;;
      legacy) detect_legacy_python "${candidates[@]}"; return ;;
      blocked) break ;;
    esac
  done
  fail "could not inspect the Hermes PM environment module"
}

run_python() {
  if [ -n "$PM_MANAGEMENT_DIR" ]; then
    "$PYTHON_BIN" -I "$@"
  else
    "$PYTHON_BIN" "$@"
  fi
}

configure_pip_user_flag() {
  if [ "${HFC_PIP_USER+x}" = "x" ]; then
    PIP_USER_FLAG="$HFC_PIP_USER"
    return
  fi
  # Never pass --user for an interpreter that already belongs to a virtual environment:
  # pip --user writes to the user site directory, which the venv does not import, so the
  # Gateway would still not see the package.
  if [ -n "$PYTHON_BIN" ] && \
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
    PIP_USER_FLAG="0"
    return
  fi
  case "$PYTHON_BIN" in
    "$HERMES_DIR"/venv/bin/python|"$HERMES_DIR"/venv/bin/python3|\
    "$HERMES_DIR"/.venv/bin/python|"$HERMES_DIR"/.venv/bin/python3|\
    "$HERMES_DIR"/gateway/.venv/bin/python|"$HERMES_DIR"/gateway/venv/bin/python)
      PIP_USER_FLAG="0"
      ;;
    *)
      PIP_USER_FLAG="--user"
      ;;
  esac
}

install_package() {
  local spec="$1"
  local resolved_version="$2"
  have "$PYTHON_BIN" || fail "$PYTHON_BIN was not found. Install Python 3.9+ first."
  export PIP_ROOT_USER_ACTION="${PIP_ROOT_USER_ACTION:-ignore}"
  if [ -n "$PM_MANAGEMENT_DIR" ]; then
    log "installing management CLI from $REPO@$resolved_version"
    (
      # Only this private venv receives pip writes, including when the caller
      # has pip defaults for a different installation target.
      unset PIP_TARGET PIP_PREFIX PIP_ROOT PIP_USER PIP_PYTHON
      export PIP_CONFIG_FILE=/dev/null
      run_python -m pip --version >/dev/null 2>&1 || run_python -m ensurepip --upgrade >/dev/null
      run_python -m pip --require-virtualenv install --force-reinstall "$spec"
    ) || return "$?"
    # Retain the source key used by existing PM registrations for immutable
    # release tags. Resolve a moving ref to the installed CLI's Git commit.
    HFC_INSTALL_SPEC="$(run_python -c '
import re, sys
from hermes_feishu_card.cli import _installed_distribution_spec

requested = sys.argv[1]
source, ref = requested.rsplit("@", 1)
spec = _installed_distribution_spec()
if not spec or not re.fullmatch(re.escape(source) + r"@(?:[0-9a-f]{40}|[0-9a-f]{64})", spec):
    raise SystemExit("The installed HFC management CLI lacks a verified Git commit.")
print(requested if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", ref) else spec)
' "$spec")" || fail "could not pin the HFC runtime wheel source"
    export HFC_INSTALL_SPEC
    pm_management_venv commit || fail "could not finalize the PM management venv"
    trap - EXIT INT TERM
    printf '[hermes-feishu-card] management CLI: %q -I -m hermes_feishu_card.cli\n' "$PYTHON_BIN"
    return
  fi
  "$PYTHON_BIN" -m pip --version >/dev/null 2>&1 || "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null
  export HFC_INSTALL_SPEC="$spec"
  log "installing $REPO@$resolved_version"
  local pip_args=(install --upgrade)
  case "$PIP_USER_FLAG" in
    ""|"0"|"false"|"False") ;;
    *) pip_args=(install "$PIP_USER_FLAG" --upgrade) ;;
  esac
  local pip_log
  pip_log="$(mktemp)"
  local pip_status
  if "$PYTHON_BIN" -m pip "${pip_args[@]}" "$spec" >"$pip_log" 2>&1; then
    cat "$pip_log"
    rm -f "$pip_log"
    return
  else
    pip_status=$?
  fi
  if grep -q "externally-managed-environment" "$pip_log"; then
    log "Python environment is externally managed; retrying with --break-system-packages"
    if "$PYTHON_BIN" -m pip "${pip_args[@]}" --break-system-packages "$spec" >"$pip_log" 2>&1; then
      cat "$pip_log"
      log "pip warning handled safely; package install completed"
      rm -f "$pip_log"
      return
    else
      pip_status=$?
    fi
    cat "$pip_log" >&2
    rm -f "$pip_log"
    return "$pip_status"
  fi
  cat "$pip_log" >&2
  rm -f "$pip_log"
  return "$pip_status"
}

run_setup() {
  local setup_args=(
    -m hermes_feishu_card.cli setup
    --hermes-dir "$HERMES_DIR"
    --hermes-home "$HERMES_HOME_DIR"
    --config "$CONFIG_PATH"
    --env-file "$ENV_FILE"
  )
  if [ "$PROFILE_ID_EXPLICIT" = "1" ]; then
    setup_args+=(--profile-id "$PROFILE_ID")
  fi
  setup_args+=(
    --event-url "$EVENT_URL"
    --yes
  )
  if [ "${HFC_SKIP_START:-0}" = "1" ]; then
    setup_args+=(--skip-start)
  fi
  if [ "$NO_REPAIR" = "1" ]; then
    setup_args+=(--no-repair)
  else
    setup_args+=(--accept-hermes-upgrade)
  fi
  log "running setup"
  run_python "${setup_args[@]}"
}

absolute_status_path() {
  case "$2" in
    /*) printf '%s' "$2" ;;
    *) printf '%s/%s' "${1%/}" "$2" ;;
  esac
}

print_status_command() {
  local cwd python="$PYTHON_BIN"
  cwd="$(pwd -P)"
  case "$python" in
    */*) python="$(absolute_status_path "$cwd" "$python")" ;;
  esac
  local command=("$python")
  if [ -n "$PM_MANAGEMENT_DIR" ]; then
    command+=(-I)
  fi
  # Prefix the current directory without folding symlinks or parent components.
  command+=(-m hermes_feishu_card.cli status
    --config "$(absolute_status_path "$cwd" "$CONFIG_PATH")"
    --env-file "$(absolute_status_path "$cwd" "$ENV_FILE")")
  if [ -n "$PM_MANAGEMENT_DIR" ]; then
    command+=(--hermes-dir "$(absolute_status_path "$cwd" "$HERMES_DIR")"
      --hermes-home "$(absolute_status_path "$cwd" "$HERMES_HOME_DIR")")
  fi
  printf '[hermes-feishu-card] status:'
  printf ' %q' "${command[@]}"
  printf '\n'
}

main() {
  parse_args "$@"
  if [ -z "$ENV_FILE" ]; then
    local initial_config="${CONFIG_PATH:-$HOME/.hermes/config.yaml}"
    ENV_FILE="$(dirname "$initial_config")/.env"
  fi
  ENV_FILE="$(expand_path "$ENV_FILE")"
  load_env_file

  VERSION="${VERSION:-latest}"
  CONFIG_PATH="${CONFIG_PATH:-$HOME/.hermes/config.yaml}"
  EVENT_URL="${EVENT_URL:-http://127.0.0.1:8765/events}"
  NO_REPAIR="${NO_REPAIR:-0}"
  HERMES_DIR="$(expand_path "$HERMES_DIR")"
  if [ -n "$HERMES_HOME_DIR" ]; then
    HERMES_HOME_DIR="$(expand_path "$HERMES_HOME_DIR")"
  else
    HERMES_HOME_DIR="$(dirname "$HERMES_DIR")"
  fi
  CONFIG_PATH="$(expand_path "$CONFIG_PATH")"
  detect_python
  have "$PYTHON_BIN" || fail "$PYTHON_BIN was not found. Install Python 3.9+ first."
  local resolved_version
  local resolved_install_spec
  resolved_version="$(resolve_version "$PYTHON_BIN")"
  resolved_install_spec="$(build_install_spec "$resolved_version")"
  VERSION="$resolved_version"
  if [ -z "$PM_MANAGEMENT_DIR" ]; then
    configure_pip_user_flag
  fi

  export HFC_CONFIG="$CONFIG_PATH"
  export HFC_ENV_FILE="$ENV_FILE"
  export HFC_VERSION="$VERSION"
  if [ -n "$PROFILE_ID" ]; then
    export HERMES_FEISHU_CARD_PROFILE_ID="$PROFILE_ID"
  fi
  export HERMES_FEISHU_CARD_EVENT_URL="$EVENT_URL"
  export HFC_NO_REPAIR="$NO_REPAIR"
  if [ -f "$CONFIG_PATH" ]; then
    install_package "$resolved_install_spec" "$resolved_version"
    prompt_credentials
  else
    prompt_credentials
    install_package "$resolved_install_spec" "$resolved_version"
  fi
  run_setup

  log "done"
  print_status_command
}

main "$@"
