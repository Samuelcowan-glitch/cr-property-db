#!/bin/sh
# The browser checks. Run from the repository root: sh tests/dom/run.sh
export PATH="$HOME/.local/node/bin:$PATH"
export NODE_PATH="$HOME/.local/cr-domtest/node_modules"
dir="$(dirname "$0")"
fail=0
for t in "$dir"/test_*.js; do
  node "$t" || fail=1
done
exit $fail
