#!/usr/bin/env python3
"""odt_text.py - surgical text editing for a single-column ODT resume.

Operates by raw-string surgery on content.xml rather than XML re-serialization,
so everything outside a changed region stays byte-identical. Every other zip
entry (notably styles.xml, which holds all real formatting) is copied verbatim.

Commands:
  normalize  replace faked right-alignment (literal tab/space runs) with a real
             right tab stop at the text-width margin
  shape      report whether the document parses as a resume this tool can edit
  extract    list the editable text units as JSON
  match      classify job-description terms against the resume (no scoring)
  apply      apply an edit set (replace / delete / insert_after / reorder_projects)
  check      verify an output against canon: diff, voice lint, page budget, parse
  budget     measure a single resume's current page fill (pages, bottom-margin slack)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# ---------------------------------------------------------------- constants

RIGHT_TAB_STYLE = "PRA"          # generated automatic style carrying the right tab stop
BULLET_STYLES = ("P8", "P11")    # paragraph styles used inside list items

# Phrases that read as LLM boilerplate. "leveraged"/"leveraging" are deliberately
# absent - canon uses them.
BANNED_PHRASES = (
    "spearheaded", "orchestrated", "seamlessly", "robust", "cutting-edge",
    "utilized", "impactful", "key stakeholder", "best-in-class", "synerg",
    "delve", "tapestry", "testament to",
)

SECTION_HEADINGS = ("EDUCATION", "SKILLS", "EXPERIENCE", "PROJECTS")

# Job-description boilerplate that should never become a keyword.
JD_STOP_SECTIONS = re.compile(
    r"(equal opportunity|eeo\b|affirmative action|benefits|about us|about the company"
    r"|why join|our mission|accommodation|e-verify|drug[- ]free)",
    re.I,
)

STOPWORDS = set("""
a an and are as at be been being by for from has have in into is it its of on or
that the to was were will with you your our we us they their this these those
work working works experience experiences experienced ability able strong excellent
good including include includes such other others new all any more most can may
role position candidate candidates team teams year years plus preferred required
requirements responsibilities qualifications skills skill knowledge understanding
demonstrated proven track record etc via using use used across within while also
who whom which what when where how not no nor but if then than so very much many
proficiency proficient familiarity familiar exposure pursuing enrolled currently
comfortable solid deep hands-on hands on basic advanced least one both either
related field degree bs ba ms phd equivalent similar relevant looking seeking
ideal successful will must should would could like well great etc
""".split())

# Connectives that separate distinct requirements within one line.
SPLIT_RE = re.compile(
    r",|;|\(|\)|\bor\b|\band\b|\bwith\b|\bsuch as\b|\bincluding\b|\bto\b|\bin\b|\bof\b",
    re.I,
)

# Small stem / synonym table for near-miss detection.
SYNONYMS = {
    "ci/cd": ["continuous integration", "continuous delivery", "continuous deployment", "pipelines"],
    "continuous integration": ["ci/cd", "pipelines"],
    "js": ["javascript"],
    "javascript": ["js"],
    "ts": ["typescript"],
    "typescript": ["ts"],
    "testing": ["test", "tests", "qa", "quality assurance"],
    "test": ["testing", "tests"],
    "qa": ["quality assurance", "testing"],
    "quality assurance": ["qa", "testing"],
    "automation": ["automated", "automatic"],
    "automated": ["automation"],
    "unit testing": ["pytest", "test suite"],
    "regression testing": ["regression test"],
    "version control": ["git", "github"],
    "git": ["github", "version control"],
    "rest": ["rest api", "restful"],
    "rest api": ["rest", "restful"],
    "sql": ["sql server", "database"],
    "database": ["sql", "sql server"],
    "agile": ["scrum", "stand-ups", "sprint"],
    "scrum": ["agile", "stand-ups"],
    "machine learning": ["pytorch", "ml", "model"],
    "ml": ["machine learning", "pytorch"],
    "c++": ["cpp"],
    "devops": ["azure devops", "ci/cd"],
    "cloud": ["azure"],
    "frontend": ["react", "front-end"],
    "front-end": ["react", "frontend"],
    "web development": ["react", "web app"],
}


# ---------------------------------------------------------------- zip helpers

def read_content(path):
    with zipfile.ZipFile(path) as z:
        return z.read("content.xml").decode("utf-8")


def write_odt(src, dst, new_content):
    """Copy src to dst, replacing content.xml. mimetype stays first and STORED."""
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        data = {i.filename: zin.read(i.filename) for i in infos}
    data["content.xml"] = new_content.encode("utf-8")
    order = sorted(infos, key=lambda i: (i.filename != "mimetype",))
    tmp = dst + ".tmp"
    with zipfile.ZipFile(tmp, "w") as zout:
        for info in order:
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.external_attr = info.external_attr
            if info.filename == "mimetype":
                zi.compress_type = zipfile.ZIP_STORED
            else:
                zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data[info.filename])
    os.replace(tmp, dst)


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unesc(text):
    return text.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")


def strip_tags(xml):
    """Inner XML -> plain text, expanding <text:s> runs and tabs."""
    out = re.sub(r'<text:s text:c="(\d+)"/>', lambda m: " " * int(m.group(1)), xml)
    out = out.replace("<text:s/>", " ").replace("<text:tab/>", "\t")
    out = re.sub(r"<[^>]+>", "", out)
    return unesc(out)


# ---------------------------------------------------------------- normalize

TOKEN_RE = re.compile(r"<[^>]*>|[^<]+")
FILLER_TAG_RE = re.compile(r'^<text:(?:tab/|s(?: text:c="\d+")?/)>$')

# A paragraph OPEN tag: attributes must not end in "/", or a self-closing
# spacer like <text:p text:style-name="P9"/> would be read as an open tag and
# swallow the following real paragraph as its content.
PARA_RE = re.compile(r"<text:p([^>]*(?<!/))>(.*?)</text:p>", re.S)
BLOCK_RE = re.compile(
    r"<text:p([^>]*(?<!/))>(.*?)</text:p>"
    r"|<text:list\b([^>]*)>(.*?)</text:list>",
    re.S,
)


def _tokens(inner):
    return TOKEN_RE.findall(inner)


def _is_filler(tok):
    """A tab/space element, or a text node that is only whitespace."""
    if tok.startswith("<"):
        return bool(FILLER_TAG_RE.match(tok))
    return tok.strip() == ""


def _page_text_width_in(path):
    """Text width in inches from the page layout: width - left - right margin."""
    with zipfile.ZipFile(path) as z:
        styles = z.read("styles.xml").decode("utf-8")
    m = re.search(r"<style:page-layout-properties[^>]*>", styles)
    if not m:
        return 7.5
    props = m.group(0)

    def get(attr):
        mm = re.search(attr + r'="([\d.]+)in"', props)
        return float(mm.group(1)) if mm else None

    w, ml, mr = get("fo:page-width"), get("fo:margin-left"), get("fo:margin-right")
    if None in (w, ml, mr):
        return 7.5
    return round(w - ml - mr, 4)


def normalize_content(content, width_in):
    """Collapse faked alignment into one real right-tab stop. Idempotent."""
    style_def = (
        '<style:style style:name="%s" style:family="paragraph" '
        'style:parent-style-name="Standard"><style:paragraph-properties>'
        '<style:tab-stops><style:tab-stop style:position="%sin" '
        'style:type="right"/></style:tab-stops></style:paragraph-properties>'
        "</style:style>" % (RIGHT_TAB_STYLE, width_in)
    )
    if RIGHT_TAB_STYLE not in content:
        content = content.replace(
            "</office:automatic-styles>", style_def + "</office:automatic-styles>", 1
        )

    def fix_para(m):
        head, inner = m.group(1), m.group(2)
        if "<text:tab/>" not in inner and "<text:s" not in inner:
            return m.group(0)
        if 'text:style-name="%s"' % RIGHT_TAB_STYLE in head:
            return m.group(0)

        toks = _tokens(inner)
        # Index maximal filler runs that sit between real content.
        runs, i = [], 0
        while i < len(toks):
            if _is_filler(toks[i]):
                j = i
                while j < len(toks) and _is_filler(toks[j]):
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        if not runs:
            return m.group(0)

        def has_text(seq):
            return strip_tags("".join(seq)).strip() != ""

        # The split point is the last filler run with real content on both sides.
        split = None
        for a, b in reversed(runs):
            if has_text(toks[:a]) and has_text(toks[b:]):
                split = (a, b)
                break
        if split is None:
            return m.group(0)

        a, b = split
        # Drop every remaining filler element on the left, and trailing filler on the right.
        left = [t for t in toks[:a] if not _is_filler(t)]
        right = [t for t in toks[b:] if not _is_filler(t)]
        inner_new = "".join(left) + "<text:tab/>" + "".join(right)
        # Remove spans emptied by filler removal.
        inner_new = re.sub(r"<text:span[^>]*></text:span>", "", inner_new)
        head_new = re.sub(
            r'text:style-name="[^"]*"',
            'text:style-name="%s"' % RIGHT_TAB_STYLE,
            head,
            count=1,
        )
        return "<text:p" + head_new + ">" + inner_new + "</text:p>"

    return PARA_RE.sub(fix_para, content)


# ---------------------------------------------------------------- extract

def extract_units(content):
    """Editable text units. Contact block and section headings are never surfaced."""
    units = []
    body_start = content.find("<office:body>")
    body = content[body_start:]

    section = None
    job = None
    exp_i = proj_i = 0
    b_i = 0
    seen_heading = False

    for m in BLOCK_RE.finditer(body):
        if m.group(1) is not None:
            kind, attrs, inner = "p", m.group(1), m.group(2) or ""
        else:
            kind, attrs, inner = "list", m.group(3), m.group(4) or ""
        if kind == "p":
            txt = strip_tags(inner).strip()
            if txt in SECTION_HEADINGS:
                section = txt
                seen_heading = True
                continue
            if not seen_heading or not txt:
                continue  # contact block / spacers - never editable
            if section == "SKILLS":
                sm = re.match(r"([^:]+:)\s*(.*)$", txt, re.S)
                if sm:
                    units.append({
                        "id": "skills." + re.sub(r"[^a-z]+", "", sm.group(1).lower()),
                        "kind": "skills", "section": section,
                        "label": sm.group(1), "text": sm.group(2).strip(),
                        "_ptext": txt,
                    })
                continue
            if "\t" in txt:
                left, right = txt.split("\t", 1)
                left, right = left.strip(), right.strip()
                mid = ""
                if "|" in left:
                    left, mid = left.split("|", 1)
                    left, mid = left.strip(), "| " + mid.strip()
                if section == "EXPERIENCE":
                    exp_i += 1
                    uid = "exp%d.head" % exp_i
                    job = uid
                    b_i = 0
                elif section == "PROJECTS":
                    proj_i += 1
                    uid = "proj%d.head" % proj_i
                    job = uid
                    b_i = 0
                else:
                    uid = "edu.head%d" % (len(units) + 1)
                units.append({
                    "id": uid, "kind": "header", "section": section,
                    "left": left, "mid": mid, "right": right, "_ptext": txt,
                })
            elif section == "EXPERIENCE" and re.search(
                r'text:style-name="P7"', attrs
            ):
                pass  # job title line - not editable
        else:  # text:list
            for lm in re.finditer(
                r"<text:list-item>\s*<text:p([^>]*)>(.*?)</text:p>\s*</text:list-item>",
                inner, re.S,
            ):
                b_i += 1
                base = (job or "misc").replace(".head", "")
                units.append({
                    "id": "%s.b%d" % (base, b_i),
                    "kind": "bullet", "section": section,
                    "parent": job or "", "text": strip_tags(lm.group(2)).strip(),
                    "_raw": lm.group(2),
                })
    return units


def public_units(units):
    return [{k: v for k, v in u.items() if not k.startswith("_")} for u in units]


def resume_text(content):
    units = extract_units(content)
    parts = []
    for u in units:
        if u["kind"] == "bullet":
            parts.append(u["text"])
        elif u["kind"] == "skills":
            parts.append(u["label"] + " " + u["text"])
        else:
            parts.append(" ".join([u.get("left", ""), u.get("mid", ""), u.get("right", "")]))
    return "\n".join(parts)


# ---------------------------------------------------------------- shape

# Is this document the kind of thing this tool can edit? Purely structural.
# It is NOT a quality judgment and NOT a score: it never says a resume is good,
# bad, strong, or weak, and its output must never be reported as a rating.
# It also cannot identify what a document IS - only that it does not read as a
# resume - so an "unrecognized" verdict is a question for the user, not a fact.

CONTACT_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.\w+"                        # email
    r"|\(?\d{3}\)?[ .-]?\d{3}[ .-]\d{4}"           # phone
    r"|linkedin\.com|github\.com"
    r"|\[(?:Name|Email|Phone|Website|City)\]",     # canon's redaction tokens
    re.I,
)

DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"(?:\s+\d{1,2},)?\s+(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|Present|Current)\b",
    re.I,
)

# Section words resumes use generally, not just canon's exact four headings.
# This is what tells "a resume this editor cannot parse" apart from "not a
# resume at all" - the two need different advice, so they need different signals.
GENERIC_SECTION_RE = re.compile(
    r"^\s*(?:work |professional |technical |relevant |additional )?"
    r"(?:experience|employment history|employment|education|skills|projects|"
    r"certifications?|publications|awards|honors|leadership|activities|"
    r"volunteer|references|summary|objective|profile|coursework)"
    r"\s*:?\s*$",
    re.I,
)

# Documents most often attached by mistake. A hit is a hint to put to the user,
# never a conclusion - the phrase can appear innocently.
OTHER_DOC_TELLS = (
    ("a cover letter", re.compile(
        r"dear (?:hiring|sir|madam|mr\.|ms\.|mx\.|recruit)"
        r"|to whom it may concern|sincerely,|yours truly", re.I)),
    ("an academic paper", re.compile(
        r"^\s*abstract\s*$|\bet al\.|\bworks cited\b|\bdoi:", re.I | re.M)),
    ("an invoice or statement", re.compile(
        r"\binvoice\b|\bamount due\b|\bsubtotal\b|\bremit\b|\bbill to\b", re.I)),
    ("a letter or memo", re.compile(r"^\s*(?:memorandum|re:)\b", re.I | re.M)),
    ("a transcript", re.compile(
        r"\btranscript\b|\bcredit hours\b|\bsemester gpa\b", re.I)),
    ("a job description", re.compile(
        r"\bequal opportunity employer\b|\bwe are (?:seeking|looking for)\b"
        r"|\bresponsibilities include\b|\bthe ideal candidate\b", re.I)),
)


def document_text(content):
    """Every body paragraph as plain text, one per line.

    Unlike resume_text this keeps the contact block and anything outside a
    recognized section, because the shape check has to see exactly the parts of
    the document the editor deliberately ignores.
    """
    body = content[content.find("<office:body>"):]
    lines = (strip_tags(m.group(2)).strip() for m in PARA_RE.finditer(body))
    return "\n".join(ln for ln in lines if ln)


def shape_signals(content):
    units = extract_units(content)
    text = document_text(content)
    lines = text.split("\n")
    bullets = [u for u in units if u["kind"] == "bullet"]
    canon = [h for h in SECTION_HEADINGS if any(ln == h for ln in lines)]
    other = sorted({ln.rstrip(":").strip() for ln in lines
                    if GENERIC_SECTION_RE.match(ln) and ln not in SECTION_HEADINGS})

    sig = {
        "canon_sections": canon,
        "other_sections": other,
        "units": len(units),
        "bullets": len(bullets),
        "contact": bool(CONTACT_RE.search("\n".join(lines[:8]))),
        "dates": len(DATE_RE.findall(text)),
        "words": len(text.split()),
        "tells": [name for name, rx in OTHER_DOC_TELLS if rx.search(text)],
    }
    # The editor works on canon's four headings and the units under them, so
    # those two are what "editable at all" means. Everything else is context
    # for the question put to the user.
    sig["verdict"] = ("resume" if len(canon) >= 2 and len(bullets) >= 3
                      else "unrecognized")
    if sig["verdict"] == "resume":
        sig["reading"] = ""
    elif len(other) >= 2:
        sig["reading"] = "a resume in a layout this editor cannot parse"
    elif sig["tells"]:
        sig["reading"] = "possibly " + " or ".join(sig["tells"])
    else:
        sig["reading"] = "unclear"
    return sig


def cmd_shape(args):
    sig = shape_signals(read_content(args.resume))
    ok = sig["verdict"] == "resume"

    print("VERDICT: %s%s" % (
        sig["verdict"], "" if ok else " - does not parse as a resume"))
    print("  headings   %s" % (", ".join(sig["canon_sections"]) or
                               "none of " + " / ".join(SECTION_HEADINGS)))
    print("  editable   %d units, %d bullets%s" % (
        sig["units"], sig["bullets"], "" if sig["units"] else
        "   <- nothing here can be tailored"))
    print("  contact    %s" % ("present" if sig["contact"] else "absent"))
    print("  dates      %d" % sig["dates"])
    print("  length     %d words" % sig["words"])
    if sig["other_sections"]:
        print("  other headings found: %s" % ", ".join(sig["other_sections"]))
    if not ok:
        print("  reads like: %s" % sig["reading"])
        print("\nThis says the document is not in the shape this tool edits. It does\n"
              "not say what the document is. Ask the user before going further.")
    if args.exit_code:
        sys.exit(0 if ok else 2)


# ---------------------------------------------------------------- match

def jd_terms(jd):
    """Extract candidate keywords from a job description, weighted by section."""
    lines = jd.splitlines()
    terms = {}
    weight = 1
    skip = False
    for line in lines:
        low = line.lower()
        if JD_STOP_SECTIONS.search(low):
            skip = True
            continue
        if re.match(r"^\s*(requirement|qualification|what you|must have|you have)", low):
            skip, weight = False, 3
            continue
        if re.match(r"^\s*(preferred|nice to have|bonus|plus)", low):
            skip, weight = False, 2
            continue
        if re.match(r"^\s*(responsibilit|what you.ll do|the role|about the role)", low):
            skip, weight = False, 2
            continue
        if skip:
            continue
        line = re.sub(r"^[\s\-•*\d.]+", "", line)
        for clause in SPLIT_RE.split(line):
            words = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*|C\+\+|C#", clause)
            # Trim stopwords from both edges; what remains is the real term.
            while words and words[0].lower() in STOPWORDS:
                words.pop(0)
            while words and words[-1].lower() in STOPWORDS:
                words.pop()
            if not words or len(words) > 4:
                continue
            p = " ".join(words).lower().strip(" .-")
            if len(p) < 2 or p in STOPWORDS:
                continue
            if all(w.lower() in STOPWORDS for w in words):
                continue
            terms[p] = max(terms.get(p, 0), weight)
    # Drop a term that is wholly contained in a longer retained term.
    for t in list(terms):
        if any(t != o and t in o for o in terms):
            terms.pop(t, None)
    return terms


def classify(terms, text):
    low = text.lower()
    exact, near, missing = [], [], []
    for term, w in sorted(terms.items(), key=lambda kv: -kv[1]):
        if term in low:
            exact.append((term, w))
            continue
        alts = SYNONYMS.get(term, [])
        hit = next((a for a in alts if a in low), None)
        if hit:
            near.append((term, w, hit))
            continue
        # single-word stem match
        base = term.rstrip("s")
        if len(base) > 4 and base in low:
            near.append((term, w, base))
            continue
        # Multi-word term whose words all appear, just not adjacently: the user
        # has the substance and only the JD's exact phrasing is absent, which is
        # a rewording opportunity rather than a gap.
        words = [x for x in term.split() if len(x) > 2]
        if len(words) > 1 and all(x.rstrip("s") in low for x in words):
            near.append((term, w, "has all of: " + ", ".join(words)))
            continue
        missing.append((term, w))
    return exact, near, missing


def cmd_match(args):
    content = read_content(args.resume)
    text = resume_text(content)
    jd = open(args.jd, encoding="utf-8").read()
    terms = jd_terms(jd)
    exact, near, missing = classify(terms, text)
    # Only the interesting rows are surfaced. No score, ratio, or total.
    near = near[: args.limit]
    missing = missing[: args.limit]
    if args.json:
        print(json.dumps({
            "near": [{"term": t, "weight": w, "resume_has": h} for t, w, h in near],
            "missing": [{"term": t, "weight": w} for t, w in missing],
        }, indent=2))
    else:
        if near:
            print("NEAR MISS (resume says something close, JD says it differently):")
            for t, w, h in near:
                print("  %-34s resume has: %s" % (t, h))
        if missing:
            print("\nMISSING (not present in any form):")
            for t, w in missing:
                print("  %s" % t)
        if not near and not missing:
            print("No actionable gaps. Canon already covers this posting.")
    if args.exit_code:
        sys.exit(0 if not near and not missing else 1)


# ---------------------------------------------------------------- apply

def _unit_map(content):
    return {u["id"]: u for u in extract_units(content)}


def _set_skills_value(content, ptext, new):
    """Rewrite a Skills line's value, keeping the bold label span intact.

    The value is split across several spans that differ only by spell-check
    language, so no single text node holds it. Collapse those into one span
    carrying the style that already covers most of the value.
    """
    for m in PARA_RE.finditer(content):
        if strip_tags(m.group(2)).strip() != ptext.strip():
            continue
        spans = re.findall(r"(<text:span[^>]*>)(.*?)</text:span>", m.group(2), re.S)
        if len(spans) < 2:
            sys.exit("apply: skills line has unexpected span layout")
        label_open, label_txt = spans[0]
        body_open = max(spans[1:], key=lambda s: len(strip_tags(s[1])))[0]
        inner = "%s%s</text:span>%s%s</text:span>" % (
            label_open, label_txt, body_open, esc(new))
        return content[: m.start(2)] + inner + content[m.end(2):]
    sys.exit("apply: could not locate skills paragraph %r" % ptext[:40])


def _replace_in_para(content, ptext, old, new):
    """Replace old->new inside the one paragraph whose text equals ptext.

    Scoping matters: a bare content.replace() of a short field like "City, State"
    would hit the first textual occurrence in the document (the contact line's
    "City, State, USA") rather than the unit being edited.
    """
    for m in PARA_RE.finditer(content):
        if strip_tags(m.group(2)).strip() != ptext.strip():
            continue
        inner = m.group(2)
        if esc(old) not in inner:
            sys.exit("apply: %r not found inside its own paragraph" % old)
        new_inner = inner.replace(esc(old), esc(new), 1)
        return content[: m.start(2)] + new_inner + content[m.end(2):]
    sys.exit("apply: could not locate paragraph for %r" % ptext[:40])


def cmd_apply(args):
    content = read_content(args.resume)
    edits = json.load(open(args.edits, encoding="utf-8"))

    # Phases run delete -> replace -> insert_after, re-resolving ids in between.
    # Bullet ids are positional, so a structural edit shifts them; re-extracting
    # keeps each phase's ids meaningful instead of silently hitting the wrong bullet.

    units = _unit_map(content)
    for uid in edits.get("delete", []):
        u = units.get(uid)
        if not u or u["kind"] != "bullet":
            sys.exit("apply: delete target %r is not a bullet" % uid)
        pat = re.compile(
            r"<text:list-item>\s*<text:p[^>]*(?<!/)>" + re.escape(u["_raw"]) +
            r"</text:p>\s*</text:list-item>", re.S)
        hits = pat.findall(content)
        if len(hits) != 1:
            sys.exit("apply: delete target %s matches %d list items, need exactly 1"
                     % (uid, len(hits)))
        m = pat.search(content)
        content = content[: m.start()] + content[m.end():]

    for uid, new in edits.get("replace", {}).items():
        # Re-resolve every time: two edits can target the same paragraph (a
        # header's .left and .right), and the first invalidates the second's
        # locating probe.
        units = _unit_map(content)
        field = None
        u = units.get(uid)
        if u is None and uid.count(".") > 1:
            base, field = uid.rsplit(".", 1)
            u = units.get(base)
        if u is None:
            sys.exit("apply: unknown id %r" % uid)
        if u["kind"] == "bullet":
            old = u["_raw"]
            if "<" in old:
                sys.exit("apply: bullet %s contains markup; refusing" % uid)
            needle = ">" + old + "<"
            if content.count(needle) != 1:
                sys.exit("apply: bullet %s text matches %d places, need exactly 1"
                         % (uid, content.count(needle)))
            content = content.replace(needle, ">" + esc(new) + "<", 1)
        elif u["kind"] == "skills":
            content = _set_skills_value(content, u["_ptext"], new)
        elif u["kind"] == "header":
            if field == "mid" and u["mid"]:
                content = _replace_in_para(content, u["_ptext"], u["mid"], new)
            elif field == "left":
                content = _replace_in_para(content, u["_ptext"], u["left"], new)
            elif field == "right" and args.identity:
                # Dates are facts, not tailoring surface; only the one-time
                # tokenization may touch a right field.
                content = _replace_in_para(content, u["_ptext"], u["right"], new)
            else:
                sys.exit("apply: header %s - only .mid and .left are writable" % uid)

    # Resolve every anchor before inserting: an insert never changes another
    # anchor's raw text, so one resolution pass is safe and order-independent.
    units = _unit_map(content)
    for uid, new in edits.get("insert_after", {}).items():
        u = units.get(uid)
        if not u or u["kind"] != "bullet":
            sys.exit("apply: insert_after anchor %r is not a bullet" % uid)
        items = new if isinstance(new, list) else [new]
        pat = re.compile(
            r"<text:list-item>\s*<text:p([^>]*(?<!/))>" + re.escape(u["_raw"]) +
            r"</text:p>\s*</text:list-item>", re.S)
        if len(pat.findall(content)) != 1:
            sys.exit("apply: insert anchor %s matches %d list items, need exactly 1"
                     % (uid, len(pat.findall(content))))
        m = pat.search(content)
        clones = "".join(
            "<text:list-item><text:p%s>%s</text:p></text:list-item>" % (m.group(1), esc(t))
            for t in items)
        content = content[: m.end()] + clones + content[m.end():]

    # Identity-only edits: the contact block, unlocked exclusively for the
    # one-time Phase A tokenization.
    for old, new in edits.get("identity", {}).items():
        if not args.identity:
            sys.exit("apply: identity edits require --identity")
        # Anchored to text-node boundaries so a short string like "Name" cannot
        # match inside an attribute such as style:name="...".
        needle = ">" + esc(old) + "<"
        if content.count(needle) != 1:
            sys.exit("apply: identity text %r matched %d text nodes, need exactly 1"
                     % (old, content.count(needle)))
        content = content.replace(needle, ">" + esc(new) + "<", 1)

    order = edits.get("reorder_projects")
    if order:
        content = _reorder_projects(content, order)

    write_odt(args.resume, args.output, content)
    print("wrote %s" % args.output)


# One project = its title paragraph, the bullet list that follows, and the
# trailing spacer paragraph. The open-tag pattern carries the (?<!/) guard so a
# self-closing spacer is never mistaken for the start of a project.
PROJECT_BLOCK_RE = re.compile(
    r"<text:p[^>]*(?<!/)>.*?</text:p>"          # title line
    r"(?:<text:list\b[^>]*>.*?</text:list>)?"   # its bullets
    r"(?:<text:p[^>]*/>)?",                     # trailing spacer
    re.S,
)


def _reorder_projects(content, order):
    start = content.find(">PROJECTS<")
    if start < 0:
        sys.exit("apply: PROJECTS section not found")
    tail = content.find("</text:p>", start) + len("</text:p>")

    blocks = []  # (name, abs_start, abs_end)
    for m in PROJECT_BLOCK_RE.finditer(content, tail):
        text = strip_tags(m.group(0)).strip()
        if not text:
            continue
        name = text.split("|")[0].split("\t")[0].strip()
        blocks.append((name, m.start(), m.end()))

    wanted = [b for b in blocks if b[0] in order]
    missing = [o for o in order if o not in [b[0] for b in wanted]]
    if missing:
        sys.exit("apply: reorder_projects names not found: %s" % ", ".join(missing))
    if len(wanted) != len(order):
        sys.exit("apply: reorder_projects must list every project exactly once")

    # Blocks are contiguous, so splice the whole span by real offsets rather
    # than assuming a join of the pieces is a literal substring of the document.
    lo, hi = wanted[0][1], wanted[-1][2]
    if any(wanted[i][2] != wanted[i + 1][1] for i in range(len(wanted) - 1)):
        sys.exit("apply: project blocks are not contiguous; refusing to reorder")
    by_name = {n: content[s:e] for n, s, e in wanted}
    return content[:lo] + "".join(by_name[o] for o in order) + content[hi:]


# ---------------------------------------------------------------- check

SOFFICE = ["flatpak", "run", "--command=/app/libreoffice/program/soffice",
           "org.libreoffice.LibreOffice"]


def to_pdf(odt):
    """Render to PDF via the Flatpak LibreOffice.

    The temp dir lives under $HOME, not /tmp: Flatpak gives the app a private
    /tmp, so anything staged there is invisible to it and the load silently
    fails. The input is copied in for the same reason.
    """
    base = os.path.expanduser("~/.cache/odt_text")
    os.makedirs(base, exist_ok=True)
    out = tempfile.mkdtemp(prefix="render-", dir=base)
    staged = os.path.join(out, os.path.basename(odt))
    shutil.copy(odt, staged)
    subprocess.run(
        SOFFICE + ["--headless", "--convert-to", "pdf", "--outdir", out, staged],
        check=True, capture_output=True, timeout=180)
    pdf = os.path.splitext(staged)[0] + ".pdf"
    return pdf if os.path.exists(pdf) else None


def measure_page_budget(odt_path):
    """Render odt_path to PDF and return (pages, last_yMax, pdf_path), or
    (None, None, None) if PDF conversion is unavailable. last_yMax is the
    deepest bottom edge of any text box on the page, in points. The caller
    owns pdf_path's containing directory and must clean it up."""
    pdf = to_pdf(odt_path)
    if not pdf:
        return None, None, None
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    bbox = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"],
                          capture_output=True, text=True).stdout
    ys = [float(y) for y in re.findall(r'yMax="([\d.]+)"', bbox)]
    last = max(ys) if ys else None
    return pages, last, pdf


