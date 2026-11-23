#!/usr/bin/env bash
# Install the Crawl4AI MCP server's Python environment into crawl4ai-mcp/.venv.
# Safe to re-run. Progress goes to stderr so it never corrupts MCP stdio.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
log() { echo "[crawl4ai-mcp setup] $*" >&2; }

if command -v uv >/dev/null 2>&1; then
  [ -x "$VENV/bin/python" ] || uv venv "$VENV" --python 3.12 >&2
  PIP=(env VIRTUAL_ENV="$VENV" uv pip install)
else
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
  PIP=("$VENV/bin/pip" install)
fi

log "installing crawl4ai + mcp"
"${PIP[@]}" -r "$DIR/requirements.txt" >&2

# Reuse a preinstalled Playwright browser when one exists (e.g. Claude Code cloud
# containers ship one at /opt/pw-browsers). The Python playwright version must
# match the browser build, so pin it to the globally installed Node playwright.
if [ -d /opt/pw-browsers ] && command -v npm >/dev/null 2>&1; then
  PW_VER="$(node -p "require('$(npm root -g)/playwright/package.json').version" 2>/dev/null || true)"
  if [ -n "$PW_VER" ]; then
    log "pinning playwright==$PW_VER to match /opt/pw-browsers"
    "${PIP[@]}" "playwright==$PW_VER" >&2
    exit 0
  fi
fi

log "downloading Chromium for Playwright"
"$VENV/bin/python" -m playwright install --with-deps chromium >&2 || "$VENV/bin/python" -m playwright install chromium >&2
log "done"
