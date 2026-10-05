#!/bin/bash -p
set -euo pipefail
umask 077

if [[ ! -o privileged ]]; then
  printf '%s\n' 'google-search: run.sh requires direct execution or /bin/bash -p' >&2
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
PYTHON="$SKILL_DIR/.venv/bin/python"
ENTRY="$SCRIPT_DIR/search.py"
INSTALL_LOCK="$SKILL_DIR/.venv.install.lock"
CURRENT_UID="$(/usr/bin/id -u)"

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
  if ! check_directory "$directory"; then
    printf 'google-search: unsafe or missing directory: %s\n' "$directory" >&2
    exit 3
  fi
done

if [[ ! -f "$INSTALL_LOCK" || -L "$INSTALL_LOCK" ]]; then
  printf '%s\n' 'google-search: runtime lock missing or unsafe; run scripts/install.sh first' >&2
  exit 3
fi
exec 9<"$INSTALL_LOCK"
if ! /usr/bin/flock -s -n 9; then
  printf '%s\n' 'google-search: runtime installation is active; try again shortly' >&2
  exit 3
fi

for directory in "$SKILL_DIR/.venv" "$SKILL_DIR/.venv/bin"; do
  if ! check_directory "$directory"; then
    printf 'google-search: unsafe or missing directory: %s\n' "$directory" >&2
    exit 3
  fi
done

guard_runtime --runtime "$SKILL_DIR/.venv" || exit 3

if [[ ! -f "$ENTRY" || -L "$ENTRY" || ! -x "$PYTHON" ]]; then
  printf '%s\n' 'google-search: runtime missing; run scripts/install.sh first' >&2
  exit 3
fi

PYTHON_VERSION="$("$PYTHON" -I -S -B -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VERSION" in
  3.10|3.11|3.12|3.13|3.14) ;;
  *) printf '%s\n' 'google-search: unsupported runtime version' >&2; exit 3 ;;
esac
SITE_PACKAGES="$SKILL_DIR/.venv/lib/python$PYTHON_VERSION/site-packages"
if ! check_directory "$SITE_PACKAGES"; then
  printf '%s\n' 'google-search: runtime site-packages is missing or unsafe' >&2
  exit 3
fi

exec "$PYTHON" -I -S -c '
import runpy
import sys
sys.dont_write_bytecode = True
entry, site_packages = sys.argv[1:3]
sys.argv = [entry, *sys.argv[3:]]
sys.path[:0] = [entry.rsplit("/", 1)[0], site_packages]
runpy.run_path(entry, run_name="__main__")
' "$ENTRY" "$SITE_PACKAGES" "$@"
