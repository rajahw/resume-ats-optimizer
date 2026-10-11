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


def fake_layout(pages=1, last=500.0, lines=(), subs=()):
    """What render_layout() returns, minus a renderer."""
    return {"pages": pages, "last": last, "lines": list(lines),
            "flat": "EDUCATION SKILLS EXPERIENCE PROJECTS", "substitutes": list(subs)}


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

    def run_stubbed(self, patch, *args):
        """Run the CLI with one line of monkeypatching applied first."""
        stub = self.path("stub.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport odt_text\n%s\n"
                     "sys.argv = ['odt_text'] + %r\nodt_text.main()\n"
                     % (helpers.SCRIPTS, patch, list(args)))
        return subprocess.run([sys.executable, stub], capture_output=True, text=True)


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
        res = self.run_cli("fill", src, "--map", mp, "-o", self.path("o_filled.odt"),
                           "--no-page-check")
        self.assertNotEqual(res.returncode, 0,
                            "an unmapped token slipped through as OK")
        self.assertIn("[Work_Phone]", res.stdout)


class TestFill(TempCase):
    def test_fills_every_token_including_link_targets(self):
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        out = self.path("o_filled.odt")
        res = self.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
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
        res = self.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        ET.fromstring(m.read_content(out))

    def test_an_ampersand_in_a_url_survives(self):
        src = self.odt()
        values = dict(helpers.FAKE_VALUES)
        values["[ProjectRepo]"] = "https://example.com/?a=1&b=2"
        mp = helpers.write_map(self.path("map.txt"), values)
        out = self.path("o_filled.odt")
        res = self.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
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
        res = self.run_stubbed(
            "odt_text.render_layout = lambda p: %r" % fake_layout(pages=2, last=900.0),
            "fill", src, "--map", mp, "-o", out)
        self.assertNotEqual(res.returncode, 0, "a 2-page fill was reported as OK")
        self.assertFalse(os.path.exists(out),
                         "rejected fill left a filled resume on disk")

    def test_no_renderer_fails_and_writes_nothing(self):
        """An unmeasured fill is how a two-page resume got submitted."""
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        out = self.path("o_filled.odt")
        res = self.run_stubbed("odt_text.render_layout = lambda p: None",
                               "fill", src, "--map", mp, "-o", out)
        self.assertNotEqual(res.returncode, 0, "an unmeasured fill passed")
        self.assertIn("--no-page-check", res.stdout)
        self.assertFalse(os.path.exists(out))

    def test_no_page_check_accepts_an_unmeasured_fill_loudly(self):
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        out = self.path("o_filled.odt")
        res = self.run_stubbed("odt_text.render_layout = lambda p: None",
                               "fill", src, "--map", mp, "-o", out, "--no-page-check")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("SKIPPED", res.stdout)
        self.assertTrue(os.path.exists(out))

    def test_substituted_font_fails_the_fill(self):
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        out = self.path("o_filled.odt")
        res = self.run_stubbed(
            "odt_text.render_layout = lambda p: %r" % fake_layout(subs=["NotoSerif"]),
            "fill", src, "--map", mp, "-o", out)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("NotoSerif", res.stdout)
        self.assertFalse(os.path.exists(out))

    def test_width_report_gives_lengths_never_values(self):
        src = self.odt()
        mp = helpers.write_map(self.path("map.txt"), helpers.FAKE_VALUES)
        res = self.run_cli("fill", src, "--map", mp, "-o", self.path("o_filled.odt"),
                           "--no-page-check")
        self.assertIn("[Name] %+d" % (len("Ada Lovelace") - len("[Name]")), res.stdout)
        for value in helpers.FAKE_VALUES.values():
            self.assertNotIn(value, res.stdout)


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


def line(text, x0, x1, y, page=1):
    return {"text": text, "xMin": x0, "xMax": x1, "yMin": y, "yMax": y + 12, "page": page}


class TestLineBudget(TempCase):
    def test_counts_lines_and_slack_per_bullet(self):
        lines = [line("Alpha beta gamma", 54, 570, 100),
                 line("delta epsilon.", 72, 200, 113),
                 line("Second bullet.", 54, 150, 126)]
        geo = m.bullet_geometry(lines, [("b1", "Alpha beta gamma delta epsilon."),
                                        ("b2", "Second bullet.")], 576.0)
        self.assertEqual(geo["b1"][0], 2)
        self.assertEqual(geo["b2"][0], 1)
        self.assertGreater(geo["b1"][1], 0, "a short last line has room left")

    def test_full_last_line_has_no_slack(self):
        geo = m.bullet_geometry([line("Ends at the margin.", 54, 576, 100)],
                                [("b1", "Ends at the margin.")], 576.0)
        self.assertEqual(geo["b1"], (1, 0))

    def test_ligatures_still_match(self):
        geo = m.bullet_geometry([line("Joined ACME's \ufb01rst team.", 54, 300, 100)],
                                [("b1", "Joined ACME's first team.")], 576.0)
        self.assertIn("b1", geo)


