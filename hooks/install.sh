#!/usr/bin/env bash
# Point git at this repo's hooks/ directory.
#
# No symlink into .git/hooks: creating one needs Developer Mode on Windows, and
# a worktree or core.hooksPath setting moves the target anyway. One config value
# behaves the same on all three platforms.
#
# Git deliberately never installs hooks on clone - it will not run code from a
# repository you have only downloaded - so this stays a one-time opt-in step.
set -euo pipefail

root="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

git -C "$root" config core.hooksPath hooks
chmod +x "$root/hooks/pre-commit" "$root/hooks/guard.sh" 2>/dev/null || true

echo "hook:   core.hooksPath = hooks (commit-time identity guard active)"
