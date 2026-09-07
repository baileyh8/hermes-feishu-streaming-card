#!/usr/bin/env bash
set -euo pipefail

REPO="${HFC_REPO:-baileyh8/hermes-feishu-streaming-card}"
VERSION="${HFC_VERSION:-}"
HERMES_DIR="${HERMES_DIR:-/opt/hermes}"
HERMES_HOME_DIR="${HERMES_HOME:-}"
CONFIG_PATH="${HFC_CONFIG:-}"
ENV_FILE="${HFC_ENV_FILE:-}"
PROFILE_ID="${HERMES_FEISHU_CARD_PROFILE_ID:-}"
PROFILE_ID_EXPLICIT=0
EVENT_URL="${HERMES_FEISHU_CARD_EVENT_URL:-}"
NO_REPAIR="${HFC_NO_REPAIR:-}"
NO_PROMPT="${HFC_NO_PROMPT:-1}"
SKIP_START="${HFC_SKIP_START:-0}"
SERVICE_MANAGER="${HERMES_FEISHU_CARD_SERVICE_MANAGER:-detached}"
STATE_DIR="${HERMES_FEISHU_CARD_STATE_DIR:-}"
INSTALL_SOURCE="${HFC_INSTALL_SOURCE:-}"
TEST_NOOP_DELIVERY="${HFC_TEST_NOOP_DELIVERY:-0}"

# CI-only no-op mode is intentionally narrow: it cannot select the normal
# remote package path, cannot start a service, and never changes credential
# requirements unless an explicit absolute local source is also supplied.

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
  while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in
      ""|\#*) continue ;;
      export\ *) entry="${entry#export }" ;;
    esac
    case "$entry" in
      FEISHU_APP_ID=*|FEISHU_APP_SECRET=*|FEISHU_CONNECTION_MODE=*|FEISHU_HOME_CHANNEL=*|HERMES_FEISHU_CARD_HOST=*|HERMES_FEISHU_CARD_PORT=*|HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK=*|HERMES_FEISHU_CARD_SERVICE_MANAGER=*|HERMES_FEISHU_CARD_STATE_DIR=*|HERMES_FEISHU_CARD_PROFILE_ID=*|HERMES_FEISHU_CARD_EVENT_URL=*|HFC_CONFIG=*|HFC_VERSION=*|HFC_NO_REPAIR=*)
        key="${entry%%=*}"
        value="${entry#*=}"
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
          HERMES_FEISHU_CARD_SERVICE_MANAGER)
            if [ -z "${HERMES_FEISHU_CARD_SERVICE_MANAGER:-}" ]; then
              SERVICE_MANAGER="$value"
            fi
            ;;
          HERMES_FEISHU_CARD_STATE_DIR)
            if [ -z "${HERMES_FEISHU_CARD_STATE_DIR:-}" ]; then
              STATE_DIR="$value"
            fi
            ;;
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
    chmod 600 "$ENV_FILE" 2>/dev/null || true
  else
    printf '%s=%s\n' "$key" "$quoted" >> "$ENV_FILE"
  fi
  export "$key=$value"
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
    "$HERMES_DIR/gateway/.venv/bin/python"
    "$HERMES_DIR/gateway/venv/bin/python"
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

