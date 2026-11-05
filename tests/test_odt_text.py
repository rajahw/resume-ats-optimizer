"""Tests for odt_text.py.

Run with: python3 -m pytest
"""

import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

import helpers

m = helpers.load_odt_text()


def fake_layout(pages=1, last=500.0, lines=(), subs=()):
    """What render_layout() returns, minus a renderer."""
    return {"pages": pages, "last": last, "lines": list(lines),
            "flat": "EDUCATION SKILLS EXPERIENCE PROJECTS", "substitutes": list(subs)}


class TestNormalize:
    def test_tokenizer_is_not_shadowed(self):
        """_tokens must split XML, not hunt for [Token] placeholders."""
        toks = m._tokens('<text:p text:style-name="P1">A<text:tab/>B</text:p>')
        assert "<text:tab/>" in toks
        assert "A" in toks

    def test_normalize_actually_restyles_a_paragraph(self):
        """The whole point of normalize: a faked-alignment line gets the tab stop."""
        out = m.normalize_content(helpers.CONTENT, 7.5)
        assert 'style:name="%s"' % m.RIGHT_TAB_STYLE in out, \
            "style definition was not injected"
        assert 'text:style-name="%s"' % m.RIGHT_TAB_STYLE in out, \
            "no paragraph was restyled - normalize did nothing"

    def test_normalize_is_idempotent(self):
        once = m.normalize_content(helpers.CONTENT, 7.5)
        twice = m.normalize_content(once, 7.5)
        assert once == twice

    def test_style_guard_is_not_a_bare_substring(self):
        """A document containing the letters PRA must still get the style def."""
        content = helpers.CONTENT.replace(
            "<text:p text:style-name=\"P3\">EDUCATION</text:p>",
            "<text:p text:style-name=\"P3\">PRACTICUM</text:p>")
        out = m.normalize_content(content, 7.5)
        assert 'style:name="%s"' % m.RIGHT_TAB_STYLE in out
        assert '<style:tab-stop' in out


class TestExtract:
    def test_finds_the_expected_units(self):
        units = {u["id"]: u for u in m.extract_units(helpers.CONTENT)}
        assert "exp1.b1" in units
        assert "exp1.b2" in units
        assert "proj1.b1" in units
        assert units["proj1.head"]["left"] == "Sample Project"
        assert units["proj1.head"]["mid"] == "| Python, SQL"
        assert units["skills.languages"]["text"] == "Python, C++, SQL"

    def test_bullet_text_survives_inline_markup(self):
        units = {u["id"]: u for u in m.extract_units(helpers.CONTENT)}
        assert units["exp1.b2"]["text"] == "Reduced testing time by >90% across the suite."


class TestApply:
    def _apply(self, ws, edits, src=None):
        src = src or ws.odt()
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump(edits, fh)
        out = ws.path("out.odt")
        return ws.run_cli("apply", src, epath, "-o", out), out

    def test_replaces_a_plain_bullet(self, ws):
        res, out = self._apply(ws, {"replace": {"exp1.b1": "A rewritten plain bullet."}})
        assert res.returncode == 0, res.stderr
        units = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        assert units["exp1.b1"]["text"] == "A rewritten plain bullet."

    def test_replaces_a_bullet_that_contains_markup(self, ws):
        """Bolding a bullet must not make it permanently untailorable."""
        res, out = self._apply(ws, {"replace": {"exp1.b2": "Cut regression time sharply."}})
        assert res.returncode == 0, res.stdout + res.stderr
        units = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        assert units["exp1.b2"]["text"] == "Cut regression time sharply."

    def test_markup_replacement_announces_dropped_emphasis(self, ws):
        res, _ = self._apply(ws, {"replace": {"exp1.b2": "Cut regression time sharply."}})
        assert "exp1.b2" in res.stdout
        assert "emphasis" in res.stdout.lower()

    def test_replacement_escapes_markup_in_new_text(self, ws):
        res, out = self._apply(ws, {"replace": {"exp1.b1": "Handled <200ms & p99 latency."}})
        assert res.returncode == 0, res.stderr
        content = m.read_content(out)
        ET.fromstring(content)
        units = {u["id"]: u for u in m.extract_units(content)}
        assert units["exp1.b1"]["text"] == "Handled <200ms & p99 latency."

    def test_header_left_is_not_writable_without_identity(self, ws):
        """Employer/project/school names are identity, not tailoring surface."""
        res, _ = self._apply(ws, {"replace": {"proj1.head.left": "Renamed Project"}})
        assert res.returncode != 0, "header .left was rewritten without --identity"

    def test_header_mid_can_be_added_when_absent(self, ws):
        res, out = self._apply(ws, {"replace": {"exp1.head.mid": "| Python, SQL"}})
        assert res.returncode == 0, res.stdout + res.stderr
        units = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        assert units["exp1.head"]["mid"] == "| Python, SQL"


class TestEscaping:
    def test_esc_escapes_the_xml_metacharacters(self):
        assert m.esc("a<b>c&d") == "a&lt;b&gt;c&amp;d"

    def test_esc_attr_escapes_quotes(self):
        """Tokens now live inside xlink:href="...", so quotes must be escaped."""
        assert '"' not in m.esc_attr('a"b')

    def test_roundtrip(self):
        assert m.unesc(m.esc("a<b>c&d")) == "a<b>c&d"


class TestTokens:
    def test_finds_tokens_that_exist_only_in_hyperlinks(self):
        found = m.body_tokens(helpers.BODY)
        assert "[ProjectRepo]" in found
        assert "[Website]" in found

    @pytest.mark.parametrize("token", ["[Uni_City]", "[Company-1]", "[2ndEmail]"])
    def test_accepts_underscores_and_hyphens_and_digits(self, token):
        assert m.TOKEN_RE.fullmatch(token), \
            "%s is invisible to the placeholder scanner" % token

    def test_unusual_token_is_reported_as_unmapped(self, ws):
        content = helpers.CONTENT.replace("[Phone]", "[Work_Phone]")
        src = ws.odt(content=content)
        values = dict(helpers.FAKE_VALUES)
        values.pop("[Phone]", None)
        mp = helpers.write_map(ws.path("map.txt"), values)
        res = ws.run_cli("fill", src, "--map", mp, "-o", ws.path("o_filled.odt"),
                           "--no-page-check")
        assert res.returncode != 0, "an unmapped token slipped through as OK"
        assert "[Work_Phone]" in res.stdout


