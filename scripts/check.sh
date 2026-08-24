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
unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX
unset VIRTUAL_ENV __PYVENV_LAUNCHER__
export -n SHELLOPTS 2>/dev/null || true
set -euo pipefail

umask 077
IFS=$' \t\n'
PATH='/usr/bin:/bin'
TMPDIR='/tmp'
TMP='/tmp'
TEMP='/tmp'
export PATH TMPDIR TMP TEMP

while IFS= read -r variable; do
  case "$variable" in
    GIT_*) unset -v "$variable" 2>/dev/null || true ;;
  esac
done < <(compgen -e)
unset variable
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null
GIT_NO_REPLACE_OBJECTS=1
GIT_OPTIONAL_LOCKS=0
export GIT_CONFIG_NOSYSTEM GIT_CONFIG_GLOBAL GIT_NO_REPLACE_OBJECTS GIT_OPTIONAL_LOCKS

ENTRYPOINT_PATH="${BASH_SOURCE[0]}"
ENTRYPOINT_DIR="${ENTRYPOINT_PATH%/*}"
[ "$ENTRYPOINT_DIR" != "$ENTRYPOINT_PATH" ] || ENTRYPOINT_DIR='.'
BASE_DIR="$(cd -- "$ENTRYPOINT_DIR/.." && pwd -P)"
unset ENTRYPOINT_PATH ENTRYPOINT_DIR
RUNNER="$BASE_DIR/scripts/run.sh"
RUNTIME_INFO_SENTINEL="google-search-runtime-info-v1"
PYTEST_SENTINEL="google-search-pytest-ok-v1"
KEYS_SENTINEL="google-search-keys-ok-v1"
SMOKE_RESULT_SENTINEL="google-search-smoke-result-ok-v1"
PARSING_RESULT_SENTINEL="google-search-parsing-result-ok-v1"
FULL_RESULT_SENTINEL="google-search-full-result-ok-v1"
PROTOCOL_CAPTURE_MARKER=$'\036'
SAFE_TMP_DIR="/tmp"
PINNED_SHELLCHECK_VERSION="0.11.0"
PINNED_SHELLCHECK_SHA256="4da528ddb3a4d1b7b24a59d4e16eb2f5fd960f4bd9a3708a15baddbdf1d5a55b"

MODE="auto"
ONLINE_SMOKE=0
ONLINE_FULL=0
QUIET=0
RELEASE_ARCHIVE_ARG=""
RELEASE_COMMIT_ARG=""
RELEASE_ARCHIVE_SET=0
RELEASE_COMMIT_SET=0
SHELLCHECK_PATH=""
SHELLCHECK_PATH_SET=0
SELECTED_RUNTIME_TOKEN=""
RUNNER_MODE=()
TEMP_RESULTS=()

usage() {
  cat <<'EOF'
Usage: /bin/bash -p scripts/check.sh [--system|--venv] [--online-smoke|--online-full] [--quiet]
       [--shellcheck <absolute-pinned-binary>]
       [--release-archive <absolute-tar> --release-commit <full-oid>]

By default this command is entirely offline. It validates shell syntax, Python
AST parsing, the formal pytest suite, ShellCheck when available, and the local
selfcheck parsing group. Serper is contacted only by an explicit online flag.

Options:
  --system        Require the system runtime
  --venv          Require the local .venv runtime
  --online-smoke  Additionally run smoke_test.py against Serper
  --online-full   Additionally run selfcheck.py --full against Serper
  --shellcheck <path>
                  Require the pinned ShellCheck 0.11.0 binary at this path
  --release-archive <path>
                  Verify this local release tar in the formal pytest protocol
  --release-commit <oid>
                  Bind --release-archive to this full SHA-1 commit object ID
  --quiet         Suppress progress output
  -h, --help      Show this help
EOF
}

log() {
  if [ "$QUIET" -ne 1 ]; then
    printf '[google-search] %s\n' "$*"
  fi
}

without_api_keys() (
  unset SERPER_API_KEY SERPER_API_KEYS
  "$@"
)

