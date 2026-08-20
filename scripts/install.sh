#!/bin/bash -p
set +x
unset BASH_ENV ENV CDPATH GLOBIGNORE
for variable in "${!LD_@}"; do
  unset -v "$variable"
done
unset variable
unset GLIBC_TUNABLES GCONV_PATH
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE
unset PYTHONPLATLIBDIR PYTHONSAFEPATH PYTHONWARNINGS PYTHONBREAKPOINT
unset VIRTUAL_ENV __PYVENV_LAUNCHER__
export -n SHELLOPTS 2>/dev/null || true
set -euo pipefail

umask 077
IFS=$' \t\n'
PATH='/usr/local/bin:/usr/bin:/bin'
TMPDIR='/tmp'
TMP='/tmp'
TEMP='/tmp'
export PATH TMPDIR TMP TEMP

ENTRYPOINT_PATH="${BASH_SOURCE[0]}"
ENTRYPOINT_DIR="${ENTRYPOINT_PATH%/*}"
[ "$ENTRYPOINT_DIR" != "$ENTRYPOINT_PATH" ] || ENTRYPOINT_DIR='.'
BASE_DIR="$(cd -- "$ENTRYPOINT_DIR/.." && pwd -P)"
unset ENTRYPOINT_PATH ENTRYPOINT_DIR
VENV_DIR="$BASE_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQ_FILE="$BASE_DIR/requirements.txt"
DEV_REQ_FILE="$BASE_DIR/requirements-dev.txt"
CLIENT_SCRIPT="$BASE_DIR/scripts/client.py"
REQUIRED_PYTEST_VERSION="9.1.1"
REQUIRED_CERTIFI_VERSION="2026.7.22"
REQUIRED_CHARSET_NORMALIZER_VERSION="3.5.1"
REQUIRED_IDNA_VERSION="3.19"
REQUIRED_REQUESTS_VERSION="2.34.2"
REQUIRED_URLLIB3_VERSION="2.7.0"
BASE_PYTHON_SENTINEL="google-search-base-python-ok-v1"
RUNTIME_SENTINEL="google-search-runtime-ok-v1"
RUNTIME_INFO_SENTINEL="google-search-runtime-info-v1"
VENV_SENTINEL="google-search-venv-ok-v1"
LOCK_SENTINEL="google-search-install-lock-ok-v1"
LOCK_HELD_SENTINEL="google-search-install-lock-held-v1"
API_CONFIG_SENTINEL="google-search-keys-ok-v1"
SOURCE_TREE_SENTINEL="google-search-source-tree-ok-v1"
SAFE_TMP_DIR="/tmp"
INSTALL_LOCK_PATH="$BASE_DIR/.venv.install.lock"
RUNNER="$BASE_DIR/scripts/run.sh"
SECURE_IO_SCRIPT="$BASE_DIR/scripts/secure_io.py"
VENV_TRANSACTION_SCRIPT="$BASE_DIR/scripts/venv_transaction.py"
LOCKED_INSTALL_SCRIPT="$BASE_DIR/scripts/locked_install.py"
RUNTIME_GUARD_SCRIPT="$BASE_DIR/scripts/runtime_guard.py"
SMOKE_RESULT_SENTINEL="google-search-smoke-result-ok-v1"
FULL_RESULT_SENTINEL="google-search-full-result-ok-v1"

MODE="auto"
INSTALL_DEPENDENCIES=0
INSTALL_DEV_DEPENDENCIES=0
DEPENDENCY_LOCK="none"
RUN_SMOKE_TEST=0
RUN_FULL_CHECK=0
OUTPUT_FORMAT="text"
QUIET=0
SAVE_JSON_PATH=""
SAVE_JSON_REQUESTED=0
SAVE_JSON_NORMALIZED=0
DEPRECATED_SKIP_SMOKE=0

VENV_CREATED=0
REQUIREMENTS_INSTALLED=0
USED_EXISTING_VENV=0
RUNTIME_SOURCE=""
SELECTED_MODE=""
RUNTIME_DISPLAY_PATH=""
SELECTED_RUNTIME_TOKEN=""
SMOKE_RESULT_PATH=""
SELFCHECK_RESULT_PATH=""
INSTALL_EXIT_CODE=0
EXIT_KIND="ok"
TEMP_VENV_DIR=""
BOOTSTRAP_VENV_DIR=""
COMMAND_LOG=""
INSTALL_LOCK_FD=""
EXISTING_VENV_ID="absent"
POST_COMMIT_WARNING=""
ACTIVE_LOCK_FILE=""
ACTIVE_LOCK_SNAPSHOT=""
SOURCE_TREE_SNAPSHOT=""
TRANSACTION_HELPER_SNAPSHOT=""
TEMP_VENV_CLEANUP="remove"
ACTIVE_INSTALL_HELPER_PID=""
INSTALL_HELPER_LAUNCHING=0
INSTALL_HELPER_PREVIOUS_PID=""
PENDING_INSTALL_SIGNAL_STATUS=""
ACTIVE_ONLINE_WORKER_PID=""
ONLINE_WORKER_LAUNCHING=0
ONLINE_WORKER_PREVIOUS_PID=""
PENDING_ONLINE_SIGNAL_STATUS=""
INSTALL_RESULT_FILE=""
LOCKED_INSTALL_RESULT=""
CANDIDATE_RUNTIME_SNAPSHOT=""
BASE_PYTHON_PATH=""
BASE_PYTHON_RUNTIME=""
BASE_PYTHON_SNAPSHOT=""

usage() {
  cat <<'EOF'
Usage: /bin/bash -p scripts/install.sh [options]

The default installation path is offline: it only selects an already healthy
runtime and never calls Serper. Dependency downloads and API checks require
explicit flags.

Options:
  --system                 Require a healthy system python3
  --venv                   Require a healthy local .venv
  --install-dependencies   Create/use .venv and install the hashed runtime lock
  --install-dev-dependencies
                           Create/use .venv and install the hashed development lock
                           (both dependency flags may access a package index)
  --smoke-test             Run the minimal online Serper smoke test
  --full-check             Run `selfcheck.py --full` against Serper
  --skip-smoke-test        Deprecated no-op; offline is already the default
  --json                   Emit one machine-readable JSON result
  --save-json <file>       Securely save that JSON with mode 0600
  --quiet                  Suppress human-readable progress and errors
  -h, --help               Show this help

Exit codes:
  0  ok
  2  config_error
  3  dependency_error
  4  smoke_test_error
  5  selfcheck_error
  10 install_error
EOF
}

log() {
  if [ "$OUTPUT_FORMAT" = "json" ] || [ "$QUIET" -eq 1 ]; then
    return
  fi
  printf '[google-search] %s\n' "$*"
}

reportable_path() {
  local path="$1"
  local LC_ALL=C
  case "$path" in
    *'|'*|*[[:cntrl:]]*|*$'\302'[$'\200'-$'\237']*|*$'\330\234'*|\
      *$'\342\200'[$'\216'-$'\217']*|*$'\342\200'[$'\250'-$'\256']*|\
      *$'\342\201'[$'\246'-$'\251']*) return 1 ;;
  esac
}

without_install_lock() (
  if [ -n "${INSTALL_LOCK_FD:-}" ]; then
    exec {INSTALL_LOCK_FD}>&-
  fi
  "$@"
)

without_api_keys_and_install_lock() (
  unset SERPER_API_KEY SERPER_API_KEYS
  if [ -n "${INSTALL_LOCK_FD:-}" ]; then
    exec {INSTALL_LOCK_FD}>&-
  fi
  "$@"
)

with_install_lock_without_api_keys() (
  unset SERPER_API_KEY SERPER_API_KEYS
  "$@"
)

discard_private_result() {
  local path="$1"
  local kind="$2"
  local parent filename suffix safe_canonical
  case "$kind" in
    smoke|selfcheck) ;;
    *) return 1 ;;
  esac
  safe_canonical="$(/usr/bin/readlink -m -- "$SAFE_TMP_DIR")" || return 1
  [ "$safe_canonical" = "$SAFE_TMP_DIR" ] || return 1
  parent="${path%/*}"
  filename="${path##*/}"
  [ "$parent" != "$path" ] && [ "$parent" = "$SAFE_TMP_DIR" ] || return 1
  case "$filename" in
    google-search-"$kind".*) suffix="${filename#google-search-"$kind".}" ;;
    *) return 1 ;;
  esac
  [ "${#suffix}" -eq 8 ] || return 1
  case "$suffix" in *[!A-Za-z0-9]*) return 1 ;; esac
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  if [ -f "$path" ] || [ -L "$path" ]; then
    rm -f -- "$path" || return 1
  else
    return 1
  fi
  [ ! -e "$path" ] && [ ! -L "$path" ]
}

discard_private_result_or_fail() {
  discard_private_result "$1" "$2" || \
    fail "failed to remove a private online result" 10 install_error
}

active_process_is_shell_job() {
  local pid="$1"
  local job_pid
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  while IFS= read -r job_pid; do
    [ "$job_pid" != "$pid" ] || return 0
  done < <(jobs -p)
  return 1
}

terminate_active_process() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  if active_process_is_shell_job "$pid"; then
    # Background jobs can inherit an ignored SIGINT; TERM remains reliable.
    kill -s TERM "$pid" >/dev/null 2>&1 || true
  fi
  wait "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  local original_status=$?
  local result_cleanup_status=0
  if [ -n "$ACTIVE_INSTALL_HELPER_PID" ]; then
    terminate_active_process "$ACTIVE_INSTALL_HELPER_PID"
    ACTIVE_INSTALL_HELPER_PID=""
  fi
  if [ -n "$ACTIVE_ONLINE_WORKER_PID" ]; then
    terminate_active_process "$ACTIVE_ONLINE_WORKER_PID"
    ACTIVE_ONLINE_WORKER_PID=""
  fi
  if [ -n "$INSTALL_RESULT_FILE" ]; then
    case "$INSTALL_RESULT_FILE" in
      /tmp/google-search-install-result.*)
        if [ -f "$INSTALL_RESULT_FILE" ] && [ ! -L "$INSTALL_RESULT_FILE" ]; then
          rm -f -- "$INSTALL_RESULT_FILE" >/dev/null 2>&1 || true
        fi
        ;;
    esac
  fi
  if [ -n "$COMMAND_LOG" ]; then
    case "$COMMAND_LOG" in
      /tmp/google-search-install.*)
        if [ -f "$COMMAND_LOG" ] && [ ! -L "$COMMAND_LOG" ]; then
          rm -f -- "$COMMAND_LOG" >/dev/null 2>&1 || true
        fi
        ;;
    esac
  fi
  if [ "$OUTPUT_FORMAT" = "text" ] && [ "$QUIET" -eq 1 ]; then
    if [ -n "$SMOKE_RESULT_PATH" ]; then
      discard_private_result "$SMOKE_RESULT_PATH" smoke >/dev/null 2>&1 || \
        result_cleanup_status=1
      SMOKE_RESULT_PATH=""
    fi
    if [ -n "$SELFCHECK_RESULT_PATH" ]; then
      discard_private_result "$SELFCHECK_RESULT_PATH" selfcheck >/dev/null 2>&1 || \
        result_cleanup_status=1
      SELFCHECK_RESULT_PATH=""
    fi
  fi
  if [ -n "$TEMP_VENV_DIR" ] && [ "$TEMP_VENV_CLEANUP" = "remove" ]; then
    case "$TEMP_VENV_DIR" in
      "$BASE_DIR"/.venv-build.*)
        if [ -d "$TEMP_VENV_DIR" ] && [ ! -L "$TEMP_VENV_DIR" ]; then
          rm -rf --one-file-system -- "$TEMP_VENV_DIR" >/dev/null 2>&1 || true
        fi
        ;;
    esac
  fi
  if [ -n "$BOOTSTRAP_VENV_DIR" ]; then
    case "$BOOTSTRAP_VENV_DIR" in
      "$BASE_DIR"/.venv-bootstrap.*)
        if [ -d "$BOOTSTRAP_VENV_DIR" ] && [ ! -L "$BOOTSTRAP_VENV_DIR" ]; then
          rm -rf --one-file-system -- "$BOOTSTRAP_VENV_DIR" >/dev/null 2>&1 || true
        fi
        ;;
    esac
  fi
  if [ "$original_status" -lt 128 ] && [ "$result_cleanup_status" -ne 0 ]; then
    original_status=10
  fi
  exit "$original_status"
}
trap cleanup EXIT

exit_for_install_signal() {
  local status="$1"
  trap - HUP INT TERM
  if [ -n "$ACTIVE_INSTALL_HELPER_PID" ]; then
    terminate_active_process "$ACTIVE_INSTALL_HELPER_PID"
    ACTIVE_INSTALL_HELPER_PID=""
  fi
  if [ -n "$ACTIVE_ONLINE_WORKER_PID" ]; then
    terminate_active_process "$ACTIVE_ONLINE_WORKER_PID"
    ACTIVE_ONLINE_WORKER_PID=""
  fi
  exit "$status"
}
record_install_helper_signal() {
  local status="$1"
  local observed_pid
  [ -n "$PENDING_INSTALL_SIGNAL_STATUS" ] || PENDING_INSTALL_SIGNAL_STATUS="$status"
  if [ "$INSTALL_HELPER_LAUNCHING" -eq 1 ]; then
    observed_pid="${!:-}"
    if [ -n "$observed_pid" ] && [ "$observed_pid" != "$INSTALL_HELPER_PREVIOUS_PID" ]; then
      ACTIVE_INSTALL_HELPER_PID="$observed_pid"
    fi
  fi
  if [ -n "$ACTIVE_INSTALL_HELPER_PID" ]; then
    terminate_active_process "$ACTIVE_INSTALL_HELPER_PID"
  fi
}
record_online_signal() {
  local status="$1"
  local observed_pid
  [ -n "$PENDING_ONLINE_SIGNAL_STATUS" ] || PENDING_ONLINE_SIGNAL_STATUS="$status"
  if [ "$ONLINE_WORKER_LAUNCHING" -eq 1 ]; then
    observed_pid="${!:-}"
    if [ -n "$observed_pid" ] && [ "$observed_pid" != "$ONLINE_WORKER_PREVIOUS_PID" ]; then
      ACTIVE_ONLINE_WORKER_PID="$observed_pid"
    fi
  fi
  if [ -n "$ACTIVE_ONLINE_WORKER_PID" ]; then
    terminate_active_process "$ACTIVE_ONLINE_WORKER_PID"
  fi
}
set_install_signal_traps() {
  trap 'exit_for_install_signal 129' HUP
  trap 'exit_for_install_signal 130' INT
  trap 'exit_for_install_signal 143' TERM
}
set_install_helper_signal_traps() {
  trap 'record_install_helper_signal 129' HUP
  trap 'record_install_helper_signal 130' INT
  trap 'record_install_helper_signal 143' TERM
}
set_online_signal_traps() {
  trap 'record_online_signal 129' HUP
  trap 'record_online_signal 130' INT
  trap 'record_online_signal 143' TERM
}
set_install_signal_traps

