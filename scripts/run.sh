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
RUNTIME_GUARD_SCRIPT="$BASE_DIR/scripts/runtime_guard.py"
REQUIRED_REQUESTS_VERSION="2.34.2"
REQUIRED_CERTIFI_VERSION="2026.7.22"
REQUIRED_CHARSET_NORMALIZER_VERSION="3.5.1"
REQUIRED_IDNA_VERSION="3.19"
REQUIRED_URLLIB3_VERSION="2.7.0"
RUNTIME_SENTINEL="google-search-runtime-ok-v1"
SOURCE_TREE_SENTINEL="google-search-source-tree-ok-v1"
RUNTIME_INFO_SENTINEL="google-search-runtime-info-v1"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
MAX_PYTHON_MINOR=14

MODE="auto"
RUNTIME_INFO=0
EXEC_TASK=""
EXPECTED_RUNTIME_TOKEN=""
ARGUMENT_SEPARATOR_SEEN=0
QUIET=0
SELECTED_PYTHON=""
SELECTED_RUNTIME_MODE=""
VENV_RUNTIME_SNAPSHOT=""
SYSTEM_PYTHON=""
SYSTEM_PYTHON_RUNTIME=""
SYSTEM_PYTHON_SNAPSHOT=""

usage() {
  cat <<'EOF'
Usage:
  /bin/bash -p scripts/run.sh [--system|--venv] [--quiet] [--] [search arguments...]
  /bin/bash -p scripts/run.sh [--system|--venv] --runtime-info [--quiet]
  /bin/bash -p scripts/run.sh (--system|--venv) --expect-runtime-token TOKEN --task ROLE [-- task arguments]

Selects a trusted Python 3.10-3.14 runtime matching the complete five-package
runtime lock and required APIs.
Auto mode prefers a healthy local .venv and falls back to system python3.
Without --runtime-info or a token-bound task, it runs scripts/search.py with
the remaining arguments.

Options:
  --system        Use only system python3
  --venv          Use only the local .venv
  --runtime-info  Print a bound runtime selection record and exit
  --expect-runtime-token TOKEN
                  Require an exact runtime-info token before any health probe
  --task ROLE     Run one fixed built-in role; requires a token and explicit mode
  --quiet         Suppress selection errors
  -h, --help      Show this help
EOF
}

error() {
  if [ "$QUIET" -ne 1 ]; then
    printf '[google-search] ERROR: %s\n' "$*" >&2
  fi
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

without_api_keys() (
  unset SERPER_API_KEY SERPER_API_KEYS
  "$@"
)

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
  current_uid="$(/usr/bin/id -u)"

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
      link_owner="$(/usr/bin/stat -c '%u' -- "$candidate" 2>/dev/null)" || return 1
      [ "$link_owner" = "$current_uid" ] || [ "$link_owner" = "0" ] || return 1
      target="$(/usr/bin/readlink -- "$candidate" 2>/dev/null)" || return 1
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
  current_uid="$(/usr/bin/id -u)"

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
      link_owner="$(/usr/bin/stat -c '%u' -- "$candidate" 2>/dev/null)" || return 1
      [ "$link_owner" = "$current_uid" ] || [ "$link_owner" = "0" ] || return 1
      target="$(/usr/bin/readlink -- "$candidate" 2>/dev/null)" || return 1
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
  resolved="$(/usr/bin/readlink -f -- "$current" 2>/dev/null)" || return 1
  [ -n "$resolved" ] && [ "$resolved" = "$current" ] || return 1
  printf '%s\n' "$resolved"
}

path_tree_is_safe() {
  local root="$1"
  local current_uid root_device
  current_uid="$(/usr/bin/id -u)"
  root_device="$(/usr/bin/stat -c '%d' -- "$root" 2>/dev/null)" || return 1

  /usr/bin/find -P "$root" -xdev -print0 2>/dev/null |
    while IFS= read -r -d '' path; do
      local device owner mode
      IFS='|' read -r device owner mode < <(
        /usr/bin/stat -c '%d|%u|%a' -- "$path" 2>/dev/null
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
  current_uid="$(/usr/bin/id -u)"
  [ -d "$root" ] && [ ! -L "$root" ] || return 1
  path_owner_is_safe "$root" || return 1
  path_parent_chain_is_safe "$root" || return 1
  root_device="$(/usr/bin/stat -c '%d' -- "$root" 2>/dev/null)" || return 1

  /usr/bin/find -P "$root" -xdev -printf '%D|%U|%m|%y\0' 2>/dev/null |
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

  /usr/bin/find -P "$root" -xdev -type l -print0 2>/dev/null |
    while IFS= read -r -d '' link; do
      trusted_file_symlink_snapshot "$link" >/dev/null || exit 1
    done || return 1
}

trusted_file_symlink_snapshot() {
  local link="$1"
  local link_before link_after resolved resolved_after target_before target_after
  local current_uid link_owner link_path_digest target_path_digest

  [ -L "$link" ] || return 1
  link_before="$(/usr/bin/stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$link" 2>/dev/null)" || \
    return 1
  current_uid="$(/usr/bin/id -u)"
  link_owner="$(/usr/bin/stat -c '%u' -- "$link" 2>/dev/null)" || return 1
  [ "$link_owner" = "$current_uid" ] || [ "$link_owner" = "0" ] || return 1
  path_parent_chain_is_safe "$link" || return 1
  resolved="$(/usr/bin/readlink -f -- "$link" 2>/dev/null)" || return 1
  [ -n "$resolved" ] && [ -f "$resolved" ] && [ ! -L "$resolved" ] || return 1
  path_owner_is_safe "$resolved" || return 1
  path_parent_chain_is_safe "$resolved" || return 1
  target_before="$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- \
    "$resolved" 2>/dev/null)" || return 1
  link_path_digest="$(printf '%s' "$link" | /usr/bin/sha256sum)" || return 1
  link_path_digest="${link_path_digest%% *}"
  target_path_digest="$(printf '%s' "$resolved" | /usr/bin/sha256sum)" || return 1
  target_path_digest="${target_path_digest%% *}"

  link_after="$(/usr/bin/stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$link" 2>/dev/null)" || \
    return 1
  resolved_after="$(/usr/bin/readlink -f -- "$link" 2>/dev/null)" || return 1
  target_after="$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- \
    "$resolved_after" 2>/dev/null)" || return 1
  [ "$link_after" = "$link_before" ] && [ "$resolved_after" = "$resolved" ] && \
    [ "$target_after" = "$target_before" ] || return 1
  printf '%s|%s|%s|%s\n' \
    "$link_path_digest" "$link_before" "$target_path_digest" "$target_before"
}

