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

1. **Never add a section canon does not already have**, and never remove one wholesale.
   Generic ATS advice pushes a Professional Summary, Objective, Profile, or About block;
   if canon carries none, it gets none. What sections a resume has is the user's call.
2. **Never invent experience - but never assume its absence either.** Added keywords
   trace either to something canon already says, or to experience the user confirmed
   during consultation. Those are the only two sources. A gap is never silently written
   in, and never silently written off: it goes to the user as a question first.
3. **`[Token]` placeholders are literal and immutable to you.** Canon stands in for its
   own PII with bracketed tokens - whichever ones that particular document uses. The
   README suggests a schema (`[Name]`, `[City]`, `[Email]`, `[Phone]`, `[University]`,
   `[Company1]`, ...) but it is a recommendation, not a requirement - a canon is free to
   use a different or additional token, and `fill` checks each one against `pii.txt` by
   what literally appears in canon, not against any fixed list. Never reword,
   expand, guess at, or fill them; never treat one as a keyword gap, and never conclude
   from a token that a fact is missing. They pass through every edit untouched, and
   canon and `<PREFIX>_Resume.odt` keep them forever. The `fill` command (step 12) is
   the only thing that ever replaces one, it does so mechanically from a file you
   cannot read, and it writes to a separate `_filled` copy.
4. **Only what `extract` surfaces is editable** - bullets, Skills values, and header
   `.mid` (tech stack) fields. Contact block, section headings, employers, job titles,
   dates, degree, and GPA are never touched.
5. **Mirror canon's own voice** (below).
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
9. **Never read the identity map, and never write PII yourself.** `pii.txt` holds the
   real name, email, phone, and employers. Do not open, cat, grep, glob, quote, echo,
   summarize, or reconstruct it, and do not read back any `_filled` output either. A
   PreToolUse hook enforces this and denies the call: if you are denied, that is the
   system working as designed - relay it and stop, never hunt for another route. Never
   type a real name, email, phone number, or employer into any file, including the
   report, `.output_name`, and commit messages. `fill` handles all of it and tells you
   only token names and counts.

## Voice rules

**This skill has no house style.** Canon is the style guide: whatever the user writes is
correct, and the goal is edits indistinguishable from the text around them - not better
writing. Derive the conventions from the units `extract` returns, every run, because the
next canon will not write like the last one. Read for:

- **Sentence count and density per bullet.** Some canons run one long multi-clause
  sentence per bullet, others run terse fragments. Match what is there; do not compress a
  dense bullet into a punchy one or pad a terse one.
- **Opening pattern.** Many bullets open with a past-tense verb, but not all do - a canon
  may continue a thought across bullets by picking up the subject instead (`The suite
  …`, `The system …`). Copy the pattern the neighboring bullets use, not a rule about
  resumes in general.
- **Punctuation and symbol conventions.** Apostrophe and quote style, serial comma or
  not, `>` versus a spelled-out "over", `&`/`+` versus "and". Whichever form canon uses
  consistently is the correct one for that document.
- **How results attach.** A clause list hung on the verb, a trailing metric, a separate
  sentence - follow canon rather than importing a preferred shape.
- **Vocabulary the user actually uses.** A word already in canon is in-voice by
  definition, however it reads to you.

Style opinions belong to the user. The skill only overrides voice where prose meets ATS
parsing, and that list is short:

- **Exact JD terminology beats a synonym** - recruiter search matches strings.
- **Plain characters only.** Ordinary letters, digits, and punctuation; no decorative
  glyphs, ligatures, or symbol bullets, which parse unevenly.
- **No new structure.** No added sections, headings, tables, columns, or graphics. This
  editor parses units under `EDUCATION`, `SKILLS`, `EXPERIENCE`, and `PROJECTS`.

`check` enforces the same idea mechanically on the bullets you changed, reading canon
first and judging edits against it rather than against a fixed style:

