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
  if [ -f "$CONFIG_PATH" ] && "$PYTHON_BIN" -c 'import sys; from hermes_feishu_card.config import load_config; from hermes_feishu_card.cli import _has_feishu_credentials; c=load_config(sys.argv[1],env_file=sys.argv[2]); p=sys.argv[3] or "default"; ps=c.get("profiles"); p=next(iter(ps)) if ps and p=="default" and p not in ps and sys.argv[4]!="1" else p; sys.exit(0 if _has_feishu_credentials(c,p) else 1)' "$CONFIG_PATH" "$ENV_FILE" "$PROFILE_ID" "$PROFILE_ID_EXPLICIT" >/dev/null 2>&1; then
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

detect_python() {
  if [ -n "$PYTHON_BIN" ]; then
    [ -x "$PYTHON_BIN" ] || fail "HFC_PYTHON is not executable: $PYTHON_BIN"
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
      PYTHON_BIN="$candidate"
      return
    fi
  done
  PYTHON_BIN="${PYTHON:-python3}"
}

configure_pip_user_flag() {
  if [ "${HFC_PIP_USER+x}" = "x" ]; then
    PIP_USER_FLAG="$HFC_PIP_USER"
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
  "$PYTHON_BIN" "${setup_args[@]}"
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
  configure_pip_user_flag

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
  log "status: $PYTHON_BIN -m hermes_feishu_card.cli status --config \"$CONFIG_PATH\""
}

main "$@"
