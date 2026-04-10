#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$BASE_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"
CONFIG_FILE="$BASE_DIR/config/serper.env"
EXAMPLE_FILE="$BASE_DIR/config/serper.env.example"
REQ_FILE="$BASE_DIR/requirements.txt"
SMOKE_SCRIPT="$BASE_DIR/scripts/smoke_test.py"
SELFCHECK_SCRIPT="$BASE_DIR/scripts/selfcheck.py"

MODE="auto"
RUN_SMOKE_TEST=1
RUN_FULL_CHECK=0
OUTPUT_FORMAT="text"
QUIET=0
SAVE_JSON_PATH=""
VENV_CREATED=0
REQUIREMENTS_INSTALLED=0
USED_EXISTING_VENV=0
RUNTIME_SOURCE=""
SMOKE_RESULT_PATH="/tmp/google-search-smoke-test.txt"
SELFCHECK_RESULT_PATH="/tmp/google-search-selfcheck.txt"
INSTALL_EXIT_CODE=0
EXIT_KIND="ok"

log() {
  if [ "$OUTPUT_FORMAT" = "json" ] || [ "$QUIET" -eq 1 ]; then
    return
  fi
  printf '[google-search] %s\n' "$*"
}
fail() {
  local message="$1"
  local code="${2:-10}"
  local kind="${3:-install_error}"
  INSTALL_EXIT_CODE="$code"
  EXIT_KIND="$kind"
  if [ "$OUTPUT_FORMAT" = "json" ]; then
    emit_json_result 1 "failed" "$message"
  fi
  if [ "$QUIET" -ne 1 ]; then
    printf '[google-search] ERROR: %s\n' "$message" >&2
  fi
  exit "$code"
}

usage() {
  cat <<'EOF'
Usage: bash scripts/install.sh [--system|--venv] [--skip-smoke-test] [--full-check] [--json] [--save-json <file>] [--quiet]

Options:
  --system           Force using current system python3 without creating .venv
  --venv             Force creating/using local .venv
  --skip-smoke-test  Skip lightweight smoke test (not recommended)
  --full-check       Run full selfcheck after install/smoke test
  --json             Emit machine-readable JSON result summary
  --save-json <file> Save machine-readable JSON result to a file
  --quiet            Suppress non-JSON stdout/stderr chatter when possible
  -h, --help         Show this help

Exit codes:
  0  ok
  2  config_error
  3  dependency_error
  4  smoke_test_error
  5  selfcheck_error
  10 install_error
EOF
}

json_escape() {
  python3 - <<'PY' "$1"
import json, sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

emit_json_result() {
  local ok_code="$1"
  local status="$2"
  local error_message="${3:-}"
  local ok_json="false"
  if [ "$ok_code" -eq 0 ]; then
    ok_json="true"
  fi

  local error_json="null"
  if [ -n "$error_message" ]; then
    error_json="$(json_escape "$error_message")"
  fi

  local smoke_result_json="null"
  local selfcheck_result_json="null"
  local save_json_path_json="null"
  if [ "$RUN_SMOKE_TEST" -eq 1 ]; then
    smoke_result_json="$(json_escape "$SMOKE_RESULT_PATH")"
  fi
  if [ "$RUN_FULL_CHECK" -eq 1 ]; then
    selfcheck_result_json="$(json_escape "$SELFCHECK_RESULT_PATH")"
  fi
  if [ -n "$SAVE_JSON_PATH" ]; then
    save_json_path_json="$(json_escape "$SAVE_JSON_PATH")"
  fi

  local exit_kind_json exit_code_json
  exit_kind_json="$(json_escape "$EXIT_KIND")"
  exit_code_json="$INSTALL_EXIT_CODE"

  local payload
  payload=$(printf '{"ok":%s,"status":%s,"mode":%s,"python":%s,"runtimeSource":%s,"smokeTest":%s,"fullCheck":%s,"venvCreated":%s,"usedExistingVenv":%s,"requirementsInstalled":%s,"smokeTestResultPath":%s,"selfcheckResultPath":%s,"savedJsonPath":%s,"exitKind":%s,"exitCode":%s,"error":%s}' \
    "$ok_json" \
    "$(json_escape "$status")" \
    "$(json_escape "${SELECTED_MODE:-}")" \
    "$(json_escape "${SELECTED_PY:-}")" \
    "$(json_escape "${RUNTIME_SOURCE:-}")" \
    "$( [ "$RUN_SMOKE_TEST" -eq 1 ] && printf 'true' || printf 'false' )" \
    "$( [ "$RUN_FULL_CHECK" -eq 1 ] && printf 'true' || printf 'false' )" \
    "$( [ "$VENV_CREATED" -eq 1 ] && printf 'true' || printf 'false' )" \
    "$( [ "$USED_EXISTING_VENV" -eq 1 ] && printf 'true' || printf 'false' )" \
    "$( [ "$REQUIREMENTS_INSTALLED" -eq 1 ] && printf 'true' || printf 'false' )" \
    "$smoke_result_json" \
    "$selfcheck_result_json" \
    "$save_json_path_json" \
    "$exit_kind_json" \
    "$exit_code_json" \
    "$error_json")

  printf '%s\n' "$payload"
  if [ -n "$SAVE_JSON_PATH" ]; then
    mkdir -p "$(dirname "$SAVE_JSON_PATH")"
    printf '%s\n' "$payload" >"$SAVE_JSON_PATH"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --system)
      MODE="system"
      ;;
    --venv)
      MODE="venv"
      ;;
    --skip-smoke-test)
      RUN_SMOKE_TEST=0
      ;;
    --full-check)
      RUN_FULL_CHECK=1
      ;;
    --json)
      OUTPUT_FORMAT="json"
      ;;
    --save-json)
      if [ "$#" -lt 2 ]; then
        fail "--save-json requires a file path"
      fi
      OUTPUT_FORMAT="json"
      SAVE_JSON_PATH="$2"
      shift
      ;;
    --quiet)
      QUIET=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
  shift
