#!/bin/bash -p
set -euo pipefail
umask 077

if [[ ! -o privileged ]]; then
  printf '%s\n' 'google-search: check.sh requires direct execution or /bin/bash -p' >&2
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
INSTALL_LOCK="$SKILL_DIR/.venv.install.lock"
CURRENT_UID="$(/usr/bin/id -u)"
SHELLCHECK=""
SMOKE=0
REQUIRE_VENV=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --venv) REQUIRE_VENV=1; shift ;;
    --shellcheck)
      [[ "$#" -ge 2 ]] || { printf '%s\n' '--shellcheck requires a path' >&2; exit 2; }
      SHELLCHECK="$2"; shift 2 ;;
    --smoke-test) SMOKE=1; shift ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [[ "$SMOKE" -eq 0 ]]; then
  unset SERPER_API_KEY SERPER_API_KEYS
fi

printf '%s\n' '[google-search] checking shell syntax'
for script in "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/check.sh"; do
  /bin/bash -n "$script"
done

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

if [[ -e "$INSTALL_LOCK" || -L "$INSTALL_LOCK" || -e "$SKILL_DIR/.venv" || -L "$SKILL_DIR/.venv" || "$REQUIRE_VENV" -eq 1 ]]; then
  [[ -f "$INSTALL_LOCK" && ! -L "$INSTALL_LOCK" ]] || {
    printf '%s\n' 'google-search: install lock missing or unsafe; run scripts/install.sh first' >&2
    exit 3
  }
  IFS=: read -r lock_owner lock_mode lock_links < <(/usr/bin/stat -c '%u:%a:%h' -- "$INSTALL_LOCK")
  if [[ "$lock_owner" != 0 && "$lock_owner" != "$CURRENT_UID" ]] \
    || [[ "$lock_mode" != 600 || "$lock_links" != 1 ]]; then
    printf '%s\n' 'google-search: install lock metadata is unsafe' >&2
    exit 3
  fi
  exec 9<"$INSTALL_LOCK"
  /usr/bin/flock -s -n 9 || {
    printf '%s\n' 'google-search: runtime installation is busy' >&2
    exit 3
  }
  [[ -x "$PYTHON" ]] || {
    printf '%s\n' 'google-search: required .venv runtime not found' >&2
    exit 3
  }
else
  PYTHON=/usr/bin/python3
fi
if [[ "$PYTHON" == "$SKILL_DIR/.venv/bin/python" ]]; then
  guard_runtime --runtime "$SKILL_DIR/.venv" || exit 3
else
  guard_runtime || exit 3
fi
[[ -x "$PYTHON" ]] || { printf '%s\n' 'google-search: Python runtime not found' >&2; exit 3; }
PYTHON_VERSION="$("$PYTHON" -I -S -B -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PYTHON" == "$SKILL_DIR/.venv/bin/python" ]]; then
  SITE_PACKAGES="$SKILL_DIR/.venv/lib/python$PYTHON_VERSION/site-packages"
else
  SITE_PACKAGES="$("$PYTHON" -I -S -B -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
fi
[[ -d "$SITE_PACKAGES" && ! -L "$SITE_PACKAGES" ]] || {
  printf '%s\n' 'google-search: Python site-packages not found' >&2
  exit 3
}
if [[ "$PYTHON" != "$SKILL_DIR/.venv/bin/python" ]]; then
  guard_runtime --site-packages "$SITE_PACKAGES" || exit 3
fi

if [[ -z "$SHELLCHECK" ]] && command -v shellcheck >/dev/null 2>&1; then
  SHELLCHECK="$(command -v shellcheck)"
fi
if [[ -n "$SHELLCHECK" ]]; then
  [[ -x "$SHELLCHECK" ]] || { printf '%s\n' 'google-search: ShellCheck path is not executable' >&2; exit 3; }
  printf '%s\n' '[google-search] running ShellCheck'
  "$SHELLCHECK" --norc -x "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/check.sh"
fi

printf '%s\n' '[google-search] compiling Python modules'
"$PYTHON" -I -S -B -c '
import sys
for path in sys.argv[1:]:
    with open(path, "rb") as source:
        compile(source.read(), path, "exec")
' "$SCRIPT_DIR"/*.py

printf '%s\n' '[google-search] running offline regression tests'
"$PYTHON" -I -S -B -c '
import sys
import unittest
scripts, site_packages = sys.argv[1:3]
sys.path[:0] = [scripts, site_packages]
suite = unittest.defaultTestLoader.discover(scripts, pattern="test_*.py")
if suite.countTestCases() == 0:
    raise SystemExit("google-search: no offline regression tests found")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
' "$SCRIPT_DIR" "$SITE_PACKAGES"

if [[ "$SMOKE" -eq 1 ]]; then
  printf '%s\n' '[google-search] running explicit online smoke test'
  /bin/bash -p "$SCRIPT_DIR/run.sh" web OpenClaw --num 1 --json --compact >/dev/null
fi
printf '%s\n' '[google-search] checks passed'
