# resume-ats-optimizer

A Claude Code skill that tailors a canonical résumé to a specific job description,
making small edits in the author's own voice and verifying the result still parses
cleanly and fits on one page.

## Layout

```
skill/                    the skill itself (symlinked into ~/.claude/skills/)
  SKILL.md                workflow, hard rules, voice rules
  scripts/odt_text.py     normalize / shape / extract / match / apply / check / fill
  references/             report template, read only at write time
hooks/                    pre-commit guard against committing real PII, plus the
                          PreToolUse hook that keeps Claude out of pii.txt
.claude/settings.json     wires that hook up - committed, so it travels with the clone

canon_resume.odt          your resume - local only, gitignored, not shipped here
pii.txt                   real values for the [Token]s - local only, gitignored,
                          read by `fill` and by nothing else
canon_resume_preview.pdf  LibreOffice render of canon, regenerable, gitignored
migration/                edit sets that built your canon, if you keep any - gitignored
```

**Canon is not in this repository.** It is your own document, so a fresh clone arrives
without one and the skill aborts on its first run and asks you to add it. Save your
résumé as `canon_resume.odt` in the repo root before invoking.

Tailored outputs (`<First>_<Last>_Resume.odt`, `<First>_<Last>_ATS_Report.md`) are
written to the repo root and overwritten each run. Git history is the per-job archive,
so commit between applications — though that only works from a tokenized canon, since
the pre-commit hook deliberately blocks a tailored `.odt` carrying real PII.

The name in those filenames is not baked into the skill. On its first run in a clone it
asks what first and last name to file outputs under, saves the answer to `.output_name`,
and never asks again — delete that file to change the name. It is gitignored, since it
holds a real name and git history is permanent.

## Setup on a new machine

```bash
ln -s "$PWD/skill" ~/.claude/skills/resume-ats-optimizer
bash hooks/install.sh
cp /path/to/your/resume.odt canon_resume.odt   # gitignored, never committed
```

Requires `python3`, `pdftotext`, `pdfinfo`, and Flatpak LibreOffice
(`org.libreoffice.LibreOffice`) for rendering.

## Use

```
/model sonnet          # the skill hard-stops on anything but Sonnet
/resume-ats-optimizer  # with the job description pasted
```

Pasting the posting is optional when `jd.txt` already holds one — the skill quotes back
the posting it found and asks whether to reuse it or take a new paste, so re-running
against the same job costs nothing. A new paste overwrites `jd.txt`.

It surfaces the gaps and asks about them **before** writing anything, because a keyword
missing from the résumé usually means it was never written down, not that the experience
is absent. Answer the questions, and it writes the tailored `.odt` plus a report.

The script alone is usable without Claude:

```bash
python3 skill/scripts/odt_text.py shape   canon_resume.odt
python3 skill/scripts/odt_text.py match   canon_resume.odt jd.txt
python3 skill/scripts/odt_text.py extract canon_resume.odt
python3 skill/scripts/odt_text.py apply   canon_resume.odt edits.json -o out.odt
python3 skill/scripts/odt_text.py check   canon_resume.odt out.odt
python3 skill/scripts/odt_text.py fill    out.odt -o out_filled.odt
```

## Placeholders

Optional, and only relevant if you want your tailored outputs to be committable. The
author's canon replaces every piece of PII with a `[Token]` — `[Name]` `[City]`
`[State]` `[Email]` `[Phone]` `[Website]` `[University]` `[UniCity]` `[UniState]`
`[Company1]` `[Company2]` — which the skill treats as literal and never fills, so the
tailored `.odt` comes out redacted too.

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
and straight apostrophes are converted to curly ones to match canon's typography. The
token names are not fixed — whatever the file declares is what gets replaced, so a canon
with different tokens just needs different keys. Every token in the document must have a
mapping or the fill fails.

Then:

```bash
python3 skill/scripts/odt_text.py fill Ada_Lovelace_Resume.odt -o Ada_Lovelace_Resume_filled.odt
```

The filled copy is what you submit. The tokenized `<PREFIX>_Resume.odt` stays untouched
and is the one that can be committed. `fill` refuses to write anywhere but a `*_filled.*`
path or `private/`, re-renders the result to confirm the real values did not push it onto
a second page (a real name is longer than `[Name]`), and reports any token left unmapped.
It prints token names and counts only — never a value — so its output is safe to paste
anywhere.

`pii.txt` and `*_filled.*` are both gitignored, and the pre-commit hook blocks either one
from being staged. Git history is permanent; a PII leak cannot be undone by a later
delete.

### Claude never sees any of it

`pii.txt` is off limits to Claude, and that is enforced rather than merely asked for.
`hooks/block-pii-access.py` runs as a `PreToolUse` hook on every tool call and denies:

- any read, write, glob, or shell command that names a `pii*` file — including wildcards
  that would sweep one up (`cat *.txt`) and indirection (`python3 -c "open('pii.txt')"`);
- reading back a `*_filled.*` or `private/**` file, so Claude can produce a filled résumé
  but never inspect one.

Running `fill` stays allowed because the command does not name the map — the script opens
it itself. Documentation can discuss `pii.txt` freely, since the rule tests file paths and
commands, not file contents.

The hook is wired in `.claude/settings.json`, which is committed on purpose: it is a
security control, so it should travel with the clone rather than live in someone's local
config. **Claude Code asks you to trust project hooks the first time it loads them** —
approve it once, or run `/hooks` to review what it does.

## Design notes

**Formatting is preserved by construction.** Edits are raw-string surgery on
`content.xml`; every other zip entry, including the 66KB `styles.xml` that holds all
real formatting, is copied byte-for-byte. `check` asserts that.

**Right-alignment is real.** Canon originally faked flush-right dates with hardcoded
tab and space runs, landing ~2.8pt short of the margin. They now use a genuine right tab
stop at the 7.5in text width, so header text can change length without breaking.

**One sentence per bullet**, verbatim from the original prose. Splitting a multi-clause
paragraph into bullets is mechanical and should stay that way: when the author's canon
was built this way, the only wording changes in the whole conversion were two pronoun
repairs (`This` → `The suite`, `It` → `The system`) where a split sentence had lost its
antecedent. If you convert a résumé into this shape, hold to that bar - `check` lints
the voice rules, but it cannot tell you that a rewrite drifted.

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
count is 1, content stays inside the bottom margin, and no em dashes or LLM boilerplate
crept into changed prose.