tree_metadata_digest() {
  local root="$1"
  local include_followed="${2:-0}"
  local digest
  if [ "$include_followed" -eq 1 ]; then
    digest="$({
      /usr/bin/find -P "$root" -xdev \
        -printf 'P|%D|%i|%U|%G|%m|%n|%s|%T@|%C@|%y|%p|%l\0' || exit 1
      /usr/bin/find -P "$root" -xdev -type l -print0 |
        while IFS= read -r -d '' link; do
          local snapshot
          snapshot="$(trusted_file_symlink_snapshot "$link")" || exit 1
          /usr/bin/printf 'L|%s\0' "$snapshot"
        done || exit 1
    } 2>/dev/null | LC_ALL=C /usr/bin/sort -z | /usr/bin/sha256sum)" || return 1
  else
    digest="$(/usr/bin/find -P "$root" -xdev \
      -printf 'P|%D|%i|%U|%G|%m|%n|%s|%T@|%C@|%y|%p|%l\0' 2>/dev/null | \
      LC_ALL=C /usr/bin/sort -z | /usr/bin/sha256sum)" || return 1
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
  digest="$(/usr/bin/sha256sum -- "$path")" || return 1
  digest="${digest%% *}"
  printf '%s:%s\n' \
    "$(/usr/bin/stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$path")" "$digest"
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
  links="$(/usr/bin/stat -c '%h' -- "$path" 2>/dev/null)" || return 1
  size="$(/usr/bin/stat -c '%s' -- "$path" 2>/dev/null)" || return 1
  [ "$links" -eq 1 ] && [ "$size" -le 67108864 ]
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
        stdlib="$(/usr/bin/readlink -f -- "$stdlib" 2>/dev/null)" || return 1
        [ -n "$stdlib" ] || return 1
        records+=("$stdlib|$zip")
      fi
    done
  done
  [ "${#records[@]}" -gt 0 ] || return 2
  printf '%s\n' "${records[@]}"
}

system_python_runtime() {
  local candidate="$1"
  local resolved basename minor bin_dir prefix record_output layout_status
  local record stdlib zip stdlib_list zip_list control
  local stdlib_candidates=() zip_candidates=() controls=()
  local -A seen_stdlibs=() seen_zips=()

  python_link_chain_is_safe "$candidate" || return 1
  resolved="$(/usr/bin/readlink -f -- "$candidate" 2>/dev/null)" || return 1
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
    zip="$(/usr/bin/readlink -m -- "$zip" 2>/dev/null)" || return 1
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

system_python_runtime_snapshot() {
  local runtime="$1"
  local resolved minor stdlib_list zip_list executable_metadata item item_metadata item_digest
  local stdlibs=() zips=()
  IFS='|' read -r resolved minor stdlib_list zip_list <<<"$runtime"
  [ -n "$resolved" ] && [ -n "$minor" ] && [ -n "$stdlib_list" ] && [ -n "$zip_list" ] || \
    return 1
  executable_metadata="$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- \
    "$resolved")" || return 1
  IFS=':' read -r -a stdlibs <<<"$stdlib_list"
  IFS=':' read -r -a zips <<<"$zip_list"
  printf '%s|%s|%s' "$runtime" "$executable_metadata" "python-runtime-v1"
  for item in "${stdlibs[@]}"; do
    item_metadata="$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$item")" || \
      return 1
    item_digest="$(tree_metadata_digest "$item" 1)" || return 1
    printf '|stdlib:%s:%s:%s' "$item" "$item_metadata" "$item_digest"
  done
  for item in "${zips[@]}"; do
    item_digest="$(zip_metadata_digest "$item")" || return 1
    printf '|zip:%s:%s' "$item" "$item_digest"
  done
  printf '\n'
}

initialize_system_python() {
  local candidate before after
  candidate="$(command -v python3 2>/dev/null)" || return 1
  SYSTEM_PYTHON_RUNTIME="$(system_python_runtime "$candidate")" || return 1
  IFS='|' read -r SYSTEM_PYTHON _ _ _ <<<"$SYSTEM_PYTHON_RUNTIME"
  [ -n "$SYSTEM_PYTHON" ] || return 1
  before="$(system_python_runtime_snapshot "$SYSTEM_PYTHON_RUNTIME")" || return 1
  after="$(system_python_runtime_snapshot "$SYSTEM_PYTHON_RUNTIME")" || return 1
  [ "$after" = "$before" ] || return 1
  SYSTEM_PYTHON_SNAPSHOT="$before"
}

