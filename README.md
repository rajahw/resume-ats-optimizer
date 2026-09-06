# resume-ats-optimizer

A Claude Code skill that tailors a canonical résumé to a specific job description,
making small edits in the author's own voice and verifying the result still parses
cleanly and fits on one page.

## Layout

```
skill/                    the skill itself (symlinked into ~/.claude/skills/)
  SKILL.md                workflow, hard rules, voice rules
  scripts/odt_text.py     normalize / shape / extract / match / apply / check
  references/             report template, read only at write time
hooks/                    pre-commit guard against committing real PII

canon_resume.odt          your resume - local only, gitignored, not shipped here
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
```

## Placeholders

Optional, and only relevant if you want your tailored outputs to be committable. The
author's canon replaces every piece of PII with a `[Token]` — `[Name]` `[City]`
`[State]` `[Email]` `[Phone]` `[Website]` `[University]` `[UniCity]` `[UniState]`
`[Company1]` `[Company2]` — which the skill treats as literal and never fills, so the
tailored `.odt` comes out redacted too. Fill them with Ctrl+F before submitting; each
is unique, so replace-all is safe.

A canon you drop in straight from your own résumé has real details in it instead. That
is fine and the skill handles it — canon itself is gitignored either way — but the
tailored output then carries your PII, so keep it out of git.

**Fill placeholders in a copy named `*_filled.odt`** — that pattern is gitignored, and
a pre-commit hook blocks any résumé that has lost its placeholders. Git history is
permanent; a PII leak cannot be undone by a later delete.

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
