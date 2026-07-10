#!/usr/bin/env bash
set -euo pipefail

REPO="${HFC_REPO:-baileyh8/hermes-feishu-streaming-card}"
VERSION="${HFC_VERSION:-}"
HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"
CONFIG_PATH="${HFC_CONFIG:-}"
ENV_FILE="${HFC_ENV_FILE:-}"
PROFILE_ID="${HERMES_FEISHU_CARD_PROFILE_ID:-}"
EVENT_URL="${HERMES_FEISHU_CARD_EVENT_URL:-}"
NO_REPAIR="${HFC_NO_REPAIR:-}"
PYTHON_BIN="${PYTHON:-python3}"
PIP_USER_FLAG="${HFC_PIP_USER:---user}"

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
      --config|--env-file|--version|--profile-id|--event-url)
        [ "$#" -ge 2 ] || fail "$1 requires a value"
        case "$1" in
          --config) CONFIG_PATH="$2" ;;
          --env-file) ENV_FILE="$2" ;;
          --version) VERSION="$2" ;;
          --profile-id) PROFILE_ID="$2" ;;
          --event-url) EVENT_URL="$2" ;;
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
  if grep -q "^${key}=" "$ENV_FILE"; then
    local tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$quoted" '
      index($0, key "=") == 1 { print key "=" value; next }
      { print }
    ' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$quoted" >> "$ENV_FILE"
  fi
  export "$key=$value"
}

prompt_credentials() {
  if [ -n "${FEISHU_APP_ID:-}" ] && [ -n "${FEISHU_APP_SECRET:-}" ]; then
    return 0
  fi
  if [ "${HFC_NO_PROMPT:-0}" = "1" ] || [ ! -t 0 ]; then
    fail "FEISHU_APP_ID/FEISHU_APP_SECRET are missing. Set them or write them to $ENV_FILE."
  fi

  log "Feishu credentials were not found. They will be saved to $ENV_FILE."
  if [ -z "${FEISHU_APP_ID:-}" ]; then
    printf 'FEISHU_APP_ID: '
    IFS= read -r app_id
    [ -n "$app_id" ] || fail "FEISHU_APP_ID is required"
    upsert_env "FEISHU_APP_ID" "$app_id"
  fi
  if [ -z "${FEISHU_APP_SECRET:-}" ]; then
    printf 'FEISHU_APP_SECRET: '
    stty -echo 2>/dev/null || true
    IFS= read -r app_secret
    stty echo 2>/dev/null || true
    printf '\n'
    [ -n "$app_secret" ] || fail "FEISHU_APP_SECRET is required"
    upsert_env "FEISHU_APP_SECRET" "$app_secret"
  fi
}

install_package() {
  have "$PYTHON_BIN" || fail "$PYTHON_BIN was not found. Install Python 3.9+ first."
  export PIP_ROOT_USER_ACTION="${PIP_ROOT_USER_ACTION:-ignore}"
  "$PYTHON_BIN" -m pip --version >/dev/null 2>&1 || "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null
  local tag
  tag="$(resolve_version)"
  local spec="git+https://github.com/$REPO.git"
  if [ -n "$tag" ] && [ "$tag" != "main" ]; then
    spec="$spec@$tag"
  fi
  export HFC_INSTALL_SPEC="$spec"
  log "installing $REPO@$tag"
  local pip_args=(install --upgrade)
  case "$PIP_USER_FLAG" in
    ""|"0"|"false"|"False") ;;
    *) pip_args=(install "$PIP_USER_FLAG" --upgrade) ;;
  esac
  local pip_log
  pip_log="$(mktemp)"
  if "$PYTHON_BIN" -m pip "${pip_args[@]}" "$spec" >"$pip_log" 2>&1; then
    cat "$pip_log"
    rm -f "$pip_log"
    return
  fi
  local pip_status
  pip_status=$?
  if grep -q "externally-managed-environment" "$pip_log"; then
    log "Python environment is externally managed; retrying with --break-system-packages"
    if "$PYTHON_BIN" -m pip "${pip_args[@]}" --break-system-packages "$spec" >"$pip_log" 2>&1; then
      cat "$pip_log"
      log "pip warning handled safely; package install completed"
      rm -f "$pip_log"
      return
    fi
    pip_status=$?
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
    --config "$CONFIG_PATH"
    --env-file "$ENV_FILE"
    --profile-id "$PROFILE_ID"
    --event-url "$EVENT_URL"
    --yes
  )
  if [ "${HFC_SKIP_START:-0}" = "1" ]; then
    setup_args+=(--skip-start)
  fi
  if [ "$NO_REPAIR" = "1" ]; then
    setup_args+=(--no-repair)
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
  PROFILE_ID="${PROFILE_ID:-default}"
  EVENT_URL="${EVENT_URL:-http://127.0.0.1:8765/events}"
  NO_REPAIR="${NO_REPAIR:-0}"
  HERMES_DIR="$(expand_path "$HERMES_DIR")"
  CONFIG_PATH="$(expand_path "$CONFIG_PATH")"

  export HFC_CONFIG="$CONFIG_PATH"
  export HFC_ENV_FILE="$ENV_FILE"
  export HFC_VERSION="$VERSION"
  export HERMES_FEISHU_CARD_PROFILE_ID="$PROFILE_ID"
  export HERMES_FEISHU_CARD_EVENT_URL="$EVENT_URL"
  export HFC_NO_REPAIR="$NO_REPAIR"
  prompt_credentials
  install_package
  run_setup

  log "done"
  log "status: $PYTHON_BIN -m hermes_feishu_card.cli status --config \"$CONFIG_PATH\""
}

main "$@"