system_python_runtime_matches_snapshot() {
  local current_runtime current_snapshot
  [ -n "$SYSTEM_PYTHON_RUNTIME" ] && [ -n "$SYSTEM_PYTHON_SNAPSHOT" ] || return 1
  current_runtime="$(system_python_runtime "$SYSTEM_PYTHON")" || return 1
  [ "$current_runtime" = "$SYSTEM_PYTHON_RUNTIME" ] || return 1
  current_snapshot="$(system_python_runtime_snapshot "$current_runtime")" || return 1
  [ "$current_snapshot" = "$SYSTEM_PYTHON_SNAPSHOT" ]
}

copy_base_executable() {
  local home="$1"
  local minor="$2"
  local configured_executable="$3"
  local versioned resolved candidate candidate_resolved
  candidate="$home/python3.$minor"
  [ -f "$candidate" ] || return 1
  python_link_chain_is_safe "$candidate" || return 1
  versioned="$(/usr/bin/readlink -f -- "$candidate" 2>/dev/null)" || return 1
  for candidate in "$home/${VENV_PYTHON##*/}" "$home/python3"; do
    [ -f "$candidate" ] || continue
    python_link_chain_is_safe "$candidate" || return 1
    candidate_resolved="$(/usr/bin/readlink -f -- "$candidate" 2>/dev/null)" || return 1
    [ "$candidate_resolved" = "$versioned" ] || return 1
  done
  if [ -n "$configured_executable" ]; then
    candidate_resolved="$(/usr/bin/readlink -f -- "$configured_executable" 2>/dev/null)" || return 1
    [ "$candidate_resolved" = "$versioned" ] || return 1
  fi
  resolved="$versioned"
  printf '%s\n' "$resolved"
}

source_tree_python() {
  system_python_runtime_matches_snapshot || return 1
  printf '%s\n' "$SYSTEM_PYTHON"
}

capture_source_tree_snapshot() {
  local py result digest
  py="$(source_tree_python)" || return 1
  result="$(without_api_keys "$py" -I -S -B -X pycache_prefix=/dev/null/google-search \
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
source_files = frozenset({
    '.gitignore',
    'CHANGELOG.md',
    'LICENSE',
    'README.md',
    'SKILL.md',
    'requirements.in',
    'requirements.txt',
    'requirements-dev.in',
    'requirements-dev.txt',
})
source_directories = frozenset({
    '.github',
    'config',
    'references',
    'scripts',
    'test-wheels',
    'tests',
})
ignored_root_entries = frozenset({
    '.cache',
    '.git',
    '.hypothesis',
    '.mypy_cache',
    '.nox',
    '.pytest_cache',
    '.ruff_cache',
    '.tox',
    '.idea',
    '.venv',
    '.venv-test',
    '.venv.install.lock',
    '.vscode',
    'build',
    'dist',
    'htmlcov',
    'output',
    'runtime',
    'venv',
})
ignored_tree_directories = frozenset({
    '.cache',
    '.hypothesis',
    '.mypy_cache',
    '.nox',
    '.pytest_cache',
    '.ruff_cache',
    '.tox',
    '.idea',
    '.vscode',
    '__pycache__',
    'build',
    'dist',
    'htmlcov',
})
ignored_private_file_names = frozenset({
    '.DS_Store',
    '.env',
    '.netrc',
    '.npmrc',
    '.pypirc',
    'coverage.xml',
    'credentials.json',
    'id_dsa',
    'id_ecdsa',
    'id_ed25519',
    'id_rsa',
    'secrets.json',
    'secrets.yml',
    'secrets.yaml',
    'service-account.json',
})
max_source_bytes = 128 * 1024 * 1024
max_source_entries = 10_000


def validate(metadata, expected_type, *, allow_sticky_boundary=False, child=None):
    if metadata.st_uid not in allowed_owners:
        raise SystemExit(1)
    if expected_type == 'directory' and not stat.S_ISDIR(metadata.st_mode):
        raise SystemExit(1)
    if expected_type == 'file' and not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(1)
    if expected_type == 'file' and metadata.st_nlink != 1:
        raise SystemExit(1)
    if metadata.st_mode & 0o022:
        is_boundary = (
            allow_sticky_boundary
            and child is not None
            and stat.S_IMODE(metadata.st_mode) == 0o1777
            and child.st_uid in allowed_owners
        )
        if not is_boundary:
            raise SystemExit(1)


def walk_error(_error):
    raise SystemExit(1)


def ignored_root_entry(name):
    return (
        name in ignored_root_entries
        or name.startswith('.venv-build.')
        or name.startswith('.venv-bootstrap.')
        or name.startswith('.venv-stage.')
        or name.endswith('.egg-info')
        or name.endswith(('.pyc', '.pyd', '.pyo'))
        or ignored_private_file(name)
    )


def ignored_private_file(name):
    return (
        name in ignored_private_file_names
        or name.startswith(('.coverage.', '.env.'))
        or name == '.coverage'
        or name.endswith(('.key', '.p12', '.pem', '.pfx'))
    )


def ignored_tree_entry(path, *, directory):
    if directory:
        return (
            path.name in ignored_tree_directories
            or path.name.startswith(('.venv-bootstrap.', '.venv-build.', '.venv-stage.'))
            or path.name.endswith('.egg-info')
        )
    if not directory and ignored_private_file(path.name):
        return True
    return path == base / 'config' / 'serper.env'


def stable_file_digest(path, before, remaining_bytes):
    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise SystemExit(1) from None
    stable_fields = (
        'st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
        'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    digest = hashlib.sha256()
    consumed = 0
    try:
        opened = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(opened, field) for field in stable_fields):
            raise SystemExit(1)
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, remaining_bytes - consumed + 1))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > remaining_bytes:
                raise SystemExit(1)
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        named_after = path.lstat()
    except OSError:
        raise SystemExit(1) from None
    if any(
        getattr(opened, field) != getattr(after, field)
        or getattr(after, field) != getattr(named_after, field)
        for field in stable_fields
    ):
        raise SystemExit(1)
    return digest.hexdigest(), consumed


