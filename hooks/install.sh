#!/usr/bin/env bash
# Install the PII guard. Hooks live in .git/hooks, which git does not track,
# so this has to be run once per clone.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
ln -sf "../../hooks/pre-commit" "$root/.git/hooks/pre-commit"
chmod +x "$root/hooks/pre-commit"
echo "pre-commit PII guard installed"
