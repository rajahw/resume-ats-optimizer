#!/usr/bin/env python3
"""Block committing a resume whose placeholders have been filled in.

Git history is permanent. A resume committed with a real name, email, phone,
and employer names cannot be un-published by a later delete, so this refuses the
commit instead. Install with: bash hooks/install.sh

Bypass deliberately with: git commit --no-verify
"""

import os
import re
import subprocess
import sys
import zipfile

# Must stay in step with TOKEN_RE in odt_text.py; tests/test_precommit.py
# asserts the two agree. A narrower pattern here would read a document whose
# tokens this misses as "already filled" and block a clean commit.
TOKEN = re.compile(r"\[[A-Za-z0-9][A-Za-z0-9_-]*\]")
RESUME_MARKERS = ("EDUCATION", "EXPERIENCE", "PROJECTS")

# A map value shorter than this is too ambiguous to search for: a state like
# "IL" appears inside ordinary words and would block every commit.
MIN_VALUE_LEN = 5


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
        capture_output=True, text=True).stdout
    return [f for f in out.split("\0") if f.strip()]


def repo_root():
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "."


def map_values(root):
    """Real values from the identity map, keyed by token.

    This hook runs as the user at commit time, not as Claude, so it is allowed
    to read the map - that is what makes a real check possible rather than a
    guess from placeholder counts. Values are compared and never printed.
    """
    path = os.path.join(root, "pii.txt")
    values = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if TOKEN.fullmatch(key) and len(value) >= MIN_VALUE_LEN:
                    values[key] = value
    except OSError:
        return {}
    return values


def text_of(path):
    if path.endswith(".odt"):
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.read("content.xml").decode("utf-8", "ignore")
            # Both joins: ODT splits a heading across spans, so dropping tags
            # with a space gives "EDUCAT ION" and without gives "EDUCATION".
            # Searching the pair means neither form hides a real resume.
            return re.sub(r"<[^>]+>", " ", xml) + "\n" + re.sub(r"<[^>]+>", "", xml)
        except Exception:
            return ""
    if path.endswith(".pdf"):
        try:
            return subprocess.run(["pdftotext", path, "-"],
                                  capture_output=True, text=True).stdout
        except Exception:
            return ""
    return ""


def main():
    staged = staged_files()

    # The identity map itself, staged past .gitignore with `git add -f`. It is
    # nothing but PII, so it never needs the resume-shape test below.
    maps = [p for p in staged
            if re.match(r"^pii(\W|$)", os.path.basename(p), re.I)]
    if maps:
        sys.stderr.write(
            "\nCOMMIT BLOCKED - the identity map is staged:\n\n")
        for p in maps:
            sys.stderr.write("    %s\n" % p)
        sys.stderr.write(
            "\nThis file is the real name/email/phone/employers in plain text.\n"
            "It is gitignored; staging it took an explicit -f.\n\n"
            "  - To unstage:  git restore --staged <file>\n\n")
        return 1

    values = map_values(repo_root())
    offenders = {}
    for path in staged:
        if not path.endswith((".odt", ".pdf")):
            continue
        text = text_of(path)
        upper = text.upper()
        if not any(m in upper for m in RESUME_MARKERS):
            continue  # not a resume
        # A real value in the document is proof, and it catches the partial
        # fill that a placeholder count misses: substituting the name while
        # leaving one token behind used to read as "still redacted".
        low = text.lower()
        leaked = sorted(t for t, v in values.items() if v.lower() in low)
        if leaked:
            offenders[path] = leaked
        elif not TOKEN.search(text):
            # No map to compare against, or nothing matched: fall back to the
            # original heuristic - a resume with no placeholders left at all.
            offenders[path] = []

    if offenders:
        sys.stderr.write(
            "\nCOMMIT BLOCKED - resume appears to have real PII, not placeholders:\n\n")
        for path, leaked in sorted(offenders.items()):
            if leaked:
                # Name the tokens, never their values - this goes to a terminal.
                sys.stderr.write("    %s  (filled-in: %s)\n"
                                 % (path, ", ".join(leaked)))
            else:
                sys.stderr.write("    %s  (no placeholders left)\n" % path)
        sys.stderr.write(
            "\nThe real name/email/phone/employers are probably in there. Git history\n"
            "is permanent - committing this cannot be undone by deleting it later.\n\n"
            "  - To keep a filled copy locally, rename it to *_filled.odt (gitignored)\n"
            "  - To unstage:  git restore --staged <file>\n"
            "  - If this is genuinely intended:  git commit --no-verify\n\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