def scan():
    identities = {}
    total_source_bytes = 0

    def remember(path, metadata):
        nonlocal total_source_bytes
        encoded_path = os.fsencode(path)
        if encoded_path not in identities and len(identities) >= max_source_entries:
            raise SystemExit(1)
        identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_mode,
        )
        if stat.S_ISREG(metadata.st_mode):
            content_digest, consumed = stable_file_digest(
                path,
                metadata,
                max_source_bytes - total_source_bytes,
            )
            total_source_bytes += consumed
            identity += (
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                content_digest,
            )
        identities[encoded_path] = identity

    try:
        base_metadata = base.lstat()
    except OSError:
        raise SystemExit(1) from None
    validate(base_metadata, 'directory')
    remember(base, base_metadata)

    current = base
    child_metadata = base_metadata
    while current != current.parent:
        parent = current.parent
        parent_metadata = parent.lstat()
        validate(
            parent_metadata,
            'directory',
            allow_sticky_boundary=True,
            child=child_metadata,
        )
        remember(parent, parent_metadata)
        current = parent
        child_metadata = parent_metadata

    tree_roots = []
    try:
        root_entries = sorted(base.iterdir(), key=lambda path: os.fsencode(path.name))
    except OSError:
        raise SystemExit(1) from None
    for path in root_entries:
        name = path.name
        if ignored_root_entry(name):
            continue
        try:
            metadata = path.lstat()
        except OSError:
            raise SystemExit(1) from None
        if name in source_files:
            validate(metadata, 'file')
            if metadata.st_dev != base_metadata.st_dev:
                raise SystemExit(1)
            remember(path, metadata)
        elif name in source_directories:
            validate(metadata, 'directory')
            if metadata.st_dev != base_metadata.st_dev:
                raise SystemExit(1)
            tree_roots.append(path)
        else:
            raise SystemExit(1)

    if base / 'scripts' not in tree_roots:
        raise SystemExit(1)
    for tree_root in sorted(tree_roots, key=os.fsencode):
        visited = False
        for directory, child_directories, files in os.walk(
            tree_root,
            topdown=True,
            onerror=walk_error,
            followlinks=False,
        ):
            directory_path = Path(directory)
            try:
                directory_metadata = directory_path.lstat()
            except OSError:
                raise SystemExit(1) from None
            validate(directory_metadata, 'directory')
            if directory_metadata.st_dev != base_metadata.st_dev:
                raise SystemExit(1)
            remember(directory_path, directory_metadata)
            visited = True

            kept_directories = []
            for name in sorted(child_directories):
                path = directory_path / name
                if ignored_tree_entry(path, directory=True):
                    continue
                try:
                    metadata = path.lstat()
                except OSError:
                    raise SystemExit(1) from None
                validate(metadata, 'directory')
                if metadata.st_dev != base_metadata.st_dev:
                    raise SystemExit(1)
                remember(path, metadata)
                kept_directories.append(name)
            child_directories[:] = kept_directories

            for name in sorted(files):
                path = directory_path / name
                if name.endswith(('.pyc', '.pyd', '.pyo')):
                    raise SystemExit(1)
                if ignored_tree_entry(path, directory=False):
                    continue
                try:
                    metadata = path.lstat()
                except OSError:
                    raise SystemExit(1) from None
                validate(metadata, 'file')
                if metadata.st_dev != base_metadata.st_dev:
                    raise SystemExit(1)
                remember(path, metadata)
        if not visited:
            raise SystemExit(1)
    return tuple(sorted(identities.items()))


before = scan()
after = scan()
if after != before:
    raise SystemExit(1)

digest = hashlib.sha256()
for path, metadata in before:
    digest.update(len(path).to_bytes(8, 'big'))
    digest.update(path)
    for value in metadata:
        digest.update(str(value).encode('ascii'))
        digest.update(b'\0')
print(f'{sentinel}:{digest.hexdigest()}')
PY
)" || return 1
  case "$result" in
    "$SOURCE_TREE_SENTINEL":*) digest="${result#*:}" ;;
    *) return 1 ;;
  esac
  [ "${#digest}" -eq 64 ] || return 1
  case "$digest" in
    *[!0-9a-f]*) return 1 ;;
  esac
  printf '%s\n' "$digest"
}

source_tree_matches_snapshot() {
  local expected="$1"
  local current
  current="$(capture_source_tree_snapshot)" || return 1
  [ "$current" = "$expected" ]
}