def cmd_budget(args):
    pages, last, pdf = measure_page_budget(args.resume)
    if pdf:
        shutil.rmtree(os.path.dirname(pdf), ignore_errors=True)
    if last is None:
        print("PDF conversion unavailable; budget check skipped")
        return
    hard_reserve = 756 - last
    safe_reserve = 738 - last
    print("pages: %d" % pages)
    print("last content yMax: %.2fpt" % last)
    print("reserve to hard limit (756pt): %.2fpt" % hard_reserve)
    print("reserve to safe buffer (738pt): %.2fpt" % safe_reserve)
    if safe_reserve > 50:
        print("\nNOTABLE: ~%.0fpt of unused space below the safe buffer - "
              "consider using it (add a bullet, lengthen one, or a small "
              "font-size nudge)." % safe_reserve)


def cmd_check(args):
    canon = read_content(args.canon)
    out = read_content(args.output)
    problems, warnings = [], []

    cu = {u["id"]: u for u in extract_units(canon)}
    ou = {u["id"]: u for u in extract_units(out)}

    # Diff by content, not by id: bullet ids are positional, so one insert
    # shifts every later id and an id-keyed diff reports moves as rewrites.
    import difflib

    ca = [u["text"] for u in extract_units(canon) if u["kind"] == "bullet"]
    ob = [u["text"] for u in extract_units(out) if u["kind"] == "bullet"]
    print("=== bullet changes ===")
    sm = difflib.SequenceMatcher(None, ca, ob, autojunk=False)
    gone, came, reworded = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_block, new_block = ca[i1:i2], ob[j1:j2]
        pairs = min(len(old_block), len(new_block))
        reworded += list(zip(old_block[:pairs], new_block[:pairs]))
        gone += old_block[pairs:]
        came += new_block[pairs:]
    # A bullet present on both sides only moved (its project was reordered);
    # reporting it as a delete plus an add would misrepresent the edit.
    moved = [t for t in gone if t in came]
    gone = [t for t in gone if t not in moved]
    came = [t for t in came if t not in moved]

    for a, b in reworded:
        print("  ~ reworded\n      was: %s\n      now: %s" % (a, b))
    for t in gone:
        print("  - removed: %s" % t)
    for t in came:
        print("  + added:   %s" % t)
    for t in moved:
        print("  = moved:   %s" % t[:70])
    if not (reworded or gone or came or moved):
        print("  (no bullet changes)")

    # Headers are keyed by their own name, not by position, so reordering
    # projects reads as a reorder instead of a pile of content rewrites.
    print("\n=== other changed units ===")
    changed_other = False

    def by_name(units):
        return {(u["section"], u["left"]): u
                for u in units if u["kind"] == "header"}

    ha, hb = by_name(extract_units(canon)), by_name(extract_units(out))
    for key in sorted(set(ha) & set(hb)):
        for f in ("mid", "right"):
            if ha[key].get(f) != hb[key].get(f):
                changed_other = True
                print("  ~ %s (%s)\n      was: %s\n      now: %s"
                      % (key[1], f, ha[key].get(f), hb[key].get(f)))
    for key in sorted(set(ha) - set(hb)):
        changed_other = True
        print("  - header removed: %s" % key[1])
    for key in sorted(set(hb) - set(ha)):
        changed_other = True
        print("  + header added:   %s" % key[1])

    order_a = [u["left"] for u in extract_units(canon)
               if u["kind"] == "header" and u["section"] == "PROJECTS"]
    order_b = [u["left"] for u in extract_units(out)
               if u["kind"] == "header" and u["section"] == "PROJECTS"]
    if order_a != order_b and sorted(order_a) == sorted(order_b):
        changed_other = True
        print("  ~ projects reordered: %s -> %s"
              % (" | ".join(order_a), " | ".join(order_b)))

    for uid in sorted(set(cu) | set(ou)):
        a, b = cu.get(uid, {}), ou.get(uid, {})
        if a.get("kind") != "skills" and b.get("kind") != "skills":
            continue
        if a.get("text") != b.get("text"):
            changed_other = True
            print("  ~ %s\n      was: %s\n      now: %s"
                  % (uid, a.get("text"), b.get("text")))

    if not changed_other:
        print("  (none)")

    # non-content.xml entries must be byte-identical
    with zipfile.ZipFile(args.canon) as za, zipfile.ZipFile(args.output) as zb:
        for name in za.namelist():
            if name == "content.xml":
                continue
            try:
                if za.read(name) != zb.read(name):
                    problems.append("zip entry %s differs from canon" % name)
            except KeyError:
                problems.append("zip entry %s missing from output" % name)

    # voice lint on changed text only
    changed = [ou[u]["text"] for u in ou
               if ou[u].get("kind") == "bullet"
               and ou[u]["text"] != cu.get(u, {}).get("text")]
    for t in changed:
        if "—" in t:
            problems.append("em dash in: %s" % t[:60])
        if "–" in t:
            problems.append("en dash in bullet prose: %s" % t[:60])
        if "'" in t:
            problems.append("straight apostrophe in: %s" % t[:60])
        if '"' in t:
            problems.append("straight quote in: %s" % t[:60])
        for p in BANNED_PHRASES:
            if p in t.lower():
                problems.append("banned phrase %r in: %s" % (p, t[:60]))

    # one sentence per bullet
    for uid, u in ou.items():
        if u.get("kind") != "bullet":
            continue
        if len(re.findall(r"[.!?](?:\s|$)", u["text"])) > 1:
            warnings.append("bullet %s has more than one sentence" % uid)

    # page budget + parse check
    pages, last, pdf = None, None, None
    try:
        pages, last, pdf = measure_page_budget(args.output)
    except Exception as e:
        warnings.append("PDF conversion unavailable (%s); page/parse checks skipped" % e)
    if pages is not None:
        if pages > 1:
            problems.append("resume is %d pages; must be 1" % pages)
        if last is not None:
            print("\npage budget: last content yMax %.2fpt (margin 756pt)" % last)
            if last > 756:
                problems.append("content overflows bottom margin (%.2fpt)" % last)
            elif last > 738:
                warnings.append("less than 0.25in reserve left (%.2fpt)" % last)
        # parse check - what a real parser's text extraction would see
        flat = subprocess.run(["pdftotext", "-layout", pdf, "-"],
                              capture_output=True, text=True).stdout
        pos = -1
        for h in SECTION_HEADINGS:
            i = flat.find(h)
            if i < 0:
                problems.append("parse check: heading %s not recoverable" % h)
            elif i < pos:
                problems.append("parse check: heading %s out of reading order" % h)
            else:
                pos = i
        for uid, u in ou.items():
            if u.get("kind") == "bullet":
                probe = re.sub(r"\s+", " ", u["text"])[:28]
                if probe and probe not in re.sub(r"\s+", " ", flat):
                    problems.append("parse check: bullet %s text not recoverable" % uid)
        shutil.rmtree(os.path.dirname(pdf), ignore_errors=True)

    # Scan the whole body, not just editable units: the contact block holds
    # [Name]/[Email]/[Phone] and is deliberately not surfaced by extract.
    body = out[out.find("<office:body>"):]
    tokens = sorted(set(re.findall(r"\[[A-Za-z][A-Za-z0-9]*\]", strip_tags(body))))
    if tokens:
        print("\nplaceholders still present (expected - fill before submitting):")
        print("  " + "  ".join(tokens))

    for w in warnings:
        print("WARN: %s" % w)
    for p in problems:
        print("FAIL: %s" % p)
    if problems:
        sys.exit(1)
    print("\nOK")


