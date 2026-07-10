#!/usr/bin/env bash
set -euo pipefail

REPO="${HFC_REPO:-baileyh8/hermes-feishu-streaming-card}"
VERSION="${HFC_VERSION:-}"
HERMES_DIR="${HERMES_DIR:-/opt/hermes}"
CONFIG_PATH="${HFC_CONFIG:-}"
ENV_FILE="${HFC_ENV_FILE:-}"
PROFILE_ID="${HERMES_FEISHU_CARD_PROFILE_ID:-}"
EVENT_URL="${HERMES_FEISHU_CARD_EVENT_URL:-}"
NO_REPAIR="${HFC_NO_REPAIR:-}"
NO_PROMPT="${HFC_NO_PROMPT:-1}"
SKIP_START="${HFC_SKIP_START:-0}"

log() {
  printf '[hermes-feishu-card:docker] %s\n' "$*"
}

fail() {
  printf '[hermes-feishu-card:docker] error: %s\n' "$*" >&2
  exit 1
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

resolve_version() {
  if [ "$VERSION" != "latest" ]; then
    printf '%s\n' "$VERSION"
    return
  fi
  printf 'latest\n'
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
      FEISHU_APP_ID=*|FEISHU_APP_SECRET=*|FEISHU_CONNECTION_MODE=*|FEISHU_HOME_CHANNEL=*|HERMES_FEISHU_CARD_HOST=*|HERMES_FEISHU_CARD_PORT=*|HERMES_FEISHU_CARD_PROFILE_ID=*|HERMES_FEISHU_CARD_EVENT_URL=*|HFC_CONFIG=*|HFC_VERSION=*|HFC_NO_REPAIR=*)
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
  export PIP_ROOT_USER_ACTION="${PIP_ROOT_USER_ACTION:-ignore}"
  "$python_bin" -m pip --version >/dev/null 2>&1 || "$python_bin" -m ensurepip --upgrade >/dev/null
  local tag
  tag="$(resolve_version)"
  local spec="git+https://github.com/$REPO.git"
  if [ -n "$tag" ] && [ "$tag" != "latest" ]; then
    spec="$spec@$tag"
  fi
  export HFC_INSTALL_SPEC="$spec"
  if [ "$tag" = "latest" ]; then
    log "installing $REPO (latest branch) into $python_bin"
  else
    log "installing $REPO@$tag into $python_bin"
  fi
  local pip_log
  pip_log="$(mktemp)"
  if "$python_bin" -m pip install --upgrade "$spec" >"$pip_log" 2>&1; then
    cat "$pip_log"
    rm -f "$pip_log"
    return
  fi
  local pip_status
  pip_status=$?
  if grep -q "externally-managed-environment" "$pip_log"; then
    log "Python environment is externally managed; retrying with --break-system-packages"
    if "$python_bin" -m pip install --upgrade --break-system-packages "$spec" >"$pip_log" 2>&1; then
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

run_doctor() {
  local python_bin="$1"
  log "running doctor"
  "$python_bin" -m hermes_feishu_card.cli doctor \
    --config "$CONFIG_PATH" \
    --hermes-dir "$HERMES_DIR" \
    --profile-id "$PROFILE_ID" \
    --explain
}

run_setup() {
  local python_bin="$1"
  local setup_args=(
    -m hermes_feishu_card.cli setup
    --hermes-dir "$HERMES_DIR"
    --config "$CONFIG_PATH"
    --env-file "$ENV_FILE"
    --profile-id "$PROFILE_ID"
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

  validate_paths
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
