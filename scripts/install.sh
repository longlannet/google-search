#!/bin/bash -p
set -euo pipefail
umask 077

if [[ ! -o privileged ]]; then
  printf '%s\n' 'google-search: install.sh requires direct execution or /bin/bash -p' >&2
  exit 2
fi

PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS
while IFS= read -r variable; do
  case "$variable" in
    LD_*|GLIBC_TUNABLES|GCONV_PATH) unset "$variable" ;;
  esac
done < <(compgen -e)

SCRIPT_DIR="$(cd -P -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_DIR="$(cd -P -- "$SCRIPT_DIR/.." && pwd -P)"
VENV="$SKILL_DIR/.venv"
LOCK="$SKILL_DIR/requirements.txt"
INSTALL_LOCK="$SKILL_DIR/.venv.install.lock"
CURRENT_UID="$(/usr/bin/id -u)"
MODE=install

case "${1:-}" in
  '') ;;
  --check) MODE=check; shift ;;
  --smoke-test) MODE=smoke; shift ;;
  *) printf 'usage: %s [--check|--smoke-test]\n' "$0" >&2; exit 2 ;;
esac
[[ "$#" -eq 0 ]] || { printf '%s\n' 'unexpected arguments' >&2; exit 2; }

guard_runtime() {
  /usr/bin/python3 -I -S -B -c '
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
for item in (path, *path.parents):
    metadata = item.lstat()
    kind_ok = stat.S_ISREG(metadata.st_mode) if item == path else stat.S_ISDIR(metadata.st_mode)
    sticky_root = item in {Path("/tmp"), Path("/var/tmp")} and metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o1777
    if not kind_ok or metadata.st_uid not in {0, os.geteuid()} or (metadata.st_mode & 0o022 and not sticky_root):
        sys.exit("google-search: runtime guard or its parent directory is unsafe")
sys.argv = sys.argv[1:]
exec(compile(path.read_bytes(), str(path), "exec"), {"__name__": "__main__", "__file__": str(path)})
' "$SCRIPT_DIR/runtime_guard.py" --skill "$SKILL_DIR" "$@" || return 3
}

check_python_runtime() {
  local python="$1" runtime="$2"
  [[ -x "$python" ]] || return 1
  guard_runtime --runtime "$runtime" || return 1
  "$python" -I -S -B -c '
from pathlib import Path
import re
import sys

if not (3, 10) <= sys.version_info[:2] <= (3, 14):
    raise SystemExit(1)
venv = Path(sys.argv[2])
site_packages = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if not site_packages.is_dir() or site_packages.is_symlink():
    raise SystemExit(1)
sys.path.insert(0, str(site_packages))
from importlib.metadata import version

expected = {}
for raw_line in Path(sys.argv[1]).read_text(encoding="ascii").splitlines():
    line = raw_line.strip()
    if not line or line.startswith(("#", "--")) or "==" not in line:
        continue
    requirement = line.removesuffix("\\").strip()
    package, wanted = requirement.split("==", 1)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", package) or not wanted or any(char.isspace() for char in wanted):
        raise SystemExit(1)
    expected[package] = wanted
if "requests" not in expected:
    raise SystemExit(1)
for package, wanted in expected.items():
    assert version(package) == wanted, (package, version(package), wanted)
import requests
assert requests.__version__ == expected["requests"]
assert site_packages.resolve() in Path(requests.__file__).resolve().parents
' "$LOCK" "$runtime"
}

check_runtime() {
  check_python_runtime "$VENV/bin/python" "$VENV"
}