class TestFill:
    def test_fills_every_token_including_link_targets(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("o_filled.odt")
        res = ws.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "[ProjectRepo]" in res.stdout
        content = m.read_content(out)
        assert m.body_tokens(content[content.find("<office:body>"):]) == set()

    def test_fills_link_targets_the_editor_saved_url_encoded(self, ws):
        """LibreOffice stores [Token] hrefs as %5BToken%5D, relative-prefixed."""
        content = helpers.CONTENT.replace(
            'xlink:href="[ProjectRepo]"', 'xlink:href="../%5BProjectRepo%5D"').replace(
            'xlink:href="mailto:[Email]"', 'xlink:href="mailto:%5BEmail%5D"')
        src = ws.odt(content=content)
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("o_filled.odt")
        res = ws.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "matched nothing" not in res.stdout + res.stderr
        filled = m.read_content(out)
        assert "%5B" not in filled
        assert m.body_tokens(filled[filled.find("<office:body>"):]) == set()

    def test_pdf_output_is_exported_and_leaves_no_filled_odt(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("Ada_Resume.pdf")
        res = ws.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        with open(out, "rb") as f:
            assert f.read(4) == b"%PDF"
        leftovers = [n for n in os.listdir(ws.path(".")) if n.endswith(".partial.odt")]
        assert leftovers == []

    def test_plain_resume_name_is_an_accepted_output(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        res = ws.run_cli("fill", src, "--map", mp, "-o", ws.path("Ada_Resume.odt"),
                           "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr

    def test_refuses_a_non_gitignored_output_path(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        res = ws.run_cli("fill", src, "--map", mp, "-o", ws.path("resume.odt"))
        assert res.returncode != 0

    def test_a_quote_in_a_linked_value_keeps_the_xml_valid(self, ws):
        src = ws.odt()
        values = dict(helpers.FAKE_VALUES)
        values["[ProjectRepo]"] = 'https://example.com/a"b'
        mp = helpers.write_map(ws.path("map.txt"), values)
        out = ws.path("o_filled.odt")
        res = ws.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        ET.fromstring(m.read_content(out))

    def test_an_ampersand_in_a_url_survives(self, ws):
        src = ws.odt()
        values = dict(helpers.FAKE_VALUES)
        values["[ProjectRepo]"] = "https://example.com/?a=1&b=2"
        mp = helpers.write_map(ws.path("map.txt"), values)
        out = ws.path("o_filled.odt")
        res = ws.run_cli("fill", src, "--map", mp, "-o", out, "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        root = ET.fromstring(m.read_content(out))
        hrefs = [v for e in root.iter() for k, v in e.attrib.items()
                 if k.endswith("href")]
        assert "https://example.com/?a=1&b=2" in hrefs

    def test_no_output_is_left_behind_when_the_page_check_fails(self, ws):
        """A rejected fill must not leave a filled resume sitting on disk."""
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("o_filled.odt")
        res = ws.run_stubbed(
            "odt_text.render_layout = lambda p, **k: %r" % fake_layout(pages=2, last=900.0),
            "fill", src, "--map", mp, "-o", out)
        assert res.returncode != 0, "a 2-page fill was reported as OK"
        assert not os.path.exists(out), "rejected fill left a filled resume on disk"

    def test_no_renderer_fails_and_writes_nothing(self, ws):
        """An unmeasured fill is how a two-page resume got submitted."""
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("o_filled.odt")
        res = ws.run_stubbed("odt_text.render_layout = lambda p, **k: None",
                               "fill", src, "--map", mp, "-o", out)
        assert res.returncode != 0, "an unmeasured fill passed"
        assert "--no-page-check" in res.stdout
        assert not os.path.exists(out)

    def test_no_page_check_accepts_an_unmeasured_fill_loudly(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("o_filled.odt")
        res = ws.run_stubbed("odt_text.render_layout = lambda p, **k: None",
                               "fill", src, "--map", mp, "-o", out, "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "SKIPPED" in res.stdout
        assert os.path.exists(out)

    def test_substituted_font_fails_the_fill(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        out = ws.path("o_filled.odt")
        res = ws.run_stubbed(
            "odt_text.render_layout = lambda p, **k: %r" % fake_layout(subs=["NotoSerif"]),
            "fill", src, "--map", mp, "-o", out)
        assert res.returncode != 0
        assert "NotoSerif" in res.stdout
        assert not os.path.exists(out)

    def test_width_report_gives_lengths_never_values(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        res = ws.run_cli("fill", src, "--map", mp, "-o", ws.path("o_filled.odt"),
                           "--no-page-check")
        assert "[Name] %+d" % (len("Ada Lovelace") - len("[Name]")) in res.stdout
        for value in helpers.FAKE_VALUES.values():
            assert value not in res.stdout


class TestRenderCleanup:
    def test_failed_render_leaves_no_staging_copy(self, ws, render_cache, monkeypatch):
        """A staging dir holds a copy of the filled resume - it must not linger."""
        src = ws.odt()
        monkeypatch.setattr(m, "find_soffice",
                            lambda: [sys.executable, "-c", "raise SystemExit(1)"])
        assert m.to_pdf(src) is None
        leftovers = os.listdir(render_cache) if os.path.isdir(render_cache) else []
        assert leftovers == [], "staging dir survived a failed render: %s" % leftovers

    def test_missing_renderer_stages_nothing(self, ws, render_cache, monkeypatch):
        src = ws.odt()
        monkeypatch.setattr(m, "find_soffice", lambda: None)
        assert m.to_pdf(src) is None
        assert not (os.path.isdir(render_cache) and os.listdir(render_cache))


class TestPageGeometry:
    def test_reads_the_real_page_layout(self, ws):
        src = ws.odt()
        assert m._page_text_width_in(src) == pytest.approx(7.5)
        assert m.page_bottom_limit(src) == pytest.approx(756.0)

    def test_falls_back_when_the_layout_is_silent(self, ws):
        bare = helpers.STYLES.replace(
            '<style:page-layout-properties fo:page-width="8.5in" '
            'fo:page-height="11in" fo:margin-left="0.5in" fo:margin-right="0.5in" '
            'fo:margin-top="0.5in" fo:margin-bottom="0.5in"/>',
            '<style:page-layout-properties/>')
        src = ws.odt(styles=bare)
        assert m._page_text_width_in(src) == pytest.approx(7.5)
        assert m.page_bottom_limit(src) == pytest.approx(756.0)

    def test_a4_is_measured_differently(self, ws):
        a4 = helpers.STYLES.replace('fo:page-height="11in"', 'fo:page-height="11.69in"')
        src = ws.odt(styles=a4)
        assert m.page_bottom_limit(src) > 756.0


class TestWriteOdt:
    def test_mimetype_stays_first_and_stored(self, ws):
        src = ws.odt()
        out = ws.path("out.odt")
        m.write_odt(src, out, m.read_content(src))
        with zipfile.ZipFile(out) as z:
            infos = z.infolist()
            assert infos[0].filename == "mimetype"
            assert infos[0].compress_type == zipfile.ZIP_STORED
            assert z.testzip() is None


def line(text, x0, x1, y, page=1):
    return {"text": text, "xMin": x0, "xMax": x1, "yMin": y, "yMax": y + 12, "page": page}


class TestLineBudget:
    def test_counts_lines_and_slack_per_bullet(self):
        lines = [line("Alpha beta gamma", 54, 570, 100),
                 line("delta epsilon.", 72, 200, 113),
                 line("Second bullet.", 54, 150, 126)]
        geo = m.bullet_geometry(lines, [("b1", "Alpha beta gamma delta epsilon."),
                                        ("b2", "Second bullet.")], 576.0)
        assert geo["b1"][0] == 2
        assert geo["b2"][0] == 1
        assert geo["b1"][1] > 0, "a short last line has room left"

    def test_full_last_line_has_no_slack(self):
        geo = m.bullet_geometry([line("Ends at the margin.", 54, 576, 100)],
                                [("b1", "Ends at the margin.")], 576.0)
        assert geo["b1"] == (1, 0)

    def test_ligatures_still_match(self):
        geo = m.bullet_geometry([line("Joined ACME's \ufb01rst team.", 54, 300, 100)],
                                [("b1", "Joined ACME's first team.")], 576.0)
        assert "b1" in geo


class TestCheckGate:
    def _edited(self, ws):
        src = ws.odt()
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"exp1.b1": "Plain bullet carrying no inline markup "
                                   "and a good deal more text than before."}}, fh)
        out = ws.path("out.odt")
        assert ws.run_cli("apply", src, epath, "-o", out).returncode == 0
        return src, out

    def test_no_renderer_fails_check(self, ws):
        src, out = self._edited(ws)
        res = ws.run_stubbed("odt_text.render_layout = lambda p, **k: None",
                               "check", src, out, "--map", ws.path("none.txt"))
        assert res.returncode != 0, "an unmeasured check said OK"

    def test_no_page_check_passes_with_a_skip_warning(self, ws):
        src, out = self._edited(ws)
        res = ws.run_stubbed("odt_text.render_layout = lambda p, **k: None",
                               "check", src, out, "--map", ws.path("none.txt"),
                               "--no-page-check")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "SKIPPED" in res.stdout

    def test_added_line_without_spare_room_fails(self, ws):
        src, out = self._edited(ws)
        # Output is rendered first, then canon. Canon sits at the safe buffer
        # with two lines; the output renders three.
        three = [line("x", 54, 60, 724)] * 3
        two = [line("x", 54, 60, 724)] * 2
        patch = ("_n = iter([%r, %r])\nodt_text.render_layout = lambda p, **k: next(_n)"
                 % (fake_layout(last=736.0, lines=three),
                    fake_layout(last=736.0, lines=two)))
        res = ws.run_stubbed(patch, "check", src, out, "--map", ws.path("none.txt"))
        assert res.returncode != 0, res.stdout
        assert "add 1 line" in res.stdout


class TestMatch:
    def test_a_term_inside_a_longer_word_is_not_a_match(self):
        exact, near, missing = m.classify({"java": 3, "go": 3, "sql": 3},
                                          "javascript, good algorithms, postgresql")
        assert exact == []
        assert sorted(t for t, _ in missing) == ["go", "java", "sql"]

    def test_whole_words_and_symbols_still_match(self):
        exact, _, _ = m.classify({"c++": 3, "node.js": 3, "sql": 3},
                                 "Built in C++ and Node.js against SQL Server")
        assert sorted(t for t, _ in exact) == ["c++", "node.js", "sql"]

    def test_a_stem_still_finds_its_longer_form(self):
        _, near, _ = m.classify({"pipelines": 2}, "built a ci pipeline")
        assert near[0][0] == "pipelines"

    def test_java_survives_beside_javascript(self):
        terms = m.jd_terms("Requirements\n- Java\n- JavaScript\n")
        assert "java" in terms
        assert "javascript" in terms

    def test_benefits_mid_requirements_does_not_end_the_posting(self):
        terms = m.jd_terms("Requirements\n- Understand the benefits of unit tests\n"
                           "- Kubernetes\n")
        assert "kubernetes" in terms

    def test_a_benefits_heading_still_ends_the_posting(self):
        terms = m.jd_terms("Requirements\n- Kubernetes\nBenefits\n- Dental coverage\n")
        assert "kubernetes" in terms
        assert "dental coverage" not in terms


class TestMatchHeadings:
    PITCH = "About Costco\nWe are a family, employee centric atmosphere for everyone.\n"

    @pytest.mark.parametrize("heading, weight", [
        ("Required", 3),
        ("Minimum Qualifications", 3),
        ("Basic Qualifications", 3),
        ("Qualifications:", 3),
        ("You Have", 3),
        ("What You'll Need", 3),
        ("Recommended", 2),
        ("Preferred Qualifications", 2),
        ("Role Responsibilities", 2),
        ("What You'll Do", 2),
        ("Skills", 2),
        ("About Costco", 1),
    ])
    def test_heading_weight(self, heading, weight):
        assert m.jd_heading([heading, "- Kubernetes"], 0) == weight

    @pytest.mark.parametrize("line", [
        "Required to work on-site three days a week.",   # closing period: prose
        "- Required Documents",                          # a bullet, not a heading
        "Requirements are listed in the attached sheet for this role",  # 10 words
    ])
    def test_not_a_heading(self, line):
        assert m.jd_heading([line, "- Kubernetes"], 0) is None

    def test_question_heading_gets_a_longer_limit(self):
        q = "What Are the Requirements You Must Meet?"         # 8 words
        assert m.jd_heading([q, "- Kubernetes"], 0) == 3
        long_q = ("Are you ready to make an impact on millions of patients "
                  "every single day?")                          # 14 words
        assert m.jd_heading([long_q, "- Kubernetes"], 0) is None

    def test_question_heading_words_are_still_terms(self):
        terms = m.jd_terms("Do you have experience with Python or Kotlin?\n- Terraform\n")
        assert terms["kotlin"] == 2      # "you have" promotes only when bare

    def test_trim_marker_is_not_a_heading_and_ranks_lowest(self):
        other = []
        terms = m.jd_terms("Required\n- Kubernetes\n" + m.TRIM_MARKER
                           + "\n- Visa sponsorship\n", other)
        assert other == []
        assert terms["kubernetes"] == 3
        assert terms["visa sponsorship"] == 1

    def test_needs_a_bullet_or_paragraph_after(self):
        assert m.jd_heading(["SQL", "Azure DevOps"], 0) is None

    def test_unrecognized_heading_no_longer_ties_with_pitch(self):
        terms = m.jd_terms(self.PITCH + "Required\n- Ranorex\n")
        assert terms["ranorex"] == 3
        assert terms["family"] == 1

    def test_shape_only_heading_is_listed_and_weighted_1(self):
        other = []
        terms = m.jd_terms("Required\n- Ranorex\n" + self.PITCH, other)
        assert other == ["About Costco"]
        assert terms["family"] == 1

    def test_terms_after_a_heading_colon_are_kept(self):
        terms = m.jd_terms("Requirements: Kubernetes\n- Terraform\n")
        assert terms["kubernetes"] == 3

    def test_any_heading_ends_a_stop_skip(self):
        terms = m.jd_terms("Benefits\n- Dental coverage\nOur Stack\n- Kubernetes\n")
        assert "dental coverage" not in terms
        assert terms["kubernetes"] == 1

    def test_match_prints_other_headings(self, ws):
        jd = ws.path("posting.txt")
        with open(jd, "w", encoding="utf-8") as fh:
            fh.write(self.PITCH + "Required\n- Kubernetes\n")
        res = ws.run_cli("match", ws.odt(), jd)
        assert "OTHER HEADINGS" in res.stdout
        assert "About Costco" in res.stdout


@pytest.mark.usefixtures("render_cache")
class TestCanonGeometryCache:
    def report(self, subs=()):
        return {"pages": 1, "n_lines": 40, "spare": 2, "geo": {"exp1.b1": (2, 10)},
                "substitutes": list(subs), "lines": [{"text": "Ada Lovelace"}]}

    def test_round_trip_keeps_geometry_and_drops_text(self, ws):
        src = ws.odt()
        m.store_geometry(src, None, self.report())
        got = m.cached_geometry(src, None)
        assert got["n_lines"] == 40
        assert got["geo"] == {"exp1.b1": (2, 10)}
        with open(m._geometry_file(), encoding="utf-8") as fh:
            assert "Ada Lovelace" not in fh.read()

    def test_editing_canon_or_the_map_invalidates_it(self, ws):
        src = ws.odt()
        mp = helpers.write_map(ws.path("map.txt"), helpers.FAKE_VALUES)
        m.store_geometry(src, mp, self.report())
        assert m.cached_geometry(src, mp) is not None
        with open(mp, "a", encoding="utf-8") as fh:
            fh.write("# edited\n")
        assert m.cached_geometry(src, mp) is None

    def test_a_substituted_font_render_is_never_cached(self, ws):
        src = ws.odt()
        m.store_geometry(src, None, self.report(subs=["NotoSerif"]))
        assert m.cached_geometry(src, None) is None

    def test_check_renders_canon_once_across_runs(self, ws):
        src = ws.odt()
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"exp1.b1": "Plain bullet text."}}, fh)
        out = ws.path("out.odt")
        ws.run_cli("apply", src, epath, "-o", out)
        counter = ws.path("renders.txt")
        patch = ("def fake(p, **k):\n"
                 "    open(%r, 'a').write(p + '\\n')\n"
                 "    return %r\n"
                 "odt_text.render_layout = fake" % (counter, fake_layout()))
        for _ in range(2):
            ws.run_stubbed(patch, "check", src, out, "--map", ws.path("none.txt"))
        with open(counter, encoding="utf-8") as fh:
            rendered = fh.read().split()
        assert rendered.count(src) == 1, rendered
        assert rendered.count(out) == 2, rendered


class TestAnyLayout:
    def units(self):
        return {u["id"]: u for u in m.extract_units(helpers.NURSE_CONTENT)}

    def test_headings_are_canons_own(self):
        assert m.section_headings(helpers.NURSE_CONTENT) == [
            "Professional Summary", "Professional Experience",
            "LICENSES & CERTIFICATIONS", "Education", "Skills"]

    def test_ids_follow_each_section(self):
        ids = list(self.units())
        assert (ids == [
            "professional-summary.p1",
            "professional-experience1.head", "professional-experience1.b1",
            "professional-experience1.b2",
            "professional-experience2.head", "professional-experience2.b1",
            "licenses-certifications.b1", "licenses-certifications.b2",
            "edu.head1", "skills.p1"])

    def test_bullets_never_leak_into_the_previous_section(self):
        u = self.units()["licenses-certifications.b1"]
        assert u["section"] == "LICENSES & CERTIFICATIONS"
        assert u["parent"] == ""

    def test_the_original_layout_keeps_its_ids(self):
        ids = [u["id"] for u in m.extract_units(helpers.CONTENT)]
        assert ids == ["edu.head1", "skills.languages", "exp1.head",
                       "exp1.b1", "exp1.b2", "proj1.head", "proj1.b1"]

    def test_an_all_caps_name_is_not_a_heading(self):
        content = helpers.NURSE_CONTENT.replace(">[Name]<", ">ADA LOVELACE<")
        assert "ADA LOVELACE" not in m.section_headings(content)

    def test_shape_reads_it_as_a_resume(self):
        sig = m.shape_signals(helpers.NURSE_CONTENT)
        assert sig["verdict"] == "resume", sig
        assert sig["tokens"] > 0

    def test_summary_prose_can_be_replaced(self, ws):
        src = ws.odt(content=helpers.NURSE_CONTENT)
        epath = ws.path("edits.json")
        new = "Registered nurse with six years of acute and telemetry care experience."
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"professional-summary.p1": new}}, fh)
        out = ws.path("out.odt")
        res = ws.run_cli("apply", src, epath, "-o", out)
        assert res.returncode == 0, res.stderr
        got = {u["id"]: u for u in m.extract_units(m.read_content(out))}
        assert got["professional-summary.p1"]["text"] == new

    def test_entries_reorder_within_any_section(self, ws):
        src = ws.odt(content=helpers.NURSE_CONTENT)
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"reorder": {"Professional Experience": ["[Company2]", "[Company1]"]}}, fh)
        out = ws.path("out.odt")
        res = ws.run_cli("apply", src, epath, "-o", out)
        assert res.returncode == 0, res.stderr
        heads = [u["left"] for u in m.extract_units(m.read_content(out))
                 if u["kind"] == "header" and u["section"] == "Professional Experience"]
        assert heads == ["[Company2]", "[Company1]"]
        # The section after it is untouched.
        assert "licenses-certifications.b2" in [
            u["id"] for u in m.extract_units(m.read_content(out))]

    def test_check_parses_canons_own_headings(self, ws):
        src = ws.odt(content=helpers.NURSE_CONTENT)
        flat = ("Professional Summary Professional Experience LICENSES & CERTIFICATIONS "
                "Education Skills")
        layout = fake_layout()
        layout["flat"] = flat + " " + " ".join(
            u["text"] for u in m.extract_units(helpers.NURSE_CONTENT) if u["kind"] == "bullet")
        res = ws.run_stubbed("odt_text.render_layout = lambda p, **k: %r" % layout,
                               "check", src, src, "--map", ws.path("none.txt"))
        assert res.returncode == 0, res.stdout + res.stderr
        assert "parse check" not in res.stdout


