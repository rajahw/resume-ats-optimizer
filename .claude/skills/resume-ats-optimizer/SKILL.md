---
name: resume-ats-optimizer
description: Tailor canon_resume.odt to a batch of job postings (one or many) - surface the gaps across all of them, settle them in one consultation, then make small verbatim-voice edits per posting and verify each result still parses and fits one page. Use when the user pastes job postings or fills postings/inbox.txt and wants their resume adjusted, mentions ATS, keyword match, or tailoring a resume to a role.
---

# Resume tailoring against a batch of postings

Terms follow `GLOSSARY.md`: a **posting** is one employer's text, an **application** is
a posting plus everything tailored for it, a **batch** is the applications processed in
one sitting, and **consultation** is the question phase.

`SCRIPT=.claude/skills/resume-ats-optimizer/scripts/odt_text.py`

Paths are relative to the working directory, which is the repository root - canon, the
inbox, the ledger, and `applications/` all live there. Commands below say `python3`; if that
name is missing or broken on this machine (common on Windows, where it is often
absent or a Store stub), fall back to `python` or `py -3` and use the same one for
every command in the run.

## Files

All gitignored - postings are the employer's copyrighted text, and which employers the
user applies to is private.

- `postings/inbox.txt` - where the user pastes postings, a line of `=====` between each.
  An entry may be a posting's full text or just its URL. `split` empties it, except for
  any URL it could not read.
- `applications/<batch>_<company>_<role>/` - one folder per application:
  `posting.txt`, `state.json` (its stage, and `source` when fetched from a URL),
  `posting.full.txt` (the untrimmed posting - see Phase 0 step 6; never read it),
  `consultation.md` (proposal and answers),
  `edits.json`, `<PREFIX>_Resume_tokenized.odt`, `<PREFIX>_Resume.pdf` (the
  one to submit).
- `applications/<batch>_batch-summary.md` - the batch summary, one per batch.
- `experience.md` - the **Experience ledger** (below).

## Experience ledger

`experience.md` records experience the user confirmed or denied in past consultations,
one line per term, keyed by the posting's exact phrasing:

```markdown
# Experience ledger

- Kubernetes: yes (2026-10-11) - deployed two services to AKS at [Company1]
- Go: no (2026-10-11)
```

- **Read it in Phase 1, every batch.** A `yes` counts as confirmed experience under hard
  rule 2 and goes straight into proposed edits without being asked again. A `no` is
  never proposed and never asked again.
- **Write it after consultation**: add or update a line for every experience question the
  user answered. A question left blank is not recorded either way.
- **Tokens only.** Name an employer, school, or city the way canon does (`[Company1]`),
  never by its real value - and if the user's answer names one canon has no token for,
  drop the name and keep the substance. The user may edit the file; their edits win.

## Output naming

Résumé files are `<PREFIX>_Resume_tokenized.odt` and `<PREFIX>_Resume.pdf` inside each
application folder, where `PREFIX` is the
user's first and last name joined by an underscore (`Ada_Lovelace`). **No name is
hardcoded anywhere in this skill** - it comes from `.output_name` in the working
directory, or from asking (Phase 0, step 5). To build `PREFIX` from an answer: join first and
last with `_`, then drop anything that is not a letter, digit, hyphen, or underscore
(`Ada O'Neil-Smith` → `Ada_ONeil-Smith`). If the user declines to give a name, drop the
`<PREFIX>_` prefix entirely, so they are plain `Resume_tokenized.odt` and `Resume.pdf`
rather than `_Resume_tokenized.odt`. `<PREFIX>_Resume.pdf` is the filled copy holding
real values, so it is the one never read back.

`.output_name` is gitignored - it holds a real name, and git history is permanent.

## Hard rules

1. **Never add a section canon does not already have**, and never remove one wholesale.
   Generic ATS advice pushes a Professional Summary, Objective, Profile, or About block;
   if canon carries none, it gets none. What sections a resume has is the user's call.
