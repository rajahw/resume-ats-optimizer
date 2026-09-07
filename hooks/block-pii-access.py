#!/usr/bin/env python3
"""Block Claude Code from reaching real identity values, by any route.

Two rules, both enforced against every tool call:

  A. Anything named pii* is untouchable. No read, no write, no glob that
     expands onto it, no exception. `fill` reads the map itself and never
     names it on the command line, so nothing legitimate needs an allowance.

  B. Filled resumes are write-only. Claude runs `fill` to produce
     <PREFIX>_Resume_filled.odt but must never read the result back, or the
     isolation in rule A is pointless.

Rule A tests path-like fields and Bash command strings, deliberately NOT file
contents: the README and SKILL.md have to be able to document pii.txt in prose
while every route that would actually open it stays denied.

Wired as a PreToolUse hook on all tools in .claude/settings.json.
"""

import fnmatch
import glob
import json
import os
import re
import shlex
import sys

# A protected file is one *named* pii-something: pii.txt, pii.bak, pii-old.txt.
# Anchored on the filename rather than on the substring "pii" anywhere, so this
# guard's own source stays maintainable and a path like block-pii-access.py is
# not mistaken for the map. FILE_REF catches a filename embedded in a larger
# argument, e.g. python3 -c "open('pii.txt')" - the character before it must be
# a separator, quote, or whitespace, never a word character or a hyphen.
PII_BASENAME = re.compile(r"^pii(\W|$)", re.I)
PII_FILE_REF = re.compile(r"""(?:^|[\s/'"=(,;>])pii[\w-]*\.\w+""", re.I)

# Tool inputs that name a file or a search target. Content fields (content,
# new_string, old_string, prompt) are excluded on purpose - see module docstring.
PATH_FIELDS = ("file_path", "path", "notebook_path", "pattern", "glob")

# Shapes that are gitignored because they hold filled-in identity values.
PROTECTED_GLOBS = ("*_filled.*", "*-filled.*", "private/*", "*/private/*")

# Commands that print file contents. `ls` and `stat` are absent deliberately -
# metadata is not content, and blocking them makes the agent blind for no gain.
READER_COMMANDS = {
    "cat", "bat", "head", "tail", "less", "more", "grep", "egrep", "fgrep",
    "rg", "ag", "ack", "awk", "sed", "strings", "xxd", "od", "hexdump",
    "base64", "cut", "sort", "uniq", "wc", "diff", "nl", "tac", "tr", "unzip",
    "cp", "mv", "install", "tee", "dd", "curl", "wget", "scp", "rsync",
}

WRAPPERS = ("env", "sudo", "command", "nohup", "time", "xargs", "exec", "watch")


def segments(command):
    """Split a shell line into separately-executed command segments."""
    return re.split(r"&&|\|\||;|\||\n|\$\(|`", command)


def tokens_of(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def strip_wrappers(tokens):
    """Drop leading env assignments and wrapper commands."""
    i = 0
    while i < len(tokens) and (
        re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i])
        or tokens[i].rsplit("/", 1)[-1] in WRAPPERS
    ):
        i += 1
    return tokens[i:]


def is_reader(tokens):
    """Does this segment print file contents?

    A python/perl/ruby invocation counts as a reader unless it is running a
    script file - `python3 -c "open(...)"` reads, `python3 odt_text.py fill`
    does not.
    """
    if not tokens:
        return False
    name = tokens[0].rsplit("/", 1)[-1]
    if name in READER_COMMANDS:
        return True
    if re.match(r"^(python[\d.]*|perl|ruby|node|php)$", name):
        for arg in tokens[1:]:
            if arg.startswith("-"):
                continue
            return not arg.endswith((".py", ".pl", ".rb", ".js", ".mjs", ".php"))
        return True  # bare interpreter, or only flags: assume stdin/-c
    if name in ("sh", "bash", "zsh") and any(a == "-c" for a in tokens[1:]):
        return True
    return False


def expansions(arg):
    """The argument itself plus, if it is a glob, everything it matches."""
    out = [arg]
    if any(c in arg for c in "*?["):
        try:
            out += glob.glob(arg)
            out += glob.glob(os.path.join(".", arg))
        except Exception:
            pass
    return out


def hits_pii(value):
    """Rule A: does this string name, or expand onto, a pii* file?"""
    if not isinstance(value, str) or not value:
        return False
    for candidate in expansions(value):
        base = os.path.basename(candidate.rstrip("/"))
        if PII_BASENAME.match(base) or PII_FILE_REF.search(candidate):
            return True
    return False


def hits_protected(value):
    """Rule B: does this string name, or expand onto, a filled resume?"""
    if not isinstance(value, str) or not value:
        return False
    for candidate in expansions(value):
        norm = candidate.lstrip("./")
        base = os.path.basename(candidate.rstrip("/"))
        for pat in PROTECTED_GLOBS:
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat):
                return True
    return False


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }))
    return 0


PII_REASON = (
    "Blocked: pii.txt holds the user's real name, email, phone, and employers, "
    "and Claude must never read, write, or quote it under any circumstances. "
    "The `fill` subcommand reads it directly and prints only token names and "
    "counts - run that instead. Do not look for another route to this file."
)

FILLED_REASON = (
    "Blocked: filled resumes contain real identity values. Claude produces them "
    "via `odt_text.py fill` and never reads them back - the script's own output "
    "(page budget, leftover tokens) is the way to verify a fill succeeded."
)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail open on an unparseable payload, as the other hooks do

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool == "Bash":
        command = tool_input.get("command") or ""
        if not isinstance(command, str):
            return 0
        # Rule A applies to the whole line: redirection, substitution, any verb.
        for seg in segments(command):
            for arg in tokens_of(seg):
                if hits_pii(arg):
                    return deny(PII_REASON)
        if PII_FILE_REF.search(command):
            return deny(PII_REASON)
        # Rule B is scoped to segments that actually print contents.
        for seg in segments(command):
            toks = strip_wrappers(tokens_of(seg))
            if not is_reader(toks):
                continue
            for arg in toks[1:]:
                if hits_protected(arg):
                    return deny(FILLED_REASON)
        return 0

    for field in PATH_FIELDS:
        value = tool_input.get(field)
        if hits_pii(value):
            return deny(PII_REASON)
        if hits_protected(value):
            return deny(FILLED_REASON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