safe_tmp_is_available() {
  local metadata mode owner
  [ -d "$SAFE_TMP_DIR" ] && [ ! -L "$SAFE_TMP_DIR" ] || return 1
  metadata="$(stat -c '%u|%a' -- "$SAFE_TMP_DIR" 2>/dev/null)" || return 1
  IFS='|' read -r owner mode <<<"$metadata"
  [ "$owner" = "0" ] || return 1
  case "$mode" in
    *[!0-7]*|'') return 1 ;;
  esac
  [ "$mode" = "1777" ]
}

ensure_command_log() {
  if [ -z "$COMMAND_LOG" ]; then
    safe_tmp_is_available || return 1
    COMMAND_LOG="$(mktemp "$SAFE_TMP_DIR/google-search-install.XXXXXXXX")" || return 1
    chmod 600 "$COMMAND_LOG" || return 1
  fi
}

run_locked_install_helper() {
  local bootstrap_python="$1"
  local candidate_python="$2"
  local lock_file="$3"
  local metadata owner mode links size helper_status=0 signal_status
  safe_tmp_is_available || return 1
  INSTALL_RESULT_FILE="$(mktemp "$SAFE_TMP_DIR/google-search-install-result.XXXXXXXX")" || return 1
  chmod 600 "$INSTALL_RESULT_FILE" || return 1
  LOCKED_INSTALL_RESULT=""
  PENDING_INSTALL_SIGNAL_STATUS=""
  INSTALL_HELPER_PREVIOUS_PID="${!:-}"
  INSTALL_HELPER_LAUNCHING=1
  set_install_helper_signal_traps
  if [ -n "$PENDING_INSTALL_SIGNAL_STATUS" ]; then
    signal_status="$PENDING_INSTALL_SIGNAL_STATUS"
    INSTALL_HELPER_LAUNCHING=0
    set_install_signal_traps
    exit "$signal_status"
  fi

  (
    unset SERPER_API_KEY SERPER_API_KEYS BASH_ENV ENV
    PIP_CONFIG_FILE=/dev/null
    export PIP_CONFIG_FILE
    if [ -n "${INSTALL_LOCK_FD:-}" ]; then
      exec {INSTALL_LOCK_FD}>&-
    fi
    exec "$bootstrap_python" -I -B -X pycache_prefix=/dev/null/google-search \
      "$LOCKED_INSTALL_SCRIPT" \
      --base-dir "$BASE_DIR" \
      --lock-file "$lock_file" \
      --expected-uid "$(id -u)" \
      --expected-snapshot "$ACTIVE_LOCK_SNAPSHOT" \
      --candidate-python "$candidate_python" \
      --sentinel "$VENV_SENTINEL"
  ) >"$INSTALL_RESULT_FILE" 2>>"$COMMAND_LOG" &
  ACTIVE_INSTALL_HELPER_PID=$!
  INSTALL_HELPER_LAUNCHING=0
  if [ -n "$PENDING_INSTALL_SIGNAL_STATUS" ]; then
    signal_status="$PENDING_INSTALL_SIGNAL_STATUS"
    terminate_active_process "$ACTIVE_INSTALL_HELPER_PID"
    ACTIVE_INSTALL_HELPER_PID=""
    set_install_signal_traps
    exit "$signal_status"
  fi
  if wait "$ACTIVE_INSTALL_HELPER_PID"; then
    helper_status=0
  else
    helper_status=$?
  fi
  if [ -n "$PENDING_INSTALL_SIGNAL_STATUS" ]; then
    signal_status="$PENDING_INSTALL_SIGNAL_STATUS"
    terminate_active_process "$ACTIVE_INSTALL_HELPER_PID"
    ACTIVE_INSTALL_HELPER_PID=""
    set_install_signal_traps
    exit "$signal_status"
  fi
  ACTIVE_INSTALL_HELPER_PID=""
  set_install_signal_traps
  if [ -n "$PENDING_INSTALL_SIGNAL_STATUS" ]; then
    exit "$PENDING_INSTALL_SIGNAL_STATUS"
  fi
  PENDING_INSTALL_SIGNAL_STATUS=""
  [ "$helper_status" -eq 0 ] || return 1

  metadata="$(stat -c '%u|%a|%h|%s' -- "$INSTALL_RESULT_FILE" 2>/dev/null)" || return 1
  IFS='|' read -r owner mode links size <<<"$metadata"
  [ "$owner" = "$(id -u)" ] && [ "$mode" = "600" ] && [ "$links" = "1" ] || return 1
  case "$size" in ''|*[!0-9]*) return 1 ;; esac
  [ "$size" -le 256 ] || return 1
  LOCKED_INSTALL_RESULT="$(<"$INSTALL_RESULT_FILE")" || return 1
  rm -f -- "$INSTALL_RESULT_FILE" || return 1
  INSTALL_RESULT_FILE=""
}

json_python() {
  base_python
}

normalize_save_json_path() {
  local normalized py
  [ "$SAVE_JSON_REQUESTED" -eq 1 ] || return 0
  [ "$SAVE_JSON_NORMALIZED" -eq 0 ] || return 0
  source_tree_is_safe || return 1
  [ -f "$SECURE_IO_SCRIPT" ] && [ ! -L "$SECURE_IO_SCRIPT" ] || return 1
  path_owner_is_safe "$SECURE_IO_SCRIPT" || return 1
  py="$(json_python)" || return 1
  normalized="$(without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search -c '
import os
import sys
from pathlib import Path

script = Path(sys.argv[1]).resolve(strict=True)
sys.path.append(str(script.parent))
from secure_io import preflight_output_path

raw = sys.argv[2]
target = preflight_output_path(raw)
print(os.fspath(target))
' "$SECURE_IO_SCRIPT" "$SAVE_JSON_PATH" 2>/dev/null)" || return 1
  [ -n "$normalized" ] && [ "${normalized#/}" != "$normalized" ] || return 1
  SAVE_JSON_PATH="$normalized"
  SAVE_JSON_NORMALIZED=1
}

build_json_payload() {
  local ok="$1"
  local status="$2"
  local error_message="${3:-}"
  local py
  py="$(json_python)" || return 1

  without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search - \
    "$ok" "$status" "$error_message" "$SELECTED_MODE" "$RUNTIME_DISPLAY_PATH" \
    "$RUNTIME_SOURCE" "$INSTALL_DEPENDENCIES" "$INSTALL_DEV_DEPENDENCIES" \
    "$RUN_SMOKE_TEST" "$RUN_FULL_CHECK" \
    "$VENV_CREATED" "$USED_EXISTING_VENV" "$REQUIREMENTS_INSTALLED" \
    "$SMOKE_RESULT_PATH" "$SELFCHECK_RESULT_PATH" "$SAVE_JSON_PATH" \
    "$EXIT_KIND" "$INSTALL_EXIT_CODE" "$DEPRECATED_SKIP_SMOKE" "$POST_COMMIT_WARNING" <<'PY'
import json
import sys

(
    ok,
    status,
    error,
    mode,
    python,
    runtime_source,
    install_dependencies,
    install_dev_dependencies,
    smoke_test,
    full_check,
    venv_created,
    used_existing_venv,
    requirements_installed,
    smoke_result,
    selfcheck_result,
    saved_json,
    exit_kind,
    exit_code,
    deprecated_skip_smoke,
    post_commit_warning,
) = sys.argv[1:]

payload = {
    'ok': ok == 'true',
    'status': status,
    'mode': mode,
    'python': python,
    'runtimeSource': runtime_source,
    'installDependencies': install_dependencies == '1',
    'installDevDependencies': install_dev_dependencies == '1',
    'smokeTest': smoke_test == '1',
    'fullCheck': full_check == '1',
    'venvCreated': venv_created == '1',
    'usedExistingVenv': used_existing_venv == '1',
    'requirementsInstalled': requirements_installed == '1',
    'smokeTestResultPath': smoke_result or None,
    'selfcheckResultPath': selfcheck_result or None,
    'savedJsonPath': saved_json or None,
    'exitKind': exit_kind,
    'exitCode': int(exit_code),
    'deprecatedSkipSmokeTest': deprecated_skip_smoke == '1',
    'postCommitWarning': post_commit_warning or None,
    'error': error or None,
}
print(json.dumps(payload, ensure_ascii=True, separators=(',', ':')))
PY
}

secure_save_json() {
  local payload="$1"
  local py
  [ "$SAVE_JSON_REQUESTED" -eq 1 ] || return 0
  source_tree_is_safe || return 1
  py="$(json_python)" || return 1
  [ -f "$SECURE_IO_SCRIPT" ] && [ ! -L "$SECURE_IO_SCRIPT" ] || return 1
  printf '%s\n' "$payload" | without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    "$SECURE_IO_SCRIPT" --path "$SAVE_JSON_PATH" >/dev/null 2>&1
}

emit_json_result() {
  local ok="$1"
  local status="$2"
  local error_message="${3:-}"
  local payload
  normalize_save_json_path || return 1
  payload="$(build_json_payload "$ok" "$status" "$error_message")" || return 1
  secure_save_json "$payload" || return 1
  printf '%s\n' "$payload"
}

fail() {
  local message="$1"
  local code="${2:-10}"
  local kind="${3:-install_error}"
  local failed_save_path
  INSTALL_EXIT_CODE="$code"
  EXIT_KIND="$kind"

  if [ "$OUTPUT_FORMAT" = "json" ]; then
    if ! emit_json_result false failed "$message"; then
      failed_save_path="$SAVE_JSON_PATH"
      SAVE_JSON_PATH=""
      SAVE_JSON_REQUESTED=0
      SAVE_JSON_NORMALIZED=0
      INSTALL_EXIT_CODE=10
      EXIT_KIND="install_error"
      code=10
      emit_json_result false failed "$message; could not securely save JSON to $failed_save_path" || \
        printf '{"ok":false,"status":"failed","exitKind":"install_error","exitCode":10,"error":"unable to encode install failure"}\n'
    fi
  elif [ "$QUIET" -ne 1 ]; then
    printf '[google-search] ERROR: %s\n' "$message" >&2
  fi
  exit "$code"
}