class TestWordStyleLayout:
    """How resumes typed in Word or Google Docs tend to be laid out: dates after a
    pipe with no tab, descriptions as paragraphs, manual line breaks, stray tabs."""

    BODY = (
        '<office:body><office:text>'
        '<text:p text:style-name="P1">[Name]</text:p>'
        '<text:p text:style-name="P3">EXPERIENCE</text:p>'
        '<text:p text:style-name="P5">[Company1], [City]<text:line-break/>'
        'Program Manager | January 2024 - Present</text:p>'
        '<text:p text:style-name="P5">Designed and delivered training for forty staff '
        'across three sites.</text:p>'
        '<text:p text:style-name="P5">Led planning<text:tab/>and execution of the annual '
        'conference for five hundred attendees and partners across the region.</text:p>'
        '<text:list text:style-name="L1">'
        '<text:list-item><text:p text:style-name="P13"/></text:list-item>'
        '<text:list-item><text:p text:style-name="P13">Built the program.</text:p>'
        '</text:list-item></text:list>'
        '<text:p text:style-name="P5">[University1] Adjunct Professor | Fall 2016 - Spring 2019'
        '</text:p>'
        '<text:p text:style-name="P8">Culinary Arts Department</text:p>'
        '<text:p text:style-name="P3">SKILLS</text:p>'
        '<text:p text:style-name="P5">Facilitation: circles, mediation</text:p>'
        '</office:text></office:body>'
    )
    CONTENT = ('<?xml version="1.0" encoding="UTF-8"?><office:document-content '
               + helpers.NS + '>' + helpers.AUTO_STYLES + BODY + '</office:document-content>')

    def units(self):
        return {u["id"]: u for u in m.extract_units(self.CONTENT)}

    def test_a_date_after_a_pipe_makes_a_header(self):
        u = self.units()["exp1.head"]
        assert (u["left"], u["right"]) == ("[Company1], [City] Program Manager",
                                           "January 2024 - Present")

    def test_a_season_range_is_a_date(self):
        assert self.units()["exp2.head"]["right"] == "Fall 2016 - Spring 2019"

    def test_line_breaks_separate_words(self):
        assert "[City] Program" in self.units()["exp1.head"]["left"]

    def test_paragraph_descriptions_are_editable_and_titles_are_not(self):
        ids = list(self.units())
        assert "exp1.p1" in ids
        assert "exp1.p2" in ids  # a stray tab does not make it a header
        assert not any("Department" in (u.get("text") or "")
                       for u in self.units().values())

    def test_an_empty_list_item_is_not_a_bullet(self):
        assert self.units()["exp1.b1"]["text"] == "Built the program."

    def test_spacing_around_a_replaced_paragraph_survives(self):
        raw = ('<text:tab/><text:tab/><text:line-break/> Old text here.'
               '<text:line-break/> <text:tab/>')
        assert m.split_edges(raw) == ('<text:tab/><text:tab/><text:line-break/> ',
                                      'Old text here.', '<text:line-break/> <text:tab/>')
        assert m.split_edges("Plain bullet.") == ("", "Plain bullet.", "")


