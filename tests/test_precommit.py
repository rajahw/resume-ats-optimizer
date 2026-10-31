"""Tests for the commit guard in hooks/pre-commit.py.

Each test builds a throwaway git repo with an all-fake identity map. Nothing
here touches the user's real repo, real map, or real resume.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers

pc = helpers.load_precommit()
odt = helpers.load_odt_text()

HOOK = os.path.join(helpers.HOOKS, "pre-commit.py")

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
    GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid",
)


class PreCommitCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="precommit-test-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo, env=GIT_ENV,
                              capture_output=True, text=True)

    def path(self, name):
        return os.path.join(self.repo, name)

    def write_map(self, values=None):
        helpers.write_map(self.path("pii.txt"),
                          values if values is not None else helpers.FAKE_VALUES)

    def stage_resume(self, content, name="resume.odt"):
        helpers.make_odt(self.path(name), content)
        self.git("add", "-f", name)
        return name

    def run_hook(self):
        return subprocess.run([sys.executable, HOOK], cwd=self.repo,
                              env=GIT_ENV, capture_output=True, text=True)


class TestTokenGrammarAgreement(unittest.TestCase):
    def test_matches_odt_text(self):
        """A narrower pattern here would block a legitimately redacted commit."""
        self.assertEqual(pc.TOKEN.pattern, odt.TOKEN_RE.pattern)


class TestBlocksRealPii(PreCommitCase):
    def test_fully_filled_resume_is_blocked(self):
        content = helpers.CONTENT
        for token, value in helpers.FAKE_VALUES.items():
            content = content.replace(token, value)
        self.write_map()
        self.stage_resume(content)
        res = self.run_hook()
        self.assertEqual(res.returncode, 1)
        self.assertIn("COMMIT BLOCKED", res.stderr)

    def test_partially_filled_resume_is_blocked(self):
        """The old check passed this: one leftover token read as 'redacted'."""
        content = helpers.CONTENT.replace("[Name]", "Ada Lovelace")
        content = content.replace("[City]", "Springfield")
        self.write_map()
        self.stage_resume(content)
        res = self.run_hook()
        self.assertEqual(res.returncode, 1,
                         "a resume with a real name in it was allowed through")
        self.assertIn("[Name]", res.stderr)

    def test_the_offending_value_is_never_printed(self):
        content = helpers.CONTENT.replace("[Name]", "Ada Lovelace")
        self.write_map()
        self.stage_resume(content)
        res = self.run_hook()
        self.assertNotIn("Ada Lovelace", res.stderr + res.stdout)

    def test_staging_the_map_is_blocked(self):
        self.write_map()
        self.git("add", "-f", "pii.txt")
        res = self.run_hook()
        self.assertEqual(res.returncode, 1)
        self.assertIn("identity map", res.stderr)

    def test_headings_split_across_spans_still_count_as_a_resume(self):
        """Tag-stripping with a space turns EDUCATION into 'EDUCAT ION'."""
        content = helpers.CONTENT.replace(
            ">EDUCATION<",
            '><text:span text:style-name="T1">EDUCAT</text:span>ION<')
        for token, value in helpers.FAKE_VALUES.items():
            content = content.replace(token, value)
        self.write_map()
        self.stage_resume(content)
        self.assertEqual(self.run_hook().returncode, 1)


class TestAllowsCleanCommits(PreCommitCase):
    def test_redacted_resume_passes(self):
        self.write_map()
        self.stage_resume(helpers.CONTENT)
        res = self.run_hook()
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_unusual_tokens_do_not_look_like_a_filled_resume(self):
        content = helpers.CONTENT.replace("[Phone]", "[Work_Phone]")
        self.write_map()
        self.stage_resume(content)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_non_resume_files_are_ignored(self):
        with open(self.path("notes.md"), "w", encoding="utf-8") as fh:
            fh.write("Ada Lovelace wrote this note.\n")
        self.write_map()
        self.git("add", "notes.md")
        self.assertEqual(self.run_hook().returncode, 0)

    def test_short_values_do_not_trigger_false_positives(self):
        """A state like 'IL' appears inside ordinary words."""
        self.write_map({"[State]": "IL"})
        self.stage_resume(helpers.CONTENT)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_works_with_no_map_present(self):
        self.stage_resume(helpers.CONTENT)
        self.assertEqual(self.run_hook().returncode, 0)


SCAN = os.path.join(helpers.HOOKS, "scan-history.py")


class TestScanHistory(PreCommitCase):
    def scan(self):
        return subprocess.run([sys.executable, SCAN], cwd=self.repo,
                              env=GIT_ENV, capture_output=True, text=True)

    def commit(self, name, text, message="add"):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def test_a_value_in_an_old_file_version_is_found_by_token_only(self):
        self.write_map()
        self.commit("notes.md", "Worked with Ada Lovelace on it.\n")
        self.commit("notes.md", "Scrubbed.\n")
        res = self.scan()
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("[Name]  notes.md", res.stdout)
        for value in helpers.FAKE_VALUES.values():
            self.assertNotIn(value, res.stdout + res.stderr)

    def test_a_value_inside_a_committed_odt_is_found(self):
        self.write_map()
        filled = odt.fill_content(helpers.CONTENT, helpers.FAKE_VALUES)[0]
        self.stage_resume(filled)
        self.git("commit", "-q", "--no-verify", "-m", "oops")
        self.assertIn("[Name]", self.scan().stdout)

    def test_a_value_in_a_commit_message_is_found(self):
        self.write_map()
        self.commit("a.txt", "x\n", message="Tailored for Ada Lovelace")
        self.assertIn("commit message", self.scan().stdout)

    def test_clean_history_passes(self):
        self.write_map()
        self.commit("a.txt", "[Name] stays a token.\n")
        self.assertEqual(self.scan().returncode, 0)

    def test_no_map_is_its_own_exit_status(self):
        self.commit("a.txt", "x\n")
        self.assertEqual(self.scan().returncode, 2)


if __name__ == "__main__":
    unittest.main()