path_owner_is_safe() {
  local path="$1"
  local owner mode current_uid
  current_uid="$(/usr/bin/id -u)"
  owner="$(/usr/bin/stat -c '%u' -- "$path" 2>/dev/null)" || return 1
  mode="$(/usr/bin/stat -c '%a' -- "$path" 2>/dev/null)" || return 1
  case "$mode" in
    *[!0-7]*|'') return 1 ;;
  esac
  [ "$owner" = "$current_uid" ] || [ "$owner" = "0" ] || return 1
  [ $((8#$mode & 0022)) -eq 0 ]
}

path_parent_chain_is_safe() {
  local path="$1"
  local current child_owner owner mode current_uid
  current_uid="$(/usr/bin/id -u)"
  child_owner="$(/usr/bin/stat -c '%u' -- "$path" 2>/dev/null)" || return 1
  current="${path%/*}"
  [ -n "$current" ] || current='/'
  while :; do
    [ -d "$current" ] && [ ! -L "$current" ] || return 1
    IFS='|' read -r owner mode < <(/usr/bin/stat -c '%u|%a' -- "$current" 2>/dev/null) || return 1
    [ "$owner" = "$current_uid" ] || [ "$owner" = "0" ] || return 1
    case "$mode" in
      *[!0-7]*|'') return 1 ;;
    esac
    if [ $((8#$mode & 0022)) -ne 0 ]; then
      [ "$mode" = "1777" ] || return 1
      [ "$child_owner" = "$current_uid" ] || [ "$child_owner" = "0" ] || return 1
    fi
    [ "$current" != '/' ] || break
    child_owner="$owner"
    current="${current%/*}"
    [ -n "$current" ] || current='/'
  done
}

python_executable_is_safe() {
  local path="$1"
  local links mode
  [ -f "$path" ] && [ ! -L "$path" ] && [ -x "$path" ] || return 1
  path_owner_is_safe "$path" || return 1
  path_parent_chain_is_safe "$path" || return 1
  links="$(/usr/bin/stat -c '%h' -- "$path" 2>/dev/null)" || return 1
  mode="$(/usr/bin/stat -c '%a' -- "$path" 2>/dev/null)" || return 1
  [ "$links" -eq 1 ] || return 1
  [ $((8#$mode & 06000)) -eq 0 ]
}

python_link_chain_is_safe() {
  local path="$1"
  local current_uid link_owner target current component remainder pending candidate
  local link_count=0
  current_uid="$(id -u)"

  case "$path" in /*) ;; *) return 1 ;; esac
  case "$path" in *'//'*|*/./*|*/../*|*/.|*/..) return 1 ;; esac
  current="/"
  pending="${path#/}"
  while [ -n "$pending" ]; do
    component="${pending%%/*}"
    if [ "$component" = "$pending" ]; then
      remainder=""
    else
      remainder="${pending#*/}"
    fi
    [ -n "$component" ] && [ "$component" != "." ] && [ "$component" != ".." ] || return 1
    candidate="${current%/}/$component"
    if [ -L "$candidate" ]; then
      link_count=$((link_count + 1))
      [ "$link_count" -le 40 ] || return 1
      path_parent_chain_is_safe "$candidate" || return 1
      link_owner="$(stat -c '%u' -- "$candidate" 2>/dev/null)" || return 1
      [ "$link_owner" = "$current_uid" ] || [ "$link_owner" = "0" ] || return 1
      target="$(readlink -- "$candidate" 2>/dev/null)" || return 1
      [ -n "$target" ] || return 1
      case "$target" in *'//'*|*/./*|*/../*|*/.|*/..) return 1 ;; esac
      if [[ "$target" = /* ]]; then
        current="/"
        pending="${target#/}"
      else
        pending="$target"
      fi
      if [ -n "$remainder" ]; then
        pending="$pending/$remainder"
      fi
      continue
    fi
    if [ -n "$remainder" ]; then
      [ -d "$candidate" ] || return 1
      current="$candidate"
      pending="$remainder"
      continue
    fi
    path="$candidate"
    pending=""
  done
  python_executable_is_safe "$path"
}

directory_link_chain_is_safe() {
  local path="$1"
  local current_uid link_owner target current component remainder pending candidate resolved
  local link_count=0
  current_uid="$(id -u)"

  case "$path" in /*) ;; *) return 1 ;; esac
  case "$path" in *'//'*|*/./*|*/../*|*/.|*/..) return 1 ;; esac
  current="/"
  pending="${path#/}"
  while [ -n "$pending" ]; do
    component="${pending%%/*}"
    if [ "$component" = "$pending" ]; then
      remainder=""
    else
      remainder="${pending#*/}"
    fi
    [ -n "$component" ] && [ "$component" != "." ] && [ "$component" != ".." ] || return 1
    candidate="${current%/}/$component"
    if [ -L "$candidate" ]; then
      link_count=$((link_count + 1))
      [ "$link_count" -le 40 ] || return 1
      path_parent_chain_is_safe "$candidate" || return 1
      link_owner="$(stat -c '%u' -- "$candidate" 2>/dev/null)" || return 1
      [ "$link_owner" = "$current_uid" ] || [ "$link_owner" = "0" ] || return 1
      target="$(readlink -- "$candidate" 2>/dev/null)" || return 1
      [ -n "$target" ] || return 1
      case "$target" in *'//'*|*/./*|*/../*|*/.|*/..) return 1 ;; esac
      if [[ "$target" = /* ]]; then
        current="/"
        pending="${target#/}"
      else
        pending="$target"
      fi
      if [ -n "$remainder" ]; then
        pending="$pending/$remainder"
      fi
      continue
    fi
    [ -d "$candidate" ] || return 1
    current="$candidate"
    pending="$remainder"
  done
  [ -d "$current" ] && [ ! -L "$current" ] || return 1
  path_owner_is_safe "$current" || return 1
  path_parent_chain_is_safe "$current" || return 1
  resolved="$(readlink -f -- "$current" 2>/dev/null)" || return 1
  [ -n "$resolved" ] && [ "$resolved" = "$current" ] || return 1
  printf '%s\n' "$resolved"
}

path_tree_is_safe() {
  local root="$1"
  local current_uid root_device
  current_uid="$(id -u)"
  root_device="$(stat -c '%d' -- "$root" 2>/dev/null)" || return 1

  find -P "$root" -xdev -print0 2>/dev/null |
    while IFS= read -r -d '' path; do
      local device owner mode
      IFS='|' read -r device owner mode < <(
        stat -c '%d|%u|%a' -- "$path" 2>/dev/null
      ) || exit 1
      [ "$device" = "$root_device" ] || exit 1
      [ "$owner" = "$current_uid" ] || [ "$owner" = "0" ] || exit 1
      case "$mode" in
        *[!0-7]*|'') exit 1 ;;
      esac
      [ $((8#$mode & 0022)) -eq 0 ] || exit 1
      [ ! -L "$path" ] || exit 1
      [ -f "$path" ] || [ -d "$path" ] || exit 1
    done
}

trusted_tree_is_safe() {
  local root="$1"
  local current_uid root_device
  current_uid="$(id -u)"
  [ -d "$root" ] && [ ! -L "$root" ] || return 1
  path_owner_is_safe "$root" || return 1
  path_parent_chain_is_safe "$root" || return 1
  root_device="$(stat -c '%d' -- "$root" 2>/dev/null)" || return 1

  find -P "$root" -xdev -printf '%D|%U|%m|%y\0' 2>/dev/null |
    while IFS='|' read -r -d '' device owner mode kind; do
      [ "$device" = "$root_device" ] || exit 1
      [ "$owner" = "$current_uid" ] || [ "$owner" = "0" ] || exit 1
      case "$kind" in
        f|d)
          case "$mode" in *[!0-7]*|'') exit 1 ;; esac
          [ $((8#$mode & 0022)) -eq 0 ] || exit 1
          ;;
        l) ;;
        *) exit 1 ;;
      esac
    done || return 1

  find -P "$root" -xdev -type l -print0 2>/dev/null |
    while IFS= read -r -d '' link; do
      trusted_symlink_target_record "$link" >/dev/null || exit 1
    done
}

trusted_symlink_target_record() {
  local link="$1"
  local current_uid link_owner resolved before after size digest
  current_uid="$(id -u)"
  [ -L "$link" ] || return 1
  path_parent_chain_is_safe "$link" || return 1
  link_owner="$(stat -c '%u' -- "$link" 2>/dev/null)" || return 1
  [ "$link_owner" = "$current_uid" ] || [ "$link_owner" = "0" ] || return 1
  resolved="$(readlink -f -- "$link" 2>/dev/null)" || return 1
  [ -n "$resolved" ] && [ -f "$resolved" ] && [ ! -L "$resolved" ] || return 1
  path_owner_is_safe "$resolved" || return 1
  path_parent_chain_is_safe "$resolved" || return 1
  before="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$resolved" 2>/dev/null)" || return 1
  size="$(stat -Lc '%s' -- "$resolved" 2>/dev/null)" || return 1
  case "$size" in ''|*[!0-9]*) return 1 ;; esac
  [ "$size" -le 134217728 ] || return 1
  digest="$(sha256sum -- "$resolved")" || return 1
  digest="${digest%% *}"
  [ "${#digest}" -eq 64 ] || return 1
  case "$digest" in *[!0-9a-f]*) return 1 ;; esac
  after="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$resolved" 2>/dev/null)" || return 1
  [ "$after" = "$before" ] || return 1
  printf 'T|%s|%s|%s|%s\0' "$link" "$resolved" "$before" "$digest"
}

trusted_zip_is_safe() {
  local path="$1"
  local links size
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    path_owner_is_safe "${path%/*}" || return 1
    path_parent_chain_is_safe "${path%/*}" || return 1
    return 0
  fi
  [ -f "$path" ] && [ ! -L "$path" ] || return 1
  path_owner_is_safe "$path" || return 1
  path_parent_chain_is_safe "$path" || return 1
  links="$(stat -c '%h' -- "$path" 2>/dev/null)" || return 1
  size="$(stat -c '%s' -- "$path" 2>/dev/null)" || return 1
  [ "$links" -eq 1 ] && [ "$size" -le 67108864 ]
}

tree_payload_digest() {
  local root="$1"
  local include_followed="${2:-0}"
  local digest
  if [ "$include_followed" -eq 1 ]; then
    digest="$({
      find -P "$root" -xdev -mindepth 1 \
        -printf 'P|%D|%i|%U|%G|%m|%n|%s|%T@|%C@|%y|%P|%l\0' &&
      find -P "$root" -xdev -mindepth 1 -type l -print0 |
        while IFS= read -r -d '' link; do
          trusted_symlink_target_record "$link" || exit 1
        done
    } 2>/dev/null | LC_ALL=C sort -z | sha256sum)" || return 1
  else
    digest="$(find -P "$root" -xdev -mindepth 1 \
      -printf 'P|%D|%i|%U|%G|%m|%n|%s|%T@|%C@|%y|%P|%l\0' 2>/dev/null | \
      LC_ALL=C sort -z | sha256sum)" || return 1
  fi
  digest="${digest%% *}"
  [ "${#digest}" -eq 64 ] || return 1
  case "$digest" in *[!0-9a-f]*) return 1 ;; esac
  printf '%s\n' "$digest"
}

zip_metadata_digest() {
  local path="$1"
  local digest
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    printf 'absent\n'
    return
  fi
  trusted_zip_is_safe "$path" || return 1
  digest="$(sha256sum -- "$path")" || return 1
  digest="${digest%% *}"
  printf '%s:%s\n' \
    "$(stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$path")" "$digest"
}

find_stdlib_layout() {
  local start="$1"
  local minor="$2"
  local platlib abi current parent prefix zip stdlib depth
  local records=()
  local abis=("")
  case "$minor" in 13|14) abis+=("t") ;; esac

  for platlib in lib lib64; do
    for abi in "${abis[@]}"; do
      prefix=""
      zip=""
      if [ "$minor" -ne 10 ]; then
        current="$start"
        for ((depth = 0; depth < 128; depth++)); do
          [ "$current" != "/" ] || break
          if [ -e "$current/$platlib/python3${minor}${abi}.zip" ] || \
            [ -L "$current/$platlib/python3${minor}${abi}.zip" ]; then
            prefix="$current"
            zip="$current/$platlib/python3${minor}${abi}.zip"
            break
          fi
          parent="${current%/*}"
          [ -n "$parent" ] || parent="/"
          current="$parent"
        done
      fi
      if [ -n "$prefix" ]; then
        stdlib="$prefix/$platlib/python3.$minor$abi"
        if [ ! -f "$stdlib/os.py" ] && [ ! -f "$stdlib/os.pyc" ]; then
          return 1
        fi
      else
        current="$start"
        for ((depth = 0; depth < 128; depth++)); do
          [ "$current" != "/" ] || break
          stdlib="$current/$platlib/python3.$minor$abi"
          if [ -f "$stdlib/os.py" ] || [ -f "$stdlib/os.pyc" ]; then
            prefix="$current"
            zip="$current/$platlib/python3${minor}${abi}.zip"
            break
          fi
          parent="${current%/*}"
          [ -n "$parent" ] || parent="/"
          current="$parent"
        done
      fi
      if [ -n "$prefix" ]; then
        stdlib="$(readlink -f -- "$stdlib" 2>/dev/null)" || return 1
        [ -n "$stdlib" ] || return 1
        records+=("$stdlib|$zip")
      fi
    done
  done
  [ "${#records[@]}" -gt 0 ] || return 2
  printf '%s\n' "${records[@]}"
}

base_python_runtime() {
  local candidate="$1"
  local resolved basename minor bin_dir prefix record_output layout_status
  local record stdlib zip stdlib_list zip_list control
  local stdlib_candidates=() zip_candidates=() controls=()
  local -A seen_stdlibs=() seen_zips=()

  python_link_chain_is_safe "$candidate" || return 1
  resolved="$(readlink -f -- "$candidate" 2>/dev/null)" || return 1
  [ -n "$resolved" ] || return 1
  basename="${resolved##*/}"
  if [[ "$basename" =~ ^python3\.(10|11|12|13|14)t?$ ]]; then
    minor="${BASH_REMATCH[1]}"
  else
    return 1
  fi
  bin_dir="${resolved%/*}"
  prefix="${bin_dir%/*}"
  [ -n "$prefix" ] || prefix="/"
  controls=("$bin_dir/pyvenv.cfg" "$prefix/pyvenv.cfg")
  shopt -s nullglob
  controls+=("$bin_dir"/python*._pth "$prefix"/python*._pth)
  shopt -u nullglob
  for control in "${controls[@]}"; do
    if [ -e "$control" ] || [ -L "$control" ]; then
      return 1
    fi
  done

  if record_output="$(find_stdlib_layout "$bin_dir" "$minor")"; then
    :
  else
    layout_status=$?
    [ "$layout_status" -eq 2 ] || return 1
    return 1
  fi
  while IFS= read -r record; do
    [ -n "$record" ] || return 1
    IFS='|' read -r stdlib zip <<<"$record"
    [ -n "$stdlib" ] && [ -n "$zip" ] || return 1
    zip="$(readlink -m -- "$zip" 2>/dev/null)" || return 1
    case "$stdlib$zip" in *['|:']*) return 1 ;; esac
    [ -d "$stdlib/lib-dynload" ] && [ ! -L "$stdlib/lib-dynload" ] || return 1
    if [ ! -f "$stdlib/encodings/__init__.py" ] && \
      [ ! -f "$stdlib/encodings/__init__.pyc" ]; then
      return 1
    fi
    trusted_tree_is_safe "$stdlib" || return 1
    trusted_zip_is_safe "$zip" || return 1
    if [ -z "${seen_stdlibs[$stdlib]+x}" ]; then
      seen_stdlibs[$stdlib]=1
      stdlib_candidates+=("$stdlib")
    fi
    if [ -z "${seen_zips[$zip]+x}" ]; then
      seen_zips[$zip]=1
      zip_candidates+=("$zip")
    fi
  done <<<"$record_output"
  [ "${#stdlib_candidates[@]}" -gt 0 ] && [ "${#stdlib_candidates[@]}" -le 4 ] || return 1
  [ "${#zip_candidates[@]}" -gt 0 ] && [ "${#zip_candidates[@]}" -le 4 ] || return 1
  stdlib_list="$(IFS=:; printf '%s' "${stdlib_candidates[*]}")"
  zip_list="$(IFS=:; printf '%s' "${zip_candidates[*]}")"
  printf '%s|%s|%s|%s\n' "$resolved" "$minor" "$stdlib_list" "$zip_list"
}

base_python_runtime_snapshot() {
  local runtime="$1"
  local resolved minor stdlib_list zip_list executable_metadata item item_metadata item_digest
  local stdlibs=() zips=()
  IFS='|' read -r resolved minor stdlib_list zip_list <<<"$runtime"
  [ -n "$resolved" ] && [ -n "$minor" ] && [ -n "$stdlib_list" ] && [ -n "$zip_list" ] || \
    return 1
  executable_metadata="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$resolved")" || return 1
  IFS=':' read -r -a stdlibs <<<"$stdlib_list"
  IFS=':' read -r -a zips <<<"$zip_list"
  printf '%s|%s|%s' "$runtime" "$executable_metadata" "base-python-v1"
  for item in "${stdlibs[@]}"; do
    item_metadata="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$item")" || return 1
    item_digest="$(tree_payload_digest "$item" 1)" || return 1
    printf '|stdlib:%s:%s:%s' "$item" "$item_metadata" "$item_digest"
  done
  for item in "${zips[@]}"; do
    item_digest="$(zip_metadata_digest "$item")" || return 1
    printf '|zip:%s:%s' "$item" "$item_digest"
  done
  printf '\n'
}

probe_base_python_runtime() {
  local runtime="$1"
  local resolved minor stdlib_list zip_list result
  IFS='|' read -r resolved minor stdlib_list zip_list <<<"$runtime"
  result="$(without_api_keys_and_install_lock "$resolved" -I -S -B \
    -X pycache_prefix=/dev/null/google-search - \
    "$BASE_PYTHON_SENTINEL" "$resolved" "$minor" "$stdlib_list" "$zip_list" <<'PY' 2>/dev/null
import os
import sys
from pathlib import Path

import encodings

sentinel, executable, minor, stdlib_list, zip_list = sys.argv[1:]
stdlibs = {Path(value).resolve(strict=True) for value in stdlib_list.split(':')}
zips = {Path(value).resolve(strict=False) for value in zip_list.split(':')}
expected_executable = Path(executable).resolve(strict=True)
if sys.version_info[:2] != (3, int(minor)):
    raise SystemExit(1)
if Path(sys.executable).resolve(strict=True) != expected_executable:
    raise SystemExit(1)
if Path(getattr(sys, '_base_executable', sys.executable)).resolve(strict=True) != expected_executable:
    raise SystemExit(1)
if Path(sys.prefix).resolve(strict=True) != Path(sys.base_prefix).resolve(strict=True):
    raise SystemExit(1)
allowed_paths = stdlibs | zips
allowed_paths.update(root / 'lib-dynload' for root in stdlibs)
if any(not raw or Path(raw).resolve(strict=False) not in allowed_paths for raw in sys.path):
    raise SystemExit(1)

def trusted_origin(raw_origin):
    if not raw_origin:
        return False
    if any(raw_origin.startswith(f'{zip_path}{os.sep}') for zip_path in zips):
        return True
    try:
        origin = Path(raw_origin).resolve(strict=True)
    except OSError:
        return False
    return any(origin == root or root in origin.parents for root in stdlibs)

if not trusted_origin(getattr(os, '__file__', None)):
    raise SystemExit(1)
if not trusted_origin(getattr(encodings, '__file__', None)):
    raise SystemExit(1)
print(sentinel)
PY
)" || return 1
  [ "$result" = "$BASE_PYTHON_SENTINEL" ]
}

initialize_base_python() {
  local candidate before after current_runtime
  candidate="$(command -v python3 2>/dev/null)" || return 1
  BASE_PYTHON_RUNTIME="$(base_python_runtime "$candidate")" || return 1
  IFS='|' read -r BASE_PYTHON_PATH _ _ _ <<<"$BASE_PYTHON_RUNTIME"
  [ -n "$BASE_PYTHON_PATH" ] || return 1
  before="$(base_python_runtime_snapshot "$BASE_PYTHON_RUNTIME")" || return 1
  probe_base_python_runtime "$BASE_PYTHON_RUNTIME" || return 1
  current_runtime="$(base_python_runtime "$BASE_PYTHON_PATH")" || return 1
  [ "$current_runtime" = "$BASE_PYTHON_RUNTIME" ] || return 1
  after="$(base_python_runtime_snapshot "$current_runtime")" || return 1
  [ "$after" = "$before" ] || return 1
  BASE_PYTHON_SNAPSHOT="$before"
}

base_python_runtime_matches_snapshot() {
  local current_runtime current_snapshot
  [ -n "$BASE_PYTHON_RUNTIME" ] && [ -n "$BASE_PYTHON_SNAPSHOT" ] || return 1
  current_runtime="$(base_python_runtime "$BASE_PYTHON_PATH")" || return 1
  [ "$current_runtime" = "$BASE_PYTHON_RUNTIME" ] || return 1
  current_snapshot="$(base_python_runtime_snapshot "$current_runtime")" || return 1
  [ "$current_snapshot" = "$BASE_PYTHON_SNAPSHOT" ]
}

copy_base_executable() {
  local venv_python="$1"
  local home="$2"
  local minor="$3"
  local configured_executable="$4"
  local versioned candidate candidate_resolved
  candidate="$home/python3.$minor"
  [ -f "$candidate" ] || return 1
  python_link_chain_is_safe "$candidate" || return 1
  versioned="$(readlink -f -- "$candidate" 2>/dev/null)" || return 1
  for candidate in "$home/${venv_python##*/}" "$home/python3"; do
    [ -f "$candidate" ] || continue
    python_link_chain_is_safe "$candidate" || return 1
    candidate_resolved="$(readlink -f -- "$candidate" 2>/dev/null)" || return 1
    [ "$candidate_resolved" = "$versioned" ] || return 1
  done
  if [ -n "$configured_executable" ]; then
    candidate_resolved="$(readlink -f -- "$configured_executable" 2>/dev/null)" || return 1
    [ "$candidate_resolved" = "$versioned" ] || return 1
  fi
  printf '%s\n' "$versioned"
}

venv_config_runtime() {
  local venv_dir="$1"
  local include_count=0 home_count=0 executable_count=0 version_count=0
  local line value home resolved_home executable="" version minor
  local venv_python resolved_python configured_resolved="" copy_base="" record_output layout_status
  local start record stdlib zip base_list stdlib_list zip_list candidate
  local base_candidates=() starts=() stdlib_candidates=() zip_candidates=()
  local -A seen_bases=() seen_starts=() seen_stdlibs=() seen_zips=()
  local nocasematch_was_set=0

  shopt -q nocasematch && nocasematch_was_set=1
  shopt -s nocasematch
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^[[:space:]]*include-system-site-packages[[:space:]]*= ]]; then
      include_count=$((include_count + 1))
      if [[ ! "$line" =~ ^[[:space:]]*include-system-site-packages[[:space:]]*=[[:space:]]*false[[:space:]]*$ ]]; then
        [ "$nocasematch_was_set" -eq 1 ] || shopt -u nocasematch
        return 1
      fi
    elif [[ "$line" =~ ^[[:space:]]*home[[:space:]]*=(.*)$ ]]; then
      home_count=$((home_count + 1))
      value="${BASH_REMATCH[1]}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      home="$value"
    elif [[ "$line" =~ ^[[:space:]]*executable[[:space:]]*=(.*)$ ]]; then
      executable_count=$((executable_count + 1))
      value="${BASH_REMATCH[1]}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      executable="$value"
    elif [[ "$line" =~ ^[[:space:]]*version[[:space:]]*=(.*)$ ]]; then
      version_count=$((version_count + 1))
      value="${BASH_REMATCH[1]}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      version="$value"
    fi
  done <"$venv_dir/pyvenv.cfg"
  [ "$nocasematch_was_set" -eq 1 ] || shopt -u nocasematch
  [ "$include_count" -eq 1 ] && [ "$home_count" -eq 1 ] && \
    [ "$version_count" -eq 1 ] && [ -n "$home" ] || return 1
  [ "$executable_count" -le 1 ] || return 1
  if [[ ! "$version" =~ ^3\.([0-9]+)(\.[0-9]+)?$ ]]; then
    return 1
  fi
  minor="${BASH_REMATCH[1]}"
  case "$minor" in 10|11|12|13|14) ;; *) return 1 ;; esac
  case "$home" in /*) ;; *) return 1 ;; esac
  case "$home" in *'|'*) return 1 ;; esac
  resolved_home="$(directory_link_chain_is_safe "$home")" || return 1
  if [ "$executable_count" -eq 1 ]; then
    [ -n "$executable" ] || return 1
    python_link_chain_is_safe "$executable" || return 1
    configured_resolved="$(readlink -f -- "$executable" 2>/dev/null)" || return 1
  fi
  venv_python="$venv_dir/bin/python"
  python_link_chain_is_safe "$venv_python" || return 1
  resolved_python="$(readlink -f -- "$venv_python" 2>/dev/null)" || return 1
  [ -n "$resolved_python" ] || return 1
  python_executable_is_safe "$resolved_python" || return 1
  case "$resolved_python" in
    "$venv_dir"/*)
      copy_base="$(copy_base_executable "$venv_python" "$resolved_home" "$minor" "$executable")" || \
        return 1
      ;;
  esac
  for candidate in "$resolved_python" "$copy_base" "$configured_resolved"; do
    [ -n "$candidate" ] || continue
    python_executable_is_safe "$candidate" || return 1
    if [ -z "${seen_bases[$candidate]+x}" ]; then
      seen_bases[$candidate]=1
      base_candidates+=("$candidate")
    fi
  done
  [ "${#base_candidates[@]}" -gt 0 ] && [ "${#base_candidates[@]}" -le 4 ] || return 1

  for start in "$resolved_home" "${base_candidates[@]/%//}"; do
    if [[ "$start" == */ ]]; then
      start="${start%/}"
      start="${start%/*}"
      [ -n "$start" ] || start="/"
    fi
    [ -d "$start" ] && [ ! -L "$start" ] || return 1
    if [ -z "${seen_starts[$start]+x}" ]; then
      seen_starts[$start]=1
      starts+=("$start")
    fi
  done

  for start in "${starts[@]}"; do
    if record_output="$(find_stdlib_layout "$start" "$minor")"; then
      :
    else
      layout_status=$?
      [ "$layout_status" -eq 2 ] || return 1
      continue
    fi
    while IFS= read -r record; do
      [ -n "$record" ] || return 1
      IFS='|' read -r stdlib zip <<<"$record"
      [ -n "$stdlib" ] && [ -n "$zip" ] || return 1
      zip="$(readlink -m -- "$zip" 2>/dev/null)" || return 1
      case "$stdlib$zip" in *['|:']*) return 1 ;; esac
      [ -d "$stdlib/lib-dynload" ] && [ ! -L "$stdlib/lib-dynload" ] || return 1
      if [ ! -f "$stdlib/encodings/__init__.py" ] && \
        [ ! -f "$stdlib/encodings/__init__.pyc" ]; then
        return 1
      fi
      trusted_tree_is_safe "$stdlib" || return 1
      trusted_zip_is_safe "$zip" || return 1
      if [ -z "${seen_stdlibs[$stdlib]+x}" ]; then
        seen_stdlibs[$stdlib]=1
        stdlib_candidates+=("$stdlib")
      fi
      if [ -z "${seen_zips[$zip]+x}" ]; then
        seen_zips[$zip]=1
        zip_candidates+=("$zip")
      fi
    done <<<"$record_output"
  done
  [ "${#stdlib_candidates[@]}" -gt 0 ] && [ "${#stdlib_candidates[@]}" -le 8 ] || return 1
  [ "${#zip_candidates[@]}" -gt 0 ] && [ "${#zip_candidates[@]}" -le 8 ] || return 1
  case "$resolved_home${base_candidates[*]}${stdlib_candidates[*]}${zip_candidates[*]}" in
    *['|:']*) return 1 ;;
  esac
  base_list="$(IFS=:; printf '%s' "${base_candidates[*]}")"
  stdlib_list="$(IFS=:; printf '%s' "${stdlib_candidates[*]}")"
  zip_list="$(IFS=:; printf '%s' "${zip_candidates[*]}")"
  printf '%s|%s|%s|%s\n' "$resolved_home" "$base_list" "$stdlib_list" "$zip_list"
}

