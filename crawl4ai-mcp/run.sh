#!/usr/bin/env bash
# Entry point Claude launches (see .mcp.json). Installs on first run, then serves MCP over stdio.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -x "$DIR/.venv/bin/python" ] || "$DIR/setup.sh"
if [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ] && [ -d /opt/pw-browsers ]; then
  export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
fi
export CRAWL4_AI_BASE_DIRECTORY="${CRAWL4_AI_BASE_DIRECTORY:-$HOME}"
cd "$DIR"
exec "$DIR/.venv/bin/python" -I "$DIR/server.py"
