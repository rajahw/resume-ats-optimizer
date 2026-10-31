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
  import     convert a Word (.docx/.doc/.rtf) resume into canon_resume.odt
  init-map   write a blank pii.txt for the user to fill in with their real details
  tokenize   swap the real details listed in pii.txt for [Token] placeholders in canon
  fill       replace [Token] placeholders from a local pii.txt into a filled copy (*_Resume.pdf or .odt)
  doctor     report whether this machine can measure pages (and a resume's fonts)
  split      split a pasted inbox of postings into one application folder each,
             under applications/<batch>/; an entry that is only a URL is fetched
             and its posting extracted
  name       rename an application folder to <batch>_<company>_<role>; flag repeats
  stage      record how far an application has got
  status     list applications still in progress, or one batch's

`fill` is the one command that handles real identity values. It reads the map
itself and prints only token names and counts, never a value, so the command can
be run by an agent that must not see what it substitutes.
"""

import argparse
import difflib
import functools
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser

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

# Job-description boilerplate that should never become a keyword. The strong
# phrases end the posting proper wherever they appear; the weak ones only as a
# heading, since "understand the benefits of testing" is a requirement line and
# matching it anywhere silently dropped every term after it.
JD_STOP_STRONG = re.compile(
    r"equal (employment )?opportunity|\beeo\b|affirmative action|accommodation"
    r"|e-verify|drug[- ]free", re.I)
JD_STOP_HEADING = re.compile(
    r"^\W*(benefits|perks|about us|about the company|why join|our mission"
    r"|what we offer)", re.I)
JD_STOP_HEADING_WORDS = 6

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

# Near-miss pairs: a posting term and phrasings a resume may use for it. Data,
# not code, so anyone can add their own field's terms - see scripts/synonyms.json.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "synonyms.json"),
          encoding="utf-8") as _fh:
    SYNONYMS = json.load(_fh)


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
    # A manual line break separates words; dropping it glued them together.
    out = out.replace("<text:line-break/>", " ")
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

# The four headings this tool started with keep their original id prefixes, so
# edit sets written against an existing canon still resolve. Every other section
# is keyed by a slug of its own heading: certifications.b1, volunteer1.head.
LEGACY_PREFIX = {"EDUCATION": "edu", "SKILLS": "skills", "EXPERIENCE": "exp",
                 "PROJECTS": "proj"}

# What a section holds, judged from its heading. Skills-type sections carry
# `Label: values` lines; summary-type ones carry prose paragraphs; everything
# else carries entries - a header line with a tab before its dates, then bullets.
SKILLS_KIND_RE = re.compile(r"skills|competenc|proficienc|technolog|languages|tools", re.I)
SUMMARY_KIND_RE = re.compile(r"summary|profile|objective|highlights|about me", re.I)
HEADING_MAX_WORDS = 5


def heading_of(txt, seen_heading):
    """This standalone paragraph's text if it is a section heading, else None.

    A known section word always counts. Short all-caps text counts too, but
    only once a known heading has been seen: before that it is the contact
    block, where a name is often set in capitals.
    """
    if not txt or "\t" in txt:
        return None
    if txt in SECTION_HEADINGS or GENERIC_SECTION_RE.match(txt):
        return txt
    if (seen_heading and txt == txt.upper() and any(c.isalpha() for c in txt)
            and not any(c.isdigit() for c in txt) and ":" not in txt
            and len(txt.split()) <= HEADING_MAX_WORDS):
        return txt
    return None


HEADER_MAX_WORDS = 30
HEADER_SIDE_WORDS = 8            # what follows the tab: dates, a location
DATE_TAIL_CHARS = 40             # an undelimited date must sit this close to the end
ENTRY_PROSE_WORDS = 8            # a description paragraph, not a job-title line


def split_header(txt):
    """(left, mid, right) if this line heads an entry, else None.

    Two shapes. A tab before the dates or place: `Name | detail<tab>June 2026 -
    Present`. Or the dates at the end with no tab: `Employer, City Title |
    January 2024 - Present`. A long sentence with a stray tab in it is a
    description, not a header.
    """
    if len(txt.split()) > HEADER_MAX_WORDS:
        return None
    left = right = None
    if "\t" in txt:
        left, right = (s.strip() for s in txt.split("\t", 1))
        if len(right.split()) > HEADER_SIDE_WORDS:
            left = right = None
    if left is None:
        m = DATE_RE.search(txt)
        if not m or txt.endswith(".") or len(txt) - m.start() > DATE_TAIL_CHARS:
            return None
        left, right = txt[:m.start()].rstrip(" |,-–—\t"), txt[m.start():].strip()
    mid = ""
    if "|" in left:
        left, mid = left.split("|", 1)
        left, mid = left.strip(), "| " + mid.strip()
    return left, mid, right


def is_entry_prose(txt):
    """A description written as a paragraph rather than a bullet. Job-title
    lines are short and rarely end in a period, and stay uneditable."""
    words = len(txt.split())
    return words >= ENTRY_PROSE_WORDS and (txt.endswith(".") or words >= 2 * ENTRY_PROSE_WORDS)


def section_kind(heading):
    if SKILLS_KIND_RE.search(heading):
        return "skills"
    if SUMMARY_KIND_RE.search(heading):
        return "summary"
    return "entries"


def _heading_blocks(content):
    """(match, heading or None, plain text) for every top-level block of the body."""
    body_start = content.find("<office:body>")
    seen = False
    for m in BLOCK_RE.finditer(content, body_start):
        if m.group(1) is None:
            yield m, None, None
            continue
        txt = strip_tags(m.group(2) or "").strip()
        heading = heading_of(txt, seen)
        seen = seen or bool(heading)
        yield m, heading, txt


def section_headings(content):
    """Canon's own section headings, in document order."""
    return [h for _, h, _ in _heading_blocks(content) if h]


def extract_units(content):
    """Editable text units. Contact block and section headings are never surfaced."""
    units = []
    section = kind = prefix = None
    base = None          # id stem of the entry being read: exp1, volunteer2, edu1
    parent = None        # that entry's header id
    entries = {}         # prefix -> entries seen in that section
    used = set()
    b_i = p_i = 0

    for m, heading, txt in _heading_blocks(content):
        if heading:
            section, kind = heading, section_kind(heading)
            prefix = LEGACY_PREFIX.get(heading.upper()) or slug(heading)
            stem, n = prefix, 2
            while prefix in used:
                prefix, n = "%s-%d" % (stem, n), n + 1
            used.add(prefix)
            base = parent = None
            b_i = p_i = 0
            continue
        if section is None:
            continue  # contact block - never editable
        if txt is not None:
            if not txt:
                continue  # spacer
            inner = m.group(2) or ""
            if kind == "skills":
                # Several `Label: values` lines in one paragraph, split by manual
                # line breaks: each line is its own unit, so editing one category
                # cannot flatten or swallow the others.
                lines = [strip_tags(s).strip() for s in inner.split(LINE_BREAK)]
                labeled = [(k, re.match(r"([^:]+:)\s*(.*)$", t, re.S))
                           for k, t in enumerate(lines) if t]
                if len(lines) > 1 and sum(1 for _, sm in labeled if sm) > 1:
                    for k, sm in labeled:
                        if sm:
                            units.append({
                                "id": prefix + "." + re.sub(r"[^a-z]+", "", sm.group(1).lower()),
                                "kind": "skills", "section": section,
                                "label": sm.group(1), "text": sm.group(2).strip(),
                                "_ptext": txt, "_line": k,
                            })
                    continue
                sm = re.match(r"([^:]+:)\s*(.*)$", txt, re.S)
                if sm:
                    units.append({
                        "id": prefix + "." + re.sub(r"[^a-z]+", "", sm.group(1).lower()),
                        "kind": "skills", "section": section,
                        "label": sm.group(1), "text": sm.group(2).strip(),
                        "_ptext": txt,
                    })
                    continue
            header = split_header(txt) if kind != "summary" else None
            if header:
                left, mid, right = header
                n = entries[prefix] = entries.get(prefix, 0) + 1
                if prefix == "edu":
                    uid, base = "edu.head%d" % n, "edu%d" % n
                else:
                    base = "%s%d" % (prefix, n)
                    uid = base + ".head"
                parent, b_i, p_i = uid, 0, 0
                units.append({
                    "id": uid, "kind": "header", "section": section,
                    "left": left, "mid": mid, "right": right, "_ptext": txt,
                })
                continue
            if kind in ("skills", "summary") or is_entry_prose(txt):
                p_i += 1
                units.append({
                    "id": "%s.p%d" % (base or prefix, p_i), "kind": "prose",
                    "section": section, "text": txt, "_raw": inner,
                })
            # Anything else under an entries heading - a job title line, say -
            # is deliberately not surfaced, so it cannot be tailored.
            continue
        for lm in re.finditer(
            r"<text:list-item>\s*<text:p([^>]*)>(.*?)</text:p>\s*</text:list-item>",
            m.group(4) or "", re.S,
        ):
            if not strip_tags(lm.group(2)).strip():
                continue  # an empty list item is spacing, not a bullet
            b_i += 1
            units.append({
                "id": "%s.b%d" % (base or prefix, b_i),
                "kind": "bullet", "section": section,
                "parent": parent or "",
                "text": strip_tags(lm.group(2)).strip(),
                "_raw": lm.group(2),
            })
    return units


def public_units(units):
    return [{k: v for k, v in u.items() if not k.startswith("_")} for u in units]


def resume_text(content):
    units = extract_units(content)
    parts = []
    for u in units:
        if u["kind"] in ("bullet", "prose"):
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
            if u["kind"] in ("bullet", "prose")]


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
    r"|\b(?:Spring|Summer|Fall|Autumn|Winter)\s+(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|Present|Current)\b",
    re.I,
)