venv_structure_is_safe() {
  local venv_dir="$1"
  local allow_startup_hooks="${2:-0}"
  local config_size venv_home base_executables venv_stdlibs venv_zips venv_python venv_runtime
  venv_python="$venv_dir/bin/python"
  [ -d "$venv_dir" ] && [ ! -L "$venv_dir" ] || return 1
  [ -d "$venv_dir/bin" ] && [ ! -L "$venv_dir/bin" ] || return 1
  [ -f "$venv_dir/pyvenv.cfg" ] && [ ! -L "$venv_dir/pyvenv.cfg" ] || return 1
  [ -x "$venv_python" ] || return 1
  path_owner_is_safe "$venv_dir" || return 1
  path_owner_is_safe "$venv_dir/bin" || return 1
  path_owner_is_safe "$venv_dir/pyvenv.cfg" || return 1
  config_size="$(stat -c '%s' -- "$venv_dir/pyvenv.cfg" 2>/dev/null)" || return 1
  [ "$config_size" -le 16384 ] || return 1
  venv_runtime="$(venv_config_runtime "$venv_dir")" || return 1
  IFS='|' read -r venv_home base_executables venv_stdlibs venv_zips <<<"$venv_runtime"
  python_link_chain_is_safe "$venv_python" || return 1
  [ -n "$venv_home" ] && [ -n "$base_executables" ] && [ -n "$venv_stdlibs" ] && \
    [ -n "$venv_zips" ] || return 1
  venv_site_paths_are_safe "$venv_dir" "$allow_startup_hooks" || return 1
}