class TestMultiLineSkills:
    SKILLS = ('<text:p text:style-name="P5"><text:span text:style-name="T1">Languages: '
              '</text:span>Python, C++, SQL<text:tab/><text:tab/><text:line-break/> '
              '<text:span text:style-name="T1">Tools &amp; Platforms: </text:span>'
              'Git, Docker<text:line-break/></text:p>')

    def content(self):
        return helpers.CONTENT.replace(
            '<text:p text:style-name="P5">Languages: Python, C++, SQL</text:p>', self.SKILLS)

    def test_each_line_is_its_own_unit(self):
        units = {u["id"]: u for u in m.extract_units(self.content())}
        assert units["skills.languages"]["text"] == "Python, C++, SQL"
        assert units["skills.toolsplatforms"]["text"] == "Git, Docker"

    def test_editing_one_line_keeps_the_others_and_the_breaks(self, ws):
        src = ws.odt(content=self.content())
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"skills.toolsplatforms": "Git, Docker, Jenkins"}}, fh)
        out = ws.path("out.odt")
        res = ws.run_cli("apply", src, epath, "-o", out)
        assert res.returncode == 0, res.stderr
        got = m.read_content(out)
        units = {u["id"]: u for u in m.extract_units(got)}
        assert units["skills.languages"]["text"] == "Python, C++, SQL"
        assert units["skills.toolsplatforms"]["text"] == "Git, Docker, Jenkins"
        assert got.count("<text:line-break/>") == self.SKILLS.count("<text:line-break/>")
        ET.fromstring(got)

    def test_check_warns_when_most_of_a_unit_disappears(self, ws):
        src = ws.odt(content=self.content())
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"skills.languages": "Python"}}, fh)
        out = ws.path("out.odt")
        ws.run_cli("apply", src, epath, "-o", out)
        res = ws.run_stubbed("odt_text.render_layout = lambda p, **k: None", "check",
                               src, out, "--map", ws.path("none.txt"), "--no-page-check")
        assert "skills.languages lost most of its text" in res.stdout


