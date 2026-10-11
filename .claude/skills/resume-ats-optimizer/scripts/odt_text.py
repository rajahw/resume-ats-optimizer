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
  budget     measure a resume as submitted: pages, spare lines, per-bullet wrap slack
  fill       replace [Token] placeholders from a local pii.txt into a *_filled copy
  doctor     report whether this machine can measure pages (and a resume's fonts)

`fill` is the one command that handles real identity values. It reads the map
itself and prints only token names and counts, never a value, so the command can
be run by an agent that must not see what it substitutes.
"""

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

# ---------------------------------------------------------------- constants

RIGHT_TAB_STYLE = "PRA"          # generated automatic style carrying the right tab stop

# Fallbacks for a document whose page layout does not declare its geometry:
# US Letter with half-inch margins. page_bottom_limit() prefers the real values.
DEFAULT_TEXT_WIDTH_IN = 7.5
DEFAULT_BOTTOM_LIMIT_PT = 756.0
SAFE_RESERVE_PT = 18.0           # 0.25in of slack before the bottom margin
NEAR_WRAP_CHARS = 6              # a changed bullet this close to wrapping gets a warning

# Fonts the renderer brings in on its own: LibreOffice draws list bullets from
# OpenSymbol whatever the document's font is.
RENDERER_OWN_FONTS = ("opensymbol",)

# Every external tool a page measurement needs besides LibreOffice itself.
POPPLER_TOOLS = ("pdfinfo", "pdftotext", "pdffonts")

# Phrases that read as generated boilerplate rather than as something a person
# wrote. A phrase canon already uses is exempt at lint time: the user's own
# vocabulary is in-voice by definition, however it reads here.
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


def esc_attr(text):
    """esc() plus quotes, for a value landing inside an attribute.

    `fill` substitutes into raw XML, and tokens live in xlink:href="..." as well
    as in text nodes, so a value carrying a quote would otherwise close the
    attribute early and corrupt the document.
    """
    return esc(text).replace('"', "&quot;").replace("'", "&apos;")


def unesc(text):
    return text.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")


def strip_tags(xml):
    """Inner XML -> plain text, expanding <text:s> runs and tabs."""
    out = re.sub(r'<text:s text:c="(\d+)"/>', lambda m: " " * int(m.group(1)), xml)
    out = out.replace("<text:s/>", " ").replace("<text:tab/>", "\t")
    out = re.sub(r"<[^>]+>", "", out)
    return unesc(out)


# ---------------------------------------------------------------- normalize

# Splits inner XML into tags and text nodes. Distinct from TOKEN_RE, which
# matches [Token] placeholders - naming both TOKEN_RE silently shadowed this one
# and made normalize a no-op.
XML_TOKEN_RE = re.compile(r"<[^>]*>|[^<]+")
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
    return XML_TOKEN_RE.findall(inner)


def _is_filler(tok):
    """A tab/space element, or a text node that is only whitespace."""
    if tok.startswith("<"):
        return bool(FILLER_TAG_RE.match(tok))
    return tok.strip() == ""


def _page_layout_getter(path):
    """A reader for the page layout's inch-valued attributes."""
    with zipfile.ZipFile(path) as z:
        styles = z.read("styles.xml").decode("utf-8")
    m = re.search(r"<style:page-layout-properties[^>]*>", styles)
    props = m.group(0) if m else ""

    def get(attr):
        mm = re.search(attr + r'="([\d.]+)in"', props)
        return float(mm.group(1)) if mm else None

    return get


def _page_text_width_in(path):
    """Text width in inches from the page layout: width - left - right margin."""
    get = _page_layout_getter(path)
    w, ml, mr = get("fo:page-width"), get("fo:margin-left"), get("fo:margin-right")
    if None in (w, ml, mr):
        return DEFAULT_TEXT_WIDTH_IN
    return round(w - ml - mr, 4)


def page_bottom_limit(path):
    """Deepest allowed text edge, in points, from the real page geometry.

    Measured rather than assumed: the previous hardcoded 756pt silently
    mismeasures any document that is not US Letter with a half-inch bottom
    margin. The same number is the fallback when the layout does not say.
    """
    get = _page_layout_getter(path)
    h, mb = get("fo:page-height"), get("fo:margin-bottom")
    if None in (h, mb):
        return DEFAULT_BOTTOM_LIMIT_PT
    return round((h - mb) * 72.0, 2)


def normalize_content(content, width_in):
    """Collapse faked alignment into one real right-tab stop. Idempotent."""
    style_def = (
        '<style:style style:name="%s" style:family="paragraph" '
        'style:parent-style-name="Standard"><style:paragraph-properties>'
        '<style:tab-stops><style:tab-stop style:position="%sin" '
        'style:type="right"/></style:tab-stops></style:paragraph-properties>'
        "</style:style>" % (RIGHT_TAB_STYLE, width_in)
    )
    if ('style:name="%s"' % RIGHT_TAB_STYLE) not in content:
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
            # Anything else under a heading with no tab - a job title line, say -
            # is deliberately not surfaced, so it cannot be tailored.
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


# ---------------------------------------------------------- voice conventions
#
# The lint has no house style. Canon is the style guide: these helpers read
# what the user's document already does, and an edit is checked for matching
# it. Nothing below encodes how a resume ought to read.

# Characters that usually arrive from a model or a rich-text paste rather than
# from the user's own typing. One canon never uses is a failure; any other
# unfamiliar character is a warning, since a glyph the document does not
# otherwise contain is an ATS parsing risk either way.
TELL_CHARS = {
    "—": "em dash", "–": "en dash", "‒": "figure dash",
    "―": "horizontal bar", "‘": "curly quote",
    "’": "curly apostrophe", "“": "curly quote",
    "”": "curly quote", "…": "ellipsis character",
    "\u00a0": "non-breaking space", "•": "bullet glyph",
    "●": "bullet glyph", "▪": "bullet glyph", "◦": "bullet glyph",
    "·": "middle dot", "→": "arrow", "ﬁ": "fi ligature",
    "ﬂ": "fl ligature",
}

# Forms a document picks one of. The lint prefers neither; it only asks that an
# edit keep using whichever one canon already settled on.
INTERCHANGEABLE = (
    ("apostrophe", "'", "’"),
    ("double quote", '"', "“”"),
)

SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")


def sentence_count(text):
    return len(SENTENCE_END_RE.findall(text))


def plain(text):
    """Entities `unesc` leaves alone, so lint compares real characters.

    An editor may write an apostrophe or a quote either literally or as an
    entity. Text arriving from an edit set is always literal, so canon has to
    be read the same way or its own punctuation is invisible to the lint.
    """
    return text.replace("&apos;", "'").replace("&quot;", '"')


def bullet_prose(content):
    """Bullet text only - the prose an edit is measured against.

    Headers and dates are excluded deliberately: a date range may use an en
    dash in a document whose prose never does.
    """
    return [plain(u["text"]) for u in extract_units(content)
            if u["kind"] == "bullet"]


def canon_conventions(canon):
    """The punctuation habits canon's own prose settled on.

    Where canon shows no preference between two forms, neither does the lint.
    """
    prose = "\n".join(bullet_prose(canon))
    conv = {"chars": set(prose), "forms": {}}
    for name, a, b in INTERCHANGEABLE:
        na = sum(prose.count(c) for c in a)
        nb = sum(prose.count(c) for c in b)
        if na and not nb:
            conv["forms"][name] = (a, b)
        elif nb and not na:
            conv["forms"][name] = (b, a)
    return conv


def lint_prose(text, conv):
    """Check one changed bullet against canon's conventions."""
    problems, warnings = [], []
    reported = set()
    for name, (keep, avoid) in sorted(conv["forms"].items()):
        if any(c in text for c in avoid):
            problems.append("%s differs from canon, which uses %r: %s"
                            % (name, keep[0], text[:60]))
            reported.update(avoid)
    for ch in sorted(set(text) - conv["chars"] - reported):
        if ch in TELL_CHARS:
            problems.append("%s does not appear in canon's prose: %s"
                            % (TELL_CHARS[ch], text[:60]))
        elif not ch.isascii():
            warnings.append("character %r is not used anywhere in canon's prose; "
                            "unusual glyphs parse unevenly: %s" % (ch, text[:60]))
    return problems, warnings


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
            needle = ">" + old + "<"
            if content.count(needle) != 1:
                sys.exit("apply: bullet %s text matches %d places, need exactly 1"
                         % (uid, content.count(needle)))
            # A bullet carrying inline spans (bolded metrics, say) is replaced
            # whole. The emphasis described the old wording, so it cannot survive
            # a rewrite - but say which bullet lost it rather than dropping it
            # silently, and never refuse the edit outright.
            if "<" in old:
                print("note: %s had inline emphasis; it was dropped with the "
                      "old wording" % uid)
            content = content.replace(needle, ">" + esc(new) + "<", 1)
        elif u["kind"] == "skills":
            content = _set_skills_value(content, u["_ptext"], new)
        elif u["kind"] == "header":
            if field == "mid":
                if u["mid"]:
                    content = _replace_in_para(content, u["_ptext"], u["mid"], new)
                else:
                    # No tech-stack segment yet: add one after the title rather
                    # than failing, so a canon whose headers carry no "|" is
                    # still tailorable.
                    content = _replace_in_para(
                        content, u["_ptext"], u["left"], u["left"] + " " + new)
            elif field in ("left", "right") and args.identity:
                # Employers, project names, schools and dates are facts, not
                # tailoring surface. Only the one-time tokenization may touch them.
                content = _replace_in_para(content, u["_ptext"], u[field], new)
            else:
                sys.exit("apply: header %s - only .mid is writable without "
                         "--identity" % uid)

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

# Where LibreOffice actually lives, in the order a machine is likely to have
# it. A package install (apt, dnf, brew) puts one of the two names on PATH; the
# macOS and Windows installers do not, so their fixed install paths are checked
# next; Flatpak is last because it is the slowest to launch of the three.
SOFFICE_NAMES = ("soffice", "libreoffice")

SOFFICE_PATHS = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

SOFFICE_FLATPAK = ["flatpak", "run",
                   "--command=/app/libreoffice/program/soffice",
                   "org.libreoffice.LibreOffice"]

# Staging root for renders. A module constant so tests can redirect it.
RENDER_CACHE = os.path.join("~", ".cache", "odt_text")


@functools.lru_cache(maxsize=1)
def find_soffice():
    """The argv prefix that runs LibreOffice on this machine, or None."""
    for name in SOFFICE_NAMES:
        found = shutil.which(name)
        if found:
            return [found]
    for path in SOFFICE_PATHS:
        if os.path.exists(path):
            return [path]
    if shutil.which("flatpak") and subprocess.run(
            ["flatpak", "info", "org.libreoffice.LibreOffice"],
            capture_output=True).returncode == 0:
        return SOFFICE_FLATPAK
    return None


def render_unavailable_reason():
    """Why a render could not happen, phrased for a user-facing warning."""
    missing = [] if find_soffice() else ["LibreOffice"]
    missing += [t for t in POPPLER_TOOLS if not shutil.which(t)]
    if missing:
        return "not installed: " + ", ".join(missing)
    return "conversion failed"


def to_pdf(odt):
    """Render to PDF via whatever LibreOffice this machine has, or None.

    The temp dir lives under $HOME, not /tmp: a Flatpak LibreOffice gets a
    private /tmp, so anything staged there is invisible to it and the load
    silently fails. The input is copied in for the same reason.
    """
    soffice = find_soffice()
    if not soffice:
        return None
    base = os.path.expanduser(RENDER_CACHE)
    os.makedirs(base, exist_ok=True)
    out = tempfile.mkdtemp(prefix="render-", dir=base)
    # The staged copy is the caller's file verbatim - for `fill` that is the
    # resume with real identity values in it. Every path that does not hand back
    # a live pdf has to take the directory with it, or that copy outlives the run
    # somewhere no .gitignore reaches.
    ok = False
    try:
        staged = os.path.join(out, os.path.basename(odt))
        shutil.copy(odt, staged)
        try:
            subprocess.run(
                soffice + ["--headless", "--convert-to", "pdf", "--outdir", out, staged],
                check=True, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            return None
        pdf = os.path.splitext(staged)[0] + ".pdf"
        if not os.path.exists(pdf):
            return None
        ok = True
        return pdf
    finally:
        if not ok:
            shutil.rmtree(out, ignore_errors=True)


def _font_key(name):
    return re.sub(r"[\s_'\"-]", "", name).lower()


def document_fonts(path):
    """Font names the document's styles ask for."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("styles.xml").decode("utf-8") + z.read("content.xml").decode("utf-8")
    return {plain(unesc(n)).strip("'\"")
            for n in re.findall(r'style:font-name="([^"]+)"', xml)}


def missing_fonts(path):
    """Fonts the document names that this machine would substitute, per
    fc-match. None when fc-match is not available to ask."""
    if not shutil.which("fc-match"):
        return None
    missing = []
    for font in sorted(document_fonts(path)):
        got = subprocess.run(["fc-match", "-f", "%{family}", font],
                             capture_output=True, text=True).stdout
        if _font_key(font) not in [_font_key(f) for f in got.split(",")]:
            missing.append(font)
    return missing


def substituted_fonts(path, pdf):
    """Fonts in the render that the document never asked for.

    A font the document names but this machine lacks is silently swapped for
    a fallback with different metrics, and every page number measured from
    that render is wrong - a Garamond resume set in Noto Serif runs a page
    long. Comparing what was embedded against what was asked catches it.
    """
    asked = [_font_key(f) for f in document_fonts(path)]
    if not asked:
        return []  # nothing named, so the renderer's default is the font
    out = subprocess.run(["pdffonts", pdf], capture_output=True, text=True).stdout
    subs = set()
    for line in out.splitlines()[2:]:
        if not line.strip():
            continue
        family = line.split()[0].split("+", 1)[-1].split("-")[0]
        key = _font_key(family)
        if key in RENDERER_OWN_FONTS:
            continue
        if not any(a and (key.startswith(a) or a.startswith(key)) for a in asked):
            subs.add(family)
    return sorted(subs)


def _squash(text):
    """Whitespace-free, compatibility-normalized: PDF text may carry
    ligatures and breaks a line wherever it wraps."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def render_layout(odt_path):
    """Render odt_path and measure it, or None if no renderer is available.

    Returns pages, the deepest text edge (`last`, points), every text line in
    reading order with its box and page, the flat text a parser would extract,
    and any substituted fonts. The PDF is deleted before this returns.
    """
    pdf = to_pdf(odt_path)
    if not pdf:
        return None
    try:
        def run(*argv):
            return subprocess.run(list(argv), capture_output=True, text=True).stdout
        info = run("pdfinfo", pdf)
        bbox = run("pdftotext", "-bbox-layout", pdf, "-")
        flat = run("pdftotext", "-layout", pdf, "-")
        subs = substituted_fonts(odt_path, pdf)
    except OSError:
        return None
    finally:
        shutil.rmtree(os.path.dirname(pdf), ignore_errors=True)

    lines = []
    for page_no, page in enumerate(re.findall(r"<page\b.*?</page>", bbox, re.S), 1):
        for attrs, inner in re.findall(r"<line ([^>]*)>(.*?)</line>", page, re.S):
            box = dict((k, float(v)) for k, v in re.findall(r'(\w+)="([\d.]+)"', attrs))
            words = [plain(unesc(w))
                     for w in re.findall(r"<word[^>]*>(.*?)</word>", inner, re.S)]
            box.update(text=" ".join(words), page=page_no)
            lines.append(box)
    pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    return {"pages": pages,
            "last": max([l["yMax"] for l in lines] or [0.0]) or None,
            "lines": lines, "flat": flat, "substitutes": subs}


def bullet_geometry(lines, bullets, right_edge):
    """{uid: (line_count, chars_free)} for each bullet found in the render.

    chars_free is how many more characters fit on the bullet's last line
    before it wraps onto a new one, at that bullet's own average glyph width.
    """
    joined, owner = [], []
    for i, line in enumerate(lines):
        s = _squash(line["text"])
        joined.append(s)
        owner += [i] * len(s)
    joined = "".join(joined)
    geo, pos = {}, 0
    for uid, text in bullets:
        s = _squash(text)
        at = joined.find(s, pos) if s else -1
        if at < 0:
            continue
        pos = at + len(s)
        first, last = owner[at], owner[pos - 1]
        span = lines[first:last + 1]
        per_char = sum(l["xMax"] - l["xMin"] for l in span) / len(s)
        free = int((right_edge - span[-1]["xMax"]) / per_char) if per_char else 0
        geo[uid] = (last - first + 1, max(free, 0))
    return geo


def fill_text(text, mapping):
    """Plain unit text with its placeholders swapped, for matching a render."""
    if not mapping:
        return text
    return TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def load_map(path, content):
    """The identity map if one exists, else None. Values never leave here
    except into a render staged under RENDER_CACHE."""
    if not path or not os.path.exists(path):
        return None
    apostrophe = canon_conventions(content)["forms"].get("apostrophe", (None,))[0]
    return read_pii_map(path, apostrophe=apostrophe)


def render_as_submitted(odt_path, mapping):
    """render_layout() of the document as it would be submitted.

    With a mapping, the placeholders are filled in memory and the filled copy
    is staged under RENDER_CACHE only for the length of the render. Measuring
    the tokenized document instead is how a resume that "fit" ran to two pages
    once `fill` swapped short tokens for long real values.
    """
    if not mapping:
        return render_layout(odt_path)
    base = os.path.expanduser(RENDER_CACHE)
    os.makedirs(base, exist_ok=True)
    stage = tempfile.mkdtemp(prefix="fillstage-", dir=base)
    try:
        staged = os.path.join(stage, os.path.basename(odt_path))
        content = fill_content(read_content(odt_path), mapping)[0]
        write_odt(odt_path, staged, content)
        return render_layout(staged)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def page_report(odt_path, mapping):
    """Render as submitted and work out the line budget, or None."""
    layout = render_as_submitted(odt_path, mapping)
    if layout is None:
        return None
    units = extract_units(read_content(odt_path))
    bullets = [(u["id"], fill_text(plain(u["text"]), mapping))
               for u in units if u["kind"] == "bullet"]
    get = _page_layout_getter(odt_path)
    right_edge = ((get("fo:margin-left") or 0.5) + _page_text_width_in(odt_path)) * 72.0
    geo = bullet_geometry(layout["lines"], bullets, right_edge)

    # Line pitch is the commonest step between consecutive lines. Paragraph
    # gaps run larger and side-by-side header halves share a baseline, so the
    # median lands on plain line spacing.
    steps = []
    lines = layout["lines"]
    for a, b in zip(lines, lines[1:]):
        if a["page"] == b["page"] and 0 < b["yMin"] - a["yMin"] < 2 * (a["yMax"] - a["yMin"]):
            steps.append(round(b["yMin"] - a["yMin"], 2))
    pitch = sorted(steps)[len(steps) // 2] if steps else 13.0

    hard = page_bottom_limit(odt_path)
    safe = hard - SAFE_RESERVE_PT
    if layout["pages"] > 1:
        spare = -sum(1 for l in lines if l["page"] > 1)
    else:
        spare = int((safe - (layout["last"] or safe)) // pitch)
    layout.update(geo=geo, order=[u for u, _ in bullets], hard=hard, safe=safe,
                  pitch=pitch, spare=spare, filled=bool(mapping))
    return layout


def unmeasurable(report, path):
    """Why a page report cannot be trusted, or None if it can."""
    if report is None:
        return "PDF conversion unavailable (%s)" % render_unavailable_reason()
    if report["substitutes"]:
        missing = missing_fonts(path)
        return ("rendered with %s, which the document never names - a font it "
                "asks for is not installed here%s, so every page measurement "
                "used the wrong metrics"
                % (", ".join(report["substitutes"]),
                   " (fc-match finds no %s)" % ", ".join(missing) if missing else ""))
    return None


def describe_measurement(report):
    return ("as submitted - placeholders filled in memory from the map, nothing written"
            if report["filled"] else
            "with placeholders - no map found, so real values may run wider")


def cmd_budget(args):
    content = read_content(args.resume)
    report = page_report(args.resume, load_map(args.map, content))
    why = unmeasurable(report, args.resume)
    if why:
        print("FAIL: budget cannot be measured: %s" % why)
        sys.exit(1)
    last, safe, hard = report["last"], report["safe"], report["hard"]
    print("measured: %s" % describe_measurement(report))
    print("pages: %d" % report["pages"])
    print("last content yMax: %.2fpt" % last)
    print("reserve to hard limit (%.0fpt): %.2fpt" % (hard, hard - last))
    print("reserve to safe buffer (%.0fpt): %.2fpt" % (safe, safe - last))
    print("spare lines above the safe buffer: %d (at %.1fpt per line)"
          % (report["spare"], report["pitch"]))
    if report["pages"] == 1 and safe - last > 50:
        print("\nNOTABLE: ~%.0fpt of unused space below the safe buffer - "
              "consider using it (add a bullet, lengthen one, or a small "
              "font-size nudge)." % (safe - last))
    print("\nbullets - lines, then characters free on the last line before it wraps:")
    for uid in report["order"]:
        if uid in report["geo"]:
            n, free = report["geo"][uid]
            print("  %-12s %d  +%d" % (uid, n, free))
        else:
            print("  %-12s ?  (not found in the render)" % uid)


# ---------------------------------------------------------------- fill

# Wide enough to see any bracketed token a canon might actually use. A narrower
# grammar silently ignores [Work_Phone] or [Company-1]: the scanner never reports
# them unmapped, and fill declares success with a live placeholder still in the
# document. read_pii_map validates keys with the same pattern so the two agree.
TOKEN_RE = re.compile(r"\[[A-Za-z0-9][A-Za-z0-9_-]*\]")

# Output shapes .gitignore covers. Filling into anything else would put real
# identity values on a committable path.
FILLED_SUFFIXES = ("_filled", "-filled")


HREF_RE = re.compile(r'xlink:href="([^"]*)"')


def body_tokens(body):
    """Every [Token] in a body: visible text plus hyperlink targets.

    strip_tags drops attribute values, so a token that lives only in an href
    (mailto:[Email]) would otherwise look unmapped-safe and be left unfilled.
    """
    found = set(TOKEN_RE.findall(strip_tags(body)))
    for href in HREF_RE.findall(body):
        found.update(TOKEN_RE.findall(unesc(href)))
    return found


def read_pii_map(path, apostrophe=None):
    """Parse `[Token] = Value` lines into a dict.

    Nothing about the token set is hardcoded: whatever the file declares is
    what gets replaced. Values are never printed, logged, or returned to a
    caller that prints - only token names and counts leave this module.

    `apostrophe`, when the document has a settled one, normalizes the values to
    it. A map is typed by hand in a plain-text editor, so an apostrophe in a
    name would otherwise be whatever that editor produced rather than what the
    rest of the document uses.
    """
    if not os.path.exists(path):
        sys.exit(
            "fill: %s not found.\n"
            "Create it in the working directory, one mapping per line:\n"
            "    [Name] = Your Name\n"
            "    [City] = Your City\n"
            "It is gitignored and is never read by Claude." % path)

    mapping = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                sys.exit("fill: %s line %d is not `[Token] = Value`" % (path, lineno))
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not TOKEN_RE.fullmatch(key):
                sys.exit("fill: %s line %d: key %r is not a [Token]"
                         % (path, lineno, key))
            if not value:
                sys.exit("fill: %s line %d: %s has an empty value"
                         % (path, lineno, key))
            if apostrophe:
                value = value.replace("'", apostrophe).replace("’", apostrophe)
            mapping[key] = value

    if not mapping:
        sys.exit("fill: %s has no mappings" % path)
    return mapping


def fill_content(content, mapping):
    """(filled content, {token: count}, tokens present before filling)."""
    body_start = content.find("<office:body>")
    counts = dict.fromkeys(mapping, 0)

    # Which tokens need a mapping is a question about the source document, so
    # ask it before substituting. Asking afterwards would misread a [Token]
    # that arrived as part of a replacement value as an unfilled placeholder.
    present = body_tokens(content[body_start:])

    # One pass over the body, not one pass per token: substituting in sequence
    # would rescan text already inserted, so a value that happened to contain a
    # [Token] would be replaced again depending on dictionary order.
    def substitute(match):
        token = match.group(0)
        counts[token] += 1
        # esc_attr, not esc: one pass covers text nodes and xlink:href alike, and
        # quote entities resolve correctly in both.
        return esc_attr(mapping[token])

    pattern = re.compile("|".join(re.escape(t) for t in mapping))
    content = content[:body_start] + pattern.sub(substitute, content[body_start:])
    return content, counts, present


def cmd_fill(args):
    out_base = os.path.basename(args.output)
    stem = os.path.splitext(out_base)[0]
    in_private = "private" in os.path.normpath(args.output).split(os.sep)
    if not (stem.endswith(FILLED_SUFFIXES) or in_private):
        sys.exit(
            "fill: refusing to write %s - a filled resume must land on a\n"
            "gitignored path. Name it *_filled.odt or put it under private/."
            % args.output)

    content = read_content(args.resume)
    apostrophe = canon_conventions(content)["forms"].get("apostrophe", (None,))[0]
    mapping = read_pii_map(args.map, apostrophe=apostrophe)

    problems, warnings = [], []
    content, counts, present = fill_content(content, mapping)

    for token in sorted(t for t, n in counts.items() if n == 0):
        warnings.append("%s in %s matched nothing in the document"
                        % (token, args.map))

    for token in sorted(present - set(mapping)):
        problems.append("no mapping for %s" % token)

    filled = {t: n for t, n in counts.items() if n}
    if filled:
        print("filled: " + "  ".join("%s x%d" % (t, n)
                                     for t, n in sorted(filled.items())))
    else:
        print("filled: (nothing)")
    # Lengths only, never a value: how much wider each token became, which is
    # what explains a fill that tips a line over.
    if filled:
        print("width: " + "  ".join("%s %+d" % (t, len(mapping[t]) - len(t))
                                    for t in sorted(filled)))

    if not problems:
        # Staged next to the destination, not moved into place until the page
        # check passes. A rejected fill that still left the file behind invites
        # someone reading only the exit code to submit the bad one.
        staging = args.output + ".partial"
        write_odt(args.resume, staging, content)

        hard = page_bottom_limit(staging)
        safe = hard - SAFE_RESERVE_PT
        try:
            report = render_layout(staging)
            why = unmeasurable(report, staging)
        except Exception as e:
            report, why = None, "PDF conversion failed (%s)" % e
        if why:
            if args.no_page_check:
                warnings.append("%s; page check SKIPPED by --no-page-check - "
                                "nothing verified that the filled resume fits "
                                "one page" % why)
            else:
                problems.append("page check could not run: %s. Fix that, or "
                                "pass --no-page-check to accept an unverified "
                                "fill" % why)
        else:
            pages, last = report["pages"], report["last"]
            print("\npages: %d" % pages)
            print("last content yMax: %.2fpt" % last)
            print("reserve to hard limit (%.0fpt): %.2fpt" % (hard, hard - last))
            print("reserve to safe buffer (%.0fpt): %.2fpt" % (safe, safe - last))
            if pages > 1:
                problems.append("filled resume is %d pages; must be 1" % pages)
            elif last > hard:
                problems.append("filled content overflows bottom margin "
                                "(%.2fpt); real values are longer than the "
                                "tokens they replaced" % last)
            elif last > safe:
                warnings.append("less than 0.25in reserve left (%.2fpt)" % last)

        if problems:
            os.remove(staging)
            problems.append("no file was written")
        else:
            os.replace(staging, args.output)
            print("wrote %s" % args.output)

    for w in warnings:
        print("WARN: %s" % w)
    for p in problems:
        print("FAIL: %s" % p)
    if problems:
        sys.exit(1)
    print("\nOK - no placeholders remain")


# ---------------------------------------------------------------- check

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

    # Voice lint on changed prose only, and against canon's own conventions
    # rather than a house style. Bullets come from the content diff above, not
    # from an id-keyed comparison: ids are positional, so one insert would put
    # every later bullet through the lint as though the user had rewritten it.
    conv = canon_conventions(canon)
    canon_vocab = plain(resume_text(canon)).lower()
    canon_sentences = max([sentence_count(t) for t in bullet_prose(canon)] or [1])

    for t in [plain(b) for _, b in reworded] + [plain(t) for t in came]:
        p, w = lint_prose(t, conv)
        problems += p
        warnings += w
        for phrase in BANNED_PHRASES:
            if phrase in t.lower() and phrase not in canon_vocab:
                problems.append("boilerplate phrase %r in: %s" % (phrase, t[:60]))
        # Canon decides how long a bullet runs. A document of two-sentence
        # bullets is not improved by clipping an edit to one.
        n = sentence_count(t)
        if n > canon_sentences:
            warnings.append("bullet runs %d sentences; canon's longest runs %d: %s"
                            % (n, canon_sentences, t[:60]))

    # Page budget + parse check, measured on the document as it would be
    # submitted: placeholders filled in memory when the map exists. The
    # tokenized file is never what gets sent, and short tokens hide overflow.
    mapping = load_map(args.map, canon)
    why, out_rep, canon_rep = None, None, None
    try:
        out_rep = page_report(args.output, mapping)
        why = unmeasurable(out_rep, args.output)
        if not why:
            canon_rep = page_report(args.canon, mapping)
            why = unmeasurable(canon_rep, args.canon)
    except Exception as e:
        why = "PDF conversion failed (%s)" % e
    if why:
        # Never let the page and parse gates pass quietly: they are the
        # verified half of what check promises.
        if args.no_page_check:
            warnings.append("%s; page and parse checks SKIPPED by --no-page-check "
                            "- this run did not verify one page, bottom margin, "
                            "or text recoverability" % why)
        else:
            problems.append("page and parse checks could not run: %s. Fix that, "
                            "or pass --no-page-check to accept an unverified "
                            "result" % why)
    else:
        pages, last, hard = out_rep["pages"], out_rep["last"], out_rep["hard"]
        print("\n=== page budget (%s) ===" % describe_measurement(out_rep))
        print("  pages %d, last content yMax %.2fpt (margin %.0fpt)"
              % (pages, last, hard))
        if pages > 1:
            problems.append("resume is %d pages; must be 1" % pages)
        elif last > hard:
            problems.append("content overflows bottom margin (%.2fpt)" % last)
        elif last > hard - SAFE_RESERVE_PT:
            warnings.append("less than 0.25in reserve left (%.2fpt)" % last)

        # Line budget: edits may not add lines canon has no room for. This is
        # stricter than the margin check on purpose - it fails at the safe
        # buffer, before a different renderer's metrics can tip the page.
        added = len(out_rep["lines"]) - len(canon_rep["lines"])
        print("  lines %+d vs canon; canon had %d spare above the safe buffer"
              % (added, canon_rep["spare"]))
        if added > 0 and added > max(canon_rep["spare"], 0):
            problems.append("edits add %d line(s); canon has %d spare above the "
                            "safe buffer - trim a changed bullet"
                            % (added, max(canon_rep["spare"], 0)))

        id_of_canon = {u["text"]: uid for uid, u in cu.items() if u["kind"] == "bullet"}
        id_of_out = {u["text"]: uid for uid, u in ou.items() if u["kind"] == "bullet"}
        changed = [(id_of_canon.get(a), id_of_out.get(b)) for a, b in reworded]
        changed += [(None, id_of_out.get(t)) for t in came]
        for old_id, new_id in changed:
            if new_id not in out_rep["geo"]:
                continue
            n, free = out_rep["geo"][new_id]
            before = canon_rep["geo"].get(old_id, (0, 0))[0] if old_id else 0
            print("  %-12s %s -> %d lines, +%d chars free on the last"
                  % (new_id, before or "new", n, free))
            if free < NEAR_WRAP_CHARS:
                warnings.append("%s is %d chars from wrapping to another line; "
                                "any wider renderer or value tips it"
                                % (new_id, free))

        # parse check - what a real parser's text extraction would see
        flat = out_rep["flat"]
        pos = -1
        for h in SECTION_HEADINGS:
            i = flat.find(h)
            if i < 0:
                problems.append("parse check: heading %s not recoverable" % h)
            elif i < pos:
                problems.append("parse check: heading %s out of reading order" % h)
            else:
                pos = i
        squashed = _squash(flat)
        for uid, u in ou.items():
            if u.get("kind") == "bullet":
                # plain(): the PDF renders an apostrophe, not `&apos;`. The
                # probe is filled the same way the render was.
                probe = _squash(fill_text(plain(u["text"]), mapping))[:24]
                if probe and probe not in squashed:
                    problems.append("parse check: bullet %s text not recoverable" % uid)

    # Scan the whole body, not just editable units: the contact block holds
    # [Name]/[Email]/[Phone] and is deliberately not surfaced by extract.
    body = out[out.find("<office:body>"):]
    tokens = sorted(body_tokens(body))
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


def cmd_doctor(args):
    soffice = find_soffice()
    print("LibreOffice: %s" % (" ".join(soffice) if soffice else "NOT FOUND"))
    for tool in POPPLER_TOOLS:
        print("%-12s %s" % (tool + ":", shutil.which(tool) or "NOT FOUND"))
    ok = bool(soffice) and all(shutil.which(t) for t in POPPLER_TOOLS)

    # A renderer that has the tools but not the document's fonts measures the
    # wrong document, so a resume given here gets its fonts checked too.
    # fc-match alone over-reports - styles.xml names fonts no text uses - so
    # the verdict comes from what a real render had to substitute.
    if args.resume:
        missing = missing_fonts(args.resume)
        report = render_layout(args.resume) if ok else None
        if report and report["substitutes"]:
            ok = False
            print("fonts:       SUBSTITUTED with %s; not installed: %s"
                  % (", ".join(report["substitutes"]),
                     ", ".join(missing) if missing else "unknown (no fc-match)"))
        elif report:
            print("fonts:       every font the text uses is installed")
        elif missing:
            print("fonts:       not installed (some may be unused): %s"
                  % ", ".join(missing))

    if ok:
        print("\nOK - check, budget, and fill can verify page count, bottom "
              "margin, and parse recoverability.")
        return
    print("\nIncomplete.\nmatch, extract, apply, and the voice lint still work. "
          "check, budget, and fill fail rather than pass unmeasured; "
          "--no-page-check accepts an unverified result.")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("normalize"); p.add_argument("input")
    p.add_argument("-o", "--output", required=True); p.set_defaults(fn=cmd_normalize)

    p = sub.add_parser("shape"); p.add_argument("resume")
    p.add_argument("--exit-code", action="store_true"); p.set_defaults(fn=cmd_shape)

    p = sub.add_parser("extract"); p.add_argument("resume"); p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("doctor"); p.add_argument("resume", nargs="?")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("match"); p.add_argument("resume"); p.add_argument("jd")
    p.add_argument("--json", action="store_true")
    p.add_argument("--exit-code", action="store_true")
    p.add_argument("--limit", type=int, default=12); p.set_defaults(fn=cmd_match)

    p = sub.add_parser("apply"); p.add_argument("resume"); p.add_argument("edits")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--identity", action="store_true"); p.set_defaults(fn=cmd_apply)

    # --map exists so the commands can be exercised against a throwaway fixture.
    # The real run omits it; a hook denies any command that names the real map.
    p = sub.add_parser("check"); p.add_argument("canon"); p.add_argument("output")
    p.add_argument("--map", default="pii.txt")
    p.add_argument("--no-page-check", action="store_true")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("budget"); p.add_argument("resume")
    p.add_argument("--map", default="pii.txt"); p.set_defaults(fn=cmd_budget)

    p = sub.add_parser("fill"); p.add_argument("resume")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--no-page-check", action="store_true")
    p.add_argument("--map", default="pii.txt"); p.set_defaults(fn=cmd_fill)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