snapshot_path_label() {
  local venv_dir="$1"
  local path="$2"
  if [ "$path" = "$venv_dir" ]; then
    printf '@venv\n'
    return
  fi
  case "$path" in
    "$venv_dir"/*) printf '@venv/%s\n' "${path#"$venv_dir"/}" ;;
    *) printf '%s\n' "$path" ;;
  esac
}

venv_runtime_snapshot() {
  local venv_dir="$1"
  local venv_python="$venv_dir/bin/python"
  local resolved_python resolved_label venv_home base_list stdlib_list zip_list venv_runtime
  local item label item_metadata item_digest
  local root_metadata config_metadata python_metadata resolved_metadata home_metadata tree_digest
  local base_executables=() venv_stdlibs=() venv_zips=()
  venv_structure_is_safe "$venv_dir" || return 1
  resolved_python="$(readlink -f -- "$venv_python" 2>/dev/null)" || return 1
  resolved_label="$(snapshot_path_label "$venv_dir" "$resolved_python")" || return 1
  venv_runtime="$(venv_config_runtime "$venv_dir")" || return 1
  IFS='|' read -r venv_home base_list stdlib_list zip_list <<<"$venv_runtime"
  [ -n "$venv_home" ] && [ -n "$base_list" ] && [ -n "$stdlib_list" ] && \
    [ -n "$zip_list" ] || return 1
  IFS=':' read -r -a base_executables <<<"$base_list"
  IFS=':' read -r -a venv_stdlibs <<<"$stdlib_list"
  IFS=':' read -r -a venv_zips <<<"$zip_list"
  root_metadata="$(stat -c '%d:%i:%u:%g:%f:%h:%s' -- "$venv_dir")" || return 1
  config_metadata="$(stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- \
    "$venv_dir/pyvenv.cfg")" || return 1
  python_metadata="$(stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$venv_python")" || return 1
  resolved_metadata="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- \
    "$resolved_python")" || return 1
  home_metadata="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$venv_home")" || return 1
  tree_digest="$(tree_payload_digest "$venv_dir")" || return 1
  printf '%s|%s|%s|%s|%s|%s|%s' \
    "$root_metadata" "$config_metadata" "$python_metadata" "$resolved_label" \
    "$resolved_metadata" "$venv_home:$home_metadata" "venv-tree:$tree_digest"
  for item in "${base_executables[@]}"; do
    label="$(snapshot_path_label "$venv_dir" "$item")" || return 1
    item_metadata="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$item")" || return 1
    printf '|base:%s:%s' "$label" "$item_metadata"
  done
  for item in "${venv_stdlibs[@]}"; do
    item_metadata="$(stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$item")" || return 1
    item_digest="$(tree_payload_digest "$item" 1)" || return 1
    printf '|stdlib:%s:%s:%s' "$item" "$item_metadata" "$item_digest"
  done
  for item in "${venv_zips[@]}"; do
    item_digest="$(zip_metadata_digest "$item")" || return 1
    printf '|zip:%s:%s' "$item" "$item_digest"
  done
  printf '\n'
}

stable_venv_runtime_snapshot() {
  local venv_dir="$1"
  local before after
  before="$(venv_runtime_snapshot "$venv_dir")" || return 1
  after="$(venv_runtime_snapshot "$venv_dir")" || return 1
  [ "$after" = "$before" ] || return 1
  printf '%s\n' "$before"
}

candidate_runtime_matches_snapshot() {
  local venv_dir="$1"
  local current
  [ -n "$CANDIDATE_RUNTIME_SNAPSHOT" ] || return 1
  current="$(stable_venv_runtime_snapshot "$venv_dir")" || return 1
  [ "$current" = "$CANDIDATE_RUNTIME_SNAPSHOT" ]
}

venv_site_paths_are_safe() {
  local venv_dir="$1"
  local allow_startup_hooks="${2:-0}"
  local python_dir site_dir
  local found=0
  local site_dirs=()
  local startup_hooks=()

  [ -d "$venv_dir/lib" ] && [ ! -L "$venv_dir/lib" ] || return 1
  path_owner_is_safe "$venv_dir/lib" || return 1

  shopt -s nullglob
  site_dirs=("$venv_dir"/lib/python*/site-packages)
  shopt -u nullglob
  for site_dir in "${site_dirs[@]}"; do
    python_dir="$(dirname -- "$site_dir")"
    [ -d "$python_dir" ] && [ ! -L "$python_dir" ] || return 1
    [ -d "$site_dir" ] && [ ! -L "$site_dir" ] || return 1
    path_owner_is_safe "$python_dir" || return 1
    path_owner_is_safe "$site_dir" || return 1
    path_tree_is_safe "$site_dir" || return 1
    if [ "$allow_startup_hooks" -eq 0 ]; then
      if [ -e "$site_dir/sitecustomize" ] || [ -L "$site_dir/sitecustomize" ] || \
        [ -e "$site_dir/usercustomize" ] || [ -L "$site_dir/usercustomize" ]; then
        return 1
      fi
      shopt -s nullglob
      startup_hooks=(
        "$site_dir"/*.pth
        "$site_dir"/sitecustomize.*
        "$site_dir"/usercustomize.*
      )
      shopt -u nullglob
      [ "${#startup_hooks[@]}" -eq 0 ] || return 1
    fi
    found=1
  done
  [ "$found" -eq 1 ]
}

venv_prefix_is_usable() {
  local venv_dir="$1"
  local result venv_python venv_runtime venv_home base_executables venv_stdlibs venv_zips
  venv_python="$venv_dir/bin/python"
  venv_runtime="$(venv_config_runtime "$venv_dir")" || return 1
  IFS='|' read -r venv_home base_executables venv_stdlibs venv_zips <<<"$venv_runtime"
  result="$(without_api_keys_and_install_lock "$venv_python" -I -S -B \
    -X pycache_prefix=/dev/null/google-search \
    - "$VENV_SENTINEL" "$venv_dir" "$venv_dir/pyvenv.cfg" "$venv_python" \
    "$base_executables" "$venv_stdlibs" "$venv_zips" <<'PY' 2>/dev/null
import os
import re
import sys
from pathlib import Path

import encodings

sentinel = sys.argv[1]
venv = Path(sys.argv[2]).resolve()
config = Path(sys.argv[3]).read_text(encoding='utf-8')
expected_executable = os.path.abspath(sys.argv[4])
base_executables = {Path(value).resolve(strict=True) for value in sys.argv[5].split(':')}
stdlibs = {Path(value).resolve(strict=True) for value in sys.argv[6].split(':')}
zips = {Path(value).resolve(strict=False) for value in sys.argv[7].split(':')}
if not (3, 10) <= sys.version_info[:2] <= (3, 14):
    raise SystemExit(1)
if os.path.abspath(sys.executable) != expected_executable:
    raise SystemExit(1)
resolved_executable = Path(sys.executable).resolve(strict=True)
resolved_base = Path(getattr(sys, '_base_executable', sys.executable)).resolve(strict=True)
if resolved_executable not in base_executables or resolved_base not in base_executables:
    raise SystemExit(1)
allowed_paths = stdlibs | zips
allowed_paths.update(root / 'lib-dynload' for root in stdlibs)
if any(not raw or Path(raw).resolve(strict=False) not in allowed_paths for raw in sys.path):
    raise SystemExit(1)

def trusted_stdlib_origin(raw_origin):
    if not raw_origin:
        return False
    for zip_path in zips:
        if raw_origin.startswith(f'{zip_path}{os.sep}'):
            return True
    try:
        origin = Path(raw_origin).resolve(strict=True)
    except OSError:
        return False
    return any(origin == root or root in origin.parents for root in stdlibs)

if not trusted_stdlib_origin(getattr(os, '__file__', None)):
    raise SystemExit(1)
if not trusted_stdlib_origin(getattr(encodings, '__file__', None)):
    raise SystemExit(1)
match = re.search(r'^version\s*=\s*(\d+)\.(\d+)', config, re.MULTILINE)
if not match or tuple(map(int, match.groups())) != sys.version_info[:2]:
    raise SystemExit(1)
site_settings = re.findall(
    r'^\s*include-system-site-packages\s*=\s*([^\s]+)\s*$',
    config,
    re.IGNORECASE | re.MULTILINE,
)
if len(site_settings) != 1 or site_settings[0].lower() != 'false':
    raise SystemExit(1)
print(sentinel)
PY
)" || return 1
  [ "$result" = "$VENV_SENTINEL" ]
}

runtime_python_is_healthy() {
  local candidate="$1"
  local result
  result="$(without_api_keys_and_install_lock "$candidate" -I -B \
    -X pycache_prefix=/dev/null/google-search - \
    "$RUNTIME_SENTINEL" "$REQUIRED_CERTIFI_VERSION" "$REQUIRED_CHARSET_NORMALIZER_VERSION" \
    "$REQUIRED_IDNA_VERSION" "$REQUIRED_REQUESTS_VERSION" "$REQUIRED_URLLIB3_VERSION" <<'PY' 2>/dev/null
import importlib.metadata
import sys

sentinel = sys.argv[1]
expected = {
    'certifi': sys.argv[2],
    'charset-normalizer': sys.argv[3],
    'idna': sys.argv[4],
    'requests': sys.argv[5],
    'urllib3': sys.argv[6],
}
if not (3, 10) <= sys.version_info[:2] <= (3, 14):
    raise SystemExit(1)
for distribution, required_version in expected.items():
    if importlib.metadata.version(distribution) != required_version:
        raise SystemExit(1)

import certifi
import charset_normalizer
import idna
import requests
import urllib3

if not all(callable(api) for api in (
    certifi.where,
    charset_normalizer.from_bytes,
    idna.encode,
    idna.decode,
    requests.Session,
    urllib3.PoolManager,
)):
    raise SystemExit(1)
if not all(isinstance(error, type) and issubclass(error, Exception) for error in (
    requests.RequestException,
    requests.Timeout,
)):
    raise SystemExit(1)
session = requests.Session()
try:
    if not callable(getattr(session, 'post', None)):
        raise SystemExit(1)
finally:
    close = getattr(session, 'close', None)
    if callable(close):
        close()
print(sentinel)
PY
)" || return 1
  [ "$result" = "$RUNTIME_SENTINEL" ]
}

base_python() {
  local candidate runtime before after current_runtime resolved
  if [ -n "$BASE_PYTHON_PATH" ]; then
    base_python_runtime_matches_snapshot || return 1
    printf '%s\n' "$BASE_PYTHON_PATH"
    return
  fi
  candidate="$(command -v python3 2>/dev/null)" || return 1
  runtime="$(base_python_runtime "$candidate")" || return 1
  IFS='|' read -r resolved _ _ _ <<<"$runtime"
  before="$(base_python_runtime_snapshot "$runtime")" || return 1
  probe_base_python_runtime "$runtime" || return 1
  current_runtime="$(base_python_runtime "$resolved")" || return 1
  [ "$current_runtime" = "$runtime" ] || return 1
  after="$(base_python_runtime_snapshot "$current_runtime")" || return 1
  [ "$after" = "$before" ] || return 1
  printf '%s\n' "$resolved"
}

capture_source_tree_snapshot() {
  local py result
  py="$(base_python)" || return 1
  result="$(without_api_keys_and_install_lock "$py" -I -S -B \
    -X pycache_prefix=/dev/null/google-search \
    - "$BASE_DIR" "$(/usr/bin/id -u)" "$SOURCE_TREE_SENTINEL" <<'PY' 2>/dev/null
import hashlib
import os
import stat
import sys
from pathlib import Path

base = Path(sys.argv[1])
expected_uid = int(sys.argv[2])
sentinel = sys.argv[3]
allowed_owners = {expected_uid, 0}
scripts = base / 'scripts'
trusted_files = (base / 'requirements.txt', base / 'requirements-dev.txt')


def validate(metadata, expected_type):
    if metadata.st_uid not in allowed_owners or metadata.st_mode & 0o022:
        raise SystemExit(1)
    if expected_type == 'directory' and not stat.S_ISDIR(metadata.st_mode):
        raise SystemExit(1)
    if expected_type == 'file' and not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(1)
    if expected_type == 'file' and metadata.st_nlink != 1:
        raise SystemExit(1)


def walk_error(_error):
    raise SystemExit(1)


base_metadata = base.lstat()
scripts_metadata = scripts.lstat()
validate(base_metadata, 'directory')
validate(scripts_metadata, 'directory')
if scripts_metadata.st_dev != base_metadata.st_dev:
    raise SystemExit(1)

ancestor_identities = []
current = base
child_metadata = base_metadata
while True:
    metadata = current.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in allowed_owners:
        raise SystemExit(1)
    ancestor_identities.append(
        (current, metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_mode)
    )
    if current == current.parent:
        break
    parent = current.parent
    parent_metadata = parent.lstat()
    if parent_metadata.st_mode & 0o022:
        sticky_boundary = (
            stat.S_IMODE(parent_metadata.st_mode) == 0o1777
            and parent_metadata.st_uid in allowed_owners
            and child_metadata.st_uid in allowed_owners
        )
        if not sticky_boundary:
            raise SystemExit(1)
    current = parent
    child_metadata = parent_metadata

for trusted_file in trusted_files:
    metadata = trusted_file.lstat()
    validate(metadata, 'file')
    if metadata.st_dev != base_metadata.st_dev:
        raise SystemExit(1)

visited = False
for directory, child_directories, files in os.walk(
    scripts,
    topdown=True,
    onerror=walk_error,
    followlinks=False,
):
    directory_path = Path(directory)
    directory_metadata = directory_path.lstat()
    validate(directory_metadata, 'directory')
    if directory_metadata.st_dev != base_metadata.st_dev:
        raise SystemExit(1)
    if not visited:
        if (directory_metadata.st_dev, directory_metadata.st_ino) != (
            scripts_metadata.st_dev,
            scripts_metadata.st_ino,
        ):
            raise SystemExit(1)
        visited = True
    for name in child_directories:
        metadata = (directory_path / name).lstat()
        validate(metadata, 'directory')
        if metadata.st_dev != base_metadata.st_dev:
            raise SystemExit(1)
    for name in files:
        metadata = (directory_path / name).lstat()
        validate(metadata, 'file')
        if metadata.st_dev != base_metadata.st_dev:
            raise SystemExit(1)

if not visited:
    raise SystemExit(1)
final_base = base.lstat()
final_scripts = scripts.lstat()
if (final_base.st_dev, final_base.st_ino) != (base_metadata.st_dev, base_metadata.st_ino):
    raise SystemExit(1)
if (final_scripts.st_dev, final_scripts.st_ino) != (scripts_metadata.st_dev, scripts_metadata.st_ino):
    raise SystemExit(1)
for (
    ancestor,
    expected_device,
    expected_inode,
    expected_owner,
    expected_mode,
) in ancestor_identities:
    metadata = ancestor.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (expected_device, expected_inode)
        or metadata.st_uid != expected_owner
        or metadata.st_mode != expected_mode
    ):
        raise SystemExit(1)


def snapshot():
    identities = {}

    def remember(path, metadata):
        identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_mode,
        )
        if stat.S_ISREG(metadata.st_mode):
            identity += (
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        identities[os.fsencode(path)] = identity

    current = base
    child_metadata = base.lstat()
    while True:
        metadata = current.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in allowed_owners:
            raise SystemExit(1)
        if metadata.st_mode & 0o022:
            sticky_boundary = (
                current != base
                and stat.S_IMODE(metadata.st_mode) == 0o1777
                and metadata.st_uid in allowed_owners
                and child_metadata.st_uid in allowed_owners
            )
            if not sticky_boundary:
                raise SystemExit(1)
        remember(current, metadata)
        if current == current.parent:
            break
        child_metadata = metadata
        current = current.parent

    root_metadata = base.lstat()
    for trusted_file in trusted_files:
        metadata = trusted_file.lstat()
        validate(metadata, 'file')
        if metadata.st_dev != root_metadata.st_dev:
            raise SystemExit(1)
        remember(trusted_file, metadata)

    visited = False
    for directory, child_directories, files in os.walk(
        scripts,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        child_directories[:] = sorted(
            name for name in child_directories if name != '__pycache__'
        )
        files.sort()
        directory_path = Path(directory)
        directory_metadata = directory_path.lstat()
        validate(directory_metadata, 'directory')
        if directory_metadata.st_dev != root_metadata.st_dev:
            raise SystemExit(1)
        remember(directory_path, directory_metadata)
        visited = True
        for name in child_directories:
            path = directory_path / name
            metadata = path.lstat()
            validate(metadata, 'directory')
            if metadata.st_dev != root_metadata.st_dev:
                raise SystemExit(1)
            remember(path, metadata)
        for name in files:
            if name.endswith(('.pyc', '.pyo')):
                raise SystemExit(1)
            path = directory_path / name
            metadata = path.lstat()
            validate(metadata, 'file')
            if metadata.st_dev != root_metadata.st_dev:
                raise SystemExit(1)
            remember(path, metadata)
    if not visited:
        raise SystemExit(1)
    return tuple(sorted(identities.items()))


before_snapshot = snapshot()
after_snapshot = snapshot()
if before_snapshot != after_snapshot:
    raise SystemExit(1)
digest = hashlib.sha256()
for path, metadata in before_snapshot:
    digest.update(len(path).to_bytes(8, 'big'))
    digest.update(path)
    for value in metadata:
        digest.update(str(value).encode('ascii'))
        digest.update(b'\0')
print(f'{sentinel}:{digest.hexdigest()}')
PY
)" || return 1
  case "$result" in
    "$SOURCE_TREE_SENTINEL":*) result="${result#*:}" ;;
    *) return 1 ;;
  esac
  [ "${#result}" -eq 64 ] || return 1
  case "$result" in
    *[!0-9a-f]*) return 1 ;;
  esac
  printf '%s\n' "$result"
}

source_tree_is_safe() {
  local current
  current="$(capture_source_tree_snapshot)" || return 1
  if [ -z "$SOURCE_TREE_SNAPSHOT" ]; then
    SOURCE_TREE_SNAPSHOT="$current"
    return 0
  fi
  [ "$current" = "$SOURCE_TREE_SNAPSHOT" ]
}

lock_file_snapshot() {
  local lock_file="$1"
  local py="$2"
  without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    - "$BASE_DIR" "$lock_file" "$(id -u)" <<'PY' 2>/dev/null
import hashlib
import os
import stat
import sys
from pathlib import Path

MAX_LOCK_BYTES = 4 * 1024 * 1024

base = Path(sys.argv[1]).resolve(strict=True)
path = Path(sys.argv[2])
expected_uid = int(sys.argv[3])
if path.parent.resolve(strict=True) != base or path.name not in {'requirements.txt', 'requirements-dev.txt'}:
    raise SystemExit(1)

base_metadata = base.lstat()
before = path.lstat()
if (
    not stat.S_ISREG(before.st_mode)
    or before.st_uid not in {expected_uid, 0}
    or before.st_mode & 0o022
    or before.st_nlink != 1
    or before.st_dev != base_metadata.st_dev
    or before.st_size > MAX_LOCK_BYTES
):
    raise SystemExit(1)

flags = (
    os.O_RDONLY
    | getattr(os, 'O_CLOEXEC', 0)
    | getattr(os, 'O_NOFOLLOW', 0)
    | getattr(os, 'O_NONBLOCK', 0)
)
descriptor = os.open(path, flags)
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size > MAX_LOCK_BYTES
    ):
        raise SystemExit(1)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(descriptor, min(64 * 1024, MAX_LOCK_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_LOCK_BYTES:
            raise SystemExit(1)
        digest.update(chunk)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)

stable_fields = ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
    raise SystemExit(1)
named_after = path.lstat()
if any(getattr(after, field) != getattr(named_after, field) for field in stable_fields):
    raise SystemExit(1)
print(':'.join((
    str(after.st_dev),
    str(after.st_ino),
    str(after.st_uid),
    str(after.st_mode),
    str(after.st_nlink),
    str(after.st_size),
    str(after.st_mtime_ns),
    str(after.st_ctime_ns),
    digest.hexdigest(),
)))
PY
}

transaction_helper_snapshot() {
  local py="$1"
  without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    - "$BASE_DIR" "$VENV_TRANSACTION_SCRIPT" "$(id -u)" <<'PY' 2>/dev/null
import hashlib
import os
import stat
import sys
from pathlib import Path

MAX_HELPER_BYTES = 1024 * 1024
base = Path(sys.argv[1]).resolve(strict=True)
path = Path(sys.argv[2])
expected_uid = int(sys.argv[3])
if path.parent.resolve(strict=True) != base / 'scripts' or path.name != 'venv_transaction.py':
    raise SystemExit(1)

base_metadata = base.lstat()
before = path.lstat()
if (
    not stat.S_ISREG(before.st_mode)
    or before.st_uid not in {expected_uid, 0}
    or before.st_mode & 0o022
    or before.st_nlink != 1
    or before.st_dev != base_metadata.st_dev
    or before.st_size > MAX_HELPER_BYTES
):
    raise SystemExit(1)

flags = (
    os.O_RDONLY
    | getattr(os, 'O_CLOEXEC', 0)
    | getattr(os, 'O_NOFOLLOW', 0)
    | getattr(os, 'O_NONBLOCK', 0)
)
descriptor = os.open(path, flags)
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size > MAX_HELPER_BYTES
    ):
        raise SystemExit(1)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(descriptor, min(64 * 1024, MAX_HELPER_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_HELPER_BYTES:
            raise SystemExit(1)
        digest.update(chunk)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)

fields = ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
if any(getattr(opened, field) != getattr(after, field) for field in fields):
    raise SystemExit(1)
named_after = path.lstat()
if any(getattr(after, field) != getattr(named_after, field) for field in fields):
    raise SystemExit(1)
print(':'.join(str(getattr(after, field)) for field in fields) + ':' + digest.hexdigest())
PY
}

transaction_helper_is_unchanged() {
  local current
  [ -n "$TRANSACTION_HELPER_SNAPSHOT" ] || return 1
  current="$(transaction_helper_snapshot "$(base_python)")" || return 1
  [ "$current" = "$TRANSACTION_HELPER_SNAPSHOT" ]
}

active_lock_is_unchanged() {
  local current_snapshot
  [ -n "$ACTIVE_LOCK_FILE" ] && [ -n "$ACTIVE_LOCK_SNAPSHOT" ] || return 1
  current_snapshot="$(lock_file_snapshot "$ACTIVE_LOCK_FILE" "$(base_python)")" || return 1
  [ "$current_snapshot" = "$ACTIVE_LOCK_SNAPSHOT" ]
}

acquire_install_lock() {
  local py
  local current_uid lock_result lock_probe_result lock_metadata
  py="$1"
  current_uid="$(id -u)"

  if [ ! -e "$INSTALL_LOCK_PATH" ] && [ ! -L "$INSTALL_LOCK_PATH" ]; then
    if ! (set -o noclobber; : >"$INSTALL_LOCK_PATH") 2>/dev/null; then
      [ -e "$INSTALL_LOCK_PATH" ] || fail "failed to create the venv transaction lock" 3 dependency_error
    fi
  fi
  if [ ! -f "$INSTALL_LOCK_PATH" ] || [ -L "$INSTALL_LOCK_PATH" ]; then
    fail "the venv transaction lock must be a regular non-symlink file" 3 dependency_error
  fi
  lock_metadata="$(stat -c '%u:%h:%a' -- "$INSTALL_LOCK_PATH" 2>/dev/null)" || \
    fail "failed to inspect the venv transaction lock" 3 dependency_error
  [ "$lock_metadata" = "$current_uid:1:600" ] || \
    fail "the venv transaction lock must be current-UID, single-link, and mode 0600" 3 dependency_error

  exec {INSTALL_LOCK_FD}<>"$INSTALL_LOCK_PATH" || \
    fail "failed to open the venv transaction lock" 3 dependency_error
  lock_result="$(with_install_lock_without_api_keys \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    - "$INSTALL_LOCK_FD" "$INSTALL_LOCK_PATH" "$current_uid" "$LOCK_SENTINEL" <<'PY' 2>/dev/null
import fcntl
import os
import stat
import sys

descriptor = int(sys.argv[1])
path = sys.argv[2]
expected_uid = int(sys.argv[3])
sentinel = sys.argv[4]
opened = os.fstat(descriptor)
named = os.stat(path, follow_symlinks=False)
if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode):
    raise SystemExit(1)
if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
    raise SystemExit(1)
if opened.st_uid != expected_uid or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != 0o600:
    raise SystemExit(1)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(2) from None
print(sentinel)
PY
)" || fail "another dependency installation is already in progress" 3 dependency_error
  [ "$lock_result" = "$LOCK_SENTINEL" ] || \
    fail "the venv transaction lock did not return its exact sentinel" 3 dependency_error

  lock_probe_result="$(with_install_lock_without_api_keys \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    - "$INSTALL_LOCK_FD" "$INSTALL_LOCK_PATH" "$current_uid" "$LOCK_HELD_SENTINEL" <<'PY' 2>/dev/null
import fcntl
import os
import stat
import sys

inherited = os.fstat(int(sys.argv[1]))
flags = (
    os.O_RDWR
    | getattr(os, 'O_CLOEXEC', 0)
    | getattr(os, 'O_NOFOLLOW', 0)
    | getattr(os, 'O_NONBLOCK', 0)
)
descriptor = os.open(sys.argv[2], flags)
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (inherited.st_dev, inherited.st_ino)
    ):
        raise SystemExit(1)
    if opened.st_uid != int(sys.argv[3]) or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != 0o600:
        raise SystemExit(1)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(sys.argv[4])
    else:
        raise SystemExit(1)
finally:
    os.close(descriptor)
PY
)" || fail "the venv transaction lock was not retained by the installer" 3 dependency_error
  [ "$lock_probe_result" = "$LOCK_HELD_SENTINEL" ] || \
    fail "the venv transaction lock retention probe returned an invalid sentinel" 3 dependency_error
}

inspect_existing_venv() {
  local py="$1"
  source_tree_is_safe || fail "skill source tree has unsafe ownership, permissions, or entries" 10 install_error
  if [ ! -f "$VENV_TRANSACTION_SCRIPT" ] || [ -L "$VENV_TRANSACTION_SCRIPT" ]; then
    fail "missing or unsafe venv transaction helper" 10 install_error
  fi
  path_owner_is_safe "$VENV_TRANSACTION_SCRIPT" || \
    fail "venv transaction helper has unsafe ownership or permissions" 10 install_error
  EXISTING_VENV_ID="$(with_install_lock_without_api_keys \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    "$VENV_TRANSACTION_SCRIPT" inspect \
    "$BASE_DIR" "$INSTALL_LOCK_FD" "$INSTALL_LOCK_PATH" "$(id -u)" 2>/dev/null)" || \
    fail "existing .venv cannot be safely and atomically replaced" 3 dependency_error
  case "$EXISTING_VENV_ID" in
    absent|[0-9]*:[0-9]*) ;;
    *) fail "existing .venv inspection returned an invalid identity" 3 dependency_error ;;
  esac
}

create_candidate_venv() {
  local py="$1"
  local bootstrap_result

  TEMP_VENV_DIR="$(mktemp -d "$BASE_DIR/.venv-build.XXXXXXXX")" || \
    fail "failed to allocate a temporary venv directory" 3 dependency_error
  chmod 700 "$TEMP_VENV_DIR" || fail "failed to secure the temporary venv directory" 3 dependency_error
  BOOTSTRAP_VENV_DIR="$(mktemp -d "$BASE_DIR/.venv-bootstrap.XXXXXXXX")" || \
    fail "failed to allocate a bootstrap venv directory" 3 dependency_error
  chmod 700 "$BOOTSTRAP_VENV_DIR" || fail "failed to secure the bootstrap venv directory" 3 dependency_error
  ensure_command_log || fail "failed to allocate a private install log" 10 install_error

  log "creating clean candidate and disposable bootstrap venvs"
  if ! without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    -m venv --without-pip "$TEMP_VENV_DIR" >"$COMMAND_LOG" 2>&1 || \
    ! without_api_keys_and_install_lock \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    -m venv "$BOOTSTRAP_VENV_DIR" >>"$COMMAND_LOG" 2>&1; then
    fail "python3 -m venv failed; install the Python venv module manually (system packages were not changed)" 3 dependency_error
  fi
  if ! venv_structure_is_safe "$TEMP_VENV_DIR" || ! venv_prefix_is_usable "$TEMP_VENV_DIR" || \
    ! venv_structure_is_safe "$BOOTSTRAP_VENV_DIR" 1 || ! venv_prefix_is_usable "$BOOTSTRAP_VENV_DIR"; then
    fail "the candidate .venv failed structural validation" 3 dependency_error
  fi
  bootstrap_result="$(without_api_keys_and_install_lock "$BOOTSTRAP_VENV_DIR/bin/python" \
    -I -B -X pycache_prefix=/dev/null/google-search - "$VENV_SENTINEL" <<'PY' 2>/dev/null
import sys
import pip

if not isinstance(pip.__version__, str) or not pip.__version__:
    raise SystemExit(1)
print(sys.argv[1])
PY
)" || fail "the disposable bootstrap venv does not provide pip" 3 dependency_error
  [ "$bootstrap_result" = "$VENV_SENTINEL" ] || \
    fail "bootstrap pip validation returned an invalid sentinel" 3 dependency_error
}

install_requirements() {
  local lock_file="$1"
  local lock_label="$2"
  local candidate_python="$TEMP_VENV_DIR/bin/python"
  local bootstrap_python="$BOOTSTRAP_VENV_DIR/bin/python"
  local install_result pytest_result
  if [ ! -f "$lock_file" ] || [ -L "$lock_file" ]; then
    fail "missing or unsafe $lock_label lock: $lock_file" 3 dependency_error
  fi
  active_lock_is_unchanged || fail "$lock_label lock changed before dependency installation" 3 dependency_error
  ensure_command_log || fail "failed to allocate a private install log" 10 install_error
  log "installing hashed $lock_label dependencies into the isolated candidate"
  if ! run_locked_install_helper "$bootstrap_python" "$candidate_python" "$lock_file"; then
    fail "locked dependency installation failed; the existing .venv was not modified" 3 dependency_error
  fi
  install_result="$LOCKED_INSTALL_RESULT"
  [ "$install_result" = "$VENV_SENTINEL" ] || \
    fail "sealed dependency installation returned an invalid sentinel" 3 dependency_error
  active_lock_is_unchanged || fail "$lock_label lock changed during dependency installation" 3 dependency_error
  if ! venv_structure_is_safe "$TEMP_VENV_DIR"; then
    fail "installed runtime lock failed structural validation" 3 dependency_error
  fi
  CANDIDATE_RUNTIME_SNAPSHOT="$(stable_venv_runtime_snapshot "$TEMP_VENV_DIR")" || \
    fail "installed runtime lock could not be snapshotted consistently" 3 dependency_error
  # locked_install.py performs the static package/startup-hook validation from
  # the same kernel-sealed lock before candidate site initialization is used.
  if ! PIP_CONFIG_FILE=/dev/null without_api_keys_and_install_lock \
    "$bootstrap_python" -I -B -X pycache_prefix=/dev/null/google-search \
    -m pip --isolated --python "$candidate_python" \
    check >>"$COMMAND_LOG" 2>&1; then
    fail "installed dependencies failed pip check" 3 dependency_error
  fi
  candidate_runtime_matches_snapshot "$TEMP_VENV_DIR" || \
    fail "installed runtime changed during pip validation" 3 dependency_error
  if ! venv_prefix_is_usable "$TEMP_VENV_DIR"; then
    fail "installed runtime lock failed prefix validation" 3 dependency_error
  fi
  candidate_runtime_matches_snapshot "$TEMP_VENV_DIR" || \
    fail "installed runtime changed during prefix validation" 3 dependency_error
  if ! runtime_python_is_healthy "$candidate_python"; then
    fail "installed runtime lock failed complete package and API validation" 3 dependency_error
  fi
  candidate_runtime_matches_snapshot "$TEMP_VENV_DIR" || \
    fail "installed runtime changed during package and API validation" 3 dependency_error
  if [ "$lock_label" = "development" ]; then
    pytest_result="$(without_api_keys_and_install_lock "$candidate_python" -I -B \
      -X pycache_prefix=/dev/null/google-search \
      - "$REQUIRED_PYTEST_VERSION" "$VENV_SENTINEL" <<'PY' 2>/dev/null
import sys
import pytest

if pytest.__version__ != sys.argv[1]:
    raise SystemExit(1)
print(sys.argv[2])
PY
)" || fail "development lock did not provide pytest==$REQUIRED_PYTEST_VERSION" 3 dependency_error
    [ "$pytest_result" = "$VENV_SENTINEL" ] || \
      fail "development dependency validation returned an invalid sentinel" 3 dependency_error
    candidate_runtime_matches_snapshot "$TEMP_VENV_DIR" || \
      fail "installed runtime changed during development dependency validation" 3 dependency_error
  fi
  active_lock_is_unchanged || fail "$lock_label lock changed during candidate validation" 3 dependency_error
  candidate_runtime_matches_snapshot "$TEMP_VENV_DIR" || \
    fail "installed runtime changed before bootstrap cleanup" 3 dependency_error

  if ! rm -rf --one-file-system -- "$BOOTSTRAP_VENV_DIR" >>"$COMMAND_LOG" 2>&1; then
    fail "failed to remove the disposable bootstrap venv before publication" 3 dependency_error
  fi
  BOOTSTRAP_VENV_DIR=""
}

publish_candidate_venv() {
  local py="$1"
  local candidate_id candidate_name expected_live_before expected_staged_after
  local live_id staged_id publish_result="" staged_verify_result="" publish_status=0
  candidate_name="${TEMP_VENV_DIR##*/}"
  source_tree_is_safe || fail "skill source tree changed before venv publication" 10 install_error
  active_lock_is_unchanged || fail "dependency lock changed before venv publication" 3 dependency_error
  candidate_runtime_matches_snapshot "$TEMP_VENV_DIR" || \
    fail "verified candidate changed before venv publication" 3 dependency_error
  candidate_id="$(stat -c '%d:%i' -- "$TEMP_VENV_DIR" 2>/dev/null)" || \
    fail "failed to identify the verified candidate .venv" 3 dependency_error
  # Once publication starts, this pathname may become the only copy of the
  # original environment. The EXIT trap must preserve it until commit state is
  # resolved from inode identities.
  TEMP_VENV_CLEANUP="preserve"
  if ! publish_result="$(with_install_lock_without_api_keys \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    "$VENV_TRANSACTION_SCRIPT" publish \
    "$BASE_DIR" "$INSTALL_LOCK_FD" "$INSTALL_LOCK_PATH" "$(id -u)" \
    "$candidate_name" "$EXISTING_VENV_ID" "$candidate_id" 2>/dev/null)"; then
    publish_status=1
  fi

  live_id="$(stat -c '%d:%i' -- "$VENV_DIR" 2>/dev/null)" || live_id="absent"
  staged_id="$(stat -c '%d:%i' -- "$TEMP_VENV_DIR" 2>/dev/null)" || staged_id="absent"
  if [ "$EXISTING_VENV_ID" = "absent" ]; then
    expected_live_before="absent"
    expected_staged_after="absent"
  else
    expected_live_before="$EXISTING_VENV_ID"
    expected_staged_after="$EXISTING_VENV_ID"
  fi

  if [ "$live_id" = "$candidate_id" ] && [ "$staged_id" = "$expected_staged_after" ]; then
    : # The atomic rename committed, even if helper acknowledgement was lost.
  elif [ "$live_id" = "$expected_live_before" ] && [ "$staged_id" = "$candidate_id" ]; then
    TEMP_VENV_CLEANUP="remove"
    fail "failed to atomically publish the verified candidate .venv" 3 dependency_error
  else
    POST_COMMIT_WARNING="venv publication outcome is ambiguous; recovery state was preserved at $TEMP_VENV_DIR"
    TEMP_VENV_DIR=""
    fail "cannot safely resolve the atomic venv publication outcome" 10 install_error
  fi
  if [ "$publish_status" -ne 0 ] || [ "$publish_result" != "google-search-venv-published-v1" ]; then
    log "publication committed; recovered state after helper acknowledgement failure"
  fi

  if ! candidate_runtime_matches_snapshot "$VENV_DIR" || \
    ! venv_prefix_is_usable "$VENV_DIR" || \
    ! candidate_runtime_matches_snapshot "$VENV_DIR" || \
    ! runtime_python_is_healthy "$VENV_PYTHON" || \
    ! candidate_runtime_matches_snapshot "$VENV_DIR"; then
    rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
    fail "published candidate failed live-path validation; the original .venv was restored" 3 dependency_error
  fi
  if ! active_lock_is_unchanged; then
    rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
    fail "dependency lock changed after venv publication; the original .venv was restored" 3 dependency_error
  fi
  if ! source_tree_is_safe; then
    rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
    fail "skill source tree changed after venv publication; the original .venv was restored" 10 install_error
  fi

  if [ "$EXISTING_VENV_ID" = "absent" ]; then
    if ! candidate_runtime_matches_snapshot "$VENV_DIR"; then
      rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
      fail "published candidate changed before installation commit" 3 dependency_error
    fi
    VENV_CREATED=1
    TEMP_VENV_DIR=""
  else
    if ! staged_verify_result="$(with_install_lock_without_api_keys \
      "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
      "$VENV_TRANSACTION_SCRIPT" verify-staged \
      "$BASE_DIR" "$INSTALL_LOCK_FD" "$INSTALL_LOCK_PATH" "$(id -u)" \
      "$candidate_name" "$EXISTING_VENV_ID" "$candidate_id" 2>/dev/null)"; then
      POST_COMMIT_WARNING="the new venv is active, but the old venv could not be verified for removal at $TEMP_VENV_DIR"
      TEMP_VENV_DIR=""
      fail "refusing to remove an unverified post-publication recovery path" 10 install_error
    fi
    if ! candidate_runtime_matches_snapshot "$VENV_DIR"; then
      rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
      fail "published candidate changed before old-venv cleanup" 3 dependency_error
    fi
    if ! active_lock_is_unchanged; then
      rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
      fail "dependency lock changed before old-venv cleanup" 3 dependency_error
    fi
    if ! source_tree_is_safe; then
      rollback_candidate_venv "$py" "$candidate_name" "$candidate_id"
      fail "skill source tree changed before old-venv cleanup" 10 install_error
    fi
    if [ "$staged_verify_result" = "google-search-staged-venv-preserve-v1" ]; then
      POST_COMMIT_WARNING="the new venv is active, but the old venv remains at $TEMP_VENV_DIR"
      log "WARNING: $POST_COMMIT_WARNING"
      TEMP_VENV_DIR=""
      TEMP_VENV_CLEANUP="remove"
      REQUIREMENTS_INSTALLED=1
      return 0
    fi
    if [ "$staged_verify_result" != "google-search-staged-venv-verified-v1" ]; then
      POST_COMMIT_WARNING="the new venv is active, but staged cleanup returned an invalid result for $TEMP_VENV_DIR"
      TEMP_VENV_DIR=""
      fail "refusing to remove an unverified post-publication recovery path" 10 install_error
    fi
    staged_id="$(stat -c '%d:%i' -- "$TEMP_VENV_DIR" 2>/dev/null)" || staged_id="absent"
    if [ ! -d "$TEMP_VENV_DIR" ] || [ -L "$TEMP_VENV_DIR" ] || \
      [ "$staged_id" != "$EXISTING_VENV_ID" ]; then
      POST_COMMIT_WARNING="the new venv is active, but the old venv recovery path changed at $TEMP_VENV_DIR"
      TEMP_VENV_DIR=""
      fail "refusing to remove a changed post-publication recovery path" 10 install_error
    fi
    if ! rm -rf --one-file-system -- "$TEMP_VENV_DIR" >>"$COMMAND_LOG" 2>&1 || \
      [ -e "$TEMP_VENV_DIR" ] || [ -L "$TEMP_VENV_DIR" ]; then
      POST_COMMIT_WARNING="the new venv is active, but the old venv remains at $TEMP_VENV_DIR"
      log "WARNING: $POST_COMMIT_WARNING"
    fi
    TEMP_VENV_DIR=""
  fi
  TEMP_VENV_CLEANUP="remove"
  REQUIREMENTS_INSTALLED=1
}

