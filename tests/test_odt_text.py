"""Tests for odt_text.py.

Run with: python3 -m unittest discover tests
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers

m = helpers.load_odt_text()
SCRIPT = os.path.join(helpers.SCRIPTS, "odt_text.py")


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="odt-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def odt(self, name="canon.odt", content=None, styles=None):
        return helpers.make_odt(self.path(name), content, styles)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True)


class TestNormalize(TempCase):
    def test_tokenizer_is_not_shadowed(self):
        """_tokens must split XML, not hunt for [Token] placeholders."""
        toks = m._tokens('<text:p text:style-name="P1">A<text:tab/>B</text:p>')
        self.assertIn("<text:tab/>", toks)
        self.assertIn("A", toks)

    def test_normalize_actually_restyles_a_paragraph(self):
        """The whole point of normalize: a faked-alignment line gets the tab stop."""
        out = m.normalize_content(helpers.CONTENT, 7.5)
        self.assertIn('style:name="%s"' % m.RIGHT_TAB_STYLE, out,
                      "style definition was not injected")
        self.assertIn('text:style-name="%s"' % m.RIGHT_TAB_STYLE, out,
                      "no paragraph was restyled - normalize did nothing")

    def test_normalize_is_idempotent(self):
        once = m.normalize_content(helpers.CONTENT, 7.5)
        twice = m.normalize_content(once, 7.5)
        self.assertEqual(once, twice)

    def test_style_guard_is_not_a_bare_substring(self):
        """A document containing the letters PRA must still get the style def."""
        content = helpers.CONTENT.replace(
            "<text:p text:style-name=\"P3\">EDUCATION</text:p>",
            "<text:p text:style-name=\"P3\">PRACTICUM</text:p>")
        out = m.normalize_content(content, 7.5)
        self.assertIn('style:name="%s"' % m.RIGHT_TAB_STYLE, out)
        self.assertIn('<style:tab-stop', out)


class TestExtract(TempCase):
    def test_finds_the_expected_units(self):
        units = {u["id"]: u for u in m.extract_units(helpers.CONTENT)}
        self.assertIn("exp1.b1", units)
        self.assertIn("exp1.b2", units)
        self.assertIn("proj1.b1", units)
        self.assertEqual(units["proj1.head"]["left"], "Sample Project")
        self.assertEqual(units["proj1.head"]["mid"], "| Python, SQL")
        self.assertEqual(units["skills.languages"]["text"], "Python, C++, SQL")

    def test_bullet_text_survives_inline_markup(self):
        units = {u["id"]: u for u in m.extract_units(helpers.CONTENT)}
        self.assertEqual(units["exp1.b2"]["text"],
                         "Reduced testing time by >90% across the suite.")


class TestApply(TempCase):
    def _apply(self, edits, src=None):
        import json
        src = src or self.odt()
        epath = self.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump(edits, fh)
        out = self.path("out.odt")
        return self.run_cli("apply", src, epath, "-o", out), out

    def test_replaces_a_plain_bullet(self):
        res, out = self._apply({"replace": {"exp1.b1": "A rewritten plain bullet."}})
        self.assertEqual(res.returncode, 0, res.stderr)
        units = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        self.assertEqual(units["exp1.b1"]["text"], "A rewritten plain bullet.")

    def test_replaces_a_bullet_that_contains_markup(self):
        """Bolding a bullet must not make it permanently untailorable."""
        res, out = self._apply({"replace": {"exp1.b2": "Cut regression time sharply."}})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        units = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        self.assertEqual(units["exp1.b2"]["text"], "Cut regression time sharply.")

    def test_markup_replacement_announces_dropped_emphasis(self):
        res, _ = self._apply({"replace": {"exp1.b2": "Cut regression time sharply."}})
        self.assertIn("exp1.b2", res.stdout)
        self.assertIn("emphasis", res.stdout.lower())

    def test_replacement_escapes_markup_in_new_text(self):
        res, out = self._apply({"replace": {"exp1.b1": "Handled <200ms & p99 latency."}})
        self.assertEqual(res.returncode, 0, res.stderr)
        content = m.read_content(out)
        ET.fromstring(content)
        units = {u["id"]: u for u in m.extract_units(content)}
        self.assertEqual(units["exp1.b1"]["text"], "Handled <200ms & p99 latency.")

    def test_header_left_is_not_writable_without_identity(self):
        """Employer/project/school names are identity, not tailoring surface."""
        res, _ = self._apply({"replace": {"proj1.head.left": "Renamed Project"}})
        self.assertNotEqual(res.returncode, 0,
                            "header .left was rewritten without --identity")

    def test_header_mid_can_be_added_when_absent(self):
        res, out = self._apply({"replace": {"exp1.head.mid": "| Python, SQL"}})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        units = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        self.assertEqual(units["exp1.head"]["mid"], "| Python, SQL")


class TestEscaping(TempCase):
    def test_esc_escapes_the_xml_metacharacters(self):
        self.assertEqual(m.esc("a<b>c&d"), "a&lt;b&gt;c&amp;d")

    def test_esc_attr_escapes_quotes(self):
        """Tokens now live inside xlink:href="...", so quotes must be escaped."""
        self.assertNotIn('"', m.esc_attr('a"b'))

    def test_roundtrip(self):
        self.assertEqual(m.unesc(m.esc("a<b>c&d")), "a<b>c&d")


class TestTokens(TempCase):
    def test_finds_tokens_that_exist_only_in_hyperlinks(self):
        found = m.body_tokens(helpers.BODY)
        self.assertIn("[ProjectRepo]", found)
        self.assertIn("[Website]", found)

    def test_accepts_underscores_and_hyphens_and_digits(self):
        for token in ("[Uni_City]", "[Company-1]", "[2ndEmail]"):
            with self.subTest(token=token):
                self.assertTrue(m.TOKEN_RE.fullmatch(token),
                                "%s is invisible to the placeholder scanner" % token)

    def test_unusual_token_is_reported_as_unmapped(self):
        content = helpers.CONTENT.replace("[Phone]", "[Work_Phone]")
        src = self.odt(content=content)
        values = dict(helpers.FAKE_VALUES)
        values.pop("[Phone]", None)
        mp = helpers.write_map(self.path("map.txt"), values)
        res = self.run_cli("fill", src, "--map", mp, "-o", self.path("o_filled.odt"))
        self.assertNotEqual(res.returncode, 0,
                            "an unmapped token slipped through as OK")
        self.assertIn("[Work_Phone]", res.stdout)


class TestFill(TempCase):
    def test_fills_every_token_including_link_targets(self):
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        out = self.path("o_filled.odt")
        res = self.run_cli("fill", src, "--map", mp, "-o", out)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("[ProjectRepo]", res.stdout)
        content = m.read_content(out)
        self.assertEqual(m.body_tokens(content[content.find("<office:body>"):]), set())

    def test_refuses_a_non_gitignored_output_path(self):
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        res = self.run_cli("fill", src, "--map", mp, "-o", self.path("resume.odt"))
        self.assertNotEqual(res.returncode, 0)

    def test_a_quote_in_a_linked_value_keeps_the_xml_valid(self):
        src = self.odt()
        values = dict(helpers.FAKE_VALUES)
        values["[ProjectRepo]"] = 'https://example.com/a"b'
        mp = helpers.write_map(self.path("map.txt"), values)
        out = self.path("o_filled.odt")
        res = self.run_cli("fill", src, "--map", mp, "-o", out)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        ET.fromstring(m.read_content(out))

    def test_an_ampersand_in_a_url_survives(self):
        src = self.odt()
        values = dict(helpers.FAKE_VALUES)
        values["[ProjectRepo]"] = "https://example.com/?a=1&b=2"
        mp = helpers.write_map(self.path("map.txt"), values)
        out = self.path("o_filled.odt")
        res = self.run_cli("fill", src, "--map", mp, "-o", out)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        root = ET.fromstring(m.read_content(out))
        hrefs = [v for e in root.iter() for k, v in e.attrib.items()
                 if k.endswith("href")]
        self.assertIn("https://example.com/?a=1&b=2", hrefs)

    def test_no_output_is_left_behind_when_the_page_check_fails(self):
        """A rejected fill must not leave a filled resume sitting on disk."""
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        out = self.path("o_filled.odt")
        stub = self.path("stub.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(
                "import sys\n"
                "sys.path.insert(0, %r)\n"
                "import odt_text\n"
                "odt_text.measure_page_budget = lambda p: (2, 900.0, None)\n"
                "sys.argv = ['odt_text', 'fill', %r, '--map', %r, '-o', %r]\n"
                "odt_text.main()\n" % (helpers.SCRIPTS, src, mp, out))
        res = subprocess.run([sys.executable, stub], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "a 2-page fill was reported as OK")
        self.assertFalse(os.path.exists(out),
                         "rejected fill left a filled resume on disk")


class TestRenderCleanup(TempCase):
    def test_failed_render_leaves_no_staging_copy(self):
        """A staging dir holds a copy of the filled resume - it must not linger."""
        cache = self.path("cache")
        src = self.odt()
        original_cache = m.RENDER_CACHE
        original_find = m.find_soffice
        m.RENDER_CACHE = cache
        m.find_soffice = lambda: [sys.executable, "-c", "raise SystemExit(1)"]
        try:
            self.assertIsNone(m.to_pdf(src))
        finally:
            m.RENDER_CACHE = original_cache
            m.find_soffice = original_find
        leftovers = os.listdir(cache) if os.path.isdir(cache) else []
        self.assertEqual(leftovers, [],
                         "staging dir survived a failed render: %s" % leftovers)

    def test_missing_renderer_stages_nothing(self):
        cache = self.path("cache")
        src = self.odt()
        original_cache = m.RENDER_CACHE
        original_find = m.find_soffice
        m.RENDER_CACHE = cache
        m.find_soffice = lambda: None
        try:
            self.assertIsNone(m.to_pdf(src))
        finally:
            m.RENDER_CACHE = original_cache
            m.find_soffice = original_find
        self.assertFalse(os.path.isdir(cache) and os.listdir(cache))


class TestPageGeometry(TempCase):
    def test_reads_the_real_page_layout(self):
        src = self.odt()
        self.assertAlmostEqual(m._page_text_width_in(src), 7.5)
        self.assertAlmostEqual(m.page_bottom_limit(src), 756.0)

    def test_falls_back_when_the_layout_is_silent(self):
        bare = helpers.STYLES.replace(
            '<style:page-layout-properties fo:page-width="8.5in" '
            'fo:page-height="11in" fo:margin-left="0.5in" fo:margin-right="0.5in" '
            'fo:margin-top="0.5in" fo:margin-bottom="0.5in"/>',
            '<style:page-layout-properties/>')
        src = self.odt(styles=bare)
        self.assertAlmostEqual(m._page_text_width_in(src), 7.5)
        self.assertAlmostEqual(m.page_bottom_limit(src), 756.0)

    def test_a4_is_measured_differently(self):
        a4 = helpers.STYLES.replace('fo:page-height="11in"', 'fo:page-height="11.69in"')
        src = self.odt(styles=a4)
        self.assertGreater(m.page_bottom_limit(src), 756.0)


class TestWriteOdt(TempCase):
    def test_mimetype_stays_first_and_stored(self):
        src = self.odt()
        out = self.path("out.odt")
        m.write_odt(src, out, m.read_content(src))
        with zipfile.ZipFile(out) as z:
            infos = z.infolist()
            self.assertEqual(infos[0].filename, "mimetype")
            self.assertEqual(infos[0].compress_type, zipfile.ZIP_STORED)
            self.assertIsNone(z.testzip())


class TestShape(TempCase):
    def test_a_resume_shaped_document_is_recognized(self):
        sig = m.shape_signals(helpers.CONTENT)
        self.assertEqual(sig["verdict"], "resume")
        self.assertTrue(sig["contact"])


if __name__ == "__main__":
    unittest.main()