2. **Never invent experience - but never assume its absence either.** Added keywords
   trace either to something canon already says, or to experience the user confirmed -
   in this batch's consultation, or in an earlier one and recorded as `yes` in the
   Experience ledger. Those are the only two sources. A gap is never silently written
   in, and never silently written off: it goes to the user as a question first.
3. **`[Token]` placeholders are literal and immutable to you.** Canon stands in for its
   own PII with bracketed tokens - whichever ones that particular document uses. The
   README suggests a schema (`[Name]`, `[City]`, `[Email]`, `[Phone]`, `[University]`,
   `[Company1]`, ...) but it is a recommendation, not a requirement - a canon is free to
   use a different or additional token, and `fill` checks each one against `pii.txt` by
   what literally appears in canon, not against any fixed list. Never reword,
   expand, guess at, or fill them; never treat one as a keyword gap, and never conclude
   from a token that a fact is missing. They pass through every edit untouched, and
   canon and `<PREFIX>_Resume_tokenized.odt` keep them forever. The `fill` command (Phase 3) is
   the only thing that ever replaces one, it does so mechanically from a file you
   cannot read, and it writes to a separate filled PDF (`<PREFIX>_Resume.pdf`).
4. **Only bullets, Skills values, and header `.mid` (tech stack) fields are editable.**
   Contact block, section headings, employers, project names, job titles, dates, degree,
   and GPA are never touched - `apply` refuses a header `.left` or `.right` edit unless
   it is given `--identity`, which only the one-time tokenization uses. Note that
   `extract` surfaces `left` and `right` for context; being listed there does not make a
   field writable.
