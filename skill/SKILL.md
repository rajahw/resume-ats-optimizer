---
name: resume-ats-optimizer
description: Tailor canon_resume.odt to a specific pasted job description - surface the gaps, discuss them, then make small verbatim-voice edits and verify the result still parses and fits one page. Use when the user pastes a job posting and wants their resume adjusted for it, mentions ATS, keyword match, or tailoring a resume to a role.
---

# Resume tailoring against a pasted job description

`SCRIPT=~/.claude/skills/resume-ats-optimizer/scripts/odt_text.py`

## Output naming

Outputs are `<PREFIX>_Resume.odt` and `<PREFIX>_ATS_Report.md`, where `PREFIX` is the
user's first and last name joined by an underscore (`Ada_Lovelace`). **No name is
hardcoded anywhere in this skill** - it comes from `.output_name` in the working
directory, or from asking (step 0). To build `PREFIX` from an answer: join first and
last with `_`, then drop anything that is not a letter, digit, hyphen, or underscore
(`Ada O'Neil-Smith` → `Ada_ONeil-Smith`). If the user declines to give a name, `PREFIX`
is empty and the outputs are plain `Resume.odt` and `ATS_Report.md`.

`.output_name` is gitignored - it holds a real name, and git history is permanent.

## Hard rules

1. **Never add a Professional Summary, Objective, Profile, or About section.** Canon has
   none and never will. Ignore generic ATS advice recommending one.
2. **Never invent experience - but never assume its absence either.** Added keywords
   trace either to something canon already says, or to experience the user confirmed
   during consultation. Those are the only two sources. A gap is never silently written
   in, and never silently written off: it goes to the user as a question first.
3. **`[Token]` placeholders are literal and immutable.** `[Name]`, `[City]`, `[State]`,
   `[Email]`, `[Phone]`, `[Website]`, `[University]`, `[UniCity]`, `[UniState]`,
   `[Company1]`, `[Company2]`. Never reword, expand, guess at, or fill them; never treat
   one as a keyword gap. They pass through untouched.
4. **Only what `extract` surfaces is editable** - bullets, Skills values, and header
   `.mid` (tech stack) fields. Contact block, section headings, employers, job titles,
   dates, degree, and GPA are never touched.
