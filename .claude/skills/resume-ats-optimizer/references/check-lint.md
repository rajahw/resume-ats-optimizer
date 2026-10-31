# What `check`'s voice lint enforces

Read when `check` fails on a voice rule, before revising the edit set.

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