check_directory() {
  local directory="$1" owner mode
  [[ -d "$directory" && ! -L "$directory" ]] || return 1
  owner="$(/usr/bin/stat -c '%u' -- "$directory")"
  mode="$(/usr/bin/stat -c '%a' -- "$directory")"
  [[ "$owner" == 0 || "$owner" == "$CURRENT_UID" ]] || return 1
  [[ "$mode" != *[!0-7]* ]] || return 1
  (( (8#$mode & 0022) == 0 ))
}

for directory in "$SKILL_DIR" "$SCRIPT_DIR"; do
  check_directory "$directory" || {
    printf 'google-search: unsafe or missing directory: %s\n' "$directory" >&2
    exit 3
  }
done

guard_runtime || exit 3

[[ -f "$LOCK" && ! -L "$LOCK" ]] || { printf '%s\n' 'google-search: requirements.txt is missing or unsafe' >&2; exit 3; }
IFS=: read -r lock_owner lock_mode lock_links < <(/usr/bin/stat -c '%u:%a:%h' -- "$LOCK")
if [[ "$lock_owner" != 0 && "$lock_owner" != "$CURRENT_UID" ]] \
  || [[ "$lock_mode" == *[!0-7]* ]] \
  || (( (8#$lock_mode & 0022) != 0 )) \
  || [[ "$lock_links" != 1 ]]; then
    printf '%s\n' 'google-search: requirements.txt metadata is unsafe' >&2
    exit 3
fi
if [[ "$MODE" != check && ! -e "$INSTALL_LOCK" ]]; then
  (set -o noclobber; : >"$INSTALL_LOCK") 2>/dev/null || true
fi
[[ -f "$INSTALL_LOCK" && ! -L "$INSTALL_LOCK" ]] || {
  printf '%s\n' 'google-search: install lock is unsafe' >&2
  exit 3
}
IFS=: read -r install_lock_owner install_lock_mode install_lock_links \
  < <(/usr/bin/stat -c '%u:%a:%h' -- "$INSTALL_LOCK")
if [[ "$install_lock_owner" != 0 && "$install_lock_owner" != "$CURRENT_UID" ]] \
  || [[ "$install_lock_mode" == *[!0-7]* ]] \
  || (( (8#$install_lock_mode & 0022) != 0 )) \
  || [[ "$install_lock_links" != 1 ]]; then
    printf '%s\n' 'google-search: install lock metadata is unsafe' >&2
    exit 3
fi
if [[ "$MODE" == check ]]; then
  [[ "$install_lock_mode" == 600 ]] || {
    printf '%s\n' 'google-search: install lock metadata is unsafe' >&2
    exit 3
  }
  exec 9<"$INSTALL_LOCK"
  /usr/bin/flock -s -n 9 || { printf '%s\n' 'google-search: runtime installation is busy' >&2; exit 3; }
else
  /bin/chmod 0600 -- "$INSTALL_LOCK"
  exec 9<>"$INSTALL_LOCK"
  /usr/bin/flock -x -n 9 || { printf '%s\n' 'google-search: another runtime installation is active' >&2; exit 3; }
fi

if [[ "$MODE" == check ]]; then
  if check_runtime 2>/dev/null; then
    printf '%s\n' 'google-search: runtime OK'
    exit 0
  fi
  printf '%s\n' 'google-search: runtime is missing or unhealthy' >&2
  exit 3
fi

PYTHON_BASE=""
for candidate in /usr/bin/python3 /usr/local/bin/python3; do
  if [[ -x "$candidate" ]] && "$candidate" -I -c 'import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 14)))'; then
    PYTHON_BASE="$candidate"
    break
  fi
done
[[ -n "$PYTHON_BASE" ]] || { printf '%s\n' 'google-search: Python 3.10-3.14 is required' >&2; exit 3; }

CANDIDATE="$(/usr/bin/mktemp -d "$SKILL_DIR/.venv-build.XXXXXX")"
BACKUP=""
ACTIVE_PID=""
PUBLISHED=0
COMMITTED=0
cleanup() {
  local status="$?"
  trap - EXIT HUP INT TERM
  if [[ -n "$ACTIVE_PID" ]]; then
    /bin/kill -TERM "$ACTIVE_PID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
    ACTIVE_PID=""
  fi
  if [[ "$COMMITTED" -eq 0 ]]; then
    if [[ "$PUBLISHED" -eq 1 && -e "$VENV" ]]; then
      /usr/bin/rm -rf -- "$VENV"
    fi
    if [[ -n "$BACKUP" && -e "$BACKUP" && ! -e "$VENV" ]]; then
      /usr/bin/mv -T -- "$BACKUP" "$VENV" || {
        printf 'google-search: previous runtime requires manual recovery from %s\n' "$BACKUP" >&2
      }
    fi
  elif [[ -n "$BACKUP" && -e "$BACKUP" ]]; then
    printf 'google-search: runtime committed; previous backup remains at %s\n' "$BACKUP" >&2
  fi
  if [[ -n "${CANDIDATE:-}" && -d "$CANDIDATE" ]]; then
    /usr/bin/rm -rf -- "$CANDIDATE"
  fi
  exit "$status"
}
handle_signal() {
  local status="$1"
  trap - HUP INT TERM
  if [[ -n "$ACTIVE_PID" ]]; then
    /bin/kill -TERM "$ACTIVE_PID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
    ACTIVE_PID=""
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'handle_signal 129' HUP
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
/usr/bin/rmdir -- "$CANDIDATE"
"$PYTHON_BASE" -I -m venv "$CANDIDATE"
/usr/bin/timeout --signal=TERM --kill-after=5s 300s \
  "$CANDIDATE/bin/python" -I -m pip --isolated install \
  --disable-pip-version-check --no-input --require-hashes --only-binary=:all: \
  -r "$LOCK" &
ACTIVE_PID="$!"
if ! wait "$ACTIVE_PID"; then
  ACTIVE_PID=""
  printf '%s\n' 'google-search: locked dependency installation failed or timed out' >&2
  exit 3
fi
ACTIVE_PID=""
"$CANDIDATE/bin/python" -I -m pip check
check_python_runtime "$CANDIDATE/bin/python" "$CANDIDATE"

if [[ -e "$VENV" ]]; then
  BACKUP="$(/usr/bin/mktemp -d "$SKILL_DIR/.venv-previous.XXXXXX")"
  /usr/bin/rmdir -- "$BACKUP"
  /usr/bin/mv -T -- "$VENV" "$BACKUP"
fi
# Arm rollback before the rename: a signal can run before its next statement.
PUBLISHED=1
if ! /usr/bin/mv -T -- "$CANDIDATE" "$VENV"; then
  printf '%s\n' 'google-search: runtime publication failed' >&2
  exit 3
fi
CANDIDATE=""
if ! check_runtime; then
  printf '%s\n' 'google-search: new runtime failed validation; rolling back' >&2
  exit 3
fi
# Once backup removal starts, the validated runtime must survive cleanup.
COMMITTED=1
[[ -z "$BACKUP" ]] || /usr/bin/rm -rf -- "$BACKUP"
BACKUP=""
/usr/bin/flock -u 9
exec 9>&-
printf '%s\n' 'google-search: runtime installed'

if [[ "$MODE" == smoke ]]; then
  [[ -n "${SERPER_API_KEY:-}${SERPER_API_KEYS:-}" ]] || {
    printf '%s\n' 'google-search: --smoke-test requires a Serper API key' >&2
    exit 3
  }
  /bin/bash -p "$SCRIPT_DIR/run.sh" web OpenClaw --num 1 --json --compact >/dev/null
  printf '%s\n' 'google-search: smoke test passed'
fi