5. **Match canon voice exactly** (below).
6. **Never score the resume, and never overclaim about ATS.** No match score, coverage
   percentage, rating, grade, or before/after number - not in conversation, not in the
   report, not informally ("this takes you from about half to most of the
   requirements"). Never claim a change makes the resume pass or fail an ATS, and never
   cite a rejection-rate statistic. Say only what to add, remove, or reword, and why.
   Exact-terminology swaps are a recruiter-search retrieval effect; parse safety is a
   verified property. Nothing beyond that is knowable.
7. **Refuse to tailor a fundamentally misaligned resume** (below).
8. **Never tailor a document that is not a resume**, and never write one to replace it.
   `canon_resume.odt` is whatever the user put there; it is not guaranteed to be a
   resume at all (below).

## Voice rules

Canon's voice, derived from the document itself:

- **Zero em dashes.** Canon contains none. The most common LLM tell.
- Curly apostrophes only (`[Company1]’s`, never `[Company1]'s`).
- Bullets open with a past-tense verb: *Joined, Built, Collected, Optimized, Documented,
  Developed, Won, Recreated*.
- **Exactly one sentence per bullet.** Not telegraphic though - canon sentences run long
  and carry several clauses (`participating in daily stand-ups and deployments, using an
  agile workflow, and investigating/flagging software quality issues regularly`). Match
  that density; do not compress into punchy fragments.
- Serial comma (`input, rendering, and sound`).
- Results follow canon's pattern, as a clause list on the verb (`The suite reduced
  testing time by >90%, enabled …, and simplified …`), never a tacked-on `- resulting in
  X%` clause.
- Literal `>` for greater-than, not "over".
- `leveraged`/`leveraging` are fine - canon uses them. Banned: spearheaded, orchestrated,
  seamlessly, robust, cutting-edge, utilized, impactful, key stakeholder.

## Fundamental misalignment

**Default is to proceed.** Requirement lists are aspirational and a student always falls
short of something. Missing preferred skills is normal and is what tailoring is for.
Over-firing this rule talks the user out of applications they should make, which is worse
than a wasted application.

**Disqualifying** - binary facts no wording supplies: a credential not held and not
obtainable in time (graduate degree, licensure, required certification); legal
eligibility (clearance, citizenship, work authorization); a full-time role starting well
before May 2028 that is not an internship or co-op; a stated hard minimum of professional
experience far above one internship on a plainly non-entry-level posting; a different
profession with no transferable technical basis.

**Not disqualifying:** missing "nice to have" items; "3+ years" boilerplate on a
student/new-grad posting; an unfamiliar stack adjacent to something canon demonstrates;
anything consultation might fill in.

**Never conclude misalignment from the resume alone** - canon is condensed and redacted,
so you do not know whether the user holds a clearance or graduates early. Any suspected
disqualifier becomes a direct factual question. Only a confirmed *no* counts.

**If confirmed:** write **no** `.odt`. Write only the report, recommending against
applying, naming the unmeetable requirement and quoting the JD, and saying what would
change the answer. Warn that any existing `<PREFIX>_Resume.odt` is left over from a
previous application. **The user can override** - it is a recommendation, not a veto; if
they say proceed, tailor normally and note it in the report.

## Workflow

### Stage 1 - analyze and consult (produces no resume and no report)

0. **Model check.** State the active model. **Only the Sonnet family runs this skill.**
   On anything else - Haiku or an older small model below it, and Opus or Fable above
   it - **stop immediately** and tell the user to run `/model sonnet` and re-invoke.
   The larger models are not an acceptable substitute here: this runs dozens of times a
   week and Sonnet is the tier it is written and budgeted for.

   **Then confirm canon exists.** `canon_resume.odt` is deliberately not in the
   repository - it is the user's own document and stays local, so a fresh clone does
   not have one. If it is missing from the working directory, **stop and ask for it**:
   they save their resume as `canon_resume.odt` here and re-invoke. Never guess at
   another `.odt`, never substitute a differently named file, and never offer to write
   a resume to stand in for the missing one.

   **Then run `python3 $SCRIPT shape canon_resume.odt`.** It reports whether the
   document parses as a resume this tool can edit.
   - `VERDICT: resume` - continue, and say nothing about the check.
   - `VERDICT: unrecognized` - carry the finding into Stage 1. Do not abort here and
     do not decide what the document is: say plainly at step 5 that it did not parse,
     and lead step 6 with the document-type question.

   The verdict is a document-type check. It is never a quality judgment and never a
   score - it does not enter the report, and it is never described to the user as a
   rating of their resume.

   **Then resolve `PREFIX`.** If `.output_name` exists, read it and use it as-is - do
   not ask again, and do not mention it. If it does not exist, **ask in plain prose and
   stop**: what first and last name should the tailored files be filed under? Never guess
   it from the git config, the directory path, or a leftover output file, and never fill
   canon's `[Name]` token with the answer. If canon carries a real name on its contact
   line rather than a token, you may offer that as the suggested answer, but only as a
   suggestion to confirm. When they reply, derive `PREFIX` per **Output
   naming**, write that one line to `.output_name`, and continue - this is asked once per
   clone. If they decline to name the files, write nothing and use the empty prefix.
1. **Require the job description.** If none was pasted, ask and stop. Never fall back to
   generic ATS advice.
2. Save the JD to `jd.txt`, then run:
   `python3 $SCRIPT match canon_resume.odt jd.txt`
   It returns only near-miss and missing terms, and emits no score. **Early exit:** if it
   reports no actionable gaps, say so and stop - canon already covers this posting.
   Never take that exit when `shape` said `unrecognized`: a document that parsed to
   nothing can report the same thing as one that covers everything.
3. **Sanity-check the rows.** The script misses context a human catches (a requirement
   phrased as a responsibility, an industry-specific sense of a word). Add what it
   missed, drop false positives. **Also scan the JD for disqualifiers.**
4. Run `python3 $SCRIPT extract canon_resume.odt` to see editable units and their ids.
5. **Present the analysis** - near/missing terms and the proposed edits as before/after
   pairs, framed as what to add, remove, or reword and why. No score. Nothing on disk.
6. **Ask everything in ONE `AskUserQuestion` call**, multi-select grouped by category,
   never a series of yes/no rounds. Rank by leverage and ask only about what will change
   the edit set - the change budget means about five edits, so do not surface ten gaps.
   - **Unclaimed experience** (multiSelect): "Which of these do you actually have
     experience with, even if it's not on your resume?" Absence from the page is evidence
     about the page, not the candidate. Invite specifics via the free-text option.
   - **Removals** (multiSelect): each candidate bullet quoted in full. An unchecked
     bullet stays. Never drop one silently.
   - **Near-miss rewording** (multiSelect): approve swaps to the JD's exact phrasing.
   - **Suspected disqualifier** (single-select yes/no, only if one exists): kept separate
     because an unchecked box is indistinguishable from an oversight.
   - **Document type** (single-select, only if `shape` said `unrecognized`): **ask this
     first** - it decides whether the rest of the run means anything. Quote what `shape`
     actually found (the unit count, the headings it saw instead) and ask what the file
     is. Three answers: a resume in a layout this editor cannot parse; a resume in the
     expected layout after all; not a resume. Single-select, for the same reason as the
     disqualifier. Never state what you think the document is - `shape` recognizes one
     shape and cannot identify anything else, so a guess dressed as a finding is worse
     than the question.
7. **Stop and wait.**

### Not-a-resume gate

Checked before the misalignment gate: if the file is not a resume, there is nothing to
be misaligned with. Act on the step 6 answer.

- **Not a resume.** **Hard stop.** No `.odt` and **no report** - a tailoring report
  about a document that is not a resume has nothing to say, and writing one would file
  a job-application artifact for a run that never applied to anything. Say in one
  message that `canon_resume.odt` is not a resume and has to be replaced with one. Do
  not offer to write a resume, and do not tailor it anyway if asked to "just try".
- **A resume in a layout this editor cannot parse.** Also stop, no `.odt`, no report,
  but the message is different: the document is fine, the editor is the limitation. It
  edits units under the headings `EDUCATION`, `SKILLS`, `EXPERIENCE`, and `PROJECTS`,
  and it found the headings `shape` listed instead. Say what renaming would take, and
  that this is their call to make - **never rewrite their resume's structure to fit the
  tool.**
- **A resume in the expected layout after all.** `shape` is a heuristic and the user's
  answer wins, so continue. But if `shape` reported `0 units`, nothing is editable no
  matter what the document is: say so and stop rather than inventing an edit set
  against text the editor cannot see.

### Misalignment gate

If a disqualifier was confirmed and not overridden, stop here: no `.odt`, report only.
A gap that consultation *filled* is not a disqualifier - that is the system working.

### Stage 2 - apply

8. **Build the edit set** within budget: at most 3 bullet rewordings (each one sentence),
   at most 1 bullet added and 1 dropped, Skills lines and header `.mid` may gain
   confirmed terms, projects may be reordered to lead with the most relevant. Prefer
   swapping an existing term for the JD's exact phrasing over adding text - highest
   value, lowest risk, no length change.
9. `python3 $SCRIPT apply canon_resume.odt edits.json -o <PREFIX>_Resume.odt`
   Canon is never modified. Edit-set shape:
   ```json
   {"replace": {"exp1.b2": "...", "proj1.head.mid": "| TypeScript, React, Node.js"},
    "delete": ["proj3.b2"],
    "insert_after": {"exp1.b1": ["new bullet"]},
    "reorder_projects": ["Cattle Disease Detection", "Cryptography Demo", "Tetris Clone"]}
   ```
10. `python3 $SCRIPT check canon_resume.odt <PREFIX>_Resume.odt`
    Fails on voice violations, >1 page, bottom-margin overflow, or a parse regression.
    Revise and re-run until clean. Show the new/changed bullets in your message.
11. **Write `<PREFIX>_ATS_Report.md`**, reading
    `references/report-template.md` at this point and not before. Keep it terse - tables,
    not prose. Report tokens are the most expensive in the run.
12. **Offer canon write-back** if consultation surfaced something permanently true.
    Opt-in, asked once, never automatic.

Both outputs overwrite each run; git history is the per-job archive. Always start from
`canon_resume.odt`, never from a previous output, so tailoring never compounds.

## Cost

Runs dozens of times a week. The script does all mechanical work at zero token cost;
`match` returns only interesting rows. Keep the report terse, take the early exit when it
applies, and read the report template only at step 11.
