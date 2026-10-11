#!/usr/bin/env python3
"""Block Claude Code from reaching real identity values, by any route.

Two rules, both enforced against every tool call:

  A. Anything named pii* is untouchable. No read, no write, no glob that
     expands onto it, no exception. `fill` reads the map itself and never
     names it on the command line, so nothing legitimate needs an allowance.

  B. Filled resumes are write-only. Claude runs `fill` to produce
     <PREFIX>_Resume.odt (the tokenized copy is <PREFIX>_Resume_tokenized.odt) but must never read the result back, or the
     isolation in rule A is pointless.

Rule A tests path-like fields and Bash command strings, deliberately NOT file
contents: the README and SKILL.md have to be able to document pii.txt in prose
while every route that would actually open it stays denied.

Wired as a PreToolUse hook on all tools in .claude/settings.json, launched
through hooks/guard.sh - which locates a working Python across platforms and
exits 2 (blocking) rather than letting a missing interpreter fail open. A
payload this hook cannot parse is denied for the same reason.

WHAT THIS IS NOT
----------------
This is a guardrail against reaching the map by accident or by the obvious
route. It matches filenames in commands, so it cannot see through:

  - a directory-wide sweep that never names the file (`grep -r . .`)
  - shell indirection that assembles the name at runtime (`p=pii; cat $p.txt`)
  - a pre-existing symlink pointing at the map
  - a content search whose *pattern* is the secret (`Grep(pattern="<a real
    name>", path=".")`) - `pattern` is checked as a path, but for Grep it is
    content, and no path matcher can help there

Closing those needs an OS-level boundary (a different user, or the map outside
the working tree), not a longer regex. tests/test_guard.py pins these gaps so
they are not mistaken for coverage. Treat the guard as a strong hint about
intent, not as containment.
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
# The separator class must include every character a shell can put immediately
# before a filename. `<` was missing, so `cat<pii.txt` - no space - passed both
# this and the basename check.
PII_BASENAME = re.compile(r"^pii(\W|$)", re.I)
PII_FILE_REF = re.compile(r"""(?:^|[\s/'"=(,;<>&|`$])pii[\w-]*\.\w+""", re.I)

# Tool inputs that name a file or a search target. Content fields (content,
# new_string, old_string, prompt) are excluded on purpose - see module docstring.
PATH_FIELDS = ("file_path", "path", "notebook_path", "pattern", "glob")

# Shapes that are gitignored because they hold filled-in identity values.
PROTECTED_GLOBS = ("*_Resume.*", "Resume.*", "*_filled.*", "*-filled.*", "*_final.*", "*-final.*",
                   "*_submitted.*", "*-submitted.*",
                   "private", "private/*", "*/private/*")

# Commands that print file contents. `ls` and `stat` are absent deliberately -
# metadata is not content, and blocking them makes the agent blind for no gain.
READER_COMMANDS = {
    "cat", "bat", "head", "tail", "less", "more", "grep", "egrep", "fgrep",
    "rg", "ag", "ack", "awk", "sed", "strings", "xxd", "od", "hexdump",
    "base64", "cut", "sort", "uniq", "wc", "diff", "nl", "tac", "tr", "unzip",
    "cp", "mv", "install", "tee", "dd", "curl", "wget", "scp", "rsync",
    "jq", "yq", "tar", "zip", "gzip", "gunzip", "zcat", "bzcat", "xzcat",
    "git", "soffice", "libreoffice", "pandoc", "pdftotext", "pdfinfo",
    "vim", "vi", "nano", "emacs", "open", "xdg-open", "file", "md5sum",
    "sha1sum", "sha256sum", "cmp", "join", "paste", "split", "csplit", "expand",
}

WRAPPERS = ("env", "sudo", "command", "nohup", "time", "xargs", "exec", "watch",
            "timeout", "stdbuf", "script", "nice", "ionice", "doas", "setsid")


def segments(command):
    """Split a shell line into separately-executed command segments."""
    return re.split(r"&&|\|\||;|\||\n|\$\(|`", command)


def tokens_of(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `timeout`'s duration, `nice -n`'s level: a wrapper operand that is not a flag
# and is not the wrapped command either.
WRAPPER_OPERAND_RE = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def strip_wrappers(tokens):
    """Drop leading env assignments and wrappers, to reach the real command.

    A wrapper's own flags and operands go too: stopping at `timeout`'s duration
    left the scan looking at "5" instead of the `cat` behind it, which is how
    `timeout 5 cat <filled>` used to slip past the reader check.
    """
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if ENV_ASSIGN_RE.match(token):
            i += 1
            continue
        if token.rsplit("/", 1)[-1] in WRAPPERS:
            i += 1
            while i < len(tokens) and (tokens[i].startswith("-")
                                       or WRAPPER_OPERAND_RE.match(tokens[i])):
                i += 1
            continue
        break
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
        # normpath, not lstrip("./") - that strips *characters*, turning
        # ./.private/x into private/x and matching a directory nobody named.
        norm = os.path.normpath(candidate).replace(os.sep, "/")
        if norm.startswith("./"):
            norm = norm[2:]
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

MALFORMED_REASON = (
    "Blocked: this hook could not read the tool call well enough to tell whether "
    "it reaches the user's identity values, so it denied rather than guessed. "
    "This is a bug in the guard or the payload it was handed - report it instead "
    "of working around it."
)


def decide(payload):
    """The reason to deny this tool call, or None to allow it.

    Separated from main() so the matcher can be tested directly, without a
    subprocess and without naming a protected path on a command line.
    """
    if not isinstance(payload, dict):
        return MALFORMED_REASON

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    if tool_input is None or not isinstance(tool_input, dict):
        return MALFORMED_REASON

    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return MALFORMED_REASON
        # Rule A applies to the whole line: redirection, substitution, any verb.
        for seg in segments(command):
            for arg in tokens_of(seg):
                if hits_pii(arg):
                    return PII_REASON
        if PII_FILE_REF.search(command):
            return PII_REASON
        # Rule B is scoped to segments that actually print contents.
        for seg in segments(command):
            toks = strip_wrappers(tokens_of(seg))
            if not is_reader(toks):
                continue
            for arg in toks[1:]:
                if hits_protected(arg):
                    return FILLED_REASON
        return None

    for field in PATH_FIELDS:
        value = tool_input.get(field)
        if hits_pii(value):
            return PII_REASON
        if hits_protected(value):
            return FILLED_REASON
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return deny(MALFORMED_REASON)
    reason = decide(payload)
    return deny(reason) if reason else 0


if __name__ == "__main__":
    sys.exit(main())
