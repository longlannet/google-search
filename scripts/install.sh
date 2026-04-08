#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$BASE_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"
CONFIG_FILE="$BASE_DIR/config/serper.env"
EXAMPLE_FILE="$BASE_DIR/config/serper.env.example"
REQ_FILE="$BASE_DIR/requirements.txt"

log() { printf '[google-search] %s\n' "$*"; }
fail() { printf '[google-search] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "python3 not found"

if [ ! -d "$VENV_DIR" ]; then
  log "creating local venv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  log "local venv already exists: $VENV_DIR"
fi

log "upgrading pip"
"$PIP_BIN" install --upgrade pip >/dev/null

log "installing requirements"
"$PIP_BIN" install -r "$REQ_FILE" >/dev/null

[ -f "$CONFIG_FILE" ] || fail "runtime config not found: $CONFIG_FILE (copy from $EXAMPLE_FILE)"

log "running selfcheck"
(
  cd "$BASE_DIR"
  "$PYTHON_BIN" scripts/selfcheck.py >/tmp/google-search-selfcheck.txt
) || fail "selfcheck failed"

log "install complete"