validate_test_controls() {
  case "$TEST_NOOP_DELIVERY" in
    0|1) ;;
    *) fail "HFC_TEST_NOOP_DELIVERY must be 0 or 1" ;;
  esac
  if [ -n "$INSTALL_SOURCE" ]; then
    case "$INSTALL_SOURCE" in
      /*) ;;
      *) fail "HFC_INSTALL_SOURCE must be an absolute local directory: $INSTALL_SOURCE" ;;
    esac
    [ -d "$INSTALL_SOURCE" ] || fail "HFC_INSTALL_SOURCE must be a local directory: $INSTALL_SOURCE"
    [ ! -L "$INSTALL_SOURCE" ] || fail "HFC_INSTALL_SOURCE must not be a symlink: $INSTALL_SOURCE"
  fi
  if [ "$TEST_NOOP_DELIVERY" = "1" ]; then
    [ -n "$INSTALL_SOURCE" ] || fail "HFC_TEST_NOOP_DELIVERY requires HFC_INSTALL_SOURCE"
    [ "$SKIP_START" = "1" ] || fail "HFC_TEST_NOOP_DELIVERY requires HFC_SKIP_START=1"
  fi
}

prepare_private_state() {
  mkdir -p "$STATE_DIR"
  [ ! -L "$STATE_DIR" ] || fail "HFC state directory must not be a symlink: $STATE_DIR"
  chmod 0700 "$STATE_DIR"
  [ -d "$STATE_DIR" ] && [ -w "$STATE_DIR" ] || fail "HFC state directory is not writable: $STATE_DIR"
}

require_credentials() {
  if [ "$TEST_NOOP_DELIVERY" = "1" ]; then
    log "credential-free no-op delivery enabled for local-source smoke"
    return 0
  fi
  if [ -f "$CONFIG_PATH" ] && "$1" -c 'import sys; from hermes_feishu_card.config import load_config; from hermes_feishu_card.cli import _has_feishu_credentials; c=load_config(sys.argv[1],env_file=sys.argv[2]); p=sys.argv[3] or "default"; ps=c.get("profiles"); p=next(iter(ps)) if ps and p=="default" and p not in ps and sys.argv[4]!="1" else p; sys.exit(0 if _has_feishu_credentials(c,p) else 1)' "$CONFIG_PATH" "$ENV_FILE" "$PROFILE_ID" "$PROFILE_ID_EXPLICIT" >/dev/null 2>&1; then
    return 0
  fi
  if [ -n "${FEISHU_APP_ID:-}" ] && [ -n "${FEISHU_APP_SECRET:-}" ]; then
    upsert_env "FEISHU_APP_ID" "$FEISHU_APP_ID"
    upsert_env "FEISHU_APP_SECRET" "$FEISHU_APP_SECRET"
    return 0
  fi
  if [ "$NO_PROMPT" = "1" ] || [ ! -t 0 ]; then
    fail "FEISHU_APP_ID/FEISHU_APP_SECRET are missing. Set them as environment variables or write them to $ENV_FILE."
  fi
  fail "Interactive credential prompts are not supported by install-docker.sh. Set FEISHU_APP_ID and FEISHU_APP_SECRET."
}

install_package() {
  local python_bin="$1"
  local spec="$2"
  local resolved_version="$3"
  export PIP_ROOT_USER_ACTION="${PIP_ROOT_USER_ACTION:-ignore}"
  "$python_bin" -m pip --version >/dev/null 2>&1 || "$python_bin" -m ensurepip --upgrade >/dev/null
  export HFC_INSTALL_SPEC="$spec"
  if [ -n "$INSTALL_SOURCE" ]; then
    log "installing local source $INSTALL_SOURCE into $python_bin"
  else
    log "installing $REPO@$resolved_version into $python_bin"
  fi
  local pip_log
  pip_log="$(mktemp)"
  local pip_status
  if "$python_bin" -m pip install --upgrade "$spec" >"$pip_log" 2>&1; then
    cat "$pip_log"
    rm -f "$pip_log"
    return
  else
    pip_status=$?
  fi
  if grep -q "externally-managed-environment" "$pip_log"; then
    log "Python environment is externally managed; retrying with --break-system-packages"
    if "$python_bin" -m pip install --upgrade --break-system-packages "$spec" >"$pip_log" 2>&1; then
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

run_doctor() {
  local python_bin="$1"
  log "running doctor"
  local doctor_args=(
    -m hermes_feishu_card.cli doctor
    --config "$CONFIG_PATH"
    --hermes-dir "$HERMES_DIR"
    --hermes-home "$HERMES_HOME_DIR"
  )
  if [ "$PROFILE_ID_EXPLICIT" = "1" ]; then
    doctor_args+=(--profile-id "$PROFILE_ID")
  fi
  "$python_bin" "${doctor_args[@]}" --explain
}

run_setup() {
  local python_bin="$1"
  if [ "$TEST_NOOP_DELIVERY" = "1" ]; then
    local install_args=(
      -m hermes_feishu_card.cli install
      --hermes-dir "$HERMES_DIR"
      --hermes-home "$HERMES_HOME_DIR"
      --yes
    )
    if [ "$NO_REPAIR" = "1" ]; then
      install_args+=(--no-repair)
    fi
    log "running credential-free hook install smoke"
    "$python_bin" "${install_args[@]}"
    return
  fi
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
  if [ "$SKIP_START" = "1" ]; then
    setup_args+=(--skip-start)
  fi
  if [ "$NO_REPAIR" = "1" ]; then
    setup_args+=(--no-repair)
  fi
  log "running setup"
  "$python_bin" "${setup_args[@]}"
}

main() {
  parse_args "$@"
  ENV_FILE="${ENV_FILE:-/opt/data/.env}"
  ENV_FILE="$(expand_path "$ENV_FILE")"
  load_env_file

  VERSION="${VERSION:-latest}"
  CONFIG_PATH="${CONFIG_PATH:-/opt/data/config.yaml}"
  EVENT_URL="${EVENT_URL:-http://127.0.0.1:8765/events}"
  NO_REPAIR="${NO_REPAIR:-0}"
  HERMES_DIR="$(expand_path "$HERMES_DIR")"
  if [ -n "$HERMES_HOME_DIR" ]; then
    HERMES_HOME_DIR="$(expand_path "$HERMES_HOME_DIR")"
  else
    HERMES_HOME_DIR="$(dirname "$HERMES_DIR")"
  fi
  CONFIG_PATH="$(expand_path "$CONFIG_PATH")"
  STATE_DIR="${STATE_DIR:-$(dirname "$CONFIG_PATH")/state}"
  STATE_DIR="$(expand_path "$STATE_DIR")"
  if [ -n "$INSTALL_SOURCE" ]; then
    INSTALL_SOURCE="$(expand_path "$INSTALL_SOURCE")"
  fi

  validate_test_controls
  local python_bin
  python_bin="$(detect_python)"
  local resolved_version
  local resolved_install_spec
  if [ -n "$INSTALL_SOURCE" ]; then
    resolved_version="$VERSION"
    resolved_install_spec="$INSTALL_SOURCE"
  else
    resolved_version="$(resolve_version "$python_bin")"
    resolved_install_spec="$(build_install_spec "$resolved_version")"
  fi
  VERSION="$resolved_version"

  export HFC_CONFIG="$CONFIG_PATH"
  export HFC_ENV_FILE="$ENV_FILE"
  export HFC_VERSION="$resolved_version"
  if [ -n "$PROFILE_ID" ]; then
    export HERMES_FEISHU_CARD_PROFILE_ID="$PROFILE_ID"
  fi
  export HERMES_FEISHU_CARD_EVENT_URL="$EVENT_URL"
  export HERMES_FEISHU_CARD_SERVICE_MANAGER="$SERVICE_MANAGER"
  export HERMES_FEISHU_CARD_STATE_DIR="$STATE_DIR"
  export HFC_NO_REPAIR="$NO_REPAIR"
  export HFC_INSTALL_SOURCE="$INSTALL_SOURCE"
  export HFC_TEST_NOOP_DELIVERY="$TEST_NOOP_DELIVERY"

  validate_paths
  prepare_private_state
  log "using Hermes Python: $python_bin"
  if [ -f "$CONFIG_PATH" ]; then
    install_package "$python_bin" "$resolved_install_spec" "$resolved_version"
    require_credentials "$python_bin"
  else
    require_credentials "$python_bin"
    install_package "$python_bin" "$resolved_install_spec" "$resolved_version"
  fi
  run_doctor "$python_bin" || log "doctor reported issues; setup will validate the selected profile and installation"
  run_setup "$python_bin"
  log "done"
  if [ "$SKIP_START" = "1" ]; then
    log "sidecar start skipped; run hermes_feishu_card.runner as the container main process"
  else
    log "status: $python_bin -m hermes_feishu_card.cli status --config \"$CONFIG_PATH\""
  fi
  log "doctor: $python_bin -m hermes_feishu_card.cli doctor --config \"$CONFIG_PATH\" --hermes-dir \"$HERMES_DIR\" --explain"
}

main "$@"