class TestCheckGate(TempCase):
    def _edited(self):
        import json
        src = self.odt()
        epath = self.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"exp1.b1": "Plain bullet carrying no inline markup "
                                   "and a good deal more text than before."}}, fh)
        out = self.path("out.odt")
        self.assertEqual(self.run_cli("apply", src, epath, "-o", out).returncode, 0)
        return src, out

    def test_no_renderer_fails_check(self):
        src, out = self._edited()
        res = self.run_stubbed("odt_text.render_layout = lambda p: None",
                               "check", src, out, "--map", self.path("none.txt"))
        self.assertNotEqual(res.returncode, 0, "an unmeasured check said OK")

    def test_no_page_check_passes_with_a_skip_warning(self):
        src, out = self._edited()
        res = self.run_stubbed("odt_text.render_layout = lambda p: None",
                               "check", src, out, "--map", self.path("none.txt"),
                               "--no-page-check")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("SKIPPED", res.stdout)

    def test_added_line_without_spare_room_fails(self):
        src, out = self._edited()
        # Output is rendered first, then canon. Canon sits at the safe buffer
        # with two lines; the output renders three.
        three = [line("x", 54, 60, 724)] * 3
        two = [line("x", 54, 60, 724)] * 2
        patch = ("_n = iter([%r, %r])\nodt_text.render_layout = lambda p: next(_n)"
                 % (fake_layout(last=736.0, lines=three),
                    fake_layout(last=736.0, lines=two)))
        res = self.run_stubbed(patch, "check", src, out, "--map", self.path("none.txt"))
        self.assertNotEqual(res.returncode, 0, res.stdout)
        self.assertIn("add 1 line", res.stdout)


class TestShape(TempCase):
    def test_a_resume_shaped_document_is_recognized(self):
        sig = m.shape_signals(helpers.CONTENT)
        self.assertEqual(sig["verdict"], "resume")
        self.assertTrue(sig["contact"])


class TestBatch(TempCase):
    def setUp(self):
        super().setUp()
        self.inbox = self.path("inbox.txt")
        self.apps = self.path("applications")

    def split(self, text, date="2026-01-02"):
        with open(self.inbox, "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.run_cli("split", self.inbox, "--into", self.apps, "--date", date)

    def test_split_on_delimiter_lines_only(self):
        posts = m.split_postings("Acme\nRole A\n=====\n\n= = =\nGlobex\n  ========  \n\n")
        self.assertEqual(posts, ["Acme\nRole A\n", "= = =\nGlobex\n"])

    def test_split_writes_folders_and_empties_inbox(self):
        res = self.split("Acme posting\n=====\nGlobex posting\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        folders = m.application_folders(self.apps)
        self.assertEqual([os.path.basename(f) for f in folders],
                         ["2026-01-02_posting-01", "2026-01-02_posting-02"])
        with open(os.path.join(folders[1], "posting.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Globex posting\n")
        self.assertEqual(m.read_state(folders[0]), {"stage": "new", "batch": "2026-01-02"})
        self.assertEqual(os.path.getsize(self.inbox), 0)

    def test_second_split_same_day_does_not_collide(self):
        self.split("One\n")
        self.split("Two\n")
        self.assertEqual(len(m.application_folders(self.apps)), 2)

    def test_empty_inbox_is_refused(self):
        res = self.split("\n=====\n  \n")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(m.application_folders(self.apps), [])

    def test_name_slugs_and_flags_an_earlier_application(self):
        self.split("Old\n", date="2025-06-01")
        old = m.application_folders(self.apps)[0]
        self.run_cli("name", old, "Acme Corp.", "Backend Engineer")
        self.split("New\n")
        new = [f for f in m.application_folders(self.apps) if "posting" in f][0]
        res = self.run_cli("name", new, "ACME corp", "Backend  Engineer")
        self.assertIn("DUPLICATE: already applied to this company and role: "
                      "2025-06-01_acme-corp_backend-engineer", res.stdout)
        named = os.path.join(self.apps, "2026-01-02_acme-corp_backend-engineer")
        self.assertTrue(os.path.isdir(named))
        self.assertEqual(m.read_state(named)["duplicate_of"],
                         ["2025-06-01_acme-corp_backend-engineer"])

    def test_same_role_twice_in_one_batch_gets_a_suffix(self):
        self.split("A\n=====\nB\n")
        a, b = m.application_folders(self.apps)
        self.run_cli("name", a, "Acme", "Dev")
        self.run_cli("name", b, "Acme", "Dev")
        names = sorted(os.path.basename(f) for f in m.application_folders(self.apps))
        self.assertEqual(names, ["2026-01-02_acme_dev", "2026-01-02_acme_dev-2"])

    def test_status_hides_finished_applications_unless_asked(self):
        self.split("A\n=====\nB\n")
        a, b = m.application_folders(self.apps)
        self.run_cli("stage", a, "filled")
        self.run_cli("stage", b, "needs-you", "--note", "fill overflowed")
        res = self.run_cli("status", self.apps)
        self.assertNotIn(a, res.stdout)
        self.assertIn("needs-you  %s  - fill overflowed" % b, res.stdout)
        self.assertIn(a, self.run_cli("status", self.apps, "--batch", "2026-01-02").stdout)

    def test_unknown_stage_is_rejected(self):
        self.split("A\n")
        res = self.run_cli("stage", m.application_folders(self.apps)[0], "done")
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