venv_structure_is_safe() {
  local config_size venv_home base_executables venv_stdlibs venv_zips venv_runtime

  [ -d "$VENV_DIR" ] || return 1
  [ ! -L "$VENV_DIR" ] || return 1
  [ -d "$VENV_DIR/bin" ] && [ ! -L "$VENV_DIR/bin" ] || return 1
  [ -f "$VENV_DIR/pyvenv.cfg" ] && [ ! -L "$VENV_DIR/pyvenv.cfg" ] || return 1
  [ -x "$VENV_PYTHON" ] || return 1

  path_owner_is_safe "$VENV_DIR" || return 1
  path_owner_is_safe "$VENV_DIR/bin" || return 1
  path_owner_is_safe "$VENV_DIR/pyvenv.cfg" || return 1
  config_size="$(/usr/bin/stat -c '%s' -- "$VENV_DIR/pyvenv.cfg" 2>/dev/null)" || return 1
  [ "$config_size" -le 16384 ] || return 1
  venv_runtime="$(venv_config_runtime "$VENV_DIR")" || return 1
  IFS='|' read -r venv_home base_executables venv_stdlibs venv_zips <<<"$venv_runtime"

  python_link_chain_is_safe "$VENV_PYTHON" || return 1
  [ -n "$venv_home" ] && [ -n "$base_executables" ] && [ -n "$venv_stdlibs" ] && \
    [ -n "$venv_zips" ] || return 1
  venv_site_paths_are_safe || return 1
}

venv_config_runtime() {
  local venv_dir="$1"
  local include_count=0 home_count=0 executable_count=0 version_count=0
  local line value home resolved_home executable="" version minor
  local resolved_python configured_resolved="" copy_base="" record_output layout_status
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
    configured_resolved="$(/usr/bin/readlink -f -- "$executable" 2>/dev/null)" || return 1
  fi
  python_link_chain_is_safe "$VENV_PYTHON" || return 1
  resolved_python="$(/usr/bin/readlink -f -- "$VENV_PYTHON" 2>/dev/null)" || return 1
  [ -n "$resolved_python" ] || return 1
  python_executable_is_safe "$resolved_python" || return 1
  case "$resolved_python" in
    "$VENV_DIR"/*)
      copy_base="$(copy_base_executable "$resolved_home" "$minor" "$executable")" || return 1
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
      zip="$(/usr/bin/readlink -m -- "$zip" 2>/dev/null)" || return 1
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

venv_site_paths_are_safe() {
  local python_dir site_dir
  local found=0
  local site_dirs=()
  local startup_hooks=()

  [ -d "$VENV_DIR/lib" ] && [ ! -L "$VENV_DIR/lib" ] || return 1
  path_owner_is_safe "$VENV_DIR/lib" || return 1

  shopt -s nullglob
  site_dirs=("$VENV_DIR"/lib/python*/site-packages)
  shopt -u nullglob
  for site_dir in "${site_dirs[@]}"; do
    python_dir="$(dirname -- "$site_dir")"
    [ -d "$python_dir" ] && [ ! -L "$python_dir" ] || return 1
    [ -d "$site_dir" ] && [ ! -L "$site_dir" ] || return 1
    path_owner_is_safe "$python_dir" || return 1
    path_owner_is_safe "$site_dir" || return 1
    path_tree_is_safe "$site_dir" || return 1
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
    found=1
  done
  [ "$found" -eq 1 ]
}

venv_runtime_snapshot() {
  local resolved_python venv_home base_list stdlib_list zip_list venv_runtime
  local item
  local base_executables=() venv_stdlibs=() venv_zips=()
  venv_structure_is_safe || return 1
  resolved_python="$(/usr/bin/readlink -f -- "$VENV_PYTHON" 2>/dev/null)" || return 1
  venv_runtime="$(venv_config_runtime "$VENV_DIR")" || return 1
  IFS='|' read -r venv_home base_list stdlib_list zip_list <<<"$venv_runtime"
  [ -n "$venv_home" ] && [ -n "$base_list" ] && [ -n "$stdlib_list" ] && \
    [ -n "$zip_list" ] || return 1
  IFS=':' read -r -a base_executables <<<"$base_list"
  IFS=':' read -r -a venv_stdlibs <<<"$stdlib_list"
  IFS=':' read -r -a venv_zips <<<"$zip_list"
  printf '%s|%s|%s|%s|%s|%s|%s' \
    "$(/usr/bin/stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$VENV_DIR")" \
    "$(/usr/bin/stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$VENV_DIR/pyvenv.cfg")" \
    "$(/usr/bin/stat -c '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$VENV_PYTHON")" \
    "$resolved_python" \
    "$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$resolved_python")" \
    "$venv_home:$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$venv_home")" \
    "venv-tree:$(tree_metadata_digest "$VENV_DIR")"
  for item in "${base_executables[@]}"; do
    printf '|base:%s:%s' "$item" \
      "$(/usr/bin/stat -Lc '%d:%i:%u:%g:%f:%h:%s:%Y:%Z' -- "$item")"
  done
  for item in "${venv_stdlibs[@]}"; do
    printf '|stdlib:%s:%s' "$item" "$(tree_metadata_digest "$item" 1)"
  done
  for item in "${venv_zips[@]}"; do
    printf '|zip:%s:%s' "$item" "$(zip_metadata_digest "$item")"
  done
  printf '\n'
}

healthy_venv_snapshot() {
  local before after venv_runtime
  before="$(venv_runtime_snapshot)" || return 1
  venv_runtime="$(venv_config_runtime "$VENV_DIR")" || return 1
  python_is_healthy "$VENV_PYTHON" "$venv_runtime" || return 1
  after="$(venv_runtime_snapshot)" || return 1
  [ "$after" = "$before" ] || return 1
  printf '%s\n' "$before"
}

venv_runtime_matches_snapshot() {
  local current
  [ -n "$VENV_RUNTIME_SNAPSHOT" ] || return 1
  current="$(venv_runtime_snapshot)" || return 1
  [ "$current" = "$VENV_RUNTIME_SNAPSHOT" ]
}