- **Punctuation forms follow canon.** Apostrophes and quotes must be whichever form
  canon's own prose settled on - it has no preference between straight and curly, only
  that one document uses one of them. A canon that uses neither leaves the choice open.
- **A character canon's prose never uses is suspect.** Known model-or-paste tells (em
  dash, ellipsis character, curly quotes in a straight-quote document, glyph bullets,
  non-breaking space) fail; any other unfamiliar non-ASCII character warns, on the ATS
  grounds above. A canon that already uses em dashes in its bullets keeps them.
- **Boilerplate phrases fail** (*spearheaded, orchestrated, seamlessly, robust,
  cutting-edge, utilized, impactful, key stakeholder, best-in-class, synerg-, delve,
  tapestry, testament to*) **unless canon uses the word itself**, in which case it is the
  user's vocabulary and passes.
- **Sentence count warns against canon's longest bullet**, not against one sentence.

So the lint tightens around whatever canon does. If it fires on something canon plainly
does too, that is a bug in `scripts/odt_text.py` worth reporting - never an argument for
rewriting the user's document to satisfy it.

## Fundamental misalignment

**Default is to proceed.** Requirement lists are aspirational and every candidate falls
short of something. Missing preferred skills is normal and is what tailoring is for.
Over-firing this rule talks the user out of applications they should make, which is worse
than a wasted application.

**Disqualifying** - binary facts no wording supplies: a credential not held and not
obtainable in time (graduate degree, licensure, required certification); legal
eligibility (clearance, citizenship, work authorization); a start date that lands well
before the availability canon states (an expected graduation date, for instance) on a
role that is not an internship or co-op; a stated hard minimum of professional experience
far above what canon shows, on a plainly non-entry-level posting; a different profession
with no transferable technical basis.

**Not disqualifying:** missing "nice to have" items; year-count boilerplate ("3+ years")
on a posting otherwise aimed at canon's level; an unfamiliar stack adjacent to something
canon demonstrates; anything consultation might fill in.

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
1. **Require the job description.** If one was pasted with the invocation, write it to
   `jd.txt` and continue. Otherwise check the working directory for `jd.txt` before
   asking for anything - re-running against the posting already sitting there is common,
   and making the user paste it a second time is the annoyance this check exists to
   prevent.
   - **`jd.txt` exists with content:** quote back the first line or two (whatever names
     the role and company) and ask whether to tailor against that posting or a new one.
     **Pause and wait.**
   - **`jd.txt` is missing, empty, or whitespace only:** ask for the posting. **Pause and
     wait.** Never fall back to generic ATS advice, and never tailor against a posting
     recalled from earlier in the conversation instead of one on disk.
   - **They paste a new posting:** write it to `jd.txt`, replacing what was there, and
     continue against the new one.

   **Pausing here is not aborting.** The reply resumes this same run at step 2 - the
   model check, the canon check, the `shape` verdict, and `PREFIX` all still hold from
   step 0. Nothing is re-run and the user does not re-invoke the skill. Say what is
   needed, stop talking, and pick up where you left off when it arrives.
2. Run, against the JD settled at step 1:
   `python3 $SCRIPT match canon_resume.odt jd.txt`
   It returns only near-miss and missing terms, and emits no score. **Early exit:** if it
   reports no actionable gaps, say so and stop - canon already covers this posting.
   Never take that exit when `shape` said `unrecognized`: a document that parsed to
   nothing can report the same thing as one that covers everything.
3. **Sanity-check the rows.** The script misses context a human catches (a requirement
   phrased as a responsibility, an industry-specific sense of a word). Add what it
   missed, drop false positives. **Also scan the JD for disqualifiers.**
4. Run `python3 $SCRIPT extract canon_resume.odt` to see editable units and their ids.
   Also run `python3 $SCRIPT budget canon_resume.odt` to measure canon's current page
   fill before any edits exist - this is separate from `check`'s post-edit margin line
   at step 10, and exists so loose whitespace can be raised during consultation instead
   of only reported after the fact.