rollback_candidate_venv() {
  local py="$1"
  local candidate_name="$2"
  local candidate_id="$3"
  local rollback_result="" restored_id="" staged_id=""
  if ! transaction_helper_is_unchanged; then
    POST_COMMIT_WARNING="rollback helper source is no longer trusted; the original venv remains at $TEMP_VENV_DIR"
    TEMP_VENV_DIR=""
    fail "cannot safely run the rollback helper after the source tree changed" 10 install_error
  fi
  if rollback_result="$(with_install_lock_without_api_keys \
    "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
    "$VENV_TRANSACTION_SCRIPT" rollback \
    "$BASE_DIR" "$INSTALL_LOCK_FD" "$INSTALL_LOCK_PATH" "$(id -u)" \
    "$candidate_name" "$EXISTING_VENV_ID" "$candidate_id" 2>/dev/null)" && \
    [ "$rollback_result" = "google-search-venv-rolled-back-v1" ]; then
    :
  fi
  staged_id="$(stat -c '%d:%i' -- "$TEMP_VENV_DIR" 2>/dev/null)" || staged_id="absent"
  if [ "$EXISTING_VENV_ID" = "absent" ]; then
    if [ ! -e "$VENV_DIR" ] && [ ! -L "$VENV_DIR" ] && [ "$staged_id" = "$candidate_id" ]; then
      TEMP_VENV_CLEANUP="remove"
      return 0
    fi
  else
    restored_id="$(stat -c '%d:%i' -- "$VENV_DIR" 2>/dev/null)" || true
    if [ "$restored_id" = "$EXISTING_VENV_ID" ] && [ "$staged_id" = "$candidate_id" ]; then
      TEMP_VENV_CLEANUP="remove"
      return 0
    fi
  fi
  if [ "$EXISTING_VENV_ID" = "absent" ]; then
    POST_COMMIT_WARNING="rollback failed; the published venv may still be active"
  else
    POST_COMMIT_WARNING="rollback failed; the original venv remains at $TEMP_VENV_DIR"
  fi
  # Do not let the EXIT trap destroy the only recovery copy after a failed
  # rollback. The reported path is intentionally left for manual recovery.
  TEMP_VENV_DIR=""
  fail "published candidate failed validation and the original .venv could not be restored" 10 install_error
}

