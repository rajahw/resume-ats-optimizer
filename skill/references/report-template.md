# Report template

Write to `<PREFIX>_ATS_Report.md`, with the `PREFIX` already resolved in step 0. Terse -
tables, not prose. Never restate the Stage 1 analysis at length; the user already read
it.

**No scoring anywhere.** No percentage, match score, rating, grade, `N/100`, or
before/after number. No claim that a change causes an ATS to pass or reject the resume.

---

## Standard run

```markdown
# [Role] at [Company]

Tailored from `canon_resume.odt` on YYYY-MM-DD.

## Edits made

| # | Unit | Before | After | JD requirement |
|---|------|--------|-------|----------------|
| 1 | exp1.b3 | … | … | "…" (Requirements) |

## Terms addressed

| JD term | Was | Now |
|---------|-----|-----|
| continuous integration | resume said "CI/CD" | exact JD phrasing used |

## Newly surfaced in consultation

Experience confirmed by the user that was not previously on the resume, and the bullets
it produced. Keep this auditable - one line per claim.

| Confirmed | Where it landed |
|-----------|-----------------|

## Removed

| Bullet | Traded for |
|--------|------------|

## Remaining gaps

Requirements still unbacked after consultation. Genuinely absent, not merely unwritten.

- …

## Before submitting

Include this section **only if canon actually contains `[Token]` placeholders**, and
list only the tokens that are in it. A canon the user supplied from their own resume
usually carries real contact details already and needs no placeholder pass; listing
tokens that are not in the document is noise.

Replace every placeholder (Ctrl+F, each is unique):

`[Name]` `[City]` `[State]` `[Email]` `[Phone]` `[Website]` `[University]` `[UniCity]`
`[UniState]` `[Company1]` (2 occurrences) `[Company2]`

## Checks

- Parse: all sections recovered in reading order, contact line intact
- Length: one page, Npt of bottom margin remaining
- Voice: no em dashes, one sentence per bullet

---
*The resume is already parse-safe: single column, no tables, standard headings,
text-based PDF. Most applications are read by a human, and these edits are aimed at that
reader. No prediction is made about any employer's applicant tracking system.*
```

---

## Misalignment run

When the run stopped at the misalignment gate, the report is the only artifact. No
resume was written.

```markdown
# [Role] at [Company] - not recommended

No tailored resume was produced for this posting.

## Why

**Unmeetable requirement:** …

> "…" (quoted from the job description)

Confirmed during consultation: [what the user said].

No rewording of the resume can supply this, so a tailored document would misrepresent
fit rather than improve it.

## What would change this

- …

## Note

Any `<PREFIX>_Resume.odt` in this directory is left over from a previous application and
does not correspond to this posting.
```
