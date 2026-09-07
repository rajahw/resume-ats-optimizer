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

TOKEN = re.compile(r"\[[A-Za-z][A-Za-z0-9]*\]")
RESUME_MARKERS = ("EDUCATION", "EXPERIENCE", "PROJECTS")


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f.strip()]


def text_of(path):
    if path.endswith(".odt"):
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.read("content.xml").decode("utf-8", "ignore")
            return re.sub(r"<[^>]+>", " ", xml)
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
    # The identity map itself, staged past .gitignore with `git add -f`. It is
    # nothing but PII, so it never needs the resume-shape test below.
    maps = [p for p in staged_files()
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

    offenders = []
    for path in staged_files():
        if not path.endswith((".odt", ".pdf")):
            continue
        text = text_of(path)
        if not any(m in text for m in RESUME_MARKERS):
            continue  # not a resume
        if not TOKEN.search(text):
            offenders.append(path)

    if offenders:
        sys.stderr.write(
            "\nCOMMIT BLOCKED - resume appears to have real PII, not placeholders:\n\n")
        for p in offenders:
            sys.stderr.write("    %s\n" % p)
        sys.stderr.write(
            "\nNo [Token] placeholders were found in the file(s) above, which means\n"
            "the real name/email/phone/employers are probably in there. Git history\n"
            "is permanent - committing this cannot be undone by deleting it later.\n\n"
            "  - To keep a filled copy locally, rename it to *_filled.odt (gitignored)\n"
            "  - To unstage:  git restore --staged <file>\n"
            "  - If this is genuinely intended:  git commit --no-verify\n\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