fail() {
  local message="$1"
  local code="${2:-1}"
  printf '[google-search] ERROR: %s\n' "$message" >&2
  exit "$code"
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

trusted_shellcheck_path() {
  local path="$1"
  local canonical current_uid metadata owner mode links parent
  case "$path" in /*) ;; *) return 1 ;; esac
  reportable_path "$path" || return 1
  canonical="$(readlink -f -- "$path" 2>/dev/null)" || return 1
  [ "$canonical" = "$path" ] || return 1
  [ -f "$path" ] && [ ! -L "$path" ] && [ -x "$path" ] || return 1
  current_uid="$(id -u)" || return 1
  metadata="$(stat -c '%u|%a|%h' -- "$path" 2>/dev/null)" || return 1
  IFS='|' read -r owner mode links <<<"$metadata"
  [ "$owner" = "0" ] || [ "$owner" = "$current_uid" ] || return 1
  case "$mode" in *[!0-7]*|'') return 1 ;; esac
  [ $((8#$mode & 07022)) -eq 0 ] || return 1
  [ "$links" = "1" ] || return 1

  parent="${path%/*}"
  [ -n "$parent" ] || parent=/
  while :; do
    [ -d "$parent" ] && [ ! -L "$parent" ] || return 1
    canonical="$(readlink -m -- "$parent" 2>/dev/null)" || return 1
    [ "$canonical" = "$parent" ] || return 1
    metadata="$(stat -c '%u|%a' -- "$parent" 2>/dev/null)" || return 1
    IFS='|' read -r owner mode <<<"$metadata"
    [ "$owner" = "0" ] || [ "$owner" = "$current_uid" ] || return 1
    case "$mode" in *[!0-7]*|'') return 1 ;; esac
    if [ $((8#$mode & 0022)) -ne 0 ]; then
      [ "$parent" = /tmp ] && [ "$owner" = 0 ] && [ $((8#$mode)) -eq $((01777)) ] || return 1
    fi
    [ "$parent" != / ] || break
    parent="${parent%/*}"
    [ -n "$parent" ] || parent=/
  done
}

pinned_shellcheck_is_exact() {
  local path="$1"
  local digest line output version_count=0
  trusted_shellcheck_path "$path" || return 1
  digest="$(sha256sum -- "$path" 2>/dev/null)" || return 1
  digest="${digest%% *}"
  [ "$digest" = "$PINNED_SHELLCHECK_SHA256" ] || return 1
  output="$("$path" --version 2>/dev/null)" || return 1
  while IFS= read -r line; do
    if [ "$line" = "version: $PINNED_SHELLCHECK_VERSION" ]; then
      version_count=$((version_count + 1))
    fi
  done <<<"$output"
  [ "$version_count" -eq 1 ]
}

cleanup() {
  local original_status=$?
  local path
  for path in "${TEMP_RESULTS[@]}"; do
    if [ -n "$path" ] && [ -f "$path" ] && [ ! -L "$path" ]; then
      rm -f -- "$path" >/dev/null 2>&1 || true
    fi
  done
  exit "$original_status"
}
trap cleanup EXIT

private_temp_file() {
  local kind="$1"
  local metadata mode owner result
  [ -d "$SAFE_TMP_DIR" ] && [ ! -L "$SAFE_TMP_DIR" ] || return 1
  metadata="$(stat -c '%u|%a' -- "$SAFE_TMP_DIR" 2>/dev/null)" || return 1
  IFS='|' read -r owner mode <<<"$metadata"
  [ "$owner" = "0" ] || return 1
  case "$mode" in
    *[!0-7]*|'') return 1 ;;
  esac
  [ $((8#$mode)) -eq $((01777)) ] || return 1
  result="$(mktemp "$SAFE_TMP_DIR/google-search-check-${kind}.XXXXXXXX")" || return 1
  if ! chmod 600 "$result"; then
    rm -f -- "$result" >/dev/null 2>&1 || true
    return 1
  fi
  printf '%s\n' "$result"
}

capture_protocol() {
  local status
  "$@"
  status=$?
  printf '%s' "$PROTOCOL_CAPTURE_MARKER"
  return "$status"
}

run_bound_task() {
  local task="$1"
  shift
  /bin/bash -p "$RUNNER" "${RUNNER_MODE[@]}" --quiet \
    --expect-runtime-token "$SELECTED_RUNTIME_TOKEN" --task "$task" -- "$@"
}

select_runtime() {
  local requested_mode=()
  local record separators sentinel selected_mode display_path token
  case "$MODE" in
    system) requested_mode=(--system) ;;
    venv) requested_mode=(--venv) ;;
  esac

  if ! record="$(without_api_keys \
    /bin/bash -p "$RUNNER" "${requested_mode[@]}" --runtime-info 2>&1)"; then
    case "$record" in
      *'source tree or its parent chain is unsafe'*)
        fail "the skill source tree or its parent chain is unsafe"
        ;;
      *)
        fail "no healthy runtime; run install.sh --install-dev-dependencies or select another mode"
        ;;
    esac
  fi

  case "$record" in
    ''|*$'\n'*|*$'\r'*) fail "runner returned an invalid runtime selection record" ;;
  esac
  separators="${record//[!|]/}"
  [ "${#separators}" -eq 3 ] || fail "runner returned an invalid runtime selection record"
  IFS='|' read -r sentinel selected_mode display_path token <<<"$record"
  [ "$sentinel" = "$RUNTIME_INFO_SENTINEL" ] || \
    fail "runner returned an invalid runtime selection record"
  case "$selected_mode" in system|venv) ;; *) fail "runner returned an invalid runtime mode" ;; esac
  case "$display_path" in /*) ;; *) fail "runner returned an invalid runtime path" ;; esac
  reportable_path "$display_path" || fail "runner returned an invalid runtime path"
  [ "${#token}" -eq 64 ] || fail "runner returned an invalid runtime token"
  case "$token" in *[!0-9a-f]*) fail "runner returned an invalid runtime token" ;; esac

  SELECTED_RUNTIME_TOKEN="$token"
  RUNNER_MODE=("--$selected_mode")
  if ! without_api_keys run_bound_task verify >/dev/null 2>&1; then
    fail "the skill source tree, parent chain, or selected runtime changed during the check"
  fi
}

run_pytest_protocol() (
  local variable
  unset SERPER_API_KEY SERPER_API_KEYS
  unset GOOGLE_SEARCH_RELEASE_ARCHIVE GOOGLE_SEARCH_RELEASE_COMMIT
  while IFS= read -r variable; do
    case "$variable" in
      PYTEST_*) unset -v "$variable" 2>/dev/null || return 1 ;;
    esac
  done < <(compgen -e)
  PYTHONDONTWRITEBYTECODE=1
  export PYTHONDONTWRITEBYTECODE
  export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
  if [ -n "$RELEASE_ARCHIVE_ARG" ]; then
    GOOGLE_SEARCH_RELEASE_ARCHIVE="$RELEASE_ARCHIVE_ARG"
    GOOGLE_SEARCH_RELEASE_COMMIT="$RELEASE_COMMIT_ARG"
    export GOOGLE_SEARCH_RELEASE_ARCHIVE GOOGLE_SEARCH_RELEASE_COMMIT
  fi
  capture_protocol run_bound_task check-pytest
)

assert_runtime_binding_unchanged() {
  without_api_keys run_bound_task verify >/dev/null 2>&1 || \
    fail "the skill source tree, parent chain, or selected runtime changed during the check"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --system)
      [ "$MODE" = "auto" ] || fail "--system and --venv are mutually exclusive"
      MODE="system"
      ;;
    --venv)
      [ "$MODE" = "auto" ] || fail "--system and --venv are mutually exclusive"
      MODE="venv"
      ;;
    --online-smoke)
      ONLINE_SMOKE=1
      ;;
    --online-full)
      ONLINE_FULL=1
      ;;
    --shellcheck)
      [ "$#" -ge 2 ] || fail "--shellcheck requires a path"
      [ "$SHELLCHECK_PATH_SET" -eq 0 ] || fail "--shellcheck may be supplied only once"
      [ -n "$2" ] || fail "--shellcheck requires a non-empty path"
      case "$2" in /*) ;; *) fail "--shellcheck requires an absolute path" ;; esac
      reportable_path "$2" || fail "--shellcheck path is unsafe"
      SHELLCHECK_PATH="$2"
      SHELLCHECK_PATH_SET=1
      shift
      ;;
    --release-archive)
      [ "$#" -ge 2 ] || fail "--release-archive requires a path"
      [ "$RELEASE_ARCHIVE_SET" -eq 0 ] || fail "--release-archive may be supplied only once"
      [ -n "$2" ] || fail "--release-archive requires a non-empty path"
      RELEASE_ARCHIVE_ARG="$2"
      RELEASE_ARCHIVE_SET=1
      shift
      ;;
    --release-commit)
      [ "$#" -ge 2 ] || fail "--release-commit requires an object ID"
      [ "$RELEASE_COMMIT_SET" -eq 0 ] || fail "--release-commit may be supplied only once"
      [ -n "$2" ] || fail "--release-commit requires a non-empty object ID"
      RELEASE_COMMIT_ARG="$2"
      RELEASE_COMMIT_SET=1
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
      fail "unknown option"
      ;;
  esac
  shift
done

if [ "$RELEASE_ARCHIVE_SET" -eq 1 ] || [ "$RELEASE_COMMIT_SET" -eq 1 ]; then
  if [ "$RELEASE_ARCHIVE_SET" -ne 1 ] || [ "$RELEASE_COMMIT_SET" -ne 1 ]; then
    fail "--release-archive and --release-commit must be supplied together"
  fi
  case "$RELEASE_ARCHIVE_ARG" in
    /*) ;;
    *) fail "--release-archive must be an absolute path" ;;
  esac
  if [ ! -f "$RELEASE_ARCHIVE_ARG" ] || [ -L "$RELEASE_ARCHIVE_ARG" ]; then
    fail "--release-archive must name a regular non-symlink file"
  fi
  [ "${#RELEASE_COMMIT_ARG}" -eq 40 ] || fail "--release-commit must be a full lowercase object ID"
  case "$RELEASE_COMMIT_ARG" in
    *[!0-9a-f]*) fail "--release-commit must be a full lowercase object ID" ;;
  esac
fi

if [ "${RUN_SMOKE:-0}" != "0" ]; then
  log "RUN_SMOKE is deprecated and ignored; use --online-smoke explicitly"
fi

required_files=(
  "$BASE_DIR/SKILL.md"
  "$BASE_DIR/README.md"
  "$BASE_DIR/requirements.txt"
  "$BASE_DIR/requirements-dev.txt"
  "$BASE_DIR/scripts/install.sh"
  "$BASE_DIR/scripts/check.sh"
  "$BASE_DIR/scripts/run.sh"
  "$BASE_DIR/scripts/runtime_guard.py"
  "$BASE_DIR/scripts/search.py"
  "$BASE_DIR/scripts/selfcheck.py"
  "$BASE_DIR/scripts/secure_io.py"
  "$BASE_DIR/scripts/locked_install.py"
  "$BASE_DIR/scripts/client.py"
  "$BASE_DIR/scripts/check_protocol.py"
)
for file in "${required_files[@]}"; do
  if [ ! -f "$file" ] || [ -L "$file" ]; then
    fail "missing or unsafe required file: $file"
  fi
done

select_runtime

if [ "$ONLINE_SMOKE" -eq 1 ] || [ "$ONLINE_FULL" -eq 1 ]; then
  if KEY_RESULT="$(capture_protocol run_bound_task check-keys)"; then
    :
  else
    key_status=$?
    assert_runtime_binding_unchanged
    if [ "$key_status" -eq 2 ]; then
      fail "online checks require a valid Serper API key configuration" 2
    fi
    fail "online API key preflight failed"
  fi
  if [ "$KEY_RESULT" != "${KEYS_SENTINEL}"$'\n'"${PROTOCOL_CAPTURE_MARKER}" ]; then
    assert_runtime_binding_unchanged
    fail "online API key preflight returned an invalid sentinel"
  fi
fi

log "checking shell syntax"
for file in "$BASE_DIR"/scripts/*.sh; do
  without_api_keys /bin/bash -p -n "$file" || fail "shell syntax check failed: $file"
done

if [ "$SHELLCHECK_PATH_SET" -eq 1 ]; then
  log "running pinned ShellCheck"
  without_api_keys pinned_shellcheck_is_exact "$SHELLCHECK_PATH" || \
    fail "pinned ShellCheck validation failed"
  without_api_keys "$SHELLCHECK_PATH" --norc -x "$BASE_DIR"/scripts/*.sh || \
    fail "ShellCheck failed"
  without_api_keys pinned_shellcheck_is_exact "$SHELLCHECK_PATH" || \
    fail "pinned ShellCheck changed during lint"
elif command -v shellcheck >/dev/null 2>&1; then
  log "running ShellCheck"
  without_api_keys shellcheck --norc -x "$BASE_DIR"/scripts/*.sh || fail "ShellCheck failed"
else
  log "ShellCheck not installed; optional lint step skipped"
fi

log "checking Python AST parsing"
if ! without_api_keys run_bound_task check-ast; then
  assert_runtime_binding_unchanged
  fail "Python AST parsing failed"
fi

log "running formal pytest suite"
if PYTEST_RESULT="$(run_pytest_protocol)"; then
  :
else
  assert_runtime_binding_unchanged
  fail "pytest failed"
fi
if [ "$PYTEST_RESULT" != "${PYTEST_SENTINEL}"$'\n'"${PROTOCOL_CAPTURE_MARKER}" ]; then
  assert_runtime_binding_unchanged
  fail "pytest protocol returned an invalid sentinel"
fi

validate_result_file() {
  local expected="$1"
  local path="$2"
  local expected_sentinel validation_result
  case "$expected" in
    smoke) expected_sentinel="$SMOKE_RESULT_SENTINEL" ;;
    parsing) expected_sentinel="$PARSING_RESULT_SENTINEL" ;;
    full) expected_sentinel="$FULL_RESULT_SENTINEL" ;;
    *) fail "internal result protocol error" ;;
  esac
  if validation_result="$(without_api_keys capture_protocol \
    run_bound_task check-result "$expected" "$path")"; then
    :
  else
    assert_runtime_binding_unchanged
    fail "$expected check returned an invalid result document"
  fi
  if [ "$validation_result" != "${expected_sentinel}"$'\n'"${PROTOCOL_CAPTURE_MARKER}" ]; then
    assert_runtime_binding_unchanged
    fail "$expected result validation returned an invalid sentinel"
  fi
}

PARSING_RESULT="$(private_temp_file parsing)" || fail "failed to allocate parsing result file"
TEMP_RESULTS+=("$PARSING_RESULT")
log "running offline parsing selfcheck"
if ! (cd "$BASE_DIR" && without_api_keys \
  run_bound_task parsing >"$PARSING_RESULT"); then
  assert_runtime_binding_unchanged
  fail "offline parsing selfcheck failed"
fi
validate_result_file parsing "$PARSING_RESULT"

if [ "$ONLINE_SMOKE" -eq 1 ]; then
  ONLINE_RESULT="$(private_temp_file smoke)" || fail "failed to allocate smoke result file"
  TEMP_RESULTS+=("$ONLINE_RESULT")
  log "running explicit online smoke test"
  if ! (cd "$BASE_DIR" && run_bound_task smoke >"$ONLINE_RESULT"); then
    assert_runtime_binding_unchanged
    fail "online smoke test failed"
  fi
  validate_result_file smoke "$ONLINE_RESULT"
fi

if [ "$ONLINE_FULL" -eq 1 ]; then
  ONLINE_RESULT="$(private_temp_file full)" || fail "failed to allocate full-check result file"
  TEMP_RESULTS+=("$ONLINE_RESULT")
  log "running explicit online full selfcheck"
  if ! (cd "$BASE_DIR" && run_bound_task full >"$ONLINE_RESULT"); then
    assert_runtime_binding_unchanged
    fail "online full selfcheck failed"
  fi
  validate_result_file full "$ONLINE_RESULT"
fi

assert_runtime_binding_unchanged
log "check complete (offline=$((1 - (ONLINE_SMOKE || ONLINE_FULL))))"
