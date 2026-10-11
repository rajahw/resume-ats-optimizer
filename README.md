# resume-ats-optimizer

A Claude Code skill that tailors a canonical résumé to a specific job description,
making small edits in the author's own voice and verifying the result still parses
cleanly and fits on one page.

## Layout

```
install.sh                one-shot setup for a fresh clone; idempotent
.claude/skills/resume-ats-optimizer/
  SKILL.md                workflow, hard rules, voice rules
  scripts/odt_text.py     normalize / shape / extract / match / apply / check /
                          fill / budget / doctor / split / name / stage / status
  references/             batch summary template, read only at write time
GLOSSARY.md               the domain terms: canon, posting, application, batch, ...
docs/adr/                 decisions that would otherwise look odd
hooks/                    pre-commit guard against committing real PII, plus the
                          PreToolUse hook that keeps Claude out of pii.txt.
                          Both are shell shims that locate a Python interpreter
                          and block rather than pass if they cannot find one.
.claude/settings.json     wires that hook up - committed, so it travels with the clone
.gitattributes            forces LF on the scripts, so a Windows clone does not
                          rewrite the shebangs to CRLF and break them

canon_resume.odt          your resume - local only, gitignored, not shipped here
pii.txt                   real values for the [Token]s - local only, gitignored,
                          read by `fill` and by nothing else
canon_resume_preview.pdf  LibreOffice render of canon, regenerable, gitignored
migration/                edit sets that built your canon, if you keep any - gitignored
postings/inbox.txt        where you paste postings - gitignored
applications/             one folder per application - gitignored
experience.md             the Experience ledger - gitignored
```

**Canon is not in this repository.** It is your own document, so a fresh clone arrives
without one and the skill aborts on its first run and asks you to add it. Save your
résumé as `canon_resume.odt` in the repo root before invoking.

Each application gets its own folder, `applications/<date>_<company>_<role>/`, holding
the posting, the edit set, the tokenized `<First>_<Last>_Resume_tokenized.odt`, and the
`<First>_<Last>_Resume.pdf` you submit. The folders are gitignored and are the
archive of past applications — nothing to commit between them, and nothing overwritten
(see [docs/adr/0001](docs/adr/0001-applications-as-gitignored-folders.md)). Back them up
yourself if you want them kept.

The name in those filenames is not baked into the skill. On its first run in a clone it
asks what first and last name to file outputs under, saves the answer to `.output_name`,
and never asks again — delete that file to change the name. It is gitignored, since it
holds a real name and git history is permanent.

## Setup on a new machine

```bash
bash install.sh
cp /path/to/your/resume.odt canon_resume.odt   # gitignored, never committed
```

**The skill needs no installation.** It lives at
`.claude/skills/resume-ats-optimizer/`, which Claude Code loads for anyone working in
this directory — cloning the repo is the whole install, on every platform. That also
scopes it correctly: canon, the inbox, and `applications/` are all resolved relative to this
directory, so the skill was only ever useful from inside it.

What `install.sh` does is the part a clone deliberately cannot do for you: point git at
this repo's hooks (`core.hooksPath`, no symlink), then report which Python and which
renderers this machine has. Git never installs hooks on clone — it will not run code
from a repository you have only downloaded — so this stays a one-time opt-in. Re-running
is safe. To re-check the renderers alone:

```bash
python3 .claude/skills/resume-ats-optimizer/scripts/odt_text.py doctor canon_resume.odt
```

Requires `python3`. Rendering — the step that verifies one page, the bottom
margin, and text recoverability — additionally needs LibreOffice and poppler's
`pdftotext`, `pdfinfo`, and `pdffonts` (Fedora: `dnf install libreoffice-writer
poppler-utils`; Debian/Ubuntu: `apt install libreoffice-writer poppler-utils`;
macOS: `brew install --cask libreoffice && brew install poppler`).

**Every font your résumé uses must be installed too.** LibreOffice silently
substitutes a missing font with one of different width, so a résumé that fits in
Garamond measures a page long in the fallback. `doctor canon_resume.odt` names any
font the render had to substitute, and `check`, `budget`, and `fill` refuse to
report a page measurement taken with the wrong font. Fonts that ship with Microsoft
Office (Garamond, Calibri, Aptos) are not on Linux by default: copy the font files
into `~/.local/share/fonts`, or set the résumé in a font this machine has.