class TestPageLimitIsCanons:
    def _pair(self, ws):
        src = ws.odt()
        epath = ws.path("edits.json")
        with open(epath, "w", encoding="utf-8") as fh:
            json.dump({"replace": {"exp1.b1": "Plain bullet text."}}, fh)
        out = ws.path("out.odt")
        ws.run_cli("apply", src, epath, "-o", out)
        return src, out

    def run_check(self, ws, out_pages, canon_pages):
        src, out = self._pair(ws)
        two = [line("x", 54, 60, 100, page=p) for p in range(1, canon_pages + 1)]
        patch = ("_n = iter([%r, %r])\nodt_text.render_layout = lambda p, **k: next(_n)"
                 % (fake_layout(pages=out_pages, lines=two),
                    fake_layout(pages=canon_pages, lines=two)))
        return ws.run_stubbed(patch, "check", src, out, "--map", ws.path("none.txt"))

    def test_a_two_page_canon_allows_two_pages(self, ws):
        res = self.run_check(ws, 2, 2)
        assert "pages; canon is" not in res.stdout

    def test_running_past_canons_length_fails(self, ws):
        res = self.run_check(ws, 3, 2)
        assert res.returncode != 0
        assert "resume is 3 pages; canon is 2" in res.stdout

    def test_last_is_measured_on_the_final_page(self, ws, monkeypatch):
        lines = [line("a", 54, 60, 700, page=1), line("b", 54, 60, 100, page=2)]
        bbox = "".join(
            '<page>%s</page>' % "".join(
                '<line xMin="%s" yMin="%s" xMax="%s" yMax="%s"><word>%s</word></line>'
                % (l["xMin"], l["yMin"], l["xMax"], l["yMax"], l["text"])
                for l in lines if l["page"] == p) for p in (1, 2))
        pdf_dir = ws.path("pdf")
        os.mkdir(pdf_dir)
        pdf = os.path.join(pdf_dir, "x.pdf")
        open(pdf, "w").close()

        class Out:
            def __init__(self, s): self.stdout = s

        def fake_run(argv, **kw):
            if argv[0] == "pdfinfo":
                return Out("Pages:          2\n")
            if "-bbox-layout" in argv:
                return Out(bbox)
            return Out("")
        src = ws.odt()
        monkeypatch.setattr(m, "to_pdf", lambda p: pdf)
        monkeypatch.setattr(m, "substituted_fonts", lambda o, p: [])
        monkeypatch.setattr(m.subprocess, "run", fake_run)
        layout = m.render_layout(src)
        assert layout["last"] == 112.0


