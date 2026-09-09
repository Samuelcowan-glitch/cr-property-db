#!/bin/sh
# Measure the CRM's layout in a real browser.
#
# The Python suites check what the pages say. This checks where things land:
# whether boxes in a column share their edges, whether the controls on a row
# are the same height, and whether anything is wider than what holds it — at
# desktop, laptop and phone widths. Those are questions only a browser can
# answer, so it starts the CRM on a spare port, drives headless Chrome at it,
# and stops the server afterwards.
#
#   sh tests/run_layout.sh
#
# Needs Google Chrome, and node with puppeteer-core in ~/.local/cr-domtest.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PORT=${LAYOUT_PORT:-8099}
NODE="$HOME/.local/node/bin/node"
LOG=$(mktemp)

[ -x "$NODE" ] || { echo "node not found at $NODE"; exit 2; }
[ -d "$HOME/.local/cr-domtest/node_modules/puppeteer-core" ] || {
  echo "puppeteer-core missing — run:"
  echo "  cd ~/.local/cr-domtest && $HOME/.local/node/bin/npm install puppeteer-core"
  exit 2
}

python3 "$ROOT/tests/layout_server.py" "$PORT" > "$LOG" 2>&1 &
SERVER=$!
# Stop the server whatever happens, including a failed audit.
trap 'kill $SERVER 2>/dev/null || true' EXIT INT TERM

i=0
while [ $i -lt 30 ]; do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null; then break; fi
  sleep 1
  i=$((i + 1))
done
if [ $i -ge 30 ]; then
  echo "the layout server did not start:"
  tail -20 "$LOG"
  exit 1
fi

LAYOUT_BASE="http://127.0.0.1:$PORT" "$NODE" "$ROOT/tests/layout_audit.js" "$@"