LibreOffice is found wherever this machine keeps it: `soffice` or `libreoffice`
on `PATH` (any package install), then `/Applications/LibreOffice.app` on macOS
and `C:\Program Files\LibreOffice` on Windows, then a Flatpak
`org.libreoffice.LibreOffice`. Nothing is hardcoded to one platform.

Without those, the tool still runs — `match`, `extract`, `apply`, and the voice
lint are pure Python — but `check`, `budget`, and `fill` **fail** rather than pass
unmeasured. `--no-page-check` on `check` and `fill` accepts an unverified result
explicitly, and the output still says SKIPPED.

Page fit is measured on the résumé **as submitted**: when `pii.txt` exists, the
placeholders are filled in memory, rendered from a staging copy under
`~/.cache/odt_text`, and deleted, so short tokens cannot hide overflow that real
values cause. `budget` prints spare lines and, per bullet, how many characters fit
before its last line wraps; `check` fails an edit set that adds more lines than
canon had spare.

## Platform support

Linux, macOS, and Windows. Nothing in the skill or the scripts is written against one
platform, and there are no symlinks anywhere in the setup.

**Windows needs Git Bash**, which ships with Git for Windows, so cloning this repo
already implies having it. Claude Code runs hooks through Git Bash when it is present
and falls back to PowerShell when it is not — and the guards here are `sh` scripts, so
PowerShell cannot run them. If hooks do not fire, set
`CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe`; Claude Code only looks
for `bash` on `PATH`, and Git's default install puts `Git\cmd` there rather than
`Git\bin`.

**`python3` is not assumed.** On Windows that name is often missing, or is the Microsoft
Store stub that exists on `PATH` and then exits without running anything. Both guards
are shell shims that try `python3`, `python`, and `py` in turn and *probe each one* by
running it, so the stub is detected rather than trusted. `install.sh` reports which
interpreter it settled on.

**The guards fail closed.** A `PreToolUse` hook that exits non-zero for any reason other
than 2 fails *open* — the tool call proceeds — so a missing interpreter would otherwise
have silently disabled the one control keeping Claude out of `pii.txt`. When no working
interpreter is found, `hooks/guard.sh` exits 2 and blocks the call, and the pre-commit
shim blocks the commit. A guard that could not run is never mistaken for one that
allowed.

Poppler (`pdftotext`, `pdfinfo`, `pdffonts`) is the piece Windows makes least
convenient; without it you keep everything except the page and parse verification,
and `check` fails unless you pass `--no-page-check` to accept that.

## Use

```
/model sonnet          # the skill hard-stops on anything but Sonnet
/resume-ats-optimizer  # after filling postings/inbox.txt, or with one posting pasted
```

The skill works in **batches**. Paste every posting you are applying to into
`postings/inbox.txt`, with a line of `=====` between each, and invoke once. A single
posting can be pasted straight into chat instead; several go through the file, because
having Claude re-type each one costs tokens the file route does not.

1. **Analyze.** Each posting gets its own folder and a proposed edit set. No questions yet.
2. **Consult.** One numbered list for the whole batch: experience questions asked once
   across all postings, then a block per posting. Answer tersely (`E1 y, 2.R1 n`).
   Blanks resolve safely: rewordings accepted, bullets kept, unanswered experience
   treated as no. Disqualifiers and duplicate applications must be answered.
3. **Apply.** Every application is tailored, checked, and filled without pausing.
   Anything it cannot fix on its own lands on a "needs you" list instead of stopping
   the batch.
4. **Wrap up.** One `applications/<date>_batch-summary.md` and a short chat reply listing
   what is ready to submit.

It asks about gaps **before** writing anything, because a keyword missing from the
résumé usually means it was never written down, not that the experience is absent.
Your answers are kept in `experience.md`, the **Experience ledger**, so a question
answered once is never asked again — edit the file to change an answer. It stores
`[Token]`s, never real employer names.