# Section words resumes use generally, not just canon's exact four headings.
# This is what tells "a resume this editor cannot parse" apart from "not a
# resume at all" - the two need different advice, so they need different signals.
_SECTION_WORD = (
    r"(?:(?:work|professional|technical|relevant|additional|clinical|teaching|research"
    r"|leadership|volunteer|military|selected|core|key|career|community)\s+)?"
    r"(?:experience|employment(?:\s+history)?|work\s+history|career\s+history|education"
    r"|skills|projects|certifications?|licen[cs]es?|licensure|credentials|publications"
    r"|presentations|awards|honors|leadership|activities|volunteering|volunteer(?:\s+work)?"
    r"|service|references|summary|objective|profile|coursework|competencies|qualifications"
    r"|languages|training|affiliations|memberships|interests|research|accomplishments"
    r"|achievements|highlights|rotations)")
GENERIC_SECTION_RE = re.compile(
    r"^\s*(?:summary\s+of\s+|highlights\s+of\s+)?" + _SECTION_WORD
    + r"(?:\s*(?:&|and|/|,)\s*" + _SECTION_WORD + r")*\s*:?\s*$",
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
    sections = section_headings(content)

    sig = {
        "sections": sections,
        "units": len(units),
        "bullets": len(bullets),
        "contact": bool(CONTACT_RE.search("\n".join(lines[:8]))),
        "dates": len(DATE_RE.findall(text)),
        "words": len(text.split()),
        "tokens": len(body_tokens(content[content.find("<office:body>"):])),
        "tells": [name for name, rx in OTHER_DOC_TELLS if rx.search(text)],
    }
    # Editable means headed sections with bullets under them. Everything else
    # is context for the question put to the user.
    sig["verdict"] = ("resume" if len(sections) >= 2 and len(bullets) >= 3
                      else "unrecognized")
    if sig["verdict"] == "resume":
        sig["reading"] = ""
    elif len(sections) >= 2:
        sig["reading"] = ("a resume in a layout this editor cannot parse - its "
                          "bullets are not list items")
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
    print("  headings   %s" % (", ".join(sig["sections"]) or "none recognized"))
    print("  editable   %d units, %d bullets%s" % (
        sig["units"], sig["bullets"], "" if sig["units"] else
        "   <- nothing here can be tailored"))
    print("  contact    %s" % ("present" if sig["contact"] else "absent"))
    print("  placeholders %d%s" % (sig["tokens"], "" if sig["tokens"] else
                                   "   <- personal details not yet tokenized"))
    print("  dates      %d" % sig["dates"])
    print("  length     %d words" % sig["words"])
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
        if JD_STOP_STRONG.search(low) or (
                len(low.split()) <= JD_STOP_HEADING_WORDS and JD_STOP_HEADING.match(low)):
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
    # Drop a term that is wholly contained in a longer retained term - as whole
    # words, or "java" vanishes beside "javascript" and "sql" beside "postgresql".
    for t in list(terms):
        if any(t != o and has_term(t, o) for o in terms):
            terms.pop(t, None)
    return terms


def has_term(term, text, stem=False):
    """Is `term` in `text` as whole words? A plain substring test read "java"
    in "javascript" and "go" in "good", hiding real gaps. With stem, only the
    start is anchored, so "test" still finds "testing"."""
    pat = r"(?<![\w+#])" + re.escape(term) + ("" if stem else r"(?![\w+#])")
    return re.search(pat, text) is not None


def classify(terms, text):
    low = text.lower()
    exact, near, missing = [], [], []
    for term, w in sorted(terms.items(), key=lambda kv: -kv[1]):
        if has_term(term, low):
            exact.append((term, w))
            continue
        alts = SYNONYMS.get(term, [])
        hit = next((a for a in alts if has_term(a, low)), None)
        if hit:
            near.append((term, w, hit))
            continue
        # single-word stem match
        base = term.rstrip("s")
        if len(base) > 4 and has_term(base, low, stem=True):
            near.append((term, w, base))
            continue
        # Multi-word term whose words all appear, just not adjacently: the user
        # has the substance and only the JD's exact phrasing is absent, which is
        # a rewording opportunity rather than a gap.
        words = [x for x in term.split() if len(x) > 2]
        if len(words) > 1 and all(has_term(x.rstrip("s"), low, stem=True) for x in words):
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


LINE_BREAK = "<text:line-break/>"


def _set_skills_line(content, ptext, line, label, new):
    """Rewrite one `Label: values` line of a multi-line skills paragraph.

    Everything through the label (and the span carrying it) is kept, and so is
    any padding (tabs, spaces) after the values; only the values change. Other
    lines and the breaks between them are untouched.
    """
    for m in PARA_RE.finditer(content):
        if strip_tags(m.group(2)).strip() != ptext.strip():
            continue
        segs = m.group(2).split(LINE_BREAK)
        seg = segs[line]
        at = seg.find(esc(label))
        if at < 0:
            sys.exit("apply: skills label %r is split across formatting; edit it "
                     "in your editor instead" % label)
        cut = at + len(esc(label))
        close = seg.find("</text:span>", cut)
        if close >= 0 and not strip_tags(seg[cut:close]).strip():
            cut = close + len("</text:span>")
            sep = "" if strip_tags(seg[:cut]).endswith(" ") else " "
        else:
            sep = " "
        trail = TRAIL_FILLER_RE.search(seg[cut:]).group(0)
        segs[line] = seg[:cut] + sep + esc(new) + trail
        return content[: m.start(2)] + LINE_BREAK.join(segs) + content[m.end(2):]
    sys.exit("apply: could not locate skills paragraph %r" % ptext[:40])


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
        if len(spans) == 1 and m.group(2).rstrip().startswith(spans[0][0]):
            # Label span followed by the value as bare text (no value spans).
            label_open, label_txt = spans[0]
            tail = m.group(2)[m.group(2).index("</text:span>") + len("</text:span>"):]
            if strip_tags(tail).strip():
                return (content[: m.start(2)] + label_open + label_txt
                        + "</text:span>" + esc(new) + content[m.end(2):])
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


# Whitespace markup at either end of a unit: tabs, spaces, and manual line
# breaks that a Word-made document uses as blank lines around a paragraph.
EDGE_FILLER = r'(?:\s|<text:(?:tab|line-break|s(?: text:c="\d+")?)/>)*'
LEAD_FILLER_RE = re.compile("^" + EDGE_FILLER)
TRAIL_FILLER_RE = re.compile(EDGE_FILLER + "$")


def split_edges(raw):
    """(leading spacing, text, trailing spacing) of a unit's raw XML."""
    lead = LEAD_FILLER_RE.match(raw).group(0)
    rest = raw[len(lead):]
    trail = TRAIL_FILLER_RE.search(rest).group(0)
    return lead, rest[:len(rest) - len(trail)], trail


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
        if u["kind"] in ("bullet", "prose"):
            old = u["_raw"]
            needle = ">" + old + "<"
            if content.count(needle) != 1:
                sys.exit("apply: bullet %s text matches %d places, need exactly 1"
                         % (uid, content.count(needle)))
            # Spacing around the text (blank lines made of tabs and breaks)
            # stays; only the text between is replaced. Inline spans within it
            # (bolded metrics, say) described the old wording and cannot survive
            # a rewrite - say which unit lost them rather than dropping them
            # silently, and never refuse the edit outright.
            lead, core, trail = split_edges(old)
            if "<" in core:
                print("note: %s had inline emphasis; it was dropped with the "
                      "old wording" % uid)
            content = content.replace(needle, ">" + lead + esc(new) + trail + "<", 1)
        elif u["kind"] == "skills" and u.get("_line") is not None:
            content = _set_skills_line(content, u["_ptext"], u["_line"], u["label"], new)
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

    # {"<Heading>": [entry names]}; reorder_projects is the older spelling.
    reorders = dict(edits.get("reorder", {}))
    if edits.get("reorder_projects"):
        reorders.setdefault("PROJECTS", edits["reorder_projects"])
    for heading, order in reorders.items():
        content = _reorder_entries(content, heading, order)

    write_odt(args.resume, args.output, content)
    print("wrote %s" % args.output)


# A self-closing spacer paragraph, which travels with the entry it follows.
SPACER_RE = re.compile(r"<text:p[^>]*/>")


def _reorder_entries(content, heading, order):
    """Reorder the entries of one section, which runs from its heading to the next.

    An entry runs from its header line (the one with a tab before its dates) to
    the next header: job-title lines, bullets, and spacers move with it. The
    last entry ends after its last text or list, so whatever closes the section
    stays put.
    """
    start = end = None
    heads = []     # (name, header start)
    last_end = None
    for m, h, txt in _heading_blocks(content):
        if h:
            if start is not None:
                end = m.start()
                break
            if h.lower() == heading.lower():
                start = m.end()
            continue
        if start is None:
            continue
        if txt and "\t" in txt:
            heads.append((txt.split("|")[0].split("\t")[0].strip(), m.start()))
        if txt or txt is None:
            last_end = m.end()
    if start is None:
        sys.exit("apply: section %s not found" % heading)
    if not heads:
        sys.exit("apply: section %s has no entries to reorder" % heading)
    spacer = SPACER_RE.match(content, last_end)
    if spacer:
        last_end = spacer.end()

    blocks = [(name, s, heads[i + 1][1] if i + 1 < len(heads) else last_end)
              for i, (name, s) in enumerate(heads)]
    names = [b[0] for b in blocks]
    missing = [o for o in order if o not in names]
    if missing:
        sys.exit("apply: reorder names not found under %s: %s"
                 % (heading, ", ".join(missing)))
    if sorted(order) != sorted(names):
        sys.exit("apply: reorder must list every %s entry exactly once" % heading)

    lo, hi = blocks[0][1], blocks[-1][2]
    by_name = {n: content[s:e] for n, s, e in blocks}
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

# Staging root for renders, and home of the canon geometry cache. A module
# constant so tests can redirect it; ODT_TEXT_CACHE does the same for a subprocess.
RENDER_CACHE = os.environ.get("ODT_TEXT_CACHE") or os.path.join("~", ".cache", "odt_text")

# A LibreOffice profile of its own. Without one, a LibreOffice already open on
# the desktop (editing canon, say) captures the conversion - soffice hands a
# second launch to the running instance, which can silently produce nothing.
# Persistent, so only the first render pays for initializing it.
SOFFICE_PROFILE = os.path.join("~", ".cache", "odt_text", "lo-profile")


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
    """Render to PDF via whatever LibreOffice this machine has, or None."""
    return soffice_convert(odt, "pdf")


def soffice_convert(src, fmt):
    """Convert src to `fmt` (pdf, odt) with LibreOffice; the new file's path, or None.

    The temp dir lives under $HOME, not /tmp: a Flatpak LibreOffice gets a
    private /tmp, so anything staged there is invisible to it and the load
    silently fails. The input is copied in for the same reason. The caller
    removes the returned file's directory.
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
        staged = os.path.join(out, os.path.basename(src))
        shutil.copy(src, staged)
        profile = pathlib.Path(os.path.expanduser(SOFFICE_PROFILE)).resolve().as_uri()
        try:
            subprocess.run(
                soffice + ["-env:UserInstallation=" + profile, "--headless",
                           "--convert-to", fmt, "--outdir", out, staged],
                check=True, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            return None
        pdf = os.path.splitext(staged)[0] + "." + fmt
        if not os.path.exists(pdf) or pdf == staged:
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


def render_layout(odt_path, keep_pdf=None):
    """Render odt_path and measure it, or None if no renderer is available.

    Returns pages, the deepest text edge (`last`, points), every text line in
    reading order with its box and page, the flat text a parser would extract,
    and any substituted fonts. The PDF is deleted before this returns, unless
    keep_pdf names where to move it: fill exports the very render it measured
    rather than paying for a second one.
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
        if keep_pdf:
            shutil.move(pdf, keep_pdf)
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
    # The deepest text on the final page: on a multi-page document, page one's
    # bottom says nothing about whether the last page still has room.
    final = [l["yMax"] for l in lines if l["page"] == pages] or [l["yMax"] for l in lines]
    return {"pages": pages,
            "last": max(final or [0.0]) or None,
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


def page_report(odt_path, mapping, max_pages=None):
    """Render as submitted and work out the line budget, or None.

    max_pages is the length the document may run - canon's own page count. It
    defaults to this document's, which is what measuring canon itself means.
    """
    layout = render_as_submitted(odt_path, mapping)
    if layout is None:
        return None
    units = extract_units(read_content(odt_path))
    bullets = [(u["id"], fill_text(plain(u["text"]), mapping))
               for u in units if u["kind"] in ("bullet", "prose")]
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
    limit = max_pages or layout["pages"]
    if layout["pages"] > limit:
        spare = -sum(1 for l in lines if l["page"] > limit)
    else:
        spare = int((safe - (layout["last"] or safe)) // pitch)
    layout.update(geo=geo, order=[u for u, _ in bullets], hard=hard, safe=safe,
                  pitch=pitch, spare=spare, filled=bool(mapping), n_lines=len(lines))
    return layout


# Canon does not change during a batch, so check reuses its geometry instead of
# rendering it again on every call. Only geometry is stored - never line text,
# which holds real values when the render was filled from the map. The key is
# the file and map as they stand, so editing either one invalidates it.
GEOMETRY_CACHE_VERSION = 2


def _geometry_key(path, map_path):
    def sig(p):
        try:
            st = os.stat(p)
        except (OSError, TypeError):
            return None
        return [os.path.abspath(p), st.st_mtime_ns, st.st_size]
    return [GEOMETRY_CACHE_VERSION, sig(path), sig(map_path), find_soffice()]


def _geometry_file():
    return os.path.join(os.path.expanduser(RENDER_CACHE), "canon-geometry.json")


def cached_geometry(path, map_path):
    """What check needs of canon's page report, from an earlier render of this
    exact file and map, or None."""
    try:
        with open(_geometry_file(), encoding="utf-8") as fh:
            entry = json.load(fh)
        if entry["key"] != _geometry_key(path, map_path):
            return None
        return {"pages": entry["pages"], "n_lines": entry["n_lines"],
                "spare": entry["spare"],
                "geo": {k: tuple(v) for k, v in entry["geo"].items()},
                "substitutes": []}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def store_geometry(path, map_path, report):
    if report is None or report["substitutes"]:
        return
    entry = {"key": _geometry_key(path, map_path), "pages": report["pages"],
             "n_lines": report["n_lines"],
             "spare": report["spare"], "geo": report["geo"]}
    target = _geometry_file()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
        os.replace(target + ".tmp", target)
    except OSError:
        pass


def canon_geometry(path, map_path, mapping):
    """Canon's page report, from the cache when canon and the map are unchanged."""
    report = cached_geometry(path, map_path)
    if report is None:
        report = page_report(path, mapping)
        if not unmeasurable(report, path):
            store_geometry(path, map_path, report)
    return report


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
    store_geometry(args.resume, args.map, report)
    last, safe, hard = report["last"], report["safe"], report["hard"]
    print("measured: %s" % describe_measurement(report))
    print("pages: %d (tailored versions may not run longer)" % report["pages"])
    print("last content yMax: %.2fpt" % last)
    print("reserve to hard limit (%.0fpt): %.2fpt" % (hard, hard - last))
    print("reserve to safe buffer (%.0fpt): %.2fpt" % (safe, safe - last))
    print("spare lines above the safe buffer: %d (at %.1fpt per line)"
          % (report["spare"], report["pitch"]))
    if safe - last > 50:
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
FILLED_SUFFIXES = ("_filled", "-filled", "_Resume")


def is_filled_name(stem):
    """The output names .gitignore covers: <PREFIX>_Resume, a bare Resume, or
    the older _filled/-filled shapes."""
    return stem == "Resume" or stem.endswith(FILLED_SUFFIXES)


HREF_RE = re.compile(r'xlink:href="([^"]*)"')


ENCODED_TOKEN_RE = re.compile(r"(?:\.{1,2}/)*%5B([A-Za-z0-9][A-Za-z0-9_-]*)%5D", re.I)


def normalize_hrefs(content):
    """Rewrite hyperlink targets so a [Token] in one reads as a [Token].

    LibreOffice saves a link target as a URL: brackets become %5B/%5D and a
    target with no scheme gains a relative prefix (../%5BRepo%5D). Neither form
    matches the token grammar, so fill would leave the link dead. Only the
    in-memory copy is normalized; the tokenized .odt is never touched.
    """
    def fix(match):
        return 'xlink:href="%s"' % ENCODED_TOKEN_RE.sub(
            lambda m: "[%s]" % m.group(1), match.group(1))
    return HREF_RE.sub(fix, content)


def body_tokens(body):
    """Every [Token] in a body: visible text plus hyperlink targets.

    strip_tags drops attribute values, so a token that lives only in an href
    (mailto:[Email]) would otherwise look unmapped-safe and be left unfilled.
    """
    found = set(TOKEN_RE.findall(strip_tags(body)))
    for href in HREF_RE.findall(normalize_hrefs(body)):
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
                continue  # left blank: not on this resume
            if apostrophe:
                value = value.replace("'", apostrophe).replace("’", apostrophe)
            mapping[key] = value

    if not mapping:
        sys.exit("fill: %s has no mappings" % path)
    return mapping


def fill_content(content, mapping):
    """(filled content, {token: count}, tokens present before filling)."""
    content = normalize_hrefs(content)
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
    if not (is_filled_name(stem) or in_private):
        sys.exit(
            "fill: refusing to write %s - a filled resume must land on a\n"
            "gitignored path. Name it <Name>_Resume.pdf or put it under private/."
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
        # A .pdf destination is exported from the filled document, which only
        # ever exists as this staging file and is removed before returning.
        as_pdf = args.output.lower().endswith(".pdf")
        staging = (os.path.splitext(args.output)[0] + ".partial.odt" if as_pdf
                   else args.output + ".partial")
        rendered = os.path.splitext(staging)[0] + ".pdf" if as_pdf else None
        write_odt(args.resume, staging, content)

        hard = page_bottom_limit(staging)
        safe = hard - SAFE_RESERVE_PT
        try:
            report = render_layout(staging, keep_pdf=rendered)
            why = unmeasurable(report, staging)
        except Exception as e:
            report, why = None, "PDF conversion failed (%s)" % e
        if why:
            if args.no_page_check:
                warnings.append("%s; page check SKIPPED by --no-page-check - "
                                "nothing verified that the filled resume stays "
                                "within canon's length" % why)
            else:
                problems.append("page check could not run: %s. Fix that, or "
                                "pass --no-page-check to accept an unverified "
                                "fill" % why)
        else:
            pages, last = report["pages"], report["last"]
            limit = 1
            if os.path.exists(args.canon):
                canon_rep = canon_geometry(args.canon, args.map, mapping)
                if unmeasurable(canon_rep, args.canon):
                    problems.append("canon could not be measured, so its page "
                                    "count is unknown: %s"
                                    % unmeasurable(canon_rep, args.canon))
                else:
                    limit = canon_rep["pages"]
            print("\npages: %d (canon: %d)" % (pages, limit))
            print("last content yMax: %.2fpt" % last)
            print("reserve to hard limit (%.0fpt): %.2fpt" % (hard, hard - last))
            print("reserve to safe buffer (%.0fpt): %.2fpt" % (safe, safe - last))
            if pages > limit:
                problems.append("filled resume is %d pages; canon is %d" % (pages, limit))
            elif last > hard:
                problems.append("filled content overflows bottom margin "
                                "(%.2fpt); real values are longer than the "
                                "tokens they replaced" % last)
            elif last > safe:
                warnings.append("less than 0.25in reserve left (%.2fpt)" % last)

        if as_pdf and not problems and os.path.exists(rendered):
            os.replace(rendered, args.output)
            os.remove(staging)
            print("wrote %s" % args.output)
        elif as_pdf and not problems:
            # Only reached unmeasured, under --no-page-check.
            pdf = to_pdf(staging)
            if not pdf:
                problems.append("PDF export failed: LibreOffice is required "
                                "to write a PDF")
            else:
                try:
                    shutil.move(pdf, args.output)
                finally:
                    shutil.rmtree(os.path.dirname(pdf), ignore_errors=True)
                os.remove(staging)
                print("wrote %s" % args.output)
        elif problems:
            pass
        else:
            os.replace(staging, args.output)
            print("wrote %s" % args.output)
        if problems:
            for leftover in (staging, rendered):
                if leftover and os.path.exists(leftover):
                    os.remove(leftover)
            problems.append("no file was written")

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

    canon_units, out_units = extract_units(canon), extract_units(out)
    cu = {u["id"]: u for u in canon_units}
    ou = {u["id"]: u for u in out_units}

    # Diff by content, not by id: bullet ids are positional, so one insert
    # shifts every later id and an id-keyed diff reports moves as rewrites.
    ca = [u["text"] for u in canon_units if u["kind"] == "bullet"]
    ob = [u["text"] for u in out_units if u["kind"] == "bullet"]
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

    ha, hb = by_name(canon_units), by_name(out_units)
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

    for sec in dict.fromkeys(u["section"] for u in canon_units if u["kind"] == "header"):
        order_a = [u["left"] for u in canon_units
                   if u["kind"] == "header" and u["section"] == sec]
        order_b = [u["left"] for u in out_units
                   if u["kind"] == "header" and u["section"] == sec]
        if order_a != order_b and sorted(order_a) == sorted(order_b):
            changed_other = True
            print("  ~ %s reordered: %s -> %s"
                  % (sec.lower(), " | ".join(order_a), " | ".join(order_b)))

    prose_changed = []
    for uid in sorted(set(cu) | set(ou)):
        a, b = cu.get(uid, {}), ou.get(uid, {})
        if (a.get("kind") not in ("skills", "prose")
                and b.get("kind") not in ("skills", "prose")):
            continue
        if a.get("text") != b.get("text"):
            if b.get("kind") == "prose":
                prose_changed.append(b["text"])
            changed_other = True
            before, after = len((a.get("text") or "").split()), len((b.get("text") or "").split())
            if before and after < before / 2:
                warnings.append("%s lost most of its text (%d words -> %d); confirm "
                                "nothing was dropped by mistake" % (uid, before, after))
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

    for t in ([plain(b) for _, b in reworded] + [plain(t) for t in came]
              + [plain(t) for t in prose_changed]):
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
            canon_rep = canon_geometry(args.canon, args.map, mapping)
            why = unmeasurable(canon_rep, args.canon)
    except Exception as e:
        why = "PDF conversion failed (%s)" % e
    if why:
        # Never let the page and parse gates pass quietly: they are the
        # verified half of what check promises.
        if args.no_page_check:
            warnings.append("%s; page and parse checks SKIPPED by --no-page-check "
                            "- this run did not verify page count, bottom margin, "
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
        if pages > canon_rep["pages"]:
            problems.append("resume is %d pages; canon is %d"
                            % (pages, canon_rep["pages"]))
        elif last > hard:
            problems.append("content overflows bottom margin (%.2fpt)" % last)
        elif last > hard - SAFE_RESERVE_PT:
            warnings.append("less than 0.25in reserve left (%.2fpt)" % last)

        # Line budget: edits may not add lines canon has no room for. This is
        # stricter than the margin check on purpose - it fails at the safe
        # buffer, before a different renderer's metrics can tip the page.
        added = out_rep["n_lines"] - canon_rep["n_lines"]
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
        squashed = _squash(flat)

        def find_heading(h, start):
            # As written first; a style may render it in other case.
            i = squashed.find(_squash(h), start)
            return i if i >= 0 else squashed.lower().find(_squash(h).lower(), start)

        pos = -1
        for h in section_headings(canon):
            i = find_heading(h, pos + 1)
            if i >= 0:
                pos = i
            elif find_heading(h, 0) >= 0:
                problems.append("parse check: heading %s out of reading order" % h)
            else:
                problems.append("parse check: heading %s not recoverable" % h)
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


# ---------------------------------------------------------------- batch
#
# A batch is a set of postings tailored in one sitting. Each posting becomes an
# application folder under applications/, and a state.json there records how far
# it has got, so an interrupted batch resumes instead of starting over.

POSTING_DELIM_RE = re.compile(r"^[ \t]*={5,}[ \t]*$", re.M)
POSTING_FILE = "posting.txt"
STATE_FILE = "state.json"
STAGES = ("new", "analyzed", "consulted", "applied", "filled", "skipped", "needs-you")
DONE_STAGES = ("filled", "skipped")


def split_postings(text):
    """Postings separated by a line of five or more '='. Blank chunks drop."""
    return [c.strip() + "\n" for c in POSTING_DELIM_RE.split(text) if c.strip()]


def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def read_state(folder):
    with open(os.path.join(folder, STATE_FILE), encoding="utf-8") as fh:
        return json.load(fh)


def write_state(folder, state):
    path = os.path.join(folder, STATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(path + ".tmp", path)


BATCH_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def application_folders(root):
    """Every application under root: applications/<batch>/<folder>, or a
    folder directly under root from before batches got their own directory."""
    if not os.path.isdir(root):
        return []
    found = []
    for d in os.listdir(root):
        path = os.path.join(root, d)
        if os.path.isfile(os.path.join(path, STATE_FILE)):
            found.append(path)
        elif BATCH_DIR_RE.match(d) and os.path.isdir(path):
            found += [os.path.join(path, s) for s in os.listdir(path)
                      if os.path.isfile(os.path.join(path, s, STATE_FILE))]
    return sorted(found)


def applications_root(folder):
    """The applications/ directory a folder belongs to, batch directory or not."""
    parent = os.path.dirname(os.path.normpath(folder)) or "."
    if BATCH_DIR_RE.match(os.path.basename(parent)):
        return os.path.dirname(parent) or "."
    return parent


# ---------------------------------------------------------------- fetching
#
# An inbox entry that is only a URL is fetched here, outside the model, so the
# posting's text goes straight to disk and never through an agent's context.
# Only what a logged-out visitor sees is read: no cookies, no login. A page that
# yields no recognizable posting fails rather than saving whatever text it had,
# because navigation and login-wall junk would quietly mislead `match`.

URL_CHUNK_RE = re.compile(r"\A\s*https?://\S+\s*\Z")
FETCH_TIMEOUT_S = 15
FETCH_PAUSE_S = 1.0              # between fetches, so a batch is not a burst
USER_AGENT = "resume-ats-optimizer (personal use)"
MIN_POSTING_WORDS = 80
LOGIN_PATH_RE = re.compile(r"/(authwall|login|signin|uas/login|checkpoint)\b", re.I)

# Job-board markup for pages that carry no JSON-LD: LinkedIn's logged-out job
# page, then Greenhouse's job-boards page. First match wins.
MARKUP_DESCRIPTION = ("show-more-less-html__markup", "description__text",
                      "job__description")
MARKUP_TITLE = ("top-card-layout__title", "topcard__title", "job__title")
MARKUP_COMPANY = ("topcard__org-name-link", "topcard__flavor")

BLOCK_TAGS = {"p", "div", "br", "ul", "ol", "li", "tr", "section", "article",
              "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
VOID_TAGS = {"br", "img", "hr", "meta", "input", "link", "source", "wbr", "col"}


class FetchError(Exception):
    """A URL that did not yield a posting. The message is the short reason."""


class _HtmlScan(HTMLParser):
    """Collects every JSON-LD block, and the text of the first element whose
    class names each of `targets`. List items become '- ' lines."""

    def __init__(self, targets=()):
        super().__init__(convert_charrefs=True)
        self.targets = targets
        self.found = {}
        self.ld_json = []
        self._open = []          # [target, depth] for each capture in progress
        self._raw = None         # parts of the JSON-LD script being read
        self._in_raw = False

    def _emit(self, s):
        for target, _ in self._open:
            self.found[target].append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            ld = tag == "script" and (a.get("type") or "").lower() == "application/ld+json"
            self._raw = [] if ld else None
            self._in_raw = True
            return
        if tag not in VOID_TAGS:
            for cap in self._open:
                cap[1] += 1
        if tag == "li":
            self._emit("\n- ")
        elif tag in BLOCK_TAGS:
            self._emit("\n")
        classes = (a.get("class") or "").split()
        for target in self.targets:
            if target not in self.found and target in classes and tag not in VOID_TAGS:
                self.found[target] = []
                self._open.append([target, 1])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            if self._raw is not None:
                self.ld_json.append("".join(self._raw))
            self._raw = None
            self._in_raw = False
            return
        if tag in VOID_TAGS:
            return
        if tag in BLOCK_TAGS:
            self._emit("\n")
        for cap in list(self._open):
            cap[1] -= 1
            if cap[1] == 0:
                self._open.remove(cap)

    def handle_data(self, data):
        if self._in_raw:
            if self._raw is not None:
                self._raw.append(data)
            return
        self._emit(data)

    def text(self, target):
        return tidy_text("".join(self.found.get(target, ()))) or None


def tidy_text(text):
    """Collapse runs of spaces, fold a bare '-' onto the line after it, and drop
    blank lines: block tags nest, so the breaks they leave carry no meaning."""
    out = []
    pending_bullet = False
    for line in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if line == "-":
            pending_bullet = True
            continue
        if not line:
            continue
        if pending_bullet and not line.startswith("- "):
            line = "- " + line
        pending_bullet = False
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(fragment):
    scan = _HtmlScan(("_root",))
    scan.feed('<div class="_root">%s</div>' % fragment)
    scan.close()
    return scan.text("_root") or ""


def _job_posting_ld(blocks):
    """The first schema.org JobPosting among a page's JSON-LD blocks."""
    def walk(node):
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        elif isinstance(node, dict):
            kind = node.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return node
            return walk(node.get("@graph"))
        return None

    for block in blocks:
        try:
            found = walk(json.loads(block))
        except ValueError:
            continue
        if found:
            return found
    return None


def _first(scan, classes):
    """First line of the first class found: a title block may also hold a location."""
    for c in classes:
        text = scan.text(c)
        if text:
            return text.split("\n")[0]
    return None


def normalize_url(url):
    """LinkedIn job links come in many shapes (search pages, slugs, tracking
    parameters); all of them name one job id, whose logged-out page is
    /jobs/view/<id>/. Every other URL is returned unchanged."""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        job = (urllib.parse.parse_qs(parts.query).get("currentJobId") or [""])[0]
        if not job.isdigit():
            m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", parts.path)
            job = m.group(1) if m else ""
        if job.isdigit():
            return "https://www.linkedin.com/jobs/view/%s/" % job
    return url


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            final = resp.geturl()
            ctype = resp.headers.get("Content-Type", "")
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError("HTTP %d" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise FetchError("unreachable (%s)" % getattr(e, "reason", e))
    if LOGIN_PATH_RE.search(urllib.parse.urlsplit(final).path):
        raise FetchError("login wall")
    if ctype and "html" not in ctype.lower():
        raise FetchError("not a web page (%s)" % ctype)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def extract_posting(page):
    """A posting's text from its page: title, company, then the description."""
    scan = _HtmlScan(MARKUP_DESCRIPTION + MARKUP_TITLE + MARKUP_COMPANY)
    scan.feed(page)
    scan.close()

    job = _job_posting_ld(scan.ld_json)
    if job:
        title = job.get("title")
        org = job.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else org
        desc = job.get("description") or ""
        if "<" not in desc and "&lt;" in desc:
            desc = html.unescape(desc)
        # Some boards (Lever) escape line breaks twice, leaving a literal \n.
        desc = desc.replace("\\n", "\n")
        desc = html_to_text(desc)
    else:
        title = _first(scan, MARKUP_TITLE)
        company = _first(scan, MARKUP_COMPANY)
        desc = next((t for t in map(scan.text, MARKUP_DESCRIPTION) if t), "")

    if not desc:
        raise FetchError("no posting found on the page")
    words = len(desc.split())
    if words < MIN_POSTING_WORDS:
        raise FetchError("too short (%d words) - likely not the full posting" % words)
    head = "\n".join(str(x).strip() for x in (title, company)
                     if isinstance(x, str) and x.strip())
    return (head + "\n\n" if head else "") + desc + "\n"


def fetch_posting(url):
    return extract_posting(fetch_html(url))


# ---------------------------------------------------------------- trimming
#
# Postings usually end in boilerplate - equal-opportunity and accommodation
# notices, benefits, the company pitch - that costs tokens to read and holds no
# keyword. split cuts it, but only where that is safe: at a boilerplate line in
# the back half, with no requirements heading after it. Eligibility lines in the
# cut tail (citizenship, clearance, export control, sponsorship) are kept, since
# the disqualifier scan needs them. The untrimmed text is saved beside it.

FULL_POSTING_FILE = "posting.full.txt"
TRIM_MARKER = "[Kept from trimmed tail]"
TRIM_HEADING_WORDS = 10          # a line this short can be a heading

# Boilerplate that opens a tail even mid-paragraph.
TAIL_STRONG_RE = re.compile(
    r"equal (employment )?opportunity|\beeo\b|affirmative action|accommodation"
    r"|e-verify|drug[- ]free|pay transparency|applicant privacy|privacy notice"
    r"|work authori[sz]ation|sponsorship", re.I)
# Boilerplate that opens a tail only as a heading: as words they turn up anywhere.
TAIL_WEAK_RE = re.compile(
    r"benefits|perks|about us|about the company|our mission|our values|why join"
    r"|life at|what we offer", re.I)
# A heading that means the posting proper is not over yet.
POSTING_SECTION_RE = re.compile(
    r"^\s*(requirement|qualification|what you|must have|you have|preferred"
    r"|nice to have|bonus|responsibilit|the role|about the role)", re.I)
ELIGIBILITY_RE = re.compile(
    r"citizen|clearance|public trust|polygraph|\bitar\b|export control"
    r"|sponsor|visa\b|authori[sz]ed to work|work authori[sz]ation"
    r"|permanent resident|green card", re.I)
# How an EEO sentence lists citizenship among protected classes - not a requirement.
NON_DISCRIMINATION_RE = re.compile(
    r"without regard|regardless of|on the basis of|discriminat", re.I)


def _is_heading(line):
    return 0 < len(line.split()) <= TRIM_HEADING_WORDS


def trim_tail(text):
    """(trimmed text, words cut). Returns the text unchanged when no safe cut exists."""
    lines = text.splitlines()
    total = len(text.split())
    seen = 0
    cut = None
    for i, line in enumerate(lines):
        if seen >= total / 2 and (TAIL_STRONG_RE.search(line)
                                  or (_is_heading(line) and TAIL_WEAK_RE.search(line))):
            if not any(_is_heading(l) and POSTING_SECTION_RE.match(l) for l in lines[i + 1:]):
                cut = i
                break
        seen += len(line.split())
    if cut is None:
        return text, 0

    tail = lines[cut:]
    kept = [l.strip() for l in tail
            if ELIGIBILITY_RE.search(l) and not NON_DISCRIMINATION_RE.search(l)]
    head = "\n".join(lines[:cut]).rstrip()
    if kept:
        head += "\n\n%s\n%s" % (TRIM_MARKER, "\n".join(kept))
    removed = sum(len(l.split()) for l in tail) - sum(len(l.split()) for l in kept)
    return head + "\n", removed


def cmd_split(args):
    if not os.path.isfile(args.inbox):
        sys.exit("split: %s not found. Paste postings into it, separated by a "
                 "line of =====." % args.inbox)
    with open(args.inbox, encoding="utf-8") as fh:
        postings = split_postings(fh.read())
    if not postings:
        sys.exit("split: %s holds no postings." % args.inbox)

    batch_dir = os.path.join(args.into, args.date)
    os.makedirs(batch_dir, exist_ok=True)
    taken = set(os.listdir(batch_dir))
    failed = []
    written = n = 0
    fetched = False
    for chunk in postings:
        source = None
        text = chunk
        if not args.no_fetch and URL_CHUNK_RE.match(chunk):
            source = normalize_url(chunk.strip())
            if fetched:
                time.sleep(FETCH_PAUSE_S)
            fetched = True
            try:
                text = fetch_posting(source)
            except FetchError as e:
                failed.append(chunk)
                print("FETCH FAILED: %s - %s" % (chunk.strip(), e))
                continue
        n += 1
        while "%s_posting-%02d" % (args.date, n) in taken:
            n += 1
        folder = os.path.join(batch_dir, "%s_posting-%02d" % (args.date, n))
        os.makedirs(folder)
        trimmed, cut = (text, 0) if args.no_trim else trim_tail(text)
        if cut:
            with open(os.path.join(folder, FULL_POSTING_FILE), "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(folder, POSTING_FILE), "w", encoding="utf-8") as fh:
            fh.write(trimmed)
        state = {"stage": "new", "batch": args.date}
        if source:
            state["source"] = source
        write_state(folder, state)
        written += 1
        print(folder + ("  (trimmed %d words of boilerplate)" % cut if cut else ""))

    # A chunk leaves the inbox only once it is on disk: a leftover inbox would be
    # split a second time on the next run. What could not be fetched stays, so
    # its text can be pasted in place of the URL.
    with open(args.inbox, "w", encoding="utf-8") as fh:
        fh.write("=====\n".join(failed))
    if not failed:
        print("\n%d posting(s) split; %s emptied" % (written, args.inbox))
        return
    msg = ("%d URL(s) left in %s - paste each posting's text in place of its URL, "
           "then split again" % (len(failed), args.inbox))
    if not written:
        sys.exit("split: no posting written; " + msg)
    print("\n%d posting(s) split; %s" % (written, msg))


def cmd_name(args):
    folder = os.path.normpath(args.folder)
    root = os.path.dirname(folder) or "."
    state = read_state(folder)
    key = "%s_%s" % (slug(args.company), slug(args.role))

    # Search every batch, not just this one's directory, and see past the -N
    # suffix a same-batch repeat gets - otherwise a third application to the
    # same role, or one in a later batch, is never flagged.
    def same_role(f):
        part = os.path.basename(f).split("_", 1)[-1]
        return part == key or re.sub(r"-\d+$", "", part) == key

    earlier = [os.path.basename(f) for f in application_folders(applications_root(folder))
               if os.path.normpath(f) != folder and same_role(f)]

    target = os.path.join(root, "%s_%s" % (state["batch"], key))
    n = 2
    while os.path.exists(target) and os.path.normpath(target) != folder:
        target = os.path.join(root, "%s_%s-%d" % (state["batch"], key, n))
        n += 1
    if os.path.normpath(target) != folder:
        os.rename(folder, target)

    if earlier:
        state["duplicate_of"] = earlier
        write_state(target, state)
        print("DUPLICATE: already applied to this company and role: %s"
              % ", ".join(earlier))
    print(target)


def cmd_stage(args):
    state = read_state(args.folder)
    state["stage"] = args.stage
    if args.note:
        state["note"] = args.note
    else:
        state.pop("note", None)
    write_state(args.folder, state)
    print("%s: %s" % (args.folder, args.stage))


def cmd_status(args):
    rows = []
    for folder in application_folders(args.root):
        state = read_state(folder)
        if args.batch and state.get("batch") != args.batch:
            continue
        if not (args.all or args.batch) and state["stage"] in DONE_STAGES:
            continue
        rows.append((state["stage"], folder, state.get("note", "")))
    if not rows:
        print("no %sapplications" % ("" if args.all or args.batch else "open "))
        return
    for stage, folder, note in rows:
        print("%-10s %s%s" % (stage, folder, "  - " + note if note else ""))


# ---------------------------------------------------------------- onboarding
#
# Getting a new user from "my resume is a Word file" to a tokenized canon
# without editing XML or the document by hand. Placeholders are mandatory: the
# skill never extracts a canon that still holds real details, so the guarantee
# that Claude never sees them holds from the first run.

IMPORTABLE = (".docx", ".doc", ".rtf", ".odt")
BACKUP_DIR = "private"           # gitignored and hook-protected: holds real values


def cmd_import(args):
    ext = os.path.splitext(args.input)[1].lower()
    if ext not in IMPORTABLE:
        sys.exit("import: %s is not a resume file this can read (%s). From Google "
                 "Docs, use File > Download > Microsoft Word (.docx)."
                 % (args.input, ", ".join(IMPORTABLE)))
    if not os.path.isfile(args.input):
        sys.exit("import: %s not found" % args.input)
    if os.path.exists(args.output):
        sys.exit("import: %s already exists. Move it aside first if you mean to "
                 "replace your canon." % args.output)
    if ext == ".odt":
        shutil.copy(args.input, args.output)
    else:
        converted = soffice_convert(args.input, "odt")
        if not converted:
            sys.exit("import: LibreOffice could not convert %s (%s)"
                     % (args.input, render_unavailable_reason()))
        try:
            shutil.move(converted, args.output)
        finally:
            shutil.rmtree(os.path.dirname(converted), ignore_errors=True)
    print("imported %s -> %s\n" % (args.input, args.output))
    cmd_shape(argparse.Namespace(resume=args.output, exit_code=False))


MAP_TEMPLATE = """\
# Your real personal details. This file stays on your computer: it is never
# committed to git, and Claude is blocked from opening it.
#
# After each "=", type the detail EXACTLY as it appears on your resume - same
# spelling, capitals, and punctuation. Leave a line blank if your resume does
# not have it. Add a line for anything else personal, such as every employer,
# school, or personal link, with a new name in square brackets: [Company4] = ...
#
[Name] =
[Email] =
[Phone] =
[City] =
[State] =
[Website] =
[School] =
[Company1] =
[Company2] =
[Company3] =
"""


def cmd_init_map(args):
    if os.path.exists(args.map):
        sys.exit("init-map: %s already exists; open it in a text editor to change it."
                 % args.map)
    with open(args.map, "w", encoding="utf-8") as fh:
        fh.write(MAP_TEMPLATE)
    print("wrote a blank %s. Open it in a text editor, fill in your details, "
          "save it, then run tokenize." % args.map)


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[ .-]?\d{3}[ .-]\d{4}\b")
SHORT_VALUE_CHARS = 3            # a value this short can match ordinary text


def _value_re(value):
    return re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")


def _swap_outside_tokens(text, rx, token):
    """Replace rx with token in text, never inside an existing [Token]."""
    out, n = [], 0
    for piece in re.split("(" + TOKEN_RE.pattern + ")", text):
        if TOKEN_RE.fullmatch(piece):
            out.append(piece)
            continue
        piece, k = rx.subn(token, piece)
        out.append(piece)
        n += k
    return "".join(out), n


def tokenize_content(content, mapping):
    """(content, {token: count}): each value swapped for its token.

    Only text nodes and hyperlink targets are touched, longest value first so a
    full name goes before a first name. A value split across formatting runs is
    left alone here and reported by the caller's leftover check.
    """
    body_start = content.find("<office:body>")
    head, body = content[:body_start], content[body_start:]
    counts = dict.fromkeys(mapping, 0)
    for token, value in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
        text_rx, attr_rx = _value_re(esc(value)), _value_re(esc_attr(value))
        parts = []
        for piece in XML_TOKEN_RE.findall(body):
            if not piece.startswith("<"):
                piece, n = _swap_outside_tokens(piece, text_rx, token)
                counts[token] += n
            elif "xlink:href=" in piece:
                def fix(m):
                    target, n = _swap_outside_tokens(m.group(1), attr_rx, token)
                    target2, n2 = _swap_outside_tokens(target, _value_re(esc(value)), token)
                    counts[token] += n + n2
                    return 'xlink:href="%s"' % target2
                piece = HREF_RE.sub(fix, piece)
            parts.append(piece)
        body = "".join(parts)
    return head + body, counts


def leftovers(content, mapping):
    """Tokens whose value is still readable in the document, and kinds of contact
    detail (email, phone) present that no token covers. Never a value."""
    body = content[content.find("<office:body>"):]
    text = strip_tags(body) + "\n" + " ".join(unesc(h) for h in HREF_RE.findall(body))
    bare = TOKEN_RE.sub(" ", text)
    still = sorted(t for t, v in mapping.items()
                   if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", bare, re.I))
    kinds = [k for k, rx in (("an email address", EMAIL_RE), ("a phone number", PHONE_RE))
             if rx.search(bare)]
    return still, kinds


def cmd_tokenize(args):
    content = read_content(args.resume)
    apostrophe = canon_conventions(content)["forms"].get("apostrophe", (None,))[0]
    mapping = read_pii_map(args.map, apostrophe=apostrophe)
    new, counts = tokenize_content(content, mapping)

    done = {t: n for t, n in counts.items() if n}
    print("tokenized: " + ("  ".join("%s x%d" % (t, n) for t, n in sorted(done.items()))
                           or "(nothing)"))
    warnings, problems = [], []
    for t in sorted(t for t, n in counts.items() if not n):
        if t not in body_tokens(content[content.find("<office:body>"):]):
            warnings.append("%s matched nothing - check it is typed exactly as on "
                            "the resume" % t)
    for t, n in sorted(done.items()):
        if len(mapping[t]) <= SHORT_VALUE_CHARS and n > 1:
            warnings.append("%s is a short value and was replaced %d times; open "
                            "the resume and check every %s belongs there" % (t, n, t))

    still, kinds = leftovers(new, mapping)
    for t in still:
        problems.append("%s is still in the document where formatting splits it "
                        "(part bold, say). Retype that text in your editor, save, "
                        "and run tokenize again" % t)
    for k in kinds:
        problems.append("%s is still in the document but not in %s - add it there "
                        "with its own [Token] and run tokenize again" % (k, args.map))

    if done:
        backup = os.path.join(BACKUP_DIR, os.path.basename(args.resume)
                              .replace(".odt", ".orig.odt"))
        if not os.path.exists(backup):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            shutil.copy(args.resume, backup)
            print("original kept at %s" % backup)
        write_odt(args.resume, args.resume, new)
        print("wrote %s" % args.resume)

    for w in warnings:
        print("WARN: %s" % w)
    for p in problems:
        print("FAIL: %s" % p)
    if problems:
        sys.exit(1)
    if not body_tokens(new[new.find("<office:body>"):]):
        sys.exit("FAIL: no placeholders in the document - fill in %s first" % args.map)
    print("\nOK - no listed detail remains readable")


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


# How to install each missing tool, per platform. Printed as-is by `doctor`,
# so the skill can relay a command instead of describing one.
INSTALL_HINTS = {
    "darwin": {"LibreOffice": "brew install --cask libreoffice",
               "poppler": "brew install poppler"},
    "linux": {"LibreOffice": "sudo apt install libreoffice-writer   "
                             "(Fedora: sudo dnf install libreoffice-writer)",
              "poppler": "sudo apt install poppler-utils   "
                         "(Fedora: sudo dnf install poppler-utils)"},
    "win32": {"LibreOffice": "winget install --id TheDocumentFoundation.LibreOffice --exact",
              "poppler": "winget install --id oschwartz10612.Poppler --exact   "
                         "(then open a new terminal; if doctor still cannot find "
                         "pdfinfo, add Poppler's bin folder to PATH)"},
}


def install_hint(tool):
    hints = INSTALL_HINTS.get(sys.platform) or INSTALL_HINTS.get(
        "linux" if sys.platform.startswith("linux") else "", {})
    return hints.get(tool)


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
    wanted = ([] if soffice else ["LibreOffice"]) + (
        [] if all(shutil.which(t) for t in POPPLER_TOOLS) else ["poppler"])
    for tool in wanted:
        hint = install_hint(tool)
        if hint:
            print("install %s: %s" % (tool, hint))
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

    p = sub.add_parser("import"); p.add_argument("input")
    p.add_argument("-o", "--output", default="canon_resume.odt")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("init-map"); p.add_argument("--map", default="pii.txt")
    p.set_defaults(fn=cmd_init_map)

    p = sub.add_parser("tokenize"); p.add_argument("resume")
    p.add_argument("--map", default="pii.txt"); p.set_defaults(fn=cmd_tokenize)

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
    p.add_argument("--canon", default="canon_resume.odt",
                   help="the resume whose page count the filled one may not exceed")
    p.add_argument("--map", default="pii.txt"); p.set_defaults(fn=cmd_fill)

    p = sub.add_parser("split"); p.add_argument("inbox", nargs="?",
                                                default="postings/inbox.txt")
    p.add_argument("--into", default="applications")
    p.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    p.add_argument("--no-fetch", action="store_true",
                   help="keep URL-only entries as literal text")
    p.add_argument("--no-trim", action="store_true",
                   help="keep the boilerplate tail of every posting")
    p.set_defaults(fn=cmd_split)

    p = sub.add_parser("name"); p.add_argument("folder")
    p.add_argument("company"); p.add_argument("role"); p.set_defaults(fn=cmd_name)

    p = sub.add_parser("stage"); p.add_argument("folder")
    p.add_argument("stage", choices=STAGES); p.add_argument("--note")
    p.set_defaults(fn=cmd_stage)

    p = sub.add_parser("status"); p.add_argument("root", nargs="?",
                                                 default="applications")
    p.add_argument("--batch"); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