class TestShape:
    def test_a_resume_shaped_document_is_recognized(self):
        sig = m.shape_signals(helpers.CONTENT)
        assert sig["verdict"] == "resume"
        assert sig["contact"]


class TestBatch:
    @pytest.fixture
    def inbox(self, ws):
        return ws.path("inbox.txt")

    @pytest.fixture
    def apps(self, ws):
        return ws.path("applications")

    @pytest.fixture
    def split(self, ws, inbox, apps):
        def split(text, date="2026-01-02"):
            with open(inbox, "w", encoding="utf-8") as fh:
                fh.write(text)
            return ws.run_cli("split", inbox, "--into", apps, "--date", date)
        return split

    def test_split_on_delimiter_lines_only(self):
        posts = m.split_postings("Acme\nRole A\n=====\n\n= = =\nGlobex\n  ========  \n\n")
        assert posts == ["Acme\nRole A\n", "= = =\nGlobex\n"]

    def test_split_writes_folders_and_empties_inbox(self, inbox, apps, split):
        res = split("Acme posting\n=====\nGlobex posting\n")
        assert res.returncode == 0, res.stderr
        folders = m.application_folders(apps)
        assert [os.path.basename(f) for f in folders] == ["2026-01-02_posting-01",
                                                          "2026-01-02_posting-02"]
        with open(os.path.join(folders[1], "posting.txt"), encoding="utf-8") as fh:
            assert fh.read() == "Globex posting\n"
        assert m.read_state(folders[0]) == {"stage": "new", "batch": "2026-01-02"}
        assert os.path.getsize(inbox) == 0

    def test_second_split_same_day_does_not_collide(self, apps, split):
        split("One\n")
        split("Two\n")
        assert len(m.application_folders(apps)) == 2

    def test_empty_inbox_is_refused(self, ws, inbox, apps, split, split_stubbed):
        res = split("\n=====\n  \n")
        assert res.returncode != 0
        assert m.application_folders(apps) == []

    # ---- URL entries. fetch_html is stubbed: no test touches the network.

    @pytest.fixture
    def split_stubbed(self, ws, inbox, apps):
        """Split with fetch_html replaced by a lookup in `pages` (url -> html,
        or url -> an exception message raised as FetchError)."""
        def split_stubbed(text, pages, *extra):
            with open(inbox, "w", encoding="utf-8") as fh:
                fh.write(text)
            patch = ("pages = %r\n"
                     "def fake(url):\n"
                     "    page = pages[url]\n"
                     "    if page.startswith('ERR:'):\n"
                     "        raise odt_text.FetchError(page[4:])\n"
                     "    return page\n"
                     "odt_text.fetch_html = fake\n"
                     "odt_text.FETCH_PAUSE_S = 0" % (pages,))
            return ws.run_stubbed(patch, "split", inbox, "--into", apps,
                                  "--date", "2026-01-02", *extra)
        return split_stubbed

    BODY = " ".join("word%d" % i for i in range(90))

    def ld_page(self, title="Backend Engineer", company="Acme"):
        ld = json.dumps({"@context": "https://schema.org", "@type": "JobPosting",
                         "title": title, "hiringOrganization": {"@type": "Organization",
                                                                "name": company},
                         "description": "<p>About</p><ul><li><p>Go &amp; SQL</p></li>"
                                        "<li>Docker</li></ul><p>%s</p>" % self.BODY})
        return ('<html><head><script type="application/ld+json">%s</script></head>'
                '<body><nav>Sign in</nav></body></html>' % ld)

    def linkedin_page(self):
        return ('<html><body><h1 class="top-card-layout__title">Data  Analyst</h1>'
                '<a class="topcard__org-name-link">Globex</a>'
                '<div class="description__text"><div class="show-more-less-html__markup">'
                '<strong>Role</strong><br>Do things.<ul><li>SQL</li></ul>%s</div>'
                '<button>Show more</button></div></body></html>' % self.BODY)

    def test_url_entry_is_fetched_from_json_ld(self, inbox, apps, split_stubbed):
        url = "https://boards.example.com/acme/jobs/1"
        res = split_stubbed(url + "\n", {url: self.ld_page()})
        assert res.returncode == 0, res.stderr
        folder, = m.application_folders(apps)
        with open(os.path.join(folder, "posting.txt"), encoding="utf-8") as fh:
            text = fh.read()
        assert text.startswith("Backend Engineer\nAcme\n\nAbout\n- Go & SQL\n"
                               "- Docker\n"), text
        assert "Sign in" not in text
        assert m.read_state(folder)["source"] == url
        assert "word5" not in res.stdout  # fetched text is never printed
        assert os.path.getsize(inbox) == 0

    def test_linkedin_guest_markup_is_extracted(self):
        text = m.extract_posting(self.linkedin_page())
        assert text.startswith("Data Analyst\nGlobex\n\nRole\nDo things.\n- SQL\n"), text
        assert "Show more" not in text

    def test_literal_backslash_n_in_json_ld_is_a_line_break(self):
        page = self.ld_page().replace("<p>About</p>", "<p>About</p>\\\\n")
        assert "\\n" not in m.extract_posting(page)

    def test_greenhouse_markup_is_extracted(self):
        page = ('<html><body><div class="job__title"><h1>Account Executive</h1>'
                '<div>Remote, France</div></div><button>Apply</button>'
                '<div class="job__description body"><p>%s</p></div></body></html>'
                % self.BODY)
        text = m.extract_posting(page)
        assert text.startswith("Account Executive\n\nword0 "), text
        assert "Apply" not in text

    def test_linkedin_urls_normalize_to_the_job_view_page(self):
        want = "https://www.linkedin.com/jobs/view/4012345678/"
        for url in ("https://www.linkedin.com/jobs/view/4012345678/?trk=abc",
                    "https://www.linkedin.com/jobs/view/backend-dev-at-acme-4012345678",
                    "https://linkedin.com/jobs/search/?currentJobId=4012345678&geoId=1",
                    "https://m.linkedin.com/jobs/view/4012345678"):
            assert m.normalize_url(url) == want, url
        other = "https://jobs.lever.co/acme/1234?lever-source=x"
        assert m.normalize_url(other) == other

    def test_page_without_a_posting_fails_instead_of_saving_junk(self):
        with pytest.raises(m.FetchError):
            m.extract_posting("<html><body><nav>Join now</nav><p>Sign in</p></body></html>")
        short = self.ld_page().replace(self.BODY, "brief")
        with pytest.raises(m.FetchError):
            m.extract_posting(short)

    def test_mixed_inbox_keeps_only_the_failed_url(self, inbox, apps, split_stubbed):
        good, bad = "https://a.example/job", "https://www.linkedin.com/jobs/view/42/"
        res = split_stubbed("Pasted posting\n=====\n%s\n=====\n%s\n" % (good, bad),
                                 {good: self.ld_page(), bad: "ERR:HTTP 999"})
        assert res.returncode == 0, res.stderr
        assert "FETCH FAILED: %s - HTTP 999" % bad in res.stdout
        assert len(m.application_folders(apps)) == 2
        with open(inbox, encoding="utf-8") as fh:
            assert fh.read() == bad + "\n"

    def test_all_urls_failing_writes_nothing_and_exits_non_zero(self, inbox, apps, split_stubbed):
        a, b = "https://a.example/1", "https://b.example/2"
        res = split_stubbed("%s\n=====\n%s\n" % (a, b),
                                 {a: "ERR:login wall", b: "ERR:no posting found on the page"})
        assert res.returncode != 0
        assert m.application_folders(apps) == []
        with open(inbox, encoding="utf-8") as fh:
            assert m.split_postings(fh.read()) == [a + "\n", b + "\n"]

    def test_no_fetch_keeps_a_url_as_text(self, apps, split_stubbed):
        url = "https://a.example/1"
        res = split_stubbed(url + "\n", {}, "--no-fetch")
        assert res.returncode == 0, res.stderr
        folder, = m.application_folders(apps)
        assert "source" not in m.read_state(folder)

    def test_text_mentioning_a_url_is_not_a_url_entry(self):
        assert m.URL_CHUNK_RE.match("Apply at https://a.example/1\n") is None
        assert m.URL_CHUNK_RE.match("https://a.example/1\nGreat role\n") is None
        assert m.URL_CHUNK_RE.match("  https://a.example/1  \n")

    # ---- trimming the boilerplate tail

    POSTING = ("Backend Engineer\nAcme\n\nRequirements\n- %s\n"
               % "\n- ".join("Python skill %d with services" % i for i in range(12)))

    def test_boilerplate_tail_is_cut_and_eligibility_kept(self):
        text = (self.POSTING + "Benefits\nHealth, dental, and a 401k match for you.\n"
                "Acme is an equal opportunity employer, without regard to citizenship.\n"
                "Must be a U.S. citizen to obtain a security clearance.\n"
                "We cannot offer visa sponsorship for this role.\n")
        trimmed, cut = m.trim_tail(text)
        assert trimmed == (self.POSTING.rstrip() + "\n\n" + m.TRIM_MARKER + "\n"
                           "Must be a U.S. citizen to obtain a security clearance.\n"
                           "We cannot offer visa sponsorship for this role.\n")
        assert cut == 1 + 8 + 10

    def test_boilerplate_in_the_front_half_is_not_a_cut(self):
        text = ("About us\nAcme builds tools.\n" + self.POSTING)
        assert m.trim_tail(text) == (text, 0)

    def test_a_requirements_heading_after_boilerplate_blocks_the_cut(self):
        text = (self.POSTING + "Benefits\nGood ones.\nPreferred qualifications\n- Go\n")
        assert m.trim_tail(text) == (text, 0)

    def test_weak_phrase_mid_sentence_is_not_a_cut(self):
        text = (self.POSTING + "- Build a platform that benefits our customers and "
                "partners across every region we serve today\n")
        assert m.trim_tail(text) == (text, 0)

    def test_split_trims_and_saves_the_full_text(self, apps, split):
        text = self.POSTING + "Equal Opportunity Employer\nWe welcome everyone.\n"
        res = split(text)
        assert res.returncode == 0, res.stderr
        assert "(trimmed 6 words of boilerplate)" in res.stdout
        folder, = m.application_folders(apps)
        with open(os.path.join(folder, "posting.txt"), encoding="utf-8") as fh:
            assert fh.read() == self.POSTING.rstrip() + "\n"
        with open(os.path.join(folder, "posting.full.txt"), encoding="utf-8") as fh:
            assert fh.read() == text.strip() + "\n"

    def test_no_trim_keeps_the_tail(self, ws, inbox, apps, split):
        text = self.POSTING + "Equal Opportunity Employer\n"
        with open(inbox, "w", encoding="utf-8") as fh:
            fh.write(text)
        ws.run_cli("split", inbox, "--into", apps, "--date", "2026-01-02",
                     "--no-trim")
        folder, = m.application_folders(apps)
        assert not os.path.exists(os.path.join(folder, "posting.full.txt"))

    def test_name_slugs_and_flags_an_earlier_application(self, ws, apps, split):
        split("Old\n", date="2025-06-01")
        old = m.application_folders(apps)[0]
        ws.run_cli("name", old, "Acme Corp.", "Backend Engineer")
        split("New\n")
        new = [f for f in m.application_folders(apps) if "posting" in f][0]
        res = ws.run_cli("name", new, "ACME corp", "Backend  Engineer")
        assert ("DUPLICATE: already applied to this company and role: "
                "2025-06-01_acme-corp_backend-engineer") in res.stdout
        named = os.path.join(apps, "2026-01-02", "2026-01-02_acme-corp_backend-engineer")
        assert os.path.isdir(named)
        assert m.read_state(named)["duplicate_of"] == ["2025-06-01_acme-corp_backend-engineer"]

    def test_split_files_postings_under_their_batch(self, apps, split):
        split("A\n")
        folder, = m.application_folders(apps)
        assert os.path.dirname(folder) == os.path.join(apps, "2026-01-02")

    def test_status_sees_nested_and_flat_folders(self, ws, apps, split):
        split("A\n")
        flat = os.path.join(apps, "2025-01-01_old_role")
        os.makedirs(flat)
        m.write_state(flat, {"stage": "applied", "batch": "2025-01-01"})
        res = ws.run_cli("status", apps)
        assert "2026-01-02_posting-01" in res.stdout
        assert flat in res.stdout

    def test_a_suffixed_repeat_is_still_a_duplicate(self, ws, apps, split):
        split("A\n=====\nB\n", date="2025-06-01")
        a, b = m.application_folders(apps)
        ws.run_cli("name", a, "Acme", "Dev")
        ws.run_cli("name", b, "Acme", "Dev")
        split("C\n")
        new = [f for f in m.application_folders(apps) if "posting" in f][0]
        res = ws.run_cli("name", new, "Acme", "Dev")
        assert "2025-06-01_acme_dev, 2025-06-01_acme_dev-2" in res.stdout

    def test_same_role_twice_in_one_batch_gets_a_suffix(self, ws, apps, split):
        split("A\n=====\nB\n")
        a, b = m.application_folders(apps)
        ws.run_cli("name", a, "Acme", "Dev")
        ws.run_cli("name", b, "Acme", "Dev")
        names = sorted(os.path.basename(f) for f in m.application_folders(apps))
        assert names == ["2026-01-02_acme_dev", "2026-01-02_acme_dev-2"]

    def test_status_hides_finished_applications_unless_asked(self, ws, apps, split):
        split("A\n=====\nB\n")
        a, b = m.application_folders(apps)
        ws.run_cli("stage", a, "filled")
        ws.run_cli("stage", b, "needs-you", "--note", "fill overflowed")
        res = ws.run_cli("status", apps)
        assert a not in res.stdout
        assert "needs-you  %s  - fill overflowed" % b in res.stdout
        assert a in ws.run_cli("status", apps, "--batch", "2026-01-02").stdout

    def test_unknown_stage_is_rejected(self, ws, apps, split):
        split("A\n")
        res = ws.run_cli("stage", m.application_folders(apps)[0], "done")
        assert res.returncode != 0