Every application's stage is saved in its folder, so an interrupted batch picks up where
it stopped the next time you invoke the skill.

The script alone is usable without Claude:

```bash
SCRIPT=.claude/skills/resume-ats-optimizer/scripts/odt_text.py

python3 $SCRIPT doctor
python3 $SCRIPT shape   canon_resume.odt
python3 $SCRIPT split                          # postings/inbox.txt -> applications/
python3 $SCRIPT name    applications/<folder> "Acme" "Backend Engineer"
python3 $SCRIPT status                         # applications still in progress
python3 $SCRIPT match   canon_resume.odt applications/<folder>/posting.txt
python3 $SCRIPT extract canon_resume.odt
python3 $SCRIPT apply   canon_resume.odt edits.json -o out.odt
python3 $SCRIPT check   canon_resume.odt out.odt
python3 $SCRIPT fill    out_tokenized.odt -o out_Resume.pdf
```

## Placeholders

Optional, and only relevant if you want your tailored outputs to be committable. The
author's canon replaces every piece of PII with a `[Token]`, using this suggested
schema as a starting point — `[Name]` `[City]` `[State]` `[Email]` `[Phone]`
`[Website]` `[University]` `[UniCity]` `[UniState]` `[Company1]` `[Company2]` — which
the skill treats as literal and never fills, so the tailored `.odt` comes out redacted
too. It's a recommendation, not a requirement: nothing is hardcoded against it, so a
canon can use fewer tokens, extra ones, or one this list doesn't anticipate (a
`[Company1Verbose]` for a header that spells out an employer's full name, say) —
whatever bracketed tokens actually appear in the document are what `pii.txt` needs to
map.

A canon you drop in straight from your own résumé has real details in it instead. That
is fine and the skill handles it — canon itself is gitignored either way — but the
tailored output then carries your PII, so keep it out of git.

### Filling them

Write the real values into `pii.txt` in the repo root, one mapping per line:

```
[Name] = Ada Lovelace
[City] = Springfield
[State] = IL
[Email] = ada@example.com
[Phone] = 555-0100
[Website] = example.com/ada
[University] = Example State University
[UniCity] = Springfield
[UniState] = IL
[Company1] = Initech
[Company2] = Globex
```

Blank lines and `#` comments are skipped, the value is everything after the first `=`,
and an apostrophe in a value is normalized to whichever form canon itself uses, so a name
typed in a plain-text editor does not end up the odd one out. The
token names are not fixed — whatever the file declares is what gets replaced, so a canon
with different tokens just needs different keys. Every token in the document must have a
mapping or the fill fails. Tokens inside a hyperlink target count too, so `mailto:[Email]`
and `https://[Website]` reuse the values above, while a link that needs a whole URL of its
own (a project repo, say) gets its own token, e.g. `[CattleRepo] = https://github.com/...`.

A token is a bracketed run of letters, digits, underscores, and hyphens — `[Name]`,
`[Work_Phone]`, and `[Company-1]` all count. Anything else between brackets is ordinary
text and is left alone.

Then:

```bash
python3 $SCRIPT fill Ada_Lovelace_Resume_tokenized.odt -o Ada_Lovelace_Resume.pdf
```

The filled copy is what you submit. The tokenized `<PREFIX>_Resume_tokenized.odt` stays untouched
and is the one that can be committed. `fill` refuses to write anywhere but a `*_Resume.pdf`/`.odt`
(or legacy `*_filled.*`) path or `private/`, re-renders the result to confirm the real values did not push it onto
a second page (a real name is longer than `[Name]`), and reports any token left unmapped.
It prints token names and counts only — never a value — so its output is safe to paste
anywhere.

`pii.txt` and `*_Resume.*` are both gitignored, and the pre-commit hook blocks either one
from being staged. Git history is permanent; a PII leak cannot be undone by a later
delete.

### Claude never sees any of it

`pii.txt` is off limits to Claude, and that is enforced rather than merely asked for.
`hooks/block-pii-access.py` runs as a `PreToolUse` hook on every tool call — launched by
`hooks/guard.sh`, which finds a working Python or blocks the call outright — and denies:

