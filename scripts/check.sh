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

if [[ ! -x "$PYTHON" ]]; then
  [[ "$REQUIRE_VENV" -eq 0 ]] || {
    printf '%s\n' 'google-search: required .venv runtime not found' >&2
    exit 3
  }
  PYTHON=/usr/bin/python3
fi
[[ -x "$PYTHON" ]] || { printf '%s\n' 'google-search: Python runtime not found' >&2; exit 3; }
PYTHON_VERSION="$("$PYTHON" -I -S -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PYTHON" == "$SKILL_DIR/.venv/bin/python" ]]; then
  SITE_PACKAGES="$SKILL_DIR/.venv/lib/python$PYTHON_VERSION/site-packages"
else
  SITE_PACKAGES="$("$PYTHON" -I -S -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
fi
[[ -d "$SITE_PACKAGES" && ! -L "$SITE_PACKAGES" ]] || {
  printf '%s\n' 'google-search: Python site-packages not found' >&2
  exit 3
}

printf '%s\n' '[google-search] checking shell syntax'
/bin/bash -n "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/check.sh"

if [[ -z "$SHELLCHECK" ]] && command -v shellcheck >/dev/null 2>&1; then
  SHELLCHECK="$(command -v shellcheck)"
fi
if [[ -n "$SHELLCHECK" ]]; then
  [[ -x "$SHELLCHECK" ]] || { printf '%s\n' 'google-search: ShellCheck path is not executable' >&2; exit 3; }
  printf '%s\n' '[google-search] running ShellCheck'
  "$SHELLCHECK" --norc -x "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/check.sh"
fi

printf '%s\n' '[google-search] compiling active Python modules'
"$PYTHON" -I -S -B -c '
import sys
for path in sys.argv[1:]:
    with open(path, "rb") as source:
        compile(source.read(), path, "exec")
' \
  "$SCRIPT_DIR/args.py" "$SCRIPT_DIR/client.py" "$SCRIPT_DIR/helptext.py" \
  "$SCRIPT_DIR/search.py" "$SCRIPT_DIR/io_common.py" "$SCRIPT_DIR/secure_io.py" \
  "$SCRIPT_DIR/renderers_json.py" "$SCRIPT_DIR/renderers_pretty.py" "$SCRIPT_DIR/workflows.py"

printf '%s\n' '[google-search] running focused offline contract tests'
"$PYTHON" -I -S -B -c '
import runpy
import sys
path, site_packages = sys.argv[1:3]
sys.path[:0] = [path.rsplit("/", 1)[0], site_packages]
sys.argv = [path]
runpy.run_path(path, run_name="__main__")
' "$SCRIPT_DIR/test_core_contract.py" "$SITE_PACKAGES"

if [[ "$SMOKE" -eq 1 ]]; then
  printf '%s\n' '[google-search] running explicit online smoke test'
  /bin/bash -p "$SCRIPT_DIR/run.sh" web OpenClaw --num 1 --json --compact >/dev/null
fi
printf '%s\n' '[google-search] checks passed'