# ---------------------------------------------------------------- entry

def cmd_normalize(args):
    content = read_content(args.input)
    width = _page_text_width_in(args.input)
    new = normalize_content(content, width)
    write_odt(args.input, args.output, new)
    print("normalized to a %sin right tab stop -> %s" % (width, args.output))


def cmd_extract(args):
    units = public_units(extract_units(read_content(args.resume)))
    print(json.dumps(units, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("normalize"); p.add_argument("input")
    p.add_argument("-o", "--output", required=True); p.set_defaults(fn=cmd_normalize)

    p = sub.add_parser("shape"); p.add_argument("resume")
    p.add_argument("--exit-code", action="store_true"); p.set_defaults(fn=cmd_shape)

    p = sub.add_parser("extract"); p.add_argument("resume"); p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("match"); p.add_argument("resume"); p.add_argument("jd")
    p.add_argument("--json", action="store_true")
    p.add_argument("--exit-code", action="store_true")
    p.add_argument("--limit", type=int, default=12); p.set_defaults(fn=cmd_match)

    p = sub.add_parser("apply"); p.add_argument("resume"); p.add_argument("edits")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--identity", action="store_true"); p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("check"); p.add_argument("canon"); p.add_argument("output")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("budget"); p.add_argument("resume"); p.set_defaults(fn=cmd_budget)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
