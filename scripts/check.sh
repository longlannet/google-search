#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$BASE_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
CONFIG_FILE="$BASE_DIR/config/serper.env"
RUN_SMOKE="${RUN_SMOKE:-1}"

log() { printf '[google-search] %s\n' "$*"; }
fail() { printf '[google-search] ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "$PYTHON_BIN" ] || fail "python not found in local venv: $PYTHON_BIN"
[ -f "$CONFIG_FILE" ] || fail "runtime config not found: $CONFIG_FILE"
[ -f "$BASE_DIR/SKILL.md" ] || fail "missing SKILL.md"
[ -f "$BASE_DIR/README.md" ] || fail "missing README.md"
[ -f "$BASE_DIR/scripts/install.sh" ] || fail "missing scripts/install.sh"
[ -f "$BASE_DIR/scripts/check.sh" ] || fail "missing scripts/check.sh"
[ -f "$BASE_DIR/scripts/search.py" ] || fail "missing scripts/search.py"
[ -f "$BASE_DIR/scripts/selfcheck.py" ] || fail "missing scripts/selfcheck.py"

log "checking imports"
"$PYTHON_BIN" -c 'import requests' >/dev/null

if [ "$RUN_SMOKE" = "1" ]; then
  log "running selfcheck"
  (
    cd "$BASE_DIR"
    "$PYTHON_BIN" scripts/selfcheck.py >/tmp/google-search-check-selfcheck.txt
  ) || fail "selfcheck failed"
  log "selfcheck: OK"
fi

log "check complete"
