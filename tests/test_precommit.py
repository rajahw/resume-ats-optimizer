"""Tests for the commit guard in hooks/pre-commit.py.

Each test builds a throwaway git repo with an all-fake identity map. Nothing
here touches the user's real repo, real map, or real resume.
"""

import helpers

pc = helpers.load_precommit()
odt = helpers.load_odt_text()


def test_token_grammar_matches_odt_text():
    """A narrower pattern here would block a legitimately redacted commit."""
    assert pc.TOKEN.pattern == odt.TOKEN_RE.pattern


class TestBlocksRealPii:
    def test_fully_filled_resume_is_blocked(self, repo):
        content = helpers.CONTENT
        for token, value in helpers.FAKE_VALUES.items():
            content = content.replace(token, value)
        repo.write_map()
        repo.stage_resume(content)
        res = repo.run_hook()
        assert res.returncode == 1
        assert "COMMIT BLOCKED" in res.stderr

    def test_partially_filled_resume_is_blocked(self, repo):
        """The old check passed this: one leftover token read as 'redacted'."""
        content = helpers.CONTENT.replace("[Name]", "Ada Lovelace")
        content = content.replace("[City]", "Springfield")
        repo.write_map()
        repo.stage_resume(content)
        res = repo.run_hook()
        assert res.returncode == 1, "a resume with a real name in it was allowed through"
        assert "[Name]" in res.stderr

    def test_the_offending_value_is_never_printed(self, repo):
        content = helpers.CONTENT.replace("[Name]", "Ada Lovelace")
        repo.write_map()
        repo.stage_resume(content)
        res = repo.run_hook()
        assert "Ada Lovelace" not in res.stderr + res.stdout

    def test_staging_the_map_is_blocked(self, repo):
        repo.write_map()
        repo.git("add", "-f", "pii.txt")
        res = repo.run_hook()
        assert res.returncode == 1
        assert "identity map" in res.stderr

    def test_headings_split_across_spans_still_count_as_a_resume(self, repo):
        """Tag-stripping with a space turns EDUCATION into 'EDUCAT ION'."""
        content = helpers.CONTENT.replace(
            ">EDUCATION<",
            '><text:span text:style-name="T1">EDUCAT</text:span>ION<')
        for token, value in helpers.FAKE_VALUES.items():
            content = content.replace(token, value)
        repo.write_map()
        repo.stage_resume(content)
        assert repo.run_hook().returncode == 1


class TestAllowsCleanCommits:
    def test_redacted_resume_passes(self, repo):
        repo.write_map()
        repo.stage_resume(helpers.CONTENT)
        res = repo.run_hook()
        assert res.returncode == 0, res.stderr

    def test_unusual_tokens_do_not_look_like_a_filled_resume(self, repo):
        content = helpers.CONTENT.replace("[Phone]", "[Work_Phone]")
        repo.write_map()
        repo.stage_resume(content)
        assert repo.run_hook().returncode == 0

    def test_non_resume_files_are_ignored(self, repo):
        with open(repo.path("notes.md"), "w", encoding="utf-8") as fh:
            fh.write("Ada Lovelace wrote this note.\n")
        repo.write_map()
        repo.git("add", "notes.md")
        assert repo.run_hook().returncode == 0

    def test_short_values_do_not_trigger_false_positives(self, repo):
        """A state like 'IL' appears inside ordinary words."""
        repo.write_map({"[State]": "IL"})
        repo.stage_resume(helpers.CONTENT)
        assert repo.run_hook().returncode == 0

    def test_works_with_no_map_present(self, repo):
        repo.stage_resume(helpers.CONTENT)
        assert repo.run_hook().returncode == 0


class TestScanHistory:
    def test_a_value_in_an_old_file_version_is_found_by_token_only(self, repo):
        repo.write_map()
        repo.commit("notes.md", "Worked with Ada Lovelace on it.\n")
        repo.commit("notes.md", "Scrubbed.\n")
        res = repo.scan()
        assert res.returncode == 1, res.stdout + res.stderr
        assert "[Name]  notes.md" in res.stdout
        for value in helpers.FAKE_VALUES.values():
            assert value not in res.stdout + res.stderr

    def test_a_value_inside_a_committed_odt_is_found(self, repo):
        repo.write_map()
        filled = odt.fill_content(helpers.CONTENT, helpers.FAKE_VALUES)[0]
        repo.stage_resume(filled)
        repo.git("commit", "-q", "--no-verify", "-m", "oops")
        assert "[Name]" in repo.scan().stdout

    def test_a_value_in_a_commit_message_is_found(self, repo):
        repo.write_map()
        repo.commit("a.txt", "x\n", message="Tailored for Ada Lovelace")
        assert "commit message" in repo.scan().stdout

    def test_clean_history_passes(self, repo):
        repo.write_map()
        repo.commit("a.txt", "[Name] stays a token.\n")
        assert repo.scan().returncode == 0

    def test_no_map_is_its_own_exit_status(self, repo):
        repo.commit("a.txt", "x\n")
        assert repo.scan().returncode == 2
