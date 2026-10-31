#!/usr/bin/env python3
"""Search all of git history for real identity values before publishing.

The pre-commit hook guards each new commit. This covers what is already there:
every file version reachable from any branch or tag, and every commit message,
checked against the values in pii.txt. Run it before making a repository public
- once pushed, history cannot be taken back.

    python3 hooks/scan-history.py

Like the pre-commit hook it runs as you, not as Claude, so it may read the map.
It prints token names and where they were found, never a value. Author and
committer fields are not checked: your git name and email are on every commit by
design, and a later rewrite of them is a separate decision.

Exit status: 0 clean, 1 something found, 2 nothing to search for.
"""

import importlib.util
import io
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_precommit():
    spec = importlib.util.spec_from_file_location(
        "precommit", os.path.join(HERE, "pre-commit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pc = _load_precommit()


def git(*args, **kw):
    return subprocess.run(["git"] + list(args), capture_output=True, **kw)


def blob_text(data):
    """Searchable text of one file version: zip documents (.odt, .docx) by
    their XML, PDFs through pdftotext, anything else as UTF-8."""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = "".join(z.read(n).decode("utf-8", "ignore")
                              for n in z.namelist() if n.endswith(".xml"))
            # Both joins, as in pre-commit.py: a value split across spans
            # only reads whole once the tags are dropped without a space.
            return re.sub(r"<[^>]+>", " ", xml) + "\n" + re.sub(r"<[^>]+>", "", xml)
        except (zipfile.BadZipFile, KeyError):
            pass
    if data[:5] == b"%PDF-":
        try:
            return subprocess.run(["pdftotext", "-", "-"], input=data,
                                  capture_output=True).stdout.decode("utf-8", "ignore")
        except OSError:
            return ""
    return data.decode("utf-8", "ignore")


def history_blobs():
    """(sha, path) for every distinct file version in history."""
    out = git("rev-list", "--all", "--objects", text=True).stdout
    seen = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if path and sha not in seen:
            seen[sha] = path
    if not seen:
        return []
    kinds = git("cat-file", "--batch-check=%(objectname) %(objecttype)",
                input="\n".join(seen) + "\n", text=True).stdout
    blobs = {l.split()[0] for l in kinds.splitlines() if l.endswith(" blob")}
    return [(sha, seen[sha]) for sha in seen if sha in blobs]


def main():
    root = pc.repo_root()
    values = pc.map_values(root)
    if not values:
        sys.stderr.write("scan-history: no pii.txt values to search for (values "
                         "shorter than %d characters are skipped).\n"
                         % pc.MIN_VALUE_LEN)
        return 2

    found = []
    for sha, path in history_blobs():
        data = git("cat-file", "blob", sha).stdout
        low = blob_text(data).lower()
        hits = sorted(t for t, v in values.items() if v.lower() in low)
        if hits:
            found.append("%s  %s  (file version %s)" % (", ".join(hits), path, sha[:10]))

    log = git("log", "--all", "--format=%H%x00%B%x01", text=True).stdout
    for entry in log.split("\x01"):
        commit, _, message = entry.strip().partition("\x00")
        hits = sorted(t for t, v in values.items() if v.lower() in message.lower())
        if hits:
            found.append("%s  commit message  (commit %s)" % (", ".join(hits), commit[:10]))

    if not found:
        print("scan-history: clean - no pii.txt value appears in any file version "
              "or commit message.")
        return 0
    print("scan-history: real values found in history:\n")
    for line in found:
        print("    " + line)
    print("\nFind the commits behind a file version with:\n"
          "    git log --all --find-object=<file version>\n"
          "Rewriting history is the only fix, and only before the repository is public.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