install_transaction() {
  local lock_file="$1"
  local lock_label="$2"
  local py
  py="$(base_python)" || fail "a trusted Python 3.10-3.14 python3 is required to create .venv" 3 dependency_error
  ACTIVE_LOCK_FILE="$lock_file"
  ACTIVE_LOCK_SNAPSHOT="$(lock_file_snapshot "$lock_file" "$py")" || \
    fail "$lock_label lock has unsafe metadata or could not be read atomically" 3 dependency_error
  TRANSACTION_HELPER_SNAPSHOT="$(transaction_helper_snapshot "$py")" || \
    fail "venv transaction helper could not be snapshotted safely" 10 install_error
  acquire_install_lock "$py"
  inspect_existing_venv "$py"
  create_candidate_venv "$py"
  install_requirements "$lock_file" "$lock_label"
  publish_candidate_venv "$py"
}

run_selected_task() {
  local task="$1"
  local runner_mode
  shift
  case "$SELECTED_MODE" in
    system) runner_mode="--system" ;;
    venv) runner_mode="--venv" ;;
    *) return 1 ;;
  esac
  [ -n "$SELECTED_RUNTIME_TOKEN" ] || return 1
  /bin/bash -p "$RUNNER" "$runner_mode" --quiet \
    --expect-runtime-token "$SELECTED_RUNTIME_TOKEN" --task "$task" -- "$@"
}

