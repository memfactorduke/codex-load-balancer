#!/bin/sh
# Each suite gets a fresh interpreter and the same core sandbox helper contract.
set -u
cd "$(dirname "$0")/.." || exit 1
test_python=${PYTHON:-python3}
test_log=$(mktemp) || exit 1
trap 'rm -f "$test_log"' EXIT HUP INT TERM
test_failed=0
test_total=0
for test_suite in tests addons/*/tests; do
    [ -d "$test_suite" ] || continue
    printf '\n== %s ==\n' "$test_suite"
    "$test_python" -m unittest discover -s "$test_suite" >"$test_log" 2>&1 || test_failed=1
    cat "$test_log"
    test_count=$(awk '/^Ran [0-9]+ tests? in / {print $2}' "$test_log")
    test_total=$((test_total + ${test_count:-0}))
done
printf '\nCombined: %s tests; exit status %s\n' "$test_total" "$test_failed"
exit "$test_failed"