5. **Mirror canon's own voice** (below).
6. **Never score the resume, and never overclaim about ATS.** No match score, coverage
   percentage, rating, grade, or before/after number - not in conversation, not in the
   batch summary, not informally ("this takes you from about half to most of the
   requirements"). Never claim a change makes the resume pass or fail an ATS, and never
   cite a rejection-rate statistic. Say only what to add, remove, or reword, and why.
   Exact-terminology swaps are a recruiter-search retrieval effect; parse safety is a
   verified property - but only on a run where `check` actually measured it (Phase 3).
   Nothing beyond that is knowable.
7. **Refuse to tailor a fundamentally misaligned resume** (below).
8. **Never tailor a document that is not a resume**, and never write one to replace it.
   `canon_resume.odt` is whatever the user put there; it is not guaranteed to be a
   resume at all (below).
9. **Never read the identity map, and never write PII yourself.** `pii.txt` holds the
   real name, email, phone, and employers. Do not open, cat, grep, glob, quote, echo,
   summarize, or reconstruct it, and do not read back any filled `<PREFIX>_Resume.pdf` either. A
   PreToolUse hook enforces this and denies the call: if you are denied, that is the
   system working as designed - relay it and stop, never hunt for another route. Never
   type a real name, email, phone number, or employer into any file, including the
   batch summary, `consultation.md`, the Experience ledger, `.output_name`, and commit
   messages. A posting naming its own employer is the posting's text, not the user's
   PII, and folder names use it freely. `fill` handles all of it and tells you
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

- **Exact posting terminology beats a synonym** - recruiter search matches strings.
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

**If confirmed:** write **no** `.odt` for that application and mark it
`stage <folder> skipped --note "disqualified: <requirement>"`. Its section in the batch
summary recommends against applying, names the unmeetable requirement quoting the
posting, and says what would change the answer. The rest of the batch carries on. **The
user can override** - it is a recommendation, not a veto; if they say proceed, tailor
normally and note it in the summary.

## Workflow

A batch runs in four phases. Every application's stage is on disk (`state.json`), so an
interrupted batch - a closed session, a compacted context - resumes by re-invoking the
skill: Phase 0 finds the open applications and each one picks up at its stage.

| Stage | Meaning |
|-------|---------|
| `new` | split from the inbox, not yet read |
| `analyzed` | named, matched, proposal in `consultation.md` |
| `consulted` | answers recorded in `consultation.md` |
| `applied` | `check` passed on `<PREFIX>_Resume_tokenized.odt` |
| `filled` | `fill` passed; done |
| `skipped` | no résumé by decision (disqualified, duplicate declined); done |
| `needs-you` | Phase 3 hit something it could not fix unattended |

Set a stage with `python3 $SCRIPT stage <folder> <stage> [--note "..."]`.

### Phase 0 - setup (once per batch)

1. **Model check.** State the active model. **Only the Sonnet family runs this skill.**
   On anything else - Haiku or an older small model below it, and Opus or Fable above
   it - **stop immediately** and tell the user to run `/model sonnet` and re-invoke.
   The larger models are not an acceptable substitute here: this runs dozens of times a
   week and Sonnet is the tier it is written and budgeted for.

2. **Confirm canon exists.** `canon_resume.odt` is deliberately not in the repository -
   it is the user's own document and stays local, so a fresh clone does not have one.
   If it is missing from the working directory, **stop and ask for it**: they save their
   resume as `canon_resume.odt` here and re-invoke. Never guess at another `.odt`, never
   substitute a differently named file, and never offer to write a resume to stand in
   for the missing one.

3. **Run `python3 $SCRIPT shape canon_resume.odt`.** It reports whether the document
   parses as a resume this tool can edit.
   - `VERDICT: resume` - continue, and say nothing about the check.
   - `VERDICT: unrecognized` - **stop and ask the document-type question now**, before
     touching any posting: analysis against a document that did not parse means
     nothing. Quote what `shape` actually found (the unit count, the headings it saw
     instead) and ask what the file is, single-select: a resume in a layout this editor
     cannot parse; a resume in the expected layout after all; not a resume. Never state
     what you think the document is - `shape` recognizes one shape and cannot identify
     anything else. Act on the answer per the **Not-a-resume gate**.

   The verdict is a document-type check. It is never a quality judgment and never a
   score - it does not enter the summary, and it is never described to the user as a
   rating of their resume.

4. **Run `python3 $SCRIPT doctor canon_resume.odt`.** It confirms this machine can
   measure page fit: LibreOffice, poppler, and every font canon's text uses. A missing
   font is as bad as a missing renderer - LibreOffice silently swaps in a fallback with
   different metrics, and a resume that fits in its own font measures a page long in
   the substitute.
   - `OK` - continue, and say nothing about the check.
   - `Incomplete` - say so now, naming exactly what the output says is missing (a tool,
     or a font such as Garamond). `check`, `budget`, and `fill` will **fail** rather
     than pass unmeasured, so ask whether to proceed unverified. Only on an explicit yes
     does this batch pass `--no-page-check` to those commands - never add the flag on
     your own judgment, and never call a result that used it one page.

5. **Resolve `PREFIX`.** If `.output_name` exists, read it and use it as-is - do not ask
   again, and do not mention it. If it does not exist, **ask in plain prose and stop**:
   what first and last name should the tailored files be filed under? Never guess it
   from the git config, the directory path, or a leftover output file, and never fill
   canon's `[Name]` token with the answer. If canon carries a real name on its contact
   line rather than a token, you may offer that as the suggested answer, but only as a
   suggestion to confirm. When they reply, derive `PREFIX` per **Output naming**, write
   that one line to `.output_name`, and continue - this is asked once per clone. If they
   decline to name the files, write nothing and use the empty prefix.

6. **Gather the batch.** In this order:
   - **Open applications:** `python3 $SCRIPT status`. Anything listed is an unfinished
     earlier batch; it joins this one at its recorded stage. Mention it in one line.
   - **Inbox:** if `postings/inbox.txt` has content, run `python3 $SCRIPT split`. It
     creates one `applications/<today>_posting-NN/` folder per posting, at stage `new`,
     and empties the inbox. An entry that is only a URL is fetched by `split` itself,
     which writes the posting straight to disk and never prints it.
   - **Trimmed tails:** `split` also cuts each posting's closing boilerplate
     (equal-opportunity, accommodation, benefits, company pitch) to save reading it, and
     prints `(trimmed N words of boilerplate)` beside the folder. Eligibility lines from
     the cut tail (citizenship, clearance, export control, sponsorship, work
     authorization) are kept under `[Kept from trimmed tail]` for the disqualifier scan.
     The untrimmed text is in `posting.full.txt`; open it only if `posting.txt` looks
     cut off mid-requirements, and if so, say so - that is a trimming bug.
   - **A URL `split` could not read:** each prints as `FETCH FAILED: <url> - <reason>`
     and stays in the inbox. Before Phase 1, stop and prompt, one line per failure:
     "Couldn't read `<url>` (`<reason>`). Paste the posting's text into
     `postings/inbox.txt` in place of that URL (it's still there), save, and reply."
     On the reply, run `split` again - only what is left in the inbox is split, so the
     pasted posting joins this batch. If the user says to skip one, delete its URL from
     the inbox and carry on. Never fetch a failed URL yourself (WebFetch or otherwise):
     that routes the posting through output tokens and may not keep its wording verbatim.
   - **A posting pasted in chat:** a single one is fine - write it to
     `postings/inbox.txt` and `split`. For two or more, ask the user to paste them into
     the inbox file instead, separated by `=====` lines, and stop: re-typing every
     posting costs output tokens the file route does not. **Links pasted in chat** are
     short, so any number go into the inbox, one per entry, and `split` fetches them.
   - **Nothing anywhere:** ask for postings - the inbox file for several, chat for one -
     and stop. Never fall back to generic ATS advice.

   **Pausing is not aborting.** The reply resumes this same run - the checks above still
   hold. Nothing is re-run and the user does not re-invoke the skill.

7. **Measure canon once.** `python3 $SCRIPT extract canon_resume.odt` for the editable
   units and their ids, and `python3 $SCRIPT budget canon_resume.odt` for the length
   budget. Every application starts from canon, so these hold for the whole batch.
   `budget` measures the document **as submitted** - placeholders filled in memory from
   the map, printing only geometry - because short tokens hide overflow that real values
   cause. It prints spare lines above the safe buffer, and for every bullet its line
   count and `+N`: characters that still fit on its last line before it wraps. If it
   prints `FAIL`, the measurement could not be trusted; relay why and treat the batch as
   unverified per step 4.

8. **Read `experience.md`** if it exists.

### Not-a-resume gate

Acted on only when step 3 asked the document-type question.

- **Not a resume.** **Hard stop** for the whole batch. No `.odt` and no summary - there
  is nothing to tailor. Say in one message that `canon_resume.odt` is not a resume and
  has to be replaced with one. Do not offer to write a resume, and do not tailor it
  anyway if asked to "just try". Leave the inbox and any split folders as they are.
- **A resume in a layout this editor cannot parse.** Also stop, same way, but the
  message is different: the document is fine, the editor is the limitation. It edits
  units under the headings `EDUCATION`, `SKILLS`, `EXPERIENCE`, and `PROJECTS`, and it
  found the headings `shape` listed instead. Say what renaming would take, and that this
  is their call to make - **never rewrite their resume's structure to fit the tool.**
- **A resume in the expected layout after all.** `shape` is a heuristic and the user's
  answer wins, so continue. But if `shape` reported `0 units`, nothing is editable no
  matter what the document is: say so and stop rather than inventing an edit set
  against text the editor cannot see.

### Phase 1 - analyze (no questions, nothing tailored)

For each application at stage `new`:

1. **Read `posting.txt`** and name the folder:
   `python3 $SCRIPT name <folder> "<Company>" "<Role>"`. It prints the new path - use it
   from here on. A `DUPLICATE:` line means an earlier application to the same company
   and role exists; that becomes a consultation question.
2. **Match:** `python3 $SCRIPT match canon_resume.odt <folder>/posting.txt`. It returns
   only near-miss and missing terms, and emits no score.
3. **Sanity-check the rows.** The script misses context a human catches (a requirement
   phrased as a responsibility, an industry-specific sense of a word). Add what it
   missed, drop false positives. Then apply the ledger: a `yes` term is usable as
   confirmed, a `no` term is dropped. **Scan the posting for disqualifiers.**
4. **Draft the proposal** within the edit budget (Phase 3), sized against step 7's
   `budget` numbers: before/after pairs for rewordings, candidate removals quoted in
   full, candidate additions from canon or `yes` ledger entries, and the experience
   questions this posting raises. Rank by leverage - the budget means about five edits,
   so do not carry ten gaps.
5. **Write it to `<folder>/consultation.md`** and `stage <folder> analyzed`. The file is
   what makes the batch resumable; keep it terse - bullets, the unit ids, the exact
   before/after text.

If a posting has no actionable gaps and no suspected disqualifier, say so in its
`consultation.md` ("no changes needed") - it still gets a résumé in Phase 3.

### Phase 2 - consult (one message, then wait)

**Ask everything in ONE plain-text message**, not `AskUserQuestion` - its four-question
cap cannot hold a batch. Number every item so the user can answer tersely
(`E1 y, E3 y - ran it in CI at [Company1]; 2.R1 n; D1 n`). Open with how blanks resolve:

- **Rewording** to the posting's exact phrasing: blank = **accepted**.
- **Removal** of a bullet: blank = **kept**. Never drop one silently.
- **Experience**: blank = **no** for this batch, and not written to the ledger.
- **Page space**: blank = leave the whitespace alone.
- **Disqualifiers and duplicates**: must be answered. Phase 3 does not start until they
  are - if some are left blank, ask again for just those.

Order of the message:

1. **Disqualifiers** (`D1`, `D2` ...), one yes/no each, naming the posting and quoting
   the requirement. Never conclude misalignment from the resume alone.
2. **Duplicates** (`U1` ...): "you applied to <role> at <company> on <date> - apply
   again?"
3. **Experience** (`E1` ...), **asked once for the whole batch** - each line names the
   term and which postings want it. Absence from the page is evidence about the page,
   not the candidate; invite specifics.
4. **One block per posting** (`1.`, `2.` ...), headed by company and role: its
   rewordings (`1.W1` ...) as before/after, removals (`1.R1` ...) quoted in full, and a
   page-space item (`1.P`) only if step 7's `budget` printed `NOTABLE` - options: add a
   bullet from confirmed experience, lengthen an existing bullet, or leave it. A
   posting with no changes gets one line saying so.

If `budget` printed `NOTABLE`, mention the unused space once, near the top (e.g. "canon
has about Npt of unused space at the bottom of the page"). A small body font-size nudge
in their own editor is one way to use it - a passing suggestion only, never something
this tool does.

**Stop and wait.** When the answers arrive:

- Update `experience.md` with every answered experience question.
- Append each posting's resolved decisions to its `consultation.md`.
- Disqualified (and not overridden) or duplicate declined:
  `stage <folder> skipped --note "<why>"`. Everything else: `stage <folder> consulted`.

A gap that consultation *filled* is not a disqualifier - that is the system working.

### Phase 3 - apply (unattended)

For each application at stage `consulted`, **without pausing**. Work from
`consultation.md`, not from memory.

**No changes needed:** copy `canon_resume.odt` to `<folder>/<PREFIX>_Resume_tokenized.odt` and go
straight to fill.

**Otherwise, build the edit set** within budget: at most 3 bullet rewordings (each at
canon's own bullet length), at most 1 bullet added and 1 dropped, Skills lines and
header `.mid` may gain confirmed terms, projects may be reordered to lead with the most
relevant. Prefer swapping an existing term for the posting's exact phrasing over adding
text - highest value, lowest risk, no length change. If the user opted to fill page
space, spend the bullet-add / reword-longer levers toward length - this doesn't raise
the budget, it just gives a reason to use it that way.

**Line budget.** Size every edit against Phase 0's `budget` output, not by eye. A
reworded bullet may grow by at most its own `+N` characters, minus a few for safety,
unless canon has spare lines to spend; an added bullet costs its full line count. The
total lines added may not exceed canon's spare lines - `check` fails the edit set if
they do. When a wanted phrase would cost a line, trim elsewhere in the same bullet
rather than letting it wrap.

1. Write `<folder>/edits.json`, then
   `python3 $SCRIPT apply canon_resume.odt <folder>/edits.json -o <folder>/<PREFIX>_Resume_tokenized.odt`.
   Canon is never modified. Edit-set shape:
   ```json
   {"replace": {"exp1.b2": "...", "proj1.head.mid": "| TypeScript, React, Node.js"},
    "delete": ["proj3.b2"],
    "insert_after": {"exp1.b1": ["new bullet"]},
    "reorder_projects": ["Cattle Disease Detection", "Cryptography Demo", "Tetris Clone"]}
   ```
2. `python3 $SCRIPT check canon_resume.odt <folder>/<PREFIX>_Resume_tokenized.odt`. Fails on voice
   violations, >1 page, bottom-margin overflow, edits that add more lines than canon has
   spare, or a parse regression - all measured on the document as submitted. It also
   prints each changed bullet's line count before and after, and warns when one ends
   within a few characters of wrapping. On failure, revise the edit set and re-run.
   **It fails, rather than skips, when page fit cannot be measured.** Add
   `--no-page-check` only under the Phase 0 agreement; an `OK` under that flag means the
   voice lint passed and nothing else did. On a pass, `stage <folder> applied`.
3. **Fill.** Run it directly - do not first test whether the map exists, because every
   route to that filename is denied to you and the command reports the answer itself:

   `python3 $SCRIPT fill <folder>/<PREFIX>_Resume_tokenized.odt -o <folder>/<PREFIX>_Resume.pdf`

   It reads `pii.txt`, substitutes every `[Token]`, and prints only token names, counts,
   how many characters wider each token became, and page geometry. Pass
   `--no-page-check` only under the Phase 0 agreement.
   - **`OK - no placeholders remain`** - `stage <folder> filled`.
   - **`fill: pii.txt not found`** - the map does not exist, so no application in the
     batch can be filled. Stop filling, leave each at `applied`, and say so in the
     summary, pointing at the schema in the README. Never create the map, never offer
     to, and never ask for the values.
   - **`FAIL:`** - an unmapped token stops filling for the whole batch the same way
     (name the token). An overflow means the real values are longer than the tokens:
     trim an edit (the `width` line says which tokens grew most) and redo steps 1-3.

   Never open the filled file afterward. `fill`'s own output is the verification.

**Never pause the batch.** Revise and retry on your own; if an application still fails
after three attempts at the same step, `stage <folder> needs-you --note "<what failed>"`
and move to the next. Its edits and the exact failure go on the summary's "Needs you"
list.

### Phase 4 - wrap up

1. **Write `applications/<batch>_batch-summary.md`**, reading
   `references/batch-summary-template.md` at this point and not before. If that file
   exists from an earlier batch the same day, add `-2`. `python3 $SCRIPT status --batch
   <batch>` lists what to cover. Keep it terse - tables, not prose; summary tokens are
   the most expensive in the run. It describes tokenized résumés and never contains a
   filled value.
2. **Reply in chat** with only: how many applications are `filled` (ready to submit,
   with their folders), anything `skipped`, and the "Needs you" list. Point at the
   summary for the rest.
3. **Offer canon write-back once**, if consultation surfaced something that belongs on
   the page permanently - list every such item from the whole batch in that one offer.
   Opt-in, never automatic. Most confirmed experience belongs in the ledger only.

Always tailor from `canon_resume.odt`, never from a previous output, so tailoring never
compounds.

## Cost

Runs dozens of times a week, 10+ postings at a sitting. The script does all mechanical
work at zero token cost; `match` returns only interesting rows. Canon is extracted and
measured once per batch, not once per posting. Postings arrive through the inbox file
rather than being re-typed. Keep `consultation.md` and the summary terse, and read the
summary template only in Phase 4.