5. **Present the analysis** - near/missing terms and the proposed edits as before/after
   pairs, framed as what to add, remove, or reword and why. No score. Nothing on disk.
   If step 4's `budget` run printed `NOTABLE`, mention the unused space plainly
   alongside the keyword gaps (e.g. "canon currently has about Npt of unused space at
   the bottom of the page"). A small body font-size nudge in their own editor is one way
   to use it - say so as a passing suggestion only, never as something this tool does or
   offers to automate.
6. **Ask everything in ONE `AskUserQuestion` call**, multi-select grouped by category,
   never a series of yes/no rounds. Rank by leverage and ask only about what will change
   the edit set - the change budget means about five edits, so do not surface ten gaps.
   - **Unclaimed experience** (multiSelect): "Which of these do you actually have
     experience with, even if it's not on your resume?" Absence from the page is evidence
     about the page, not the candidate. Invite specifics via the free-text option.
   - **Removals** (multiSelect): each candidate bullet quoted in full. An unchecked
     bullet stays. Never drop one silently.
   - **Near-miss rewording** (multiSelect): approve swaps to the JD's exact phrasing.
   - **Page space** (multiSelect, only if step 4's `budget` run printed `NOTABLE`):
     "There's a few lines of unused space at the bottom of the page - want to use it?"
     Options: add a bullet from confirmed/unclaimed experience, reword an existing
     bullet to be longer and more detailed, or leave it as-is. Nothing selected means
     leave the whitespace alone - same never-silently-decide pattern as Removals.
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

8. **Build the edit set** within budget: at most 3 bullet rewordings (each at canon's own
   bullet length), at most 1 bullet added and 1 dropped, Skills lines and header `.mid` may gain
   confirmed terms, projects may be reordered to lead with the most relevant. Prefer
   swapping an existing term for the JD's exact phrasing over adding text - highest
   value, lowest risk, no length change. If the user opted to fill page space at step 6,
   spend the existing bullet-add / reword-longer levers toward length instead of only
   toward keyword swaps - this doesn't raise the budget above, it just gives a reason to
   use it that way.
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
    not prose. Report tokens are the most expensive in the run. The report describes the
    tokenized resume and never contains a filled value.
12. **Fill the placeholders.** Run it directly - do not first test whether the map
    exists, because every route to that filename is denied to you and the command
    reports the answer itself:

    `python3 $SCRIPT fill <PREFIX>_Resume.odt -o <PREFIX>_Resume_filled.odt`

    It reads `pii.txt` from the working directory, substitutes every `[Token]`, and
    prints only token names, counts, and page geometry. Three outcomes:
    - **`OK - no placeholders remain`** - relay the page numbers and say the filled copy
      is the one to submit. `<PREFIX>_Resume.odt` keeps its tokens and is the copy that
      can be committed.
    - **`fill: pii.txt not found`** - say that the map does not exist yet, point at the
      schema in the README, and stop. Never create it, never offer to, and never ask for
      the values so you can write them.
    - **`FAIL:`** - a token has no mapping, or the real values pushed the document past
      one page. Relay the exact lines. An unmapped token means the map is incomplete and
      nothing is written: say which token, and stop. An overflow means the file was
      written but does not fit, so it must not be submitted as is; the fix is a bullet
      edit back at step 8, then re-run steps 9 to 12.

    Never open the filled file afterward. `fill`'s own output is the verification.
13. **Offer canon write-back** if consultation surfaced something permanently true.
    Opt-in, asked once, never automatic.

Both outputs overwrite each run; git history is the per-job archive. Always start from
`canon_resume.odt`, never from a previous output, so tailoring never compounds.

## Cost

Runs dozens of times a week. The script does all mechanical work at zero token cost;
`match` returns only interesting rows. Keep the report terse, take the early exit when it
applies, and read the report template only at step 11.
