#!/usr/bin/env bash
# Set up a fresh clone. Re-running is safe - every step is idempotent.
#
# The skill itself needs no installation: it lives at
# .claude/skills/resume-ats-optimizer/ and Claude Code loads it for anyone
# working in this directory. All this script does is opt into the git hook and
# report what this machine can run.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$root/.claude/skills/resume-ats-optimizer/scripts/odt_text.py"

# A ZIP download has no git repository, and needs no commit guard: nothing can
# be committed. The guard keeping Claude out of pii.txt is wired through
# .claude/settings.json and works either way.
if git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
    bash "$root/hooks/install.sh"
else
    echo "hook:   not a git checkout (downloaded as a ZIP?) - commit guard not needed"
fi

# Probe each candidate rather than trusting PATH: on Windows `python3` is often
# the Microsoft Store stub, which exists and then fails when run.
py=""
for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c "" >/dev/null 2>&1; then
        py="$candidate"
        break
    fi
done

echo
if [ -z "$py" ]; then
    echo "python: NOT FOUND (tried python3, python, py)"
    echo "        Install Python 3. Nothing here runs without it, and the"
    echo "        identity guards block rather than fail open while it is missing."
else
    echo "python: $py"
    "$py" "$script" doctor || true
fi

# postings/ is gitignored, so a fresh download has no inbox to paste into.
if [ ! -f "$root/postings/inbox.txt" ]; then
    mkdir -p "$root/postings"
    : > "$root/postings/inbox.txt"
    echo
    echo "inbox:  created postings/inbox.txt - paste job postings there"
fi

echo
if [ -f "$root/canon_resume.odt" ]; then
    echo "canon:  canon_resume.odt is present"
else
    echo "canon:  not set up yet. Start Claude Code here, type /resume-ats-optimizer,"
    echo "        and give it your resume file (.docx or .odt) when it asks."
fi
