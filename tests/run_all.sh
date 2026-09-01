#!/bin/sh
# Every check, in one go. Run from the repository root:  sh tests/run_all.sh
#
# These live here rather than in a temporary directory because /tmp is swept
# after a few days and a fortnight of test coverage went with it once already.
pass=0; fail=""
for t in "$(dirname "$0")"/test_*.py; do
  if python3 "$t" 2>&1 | grep -qi "PASSED *$"; then
    pass=$((pass+1))
  else
    fail="$fail $(basename "$t")"
  fi
done
total=$(ls "$(dirname "$0")"/test_*.py | wc -l | tr -d ' ')
echo "passed: $pass of $total"
[ -n "$fail" ] && { echo "FAILED:$fail"; exit 1; }
exit 0