python_is_healthy() {
  local candidate="$1"
  local venv_runtime="$2"
  local venv_home base_executables venv_stdlibs venv_zips
  local result
  IFS='|' read -r venv_home base_executables venv_stdlibs venv_zips <<<"$venv_runtime"
  [ -n "$venv_home" ] && [ -n "$base_executables" ] && [ -n "$venv_stdlibs" ] && \
    [ -n "$venv_zips" ] || return 1
  result="$(without_api_keys "$candidate" -I -B -X pycache_prefix=/dev/null/google-search - \
    "$RUNTIME_SENTINEL" "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" "$MAX_PYTHON_MINOR" \
    "$REQUIRED_CERTIFI_VERSION" "$REQUIRED_CHARSET_NORMALIZER_VERSION" \
    "$REQUIRED_IDNA_VERSION" "$REQUIRED_REQUESTS_VERSION" "$REQUIRED_URLLIB3_VERSION" \
    "$VENV_DIR" "$base_executables" "$venv_stdlibs" "$venv_zips" <<'PY' 2>/dev/null
import importlib.metadata
import os
import sys
from pathlib import Path

import encodings

sentinel = sys.argv[1]
minimum = (int(sys.argv[2]), int(sys.argv[3]))
maximum = (int(sys.argv[2]), int(sys.argv[4]))
expected = {
    'certifi': sys.argv[5],
    'charset-normalizer': sys.argv[6],
    'idna': sys.argv[7],
    'requests': sys.argv[8],
    'urllib3': sys.argv[9],
}
venv = Path(sys.argv[10]).resolve(strict=True)
base_executables = {Path(value).resolve(strict=True) for value in sys.argv[11].split(':')}
stdlibs = {Path(value).resolve(strict=True) for value in sys.argv[12].split(':')}
zips = {Path(value).resolve(strict=False) for value in sys.argv[13].split(':')}
if not minimum <= sys.version_info[:2] <= maximum:
    raise SystemExit(1)

resolved_executable = Path(sys.executable).resolve(strict=True)
raw_base_executable = getattr(sys, '_base_executable', sys.executable)
resolved_base_executable = Path(raw_base_executable).resolve(strict=True)
if resolved_executable not in base_executables or resolved_base_executable not in base_executables:
    raise SystemExit(1)

site_roots = tuple(path.resolve(strict=True) for path in venv.glob('lib/python*/site-packages'))
if len(site_roots) != 1:
    raise SystemExit(1)
allowed_sys_paths = stdlibs | zips | set(site_roots)
allowed_sys_paths.update(root / 'lib-dynload' for root in stdlibs)
for raw_path in sys.path:
    if not raw_path or Path(raw_path).resolve(strict=False) not in allowed_sys_paths:
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

for distribution, required_version in expected.items():
    if importlib.metadata.version(distribution) != required_version:
        raise SystemExit(1)

import certifi
import charset_normalizer
import idna
import requests
import urllib3

required_apis = (
    certifi.where,
    charset_normalizer.from_bytes,
    idna.encode,
    idna.decode,
    requests.Session,
    urllib3.PoolManager,
)
if not all(callable(api) for api in required_apis):
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

system_python_is_healthy() {
  local candidate="$1"
  local runtime="$2"
  local resolved minor stdlib_list zip_list
  local result
  IFS='|' read -r resolved minor stdlib_list zip_list <<<"$runtime"
  [ "$candidate" = "$resolved" ] || return 1
  [ -f "$RUNTIME_GUARD_SCRIPT" ] && [ ! -L "$RUNTIME_GUARD_SCRIPT" ] || return 1
  result="$(without_api_keys "$candidate" -I -S -B \
    -X pycache_prefix=/dev/null/google-search \
    "$RUNTIME_GUARD_SCRIPT" probe "$RUNTIME_SENTINEL" \
    "$resolved" "$minor" "$stdlib_list" "$zip_list" runtime 2>/dev/null)" || return 1
  [ "$result" = "$RUNTIME_SENTINEL" ]
}

system_python_path() {
  system_python_runtime_matches_snapshot || return 1
  printf '%s\n' "$SYSTEM_PYTHON"
}

select_python() {
  local system_python=""

  case "$MODE" in
    auto|venv)
      if VENV_RUNTIME_SNAPSHOT="$(healthy_venv_snapshot)"; then
        SELECTED_PYTHON="$VENV_PYTHON"
        SELECTED_RUNTIME_MODE="venv"
        return 0
      fi
      if [ "$MODE" = "venv" ]; then
        error "local .venv is absent, unsafe, damaged, or does not match requirements.txt"
        return 1
      fi
      ;;
  esac

  case "$MODE" in
    auto|system)
      system_python="$(system_python_path)" || {
        error "a trusted system python3 was not found"
        return 1
      }
      if system_python_is_healthy "$system_python" "$SYSTEM_PYTHON_RUNTIME" && \
        system_python_runtime_matches_snapshot; then
        SELECTED_PYTHON="$system_python"
        SELECTED_RUNTIME_MODE="system"
        return 0
      fi
      error "system python3 must be Python 3.10-3.14 and match the complete runtime lock"
      return 1
      ;;
  esac

  error "invalid runtime mode: $MODE"
  return 1
}

runtime_token() {
  local mode="$1"
  local snapshot="$2"
  local digest
  digest="$(printf 'google-search-runtime-token-v1\0%s\0%s\0%s\0' \
    "$mode" "$SOURCE_TREE_SNAPSHOT" "$snapshot" | /usr/bin/sha256sum)" || return 1
  digest="${digest%% *}"
  [ "${#digest}" -eq 64 ] || return 1
  case "$digest" in *[!0-9a-f]*) return 1 ;; esac
  printf '%s\n' "$digest"
}