done

command -v python3 >/dev/null 2>&1 || fail "python3 not found" 2 config_error
[ -f "$CONFIG_FILE" ] || fail "runtime config not found: $CONFIG_FILE (copy from $EXAMPLE_FILE)" 2 config_error

venv_ready() {
  [ -x "$PYTHON_BIN" ] && [ -x "$PIP_BIN" ]
}

python_supports_runtime() {
  local py="$1"
  "$py" - <<'PY' >/dev/null 2>&1
import requests
print(requests.__version__)
PY
}

install_venv_support() {
  if ! command -v apt-get >/dev/null 2>&1; then
    fail "python3 -m venv is unavailable and apt-get is not present; please install the system venv package manually" 3 dependency_error
  fi

  local py_ver pkg
  py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  pkg="python${py_ver}-venv"

  log "python3 -m venv is unavailable; installing system package: $pkg"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y "$pkg"
}

ensure_venv() {
  if venv_ready; then
    USED_EXISTING_VENV=1
    log "local venv already exists: $VENV_DIR"
    return
  fi

  log "creating local venv: $VENV_DIR"
  if python3 -m venv "$VENV_DIR"; then
    VENV_CREATED=1
    return
  fi

  log "initial venv creation failed"
  rm -rf "$VENV_DIR"

  install_venv_support

  log "retrying local venv creation: $VENV_DIR"
  python3 -m venv "$VENV_DIR" || fail "failed to create local venv even after installing system venv support" 3 dependency_error
  VENV_CREATED=1
}

install_requirements() {
  log "upgrading pip"
  "$PIP_BIN" install --upgrade pip >/dev/null

  log "installing requirements"
  "$PIP_BIN" install -r "$REQ_FILE" >/dev/null
  REQUIREMENTS_INSTALLED=1
}

run_smoke_test() {
  local py="$1"
  log "running smoke test"
  (
    cd "$BASE_DIR"
    "$py" "$SMOKE_SCRIPT" >"$SMOKE_RESULT_PATH"
  ) || fail "smoke test failed" 4 smoke_test_error
}

run_full_check() {
  local py="$1"
  log "running full selfcheck"
  (
    cd "$BASE_DIR"
    "$py" "$SELFCHECK_SCRIPT" >"$SELFCHECK_RESULT_PATH"
  ) || fail "full selfcheck failed" 5 selfcheck_error
}

SELECTED_PY=""
SELECTED_MODE=""

case "$MODE" in
  system)
    python_supports_runtime python3 || fail "system python3 is missing required runtime dependency: requests" 3 dependency_error
    SELECTED_PY="python3"
    SELECTED_MODE="system"
    RUNTIME_SOURCE="system-python"
    ;;
  venv)
    ensure_venv
    install_requirements
    SELECTED_PY="$PYTHON_BIN"
    SELECTED_MODE="venv"
    RUNTIME_SOURCE="local-venv"
    ;;
  auto)
    if python_supports_runtime python3; then
      log "system python3 already satisfies runtime requirements; reusing it"
      SELECTED_PY="python3"
      SELECTED_MODE="system"
      RUNTIME_SOURCE="system-python"
    else
      log "system python3 is missing runtime dependency; falling back to local venv"
      ensure_venv
      install_requirements
      SELECTED_PY="$PYTHON_BIN"
      SELECTED_MODE="venv"
      RUNTIME_SOURCE="local-venv"
    fi
    ;;
  *)
    fail "invalid mode: $MODE" 10 install_error
    ;;
esac

if [ "$RUN_SMOKE_TEST" -eq 1 ]; then
  run_smoke_test "$SELECTED_PY"
else
  log "skipping smoke test by request"
fi

if [ "$RUN_FULL_CHECK" -eq 1 ]; then
  run_full_check "$SELECTED_PY"
fi

log "install complete (mode=$SELECTED_MODE, python=$SELECTED_PY)"
if [ "$OUTPUT_FORMAT" = "json" ]; then
  INSTALL_EXIT_CODE=0
  EXIT_KIND="ok"
  emit_json_result 0 "ok"
fi
