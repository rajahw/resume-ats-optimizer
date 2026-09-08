#!/bin/sh
# Interpreter shim for the PreToolUse identity guard.
#
# Windows is why this exists. `python3` is often absent there, or resolves to
# the Microsoft Store stub that exits non-zero without running anything. A
# PreToolUse hook that exits non-zero for any reason *other than 2* fails OPEN -
# the tool call proceeds - so a missing interpreter would silently disable the
# one control keeping Claude out of the identity map. This exits 2 instead: a
# guard that could not run must never be mistaken for a guard that allowed.
#
# Each candidate is probed with `-c ""` rather than trusted for being on PATH,
# because the Store stub is on PATH and only fails once it is run.
set -u

dir="$(dirname "$0")"

# Buffer the hook payload so a failed candidate cannot consume it.
payload="$(cat)"

for py in python3 python py; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "" >/dev/null 2>&1; then
        printf '%s' "$payload" | "$py" "$dir/block-pii-access.py"
        exit $?
    fi
done

echo "guard: no working Python interpreter found (tried python3, python, py)." >&2
echo "The identity guard cannot run, so this tool call is blocked rather than" >&2
echo "allowed. Install Python 3 and make sure it runs from this shell." >&2
exit 2
