"""Synthetic fixtures for the test suite.

Every document here is built from scratch. No test reads the user's real canon,
pii map, or any filled output - the suite has to be runnable by an agent that is
forbidden from touching those.
"""

import importlib.util
import os
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, ".claude", "skills", "resume-ats-optimizer", "scripts")
HOOKS = os.path.join(REPO, "hooks")


def load_odt_text():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import odt_text
    return odt_text


def load_module(path, name):
    """Import a module from a path whose filename is not a valid identifier."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_guard():
    return load_module(os.path.join(HOOKS, "block-pii-access.py"), "guard_mod")


def load_precommit():
    return load_module(os.path.join(HOOKS, "pre-commit.py"), "precommit_mod")


MIMETYPE = "application/vnd.oasis.opendocument.text"

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"\
 manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/"\
 manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""

# US Letter, half-inch margins: text width 7.5in, bottom limit 756pt.
STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles\
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"\
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"\
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"\
 office:version="1.2">
<office:automatic-styles><style:page-layout style:name="pm1">\
<style:page-layout-properties fo:page-width="8.5in" fo:page-height="11in"\
 fo:margin-left="0.5in" fo:margin-right="0.5in" fo:margin-top="0.5in"\
 fo:margin-bottom="0.5in"/></style:page-layout></office:automatic-styles>
</office:document-styles>"""

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'office:version="1.2"'
)

AUTO_STYLES = (
    '<office:automatic-styles>'
    '<style:style style:name="T1" style:family="text">'
    '<style:text-properties fo:font-weight="bold"/></style:style>'
    '<style:style style:name="T2" style:family="text">'
    '<style:text-properties/></style:style>'
    '</office:automatic-styles>'
)

# A resume-shaped body: contact block with tokens (two of them only inside
# hyperlink targets), the four headings, a header with a pipe, a plain bullet,
# and a bullet carrying an inline bold span.
BODY = (
    '<office:body><office:text>'
    '<text:p text:style-name="P1">[Name]</text:p>'
    '<text:p text:style-name="P2">[City], [State], USA</text:p>'
    '<text:p text:style-name="P2">'
    '<text:a xlink:type="simple" xlink:href="mailto:[Email]">[Email]</text:a>'
    ' | [Phone] | '
    '<text:a xlink:type="simple" xlink:href="https://[Website]">[Website]</text:a>'
    '</text:p>'
    '<text:p text:style-name="P3">EDUCATION</text:p>'
    '<text:p text:style-name="P5"><text:span text:style-name="T1">[University]</text:span>'
    '<text:span text:style-name="T2"><text:tab/>[UniCity], [UniState]</text:span></text:p>'
    '<text:p text:style-name="P3">SKILLS</text:p>'
    '<text:p text:style-name="P5">Languages: Python, C++, SQL</text:p>'
    '<text:p text:style-name="P3">EXPERIENCE</text:p>'
    '<text:p text:style-name="P5"><text:span text:style-name="T1">[Company1]</text:span>'
    '<text:span text:style-name="T2"><text:tab/></text:span>'
    '<text:span text:style-name="T1">June 2026 - August 2026</text:span></text:p>'
    '<text:p text:style-name="P8">Quality Assurance Intern</text:p>'
    '<text:list text:style-name="L1">'
    '<text:list-item><text:p text:style-name="P13">'
    'Plain bullet carrying no inline markup.</text:p></text:list-item>'
    '<text:list-item><text:p text:style-name="P13">Reduced testing time by '
    '<text:span text:style-name="T1">&gt;90%</text:span> across the suite.'
    '</text:p></text:list-item>'
    '</text:list>'
    '<text:p text:style-name="P3">PROJECTS</text:p>'
    '<text:p text:style-name="P5"><text:span text:style-name="T1">'
    '<text:a xlink:type="simple" xlink:href="[ProjectRepo]">Sample Project</text:a>'
    ' </text:span><text:span text:style-name="T2">| Python, SQL<text:tab/></text:span>'
    '<text:span text:style-name="T1">March 2026</text:span></text:p>'
    '<text:list text:style-name="L1">'
    '<text:list-item><text:p text:style-name="P13">'
    'Built a thing with a measurable result.</text:p></text:list-item>'
    '</text:list>'
    '</office:text></office:body>'
)

CONTENT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document-content ' + NS + '>' + AUTO_STYLES + BODY +
    '</office:document-content>'
)

# The token set BODY actually uses, including the two that appear only in hrefs.
TOKENS = [
    "[Name]", "[City]", "[State]", "[Email]", "[Phone]", "[Website]",
    "[University]", "[UniCity]", "[UniState]", "[Company1]", "[ProjectRepo]",
]


def make_odt(path, content=None, styles=None):
    """Write a minimal but structurally real .odt and return its path."""
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, MIMETYPE)
        z.writestr("META-INF/manifest.xml", MANIFEST)
        z.writestr("styles.xml", styles if styles is not None else STYLES)
        z.writestr("content.xml", content if content is not None else CONTENT)
    return path


def write_map(path, values):
    """Write a pii-style `[Token] = Value` fixture map of fake values."""
    with open(path, "w", encoding="utf-8") as fh:
        for token, value in values.items():
            fh.write("%s = %s\n" % (token, value))
    return path


FAKE_VALUES = {
    "[Name]": "Ada Lovelace",
    "[City]": "Springfield",
    "[State]": "IL",
    "[Email]": "ada@example.com",
    "[Phone]": "555-0100",
    "[Website]": "example.com/ada",
    "[University]": "Example State University",
    "[UniCity]": "Springfield",
    "[UniState]": "IL",
    "[Company1]": "Initech",
    "[ProjectRepo]": "https://github.com/ada/sample",
}
