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

bash "$root/hooks/install.sh"

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

echo
if [ -f "$root/canon_resume.odt" ]; then
    echo "canon:  canon_resume.odt is present"
else
    echo "canon:  missing. Copy your resume in before the first run - it is"
    echo "        gitignored and never leaves this machine:"
    echo "            cp /path/to/your/resume.odt \"$root/canon_resume.odt\""
fi
