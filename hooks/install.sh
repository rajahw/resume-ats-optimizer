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

# Warn before taking over a hooksPath something else set (husky, lefthook).
existing="$(git -C "$root" config --get core.hooksPath || true)"
if [ -n "$existing" ] && [ "$existing" != "$root/hooks" ] && [ "$existing" != "hooks" ]; then
    echo "note:   replacing existing core.hooksPath = $existing" >&2
fi

# Absolute, not "hooks": git resolves a relative hooksPath from the working-tree
# root, so a linked worktree with no hooks/ of its own silently loses the guard.
git -C "$root" config core.hooksPath "$root/hooks"

if ! chmod +x "$root/hooks/pre-commit" "$root/hooks/guard.sh"; then
    echo "warn:   could not mark hooks executable - the commit guard will not run" >&2
    exit 1
fi

echo "hook:   core.hooksPath = $root/hooks (commit-time identity guard active)"