run_online_task() {
  local task="$1"
  local output_path="$2"
  local runner_mode worker_status signal_status
  case "$SELECTED_MODE" in
    system) runner_mode="--system" ;;
    venv) runner_mode="--venv" ;;
    *) return 1 ;;
  esac
  [ -n "$SELECTED_RUNTIME_TOKEN" ] || return 1
  PENDING_ONLINE_SIGNAL_STATUS=""
  ONLINE_WORKER_PREVIOUS_PID="${!:-}"
  ONLINE_WORKER_LAUNCHING=1
  set_online_signal_traps
  if [ -n "$PENDING_ONLINE_SIGNAL_STATUS" ]; then
    signal_status="$PENDING_ONLINE_SIGNAL_STATUS"
    ONLINE_WORKER_LAUNCHING=0
    set_install_signal_traps
    exit "$signal_status"
  fi
  (
    trap - HUP INT TERM
    cd "$BASE_DIR"
    if [ -n "${INSTALL_LOCK_FD:-}" ]; then
      exec {INSTALL_LOCK_FD}>&-
    fi
    exec /bin/bash -p "$RUNNER" "$runner_mode" --quiet \
      --expect-runtime-token "$SELECTED_RUNTIME_TOKEN" --task "$task" --
  ) >"$output_path" 2>>"$COMMAND_LOG" &
  ACTIVE_ONLINE_WORKER_PID=$!
  ONLINE_WORKER_LAUNCHING=0
  if [ -n "$PENDING_ONLINE_SIGNAL_STATUS" ]; then
    signal_status="$PENDING_ONLINE_SIGNAL_STATUS"
    terminate_active_process "$ACTIVE_ONLINE_WORKER_PID"
    ACTIVE_ONLINE_WORKER_PID=""
    set_install_signal_traps
    exit "$signal_status"
  fi
  if wait "$ACTIVE_ONLINE_WORKER_PID"; then
    worker_status=0
  else
    worker_status=$?
  fi
  if [ -n "$PENDING_ONLINE_SIGNAL_STATUS" ]; then
    signal_status="$PENDING_ONLINE_SIGNAL_STATUS"
    terminate_active_process "$ACTIVE_ONLINE_WORKER_PID"
    ACTIVE_ONLINE_WORKER_PID=""
    set_install_signal_traps
    exit "$signal_status"
  fi
  ACTIVE_ONLINE_WORKER_PID=""
  set_install_signal_traps
  if [ -n "$PENDING_ONLINE_SIGNAL_STATUS" ]; then
    exit "$PENDING_ONLINE_SIGNAL_STATUS"
  fi
  PENDING_ONLINE_SIGNAL_STATUS=""
  [ "$worker_status" -eq 0 ]
}

select_runtime() {
  local requested_mode=()
  local runtime_info protocol mode display_path token verify_output
  source_tree_is_safe || fail "skill source tree has unsafe ownership, permissions, or entries" 10 install_error
  case "$MODE" in
    system) requested_mode=(--system) ;;
    venv) requested_mode=(--venv) ;;
    auto) requested_mode=() ;;
    *) fail "invalid mode: $MODE" 2 config_error ;;
  esac

  if ! runtime_info="$(without_api_keys_and_install_lock \
    /bin/bash -p "$RUNNER" "${requested_mode[@]}" --quiet --runtime-info)"; then
    fail "no healthy runtime found; use --install-dependencies to create/install the locked local .venv" 3 dependency_error
  fi
  case "$runtime_info" in ''|*$'\n'*) fail "runtime selector returned an invalid binding record" 3 dependency_error ;; esac
  IFS='|' read -r protocol mode display_path token <<<"$runtime_info"
  [ "$runtime_info" = "$protocol|$mode|$display_path|$token" ] || \
    fail "runtime selector returned an invalid binding record" 3 dependency_error
  [ "$protocol" = "$RUNTIME_INFO_SENTINEL" ] || \
    fail "runtime selector returned an unsupported binding protocol" 3 dependency_error
  case "$mode" in system|venv) ;; *) fail "runtime selector returned an invalid mode" 3 dependency_error ;; esac
  case "$display_path" in /*) ;; *) fail "runtime selector returned an invalid display path" 3 dependency_error ;; esac
  reportable_path "$display_path" || \
    fail "runtime selector returned an invalid display path" 3 dependency_error
  [ "${#token}" -eq 64 ] || fail "runtime selector returned an invalid binding token" 3 dependency_error
  case "$token" in *[!0-9a-f]*) fail "runtime selector returned an invalid binding token" 3 dependency_error ;; esac
  if [ "$MODE" != "auto" ] && [ "$mode" != "$MODE" ]; then
    fail "runtime selector returned a mode that does not match the request" 3 dependency_error
  fi
  if [ "$mode" = "venv" ] && [ "$display_path" != "$VENV_PYTHON" ]; then
    fail "runtime selector returned an unexpected local .venv display path" 3 dependency_error
  fi

  SELECTED_MODE="$mode"
  RUNTIME_DISPLAY_PATH="$display_path"
  SELECTED_RUNTIME_TOKEN="$token"
  case "$SELECTED_MODE" in
    venv) RUNTIME_SOURCE="local-venv" ;;
    system) RUNTIME_SOURCE="system-python" ;;
  esac

  if ! verify_output="$(without_api_keys_and_install_lock run_selected_task verify)"; then
    fail "selected runtime changed before its bound verification" 3 dependency_error
  fi
  [ -z "$verify_output" ] || fail "runtime verification returned unexpected output" 3 dependency_error
}

make_result_file() {
  local kind="$1"
  local result
  safe_tmp_is_available || return 1
  result="$(mktemp "$SAFE_TMP_DIR/google-search-${kind}.XXXXXXXX")" || return 1
  chmod 600 "$result" || return 1
  printf '%s\n' "$result"
}

require_api_config() {
  local config_result status
  source_tree_is_safe || fail "skill source tree has unsafe ownership, permissions, or entries" 10 install_error
  if [ ! -f "$CLIENT_SCRIPT" ] || [ -L "$CLIENT_SCRIPT" ]; then
    fail "missing or unsafe API client: $CLIENT_SCRIPT" 10 install_error
  fi
  if config_result="$(without_install_lock run_selected_task check-keys 2>/dev/null)"; then
    [ "$config_result" = "$API_CONFIG_SENTINEL" ] || \
      fail "API configuration validation returned an invalid sentinel" 10 install_error
  else
    status=$?
    if [ "$status" -eq 2 ]; then
      fail "online checks require valid API keys from the environment or protected config file" 2 config_error
    fi
    fail "API configuration could not be validated by the selected runtime" 10 install_error
  fi
}

run_smoke_test() {
  local protocol_result
  source_tree_is_safe || fail "skill source tree changed before the smoke test" 10 install_error
  require_api_config
  SMOKE_RESULT_PATH="$(make_result_file smoke)" || fail "failed to allocate a private smoke result file" 10 install_error
  ensure_command_log || fail "failed to allocate a private install log" 10 install_error
  log "running explicit online smoke test"
  if ! run_online_task smoke "$SMOKE_RESULT_PATH"; then
    if [ "$OUTPUT_FORMAT" = "text" ] && [ "$QUIET" -eq 1 ]; then
      discard_private_result_or_fail "$SMOKE_RESULT_PATH" smoke
      SMOKE_RESULT_PATH=""
    fi
    fail "smoke test failed; details are in $SMOKE_RESULT_PATH" 4 smoke_test_error
  fi
  protocol_result="$(without_api_keys_and_install_lock \
    run_selected_task check-result smoke "$SMOKE_RESULT_PATH" 2>>"$COMMAND_LOG")" || \
    fail "smoke test returned an invalid result protocol" 4 smoke_test_error
  [ "$protocol_result" = "$SMOKE_RESULT_SENTINEL" ] || \
    fail "smoke test returned an invalid result sentinel" 4 smoke_test_error
  if [ "$OUTPUT_FORMAT" = "text" ] && [ "$QUIET" -eq 1 ]; then
    discard_private_result_or_fail "$SMOKE_RESULT_PATH" smoke
    SMOKE_RESULT_PATH=""
  else
    log "smoke result: $SMOKE_RESULT_PATH"
  fi
}

run_full_check() {
  local protocol_result
  source_tree_is_safe || fail "skill source tree changed before the full selfcheck" 10 install_error
  require_api_config
  SELFCHECK_RESULT_PATH="$(make_result_file selfcheck)" || fail "failed to allocate a private selfcheck result file" 10 install_error
  ensure_command_log || fail "failed to allocate a private install log" 10 install_error
  log "running explicit full online selfcheck"
  if ! run_online_task full "$SELFCHECK_RESULT_PATH"; then
    if [ "$OUTPUT_FORMAT" = "text" ] && [ "$QUIET" -eq 1 ]; then
      discard_private_result_or_fail "$SELFCHECK_RESULT_PATH" selfcheck
      SELFCHECK_RESULT_PATH=""
    fi
    fail "full selfcheck failed; details are in $SELFCHECK_RESULT_PATH" 5 selfcheck_error
  fi
  protocol_result="$(without_api_keys_and_install_lock \
    run_selected_task check-result full "$SELFCHECK_RESULT_PATH" 2>>"$COMMAND_LOG")" || \
    fail "full selfcheck returned an invalid result protocol" 5 selfcheck_error
  [ "$protocol_result" = "$FULL_RESULT_SENTINEL" ] || \
    fail "full selfcheck returned an invalid result sentinel" 5 selfcheck_error
  if [ "$OUTPUT_FORMAT" = "text" ] && [ "$QUIET" -eq 1 ]; then
    discard_private_result_or_fail "$SELFCHECK_RESULT_PATH" selfcheck
    SELFCHECK_RESULT_PATH=""
  else
    log "selfcheck result: $SELFCHECK_RESULT_PATH"
  fi
}

# Detect machine-output intent before validation so option errors do not mix text
# with JSON merely because --json appeared later in argv.
for option in "$@"; do
  case "$option" in
    --json|--save-json) OUTPUT_FORMAT="json" ;;
  esac
done

initialize_base_python || \
  fail "a trusted Python 3.10-3.14 stdlib could not be validated before startup" 3 dependency_error
source_tree_is_safe || fail "skill source tree has unsafe ownership, permissions, or entries" 10 install_error

while [ "$#" -gt 0 ]; do
  case "$1" in
    --system)
      [ "$MODE" = "auto" ] || fail "--system and --venv are mutually exclusive" 2 config_error
      MODE="system"
      ;;
    --venv)
      [ "$MODE" = "auto" ] || fail "--system and --venv are mutually exclusive" 2 config_error
      MODE="venv"
      ;;
    --install-dependencies)
      if [ "$DEPENDENCY_LOCK" != "none" ] && [ "$DEPENDENCY_LOCK" != "runtime" ]; then
        fail "--install-dependencies and --install-dev-dependencies are mutually exclusive" 2 config_error
      fi
      INSTALL_DEPENDENCIES=1
      DEPENDENCY_LOCK="runtime"
      ;;
    --install-dev-dependencies)
      if [ "$DEPENDENCY_LOCK" != "none" ] && [ "$DEPENDENCY_LOCK" != "dev" ]; then
        fail "--install-dependencies and --install-dev-dependencies are mutually exclusive" 2 config_error
      fi
      INSTALL_DEPENDENCIES=1
      INSTALL_DEV_DEPENDENCIES=1
      DEPENDENCY_LOCK="dev"
      ;;
    --smoke-test)
      RUN_SMOKE_TEST=1
      ;;
    --skip-smoke-test)
      DEPRECATED_SKIP_SMOKE=1
      ;;
    --full-check)
      RUN_FULL_CHECK=1
      ;;
    --json)
      OUTPUT_FORMAT="json"
      ;;
    --save-json)
      [ "$#" -ge 2 ] || fail "--save-json requires a file path" 2 config_error
      case "$2" in
        -*) fail "--save-json requires a file path, not another option" 2 config_error ;;
      esac
      [ -n "$2" ] || fail "--save-json requires a non-empty file path" 2 config_error
      SAVE_JSON_PATH="$2"
      SAVE_JSON_REQUESTED=1
      OUTPUT_FORMAT="json"
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
      fail "unknown option" 2 config_error
      ;;
  esac
  shift
done

if [ "$SAVE_JSON_REQUESTED" -eq 1 ] && ! normalize_save_json_path; then
  SAVE_JSON_PATH=""
  SAVE_JSON_REQUESTED=0
  SAVE_JSON_NORMALIZED=0
  fail "--save-json path is invalid or unsafe" 10 install_error
fi

if [ ! -f "$RUNNER" ] || [ -L "$RUNNER" ]; then
  fail "missing or unsafe runtime selector: $RUNNER" 10 install_error
fi
if [ ! -f "$RUNTIME_GUARD_SCRIPT" ] || [ -L "$RUNTIME_GUARD_SCRIPT" ]; then
  fail "missing or unsafe system runtime guard: $RUNTIME_GUARD_SCRIPT" 10 install_error
fi
if [ "$DEPRECATED_SKIP_SMOKE" -eq 1 ]; then
  log "--skip-smoke-test is deprecated; installation is offline by default"
fi

if [ "$INSTALL_DEPENDENCIES" -eq 1 ]; then
  [ "$MODE" != "system" ] || fail "dependency installation flags cannot be combined with --system" 2 config_error
  if [ "$INSTALL_DEV_DEPENDENCIES" -eq 1 ]; then
    install_transaction "$DEV_REQ_FILE" development
  else
    install_transaction "$REQ_FILE" runtime
  fi
  MODE="venv"
fi

select_runtime

if [ "$RUN_SMOKE_TEST" -eq 1 ]; then
  run_smoke_test
fi
if [ "$RUN_FULL_CHECK" -eq 1 ]; then
  run_full_check
fi

log "install complete (mode=$SELECTED_MODE, python=$RUNTIME_DISPLAY_PATH, onlineChecks=$((RUN_SMOKE_TEST + RUN_FULL_CHECK)))"
INSTALL_EXIT_CODE=0
EXIT_KIND="ok"
if [ "$OUTPUT_FORMAT" = "json" ]; then
  if ! emit_json_result true ok; then
    failed_path="$SAVE_JSON_PATH"
    SAVE_JSON_PATH=""
    SAVE_JSON_REQUESTED=0
    SAVE_JSON_NORMALIZED=0
    fail "install succeeded but JSON could not be securely saved to $failed_path" 10 install_error
  fi
fi
