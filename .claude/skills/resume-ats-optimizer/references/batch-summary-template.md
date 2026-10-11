# Batch summary template

Write to `applications/<batch>_batch-summary.md`. Terse - tables, not prose. Never
restate the consultation at length; the user already read it. Each application's
`edits.json` holds the exact changes, so the summary names them rather than quoting
every before/after.

**No scoring anywhere.** No percentage, match score, rating, grade, `N/100`, or
before/after number. No claim that a change causes an ATS to pass or reject the resume.

---

```markdown
# Batch YYYY-MM-DD

Tailored from `canon_resume.odt`. N applications: N filled, N skipped, N need you.

## Ready to submit

| Application | Changes | Page |
|-------------|---------|------|
| [Role] at [Company] | 2 rewordings, Skills +Docker | 1 page, Npt to spare |
| [Role] at [Company] | none needed | 1 page, Npt to spare |

## Needs you

| Application | Stage | What failed |
|-------------|-------|-------------|
| [Role] at [Company] | applied | fill: real values overflow the page; widest token [Company1] +14 |

## Skipped

| Application | Why | What would change it |
|-------------|-----|----------------------|
| [Role] at [Company] | requires active clearance: "…" (quoted from the posting) | holding the clearance |
| [Role] at [Company] | duplicate of 2026-09-02 application, declined | - |

## Per application

### [Role] at [Company]

| Unit | Change | Posting term |
|------|--------|--------------|
| exp1.b3 | "CI/CD" → "continuous integration" | continuous integration |

Remaining gaps: genuinely absent requirements, after consultation. One line, or "none".

(repeat per filled or needs-you application; skipped ones are covered by their table row)

## Experience ledger changes

| Term | Answer |
|------|--------|
| Kubernetes | yes - deployed services at [Company1] |
| Go | no |

## Checks

Report only what `check` and `fill` actually measured. If page measurement was skipped
under `--no-page-check`, say so here and drop the Page column's claims above.

---
*The resume is already parse-safe: single column, no tables, standard headings,
text-based PDF. Most applications are read by a human, and these edits are aimed at that
reader. No prediction is made about any employer's applicant tracking system.*
```

An application overridden past a confirmed disqualifier goes under **Ready to submit**
with "(disqualifier overridden: <requirement>)" in its Changes cell.