class TestOnboarding:
    """A new user's resume holds real details; tokenize puts tokens in their place."""

    VALUES = {"[Name]": "Grace Hopper", "[Email]": "grace@example.com",
              "[Phone]": "555-0199", "[City]": "Arlington", "[State]": "VA",
              "[Company1]": "Remington Rand", "[University]": "Vassar College",
              "[UniCity]": "Poughkeepsie", "[UniState]": "NY",
              "[Website]": "example.org/grace", "[ProjectRepo]": "https://example.org/repo"}

    def real_resume(self, ws, content=helpers.CONTENT):
        return ws.odt(content=m.fill_content(content, self.VALUES)[0])

    def tokenize(self, ws, src, values=None):
        mp = helpers.write_map(ws.path("map.txt"), values or self.VALUES)
        return ws.run_cli("tokenize", src, "--map", mp)

    def test_tokenize_then_fill_round_trips(self, ws):
        src = self.real_resume(ws)
        res = self.tokenize(ws, src)
        assert res.returncode == 0, res.stdout + res.stderr
        tokenized = m.read_content(src)
        body = tokenized[tokenized.find("<office:body>"):]
        assert sorted(m.body_tokens(body)) == sorted(helpers.TOKENS)
        refilled = m.fill_content(tokenized, self.VALUES)[0]
        expected = m.fill_content(helpers.CONTENT, self.VALUES)[0]
        assert m.strip_tags(refilled) == m.strip_tags(expected)

    def test_output_never_prints_a_value(self, ws):
        res = self.tokenize(ws, self.real_resume(ws))
        for v in self.VALUES.values():
            assert v not in res.stdout + res.stderr

    def test_the_original_is_kept_out_of_reach(self, ws):
        self.tokenize(ws, self.real_resume(ws))
        backup = os.path.join(ws.tmp, "private", "canon.orig.odt")
        assert os.path.exists(backup)
        assert "Grace Hopper" in m.read_content(backup)

    def test_a_value_split_by_formatting_is_reported_by_token(self, ws):
        content = helpers.CONTENT.replace(
            '<text:p text:style-name="P1">[Name]</text:p>',
            '<text:p text:style-name="P1">Grace <text:span text:style-name="T1">'
            'Hopper</text:span></text:p>')
        res = self.tokenize(ws, self.real_resume(ws, content))
        assert res.returncode != 0
        assert "[Name] is still in the document" in res.stdout

    def test_an_unlisted_email_is_flagged(self, ws):
        values = dict(self.VALUES)
        del values["[Email]"]
        res = self.tokenize(ws, self.real_resume(ws), values)
        assert res.returncode != 0
        assert "an email address is still in the document" in res.stdout

    def test_a_value_inside_a_word_is_left_alone(self, ws):
        content = helpers.CONTENT.replace("Plain bullet", "Plain VAriety bullet")
        self.tokenize(ws, self.real_resume(ws, content))
        assert "VAriety" in m.read_content(ws.path("canon.odt"))

    def test_init_map_writes_a_blank_template_once(self, ws):
        mp = ws.path("map.txt")
        assert ws.run_cli("init-map", "--map", mp).returncode == 0
        assert ws.run_cli("init-map", "--map", mp).returncode != 0
        with open(mp, "a", encoding="utf-8") as fh:
            fh.write("[Name] = Grace Hopper\n")
        assert m.read_pii_map(mp) == {"[Name]": "Grace Hopper"}

    def test_import_refuses_to_overwrite_canon(self, ws):
        src = ws.odt()
        ws.odt(name="canon_resume.odt")
        res = ws.run_cli("import", src)
        assert res.returncode != 0
        assert "already exists" in res.stderr

    def test_import_rejects_unknown_types(self, ws):
        res = ws.run_cli("import", ws.path("resume.pages"))
        assert "File > Download" in res.stderr

    @pytest.mark.skipif(not m.find_soffice(), reason="LibreOffice not installed")
    def test_import_reads_a_word_file(self, ws, request):
        docx = m.soffice_convert(ws.odt(), "docx")
        assert docx is not None
        request.addfinalizer(lambda: shutil.rmtree(os.path.dirname(docx), True))
        res = ws.run_cli("import", docx)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "VERDICT: resume" in res.stdout
        assert os.path.exists(ws.path("canon_resume.odt"))
