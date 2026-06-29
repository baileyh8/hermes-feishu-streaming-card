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
      FEISHU_APP_ID=*|FEISHU_APP_SECRET=*|FEISHU_CONNECTION_MODE=*|FEISHU_HOME_CHANNEL=*|HERMES_FEISHU_CARD_HOST=*|HERMES_FEISHU_CARD_PORT=*)
        key="${entry%%=*}"
        value="${entry#*=}"
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
    "$HERMES_DIR/.venv/bin/python"
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