preflight_expected_runtime_token() {
  local snapshot token
  [ -n "$EXPECTED_RUNTIME_TOKEN" ] || return 0
  case "$MODE" in
    venv) snapshot="$(venv_runtime_snapshot)" || return 1 ;;
    system)
      system_python_runtime_matches_snapshot || return 1
      snapshot="$SYSTEM_PYTHON_SNAPSHOT"
      ;;
    *) return 1 ;;
  esac
  token="$(runtime_token "$MODE" "$snapshot")" || return 1
  [ "$token" = "$EXPECTED_RUNTIME_TOKEN" ]
}

selected_runtime_token() {
  case "$SELECTED_RUNTIME_MODE" in
    venv) runtime_token venv "$VENV_RUNTIME_SNAPSHOT" ;;
    system) runtime_token system "$SYSTEM_PYTHON_SNAPSHOT" ;;
    *) return 1 ;;
  esac
}

prepare_exec_task() {
  local task="$1"
  shift
  TARGET_PROFILE="runtime"
  case "$task" in
    verify)
      [ "$#" -eq 0 ] || return 1
      TARGET_SCRIPT=""
      TARGET_ARGUMENTS=()
      ;;
    check-keys)
      [ "$#" -eq 0 ] || return 1
      TARGET_SCRIPT="$BASE_DIR/scripts/check_protocol.py"
      TARGET_ARGUMENTS=(keys --client-path "$BASE_DIR/scripts/client.py")
      ;;
    check-result)
      [ "$#" -eq 2 ] || return 1
      case "$1" in smoke|parsing|full) ;; *) return 1 ;; esac
      case "$2" in /*) ;; *) return 1 ;; esac
      TARGET_SCRIPT="$BASE_DIR/scripts/check_protocol.py"
      TARGET_ARGUMENTS=(result --path "$2" --expected "$1")
      ;;
    check-ast)
      [ "$#" -eq 0 ] || return 1
      TARGET_SCRIPT="$BASE_DIR/scripts/check_protocol.py"
      TARGET_ARGUMENTS=(ast --base-dir "$BASE_DIR")
      ;;
    check-pytest)
      [ "$#" -eq 0 ] || return 1
      TARGET_PROFILE="development"
      TARGET_SCRIPT="$BASE_DIR/scripts/check_protocol.py"
      TARGET_ARGUMENTS=(pytest --base-dir "$BASE_DIR" --required-version 9.1.1)
      ;;
    check-release-source)
      [ "$ARGUMENT_SEPARATOR_SEEN" -eq 1 ] && [ "$#" -eq 2 ] || return 1
      case "$1" in /*) ;; *) return 1 ;; esac
      case "$2" in /*) ;; *) return 1 ;; esac
      TARGET_SCRIPT="$BASE_DIR/scripts/check_protocol.py"
      TARGET_ARGUMENTS=(release-source --base-dir "$BASE_DIR" -- "$1" "$2")
      ;;
    smoke)
      [ "$#" -eq 0 ] || return 1
      TARGET_SCRIPT="$BASE_DIR/scripts/smoke_test.py"
      TARGET_ARGUMENTS=(--compact)
      ;;
    parsing)
      [ "$#" -eq 0 ] || return 1
      TARGET_SCRIPT="$BASE_DIR/scripts/selfcheck.py"
      TARGET_ARGUMENTS=(--group parsing --compact)
      ;;
    full)
      [ "$#" -eq 0 ] || return 1
      TARGET_SCRIPT="$BASE_DIR/scripts/selfcheck.py"
      TARGET_ARGUMENTS=(--full --compact)
      ;;
    *) return 1 ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --system)
      [ "$MODE" = "auto" ] || { error "--system and --venv are mutually exclusive"; exit 2; }
      MODE="system"
      shift
      ;;
    --venv)
      [ "$MODE" = "auto" ] || { error "--system and --venv are mutually exclusive"; exit 2; }
      MODE="venv"
      shift
      ;;
    --runtime-info)
      [ "$RUNTIME_INFO" -eq 0 ] || { error "--runtime-info may be supplied only once"; exit 2; }
      RUNTIME_INFO=1
      shift
      ;;
    --expect-runtime-token)
      [ "$#" -ge 2 ] || { error "--expect-runtime-token requires a token"; exit 2; }
      [ -z "$EXPECTED_RUNTIME_TOKEN" ] || { error "runtime token may be supplied only once"; exit 2; }
      EXPECTED_RUNTIME_TOKEN="$2"
      shift 2
      ;;
    --task)
      [ "$#" -ge 2 ] || { error "--task requires a role"; exit 2; }
      [ -z "$EXEC_TASK" ] || { error "--task may be supplied only once"; exit 2; }
      EXEC_TASK="$2"
      shift 2
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      ARGUMENT_SEPARATOR_SEEN=1
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [ "$RUNTIME_INFO" -eq 1 ] && { [ -n "$EXEC_TASK" ] || [ -n "$EXPECTED_RUNTIME_TOKEN" ]; }; then
  error "--runtime-info cannot be combined with a task or expected token"
  exit 2
fi
if [ -n "$EXEC_TASK" ] && [ -z "$EXPECTED_RUNTIME_TOKEN" ]; then
  error "--task requires --expect-runtime-token"
  exit 2
fi
if [ -z "$EXEC_TASK" ] && [ -n "$EXPECTED_RUNTIME_TOKEN" ]; then
  error "--expect-runtime-token requires --task"
  exit 2
fi
if [ -n "$EXPECTED_RUNTIME_TOKEN" ]; then
  [ "${#EXPECTED_RUNTIME_TOKEN}" -eq 64 ] || { error "invalid runtime token"; exit 2; }
  case "$EXPECTED_RUNTIME_TOKEN" in *[!0-9a-f]*) error "invalid runtime token"; exit 2 ;; esac
  [ "$MODE" != "auto" ] || { error "token-bound tasks require an explicit runtime mode"; exit 2; }
fi
if [ "$RUNTIME_INFO" -eq 1 ] && [ "$#" -gt 0 ]; then
  error "--runtime-info cannot be combined with search arguments"
  exit 2
fi
if [ -n "$EXEC_TASK" ]; then
  prepare_exec_task "$EXEC_TASK" "$@" || {
    error "invalid task role or arguments"
    exit 2
  }
fi

initialize_system_python || {
  error "a trusted Python 3.10-3.14 stdlib could not be validated before startup"
  exit 3
}

SOURCE_TREE_SNAPSHOT="$(capture_source_tree_snapshot)" || {
  error "the skill source tree or its parent chain is unsafe"
  exit 3
}

preflight_expected_runtime_token || {
  error "the selected runtime changed before its health probe"
  exit 3
}

select_python || exit 3
source_tree_matches_snapshot "$SOURCE_TREE_SNAPSHOT" || {
  error "the skill source tree or its parent chain changed during runtime selection"
  exit 3
}

CURRENT_RUNTIME_TOKEN="$(selected_runtime_token)" || {
  error "failed to bind the selected runtime"
  exit 3
}
if [ -n "$EXPECTED_RUNTIME_TOKEN" ] && [ "$CURRENT_RUNTIME_TOKEN" != "$EXPECTED_RUNTIME_TOKEN" ]; then
  error "the selected runtime no longer matches the expected token"
  exit 3
fi

if [ "$RUNTIME_INFO" -eq 1 ]; then
  source_tree_matches_snapshot "$SOURCE_TREE_SNAPSHOT" || {
    error "the skill source tree or its parent chain changed before reporting the runtime"
    exit 3
  }
  if [ "$SELECTED_PYTHON" = "$VENV_PYTHON" ]; then
    venv_runtime_matches_snapshot || {
      error "the local .venv changed before reporting the runtime"
      exit 3
    }
  else
    system_python_runtime_matches_snapshot || {
      error "the system Python changed before reporting the runtime"
      exit 3
    }
  fi
  FINAL_RUNTIME_TOKEN="$(selected_runtime_token)" || {
    error "failed to rebind the selected runtime before reporting it"
    exit 3
  }
  [ "$FINAL_RUNTIME_TOKEN" = "$CURRENT_RUNTIME_TOKEN" ] || {
    error "the selected runtime changed before it was reported"
    exit 3
  }
  reportable_path "$SELECTED_PYTHON" || {
    error "selected runtime path is not reportable"
    exit 3
  }
  printf '%s|%s|%s|%s\n' "$RUNTIME_INFO_SENTINEL" "$SELECTED_RUNTIME_MODE" \
    "$SELECTED_PYTHON" "$CURRENT_RUNTIME_TOKEN"
  exit 0
fi

source_tree_matches_snapshot "$SOURCE_TREE_SNAPSHOT" || {
  error "the skill source tree or its parent chain changed before execution"
  exit 3
}
if [ "$SELECTED_PYTHON" = "$VENV_PYTHON" ]; then
  venv_runtime_matches_snapshot || {
    error "the local .venv changed before execution"
    exit 3
  }
else
  system_python_runtime_matches_snapshot || {
    error "the system Python changed before execution"
    exit 3
  }
fi
FINAL_RUNTIME_TOKEN="$(selected_runtime_token)" || {
  error "failed to rebind the selected runtime before execution"
  exit 3
}
if [ "$FINAL_RUNTIME_TOKEN" != "$CURRENT_RUNTIME_TOKEN" ]; then
  error "the selected runtime changed before execution"
  exit 3
fi

if [ -z "$EXEC_TASK" ]; then
  TARGET_PROFILE="runtime"
  TARGET_SCRIPT="$BASE_DIR/scripts/search.py"
  TARGET_ARGUMENTS=("$@")
fi
if [ "$EXEC_TASK" = "verify" ]; then
  exit 0
fi
if [ "$SELECTED_PYTHON" != "$VENV_PYTHON" ]; then
  IFS='|' read -r system_executable system_minor system_stdlibs system_zips \
    <<<"$SYSTEM_PYTHON_RUNTIME"
  exec "$SELECTED_PYTHON" -I -S -B -X pycache_prefix=/dev/null/google-search \
    "$RUNTIME_GUARD_SCRIPT" run "$system_executable" "$system_minor" \
    "$system_stdlibs" "$system_zips" "$TARGET_PROFILE" "$TARGET_SCRIPT" \
    "${TARGET_ARGUMENTS[@]}"
fi

exec "$SELECTED_PYTHON" -I -B -X pycache_prefix=/dev/null/google-search -c '
import runpy
import sys
from pathlib import Path

script = Path(sys.argv[1]).resolve(strict=True)
sys.argv = sys.argv[1:]
sys.path.append(str(script.parent))
runpy.run_path(str(script), run_name="__main__")
' "$TARGET_SCRIPT" "${TARGET_ARGUMENTS[@]}"