- any read, write, glob, or shell command that names a `pii*` file — including wildcards
  that would sweep one up (`cat *.txt`) and indirection (`python3 -c "open('pii.txt')"`);
- reading back a `*_Resume.*`, `*_filled.*`, or `private/**` file, so Claude can produce a filled résumé
  but never inspect one.

Running `fill` stays allowed because the command does not name the map — the script opens
it itself. Documentation can discuss `pii.txt` freely, since the rule tests file paths and
commands, not file contents.

The hook is wired in `.claude/settings.json`, which is committed on purpose: it is a
security control, so it should travel with the clone rather than live in someone's local
config. **Claude Code asks you to trust project hooks the first time it loads them** —
approve it once, or run `/hooks` to review what it does.

The guard is a guardrail, not a sandbox. It matches filenames in commands, so it stops
the direct and the careless route but cannot see through a directory-wide sweep that
never names the file, shell indirection that builds the name at runtime, or a content
search whose *pattern* is the secret. Closing those needs an OS-level boundary rather
than a longer regex; `tests/test_guard.py` pins the known gaps so they are not mistaken
for coverage.

## Tests

```bash
python3 -m unittest discover tests
```

Standard library only — no pytest, matching the rest of the tool. Every fixture is
synthetic: the suite builds its own `.odt` documents and its own all-fake identity maps,
and never reads your canon, your `pii.txt`, or any filled output. That is deliberate, so
an agent forbidden from touching those files can still run the suite.

## Design notes

**Formatting is preserved by construction.** Edits are raw-string surgery on
`content.xml`; every other zip entry, including the 66KB `styles.xml` that holds all
real formatting, is copied byte-for-byte. `check` asserts that.

**Right-alignment is real.** Canon originally faked flush-right dates with hardcoded
tab and space runs, landing ~2.8pt short of the margin. They now use a genuine right tab
stop at the 7.5in text width, so header text can change length without breaking.

**Your canon is the style guide.** The tool has no house voice and does not try to
improve your writing: `check` reads canon's own prose and holds edits to *that* -
whichever apostrophe and quote forms it settled on, the characters it actually uses, the
length its bullets run. A word already in your résumé is in-voice by definition, even one
on the boilerplate list. The only opinions that outrank yours are the ones that affect
parsing: exact terminology from the posting, plain characters, and no invented structure.

**Bullets stay verbatim where they can.** Splitting a multi-clause paragraph into bullets
is mechanical and should stay that way: when the author's canon was built this way, the
only wording changes in the whole conversion were two pronoun repairs (`This` → `The
suite`, `It` → `The system`) where a split sentence had lost its antecedent. If you
convert a résumé into this shape, hold to that bar - `check` lints against canon, but it
cannot tell you that a rewrite drifted.

**`shape` asks whether a document is a résumé, never whether it is a good one.** Since
canon is now whatever file you dropped in, the skill checks before it starts: does the
document carry the four section headings the editor works on, and does anything under
them parse as an editable unit? It reports the signals and stops there. It cannot
identify what a document is, so an `unrecognized` verdict becomes a question during
consultation — a résumé with different headings and a cover letter both fail the check
and need completely different answers. Confirm it is not a résumé and the run aborts
with no output at all.

**No scoring.** The tool never emits a match score, coverage percentage, or rating.
Employer ATS platforms are proprietary and cannot be tested against, so any such number
would be invented. `match` returns categorized terms — near-miss and missing — and
nothing else. The widely repeated "75% of résumés are rejected by ATS" claim traces to a
2012 sales pitch by a vendor that folded in 2013; recruiters overwhelmingly report their
systems do not auto-reject.

What *is* verifiable is checked: `check` runs `pdftotext` — the same text-extraction step
a parser performs — and asserts every section is recoverable in reading order, the page
count is 1, content stays inside the bottom margin, and no punctuation, character, or
boilerplate phrase foreign to your own résumé crept into the bullets that changed. On a machine
without LibreOffice, poppler, or the résumé's own fonts it fails instead of passing,
so an `OK` never stands in for a measurement that did not happen.
