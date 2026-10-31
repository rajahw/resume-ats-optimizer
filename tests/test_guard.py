"""Tests for the PreToolUse guard in hooks/block-pii-access.py.

These exercise the matcher on synthetic strings. Nothing here opens a protected
file; the strings are inputs to a pure function.

The guard is a guardrail against accidental access, not a sandbox. The
"acknowledged gaps" case at the bottom pins the limits that a string matcher
cannot close, so nobody mistakes them for coverage.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers

g = helpers.load_guard()

MAP = "pii.txt"
FILLED = "Someone_Resume_filled.odt"


def bash(command):
    return g.decide({"tool_name": "Bash", "tool_input": {"command": command}})


def tool(name, **kwargs):
    return g.decide({"tool_name": name, "tool_input": kwargs})


class TestDeniesTheMap(unittest.TestCase):
    def test_plain_reads(self):
        for cmd in ("cat " + MAP,
                    "head -c 50 ./" + MAP,
                    "grep Name " + MAP,
                    "python3 -c \"open('" + MAP + "')\"",
                    "cp " + MAP + " /tmp/x"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(bash(cmd))

    def test_redirection_with_and_without_a_space(self):
        """`<` was missing from the separator class, so the no-space form passed."""
        for cmd in ("cat < " + MAP, "cat <" + MAP, "cat<" + MAP):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(bash(cmd), "redirect form slipped past")

    def test_command_substitution_forms(self):
        for cmd in ("echo $(<" + MAP + ")", "echo `cat " + MAP + "`"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(bash(cmd))

    def test_variants_of_the_name(self):
        for cmd in ("cat pii-old.txt", "cat pii.bak", "cat backup/pii.txt"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(bash(cmd))

    def test_path_fields_on_other_tools(self):
        self.assertIsNotNone(tool("Read", file_path=MAP))
        self.assertIsNotNone(tool("Edit", file_path=MAP))
        self.assertIsNotNone(tool("Write", file_path=MAP))


class TestDeniesFilledResumes(unittest.TestCase):
    def test_direct_read(self):
        self.assertIsNotNone(bash("cat " + FILLED))

    def test_through_a_wrapper(self):
        """First-token-only reader detection let `timeout` through."""
        for prefix in ("timeout 5 ", "stdbuf -o0 ", "nice ", "env "):
            with self.subTest(prefix=prefix):
                self.assertIsNotNone(bash(prefix + "cat " + FILLED))

    def test_other_gitignored_filled_shapes(self):
        """.gitignore also treats _final and _submitted as filled outputs."""
        for name in ("Someone_Resume_final.odt", "Someone_Resume_submitted.odt"):
            with self.subTest(name=name):
                self.assertIsNotNone(bash("cat " + name))

    def test_filled_resume_is_the_plain_resume_name(self):
        for name in ("Someone_Resume.odt", "Resume.odt"):
            with self.subTest(name=name):
                self.assertIsNotNone(bash("cat " + name))
                self.assertIsNotNone(tool("Read", file_path=name))

    def test_tokenized_resume_is_readable(self):
        self.assertIsNone(bash("cat Someone_Resume_tokenized.odt"))

    def test_private_directory(self):
        self.assertIsNotNone(bash("cat private/resume.odt"))
        self.assertIsNotNone(tool("Read", file_path="private/resume.odt"))

    def test_a_dotfile_directory_is_not_confused_for_private(self):
        """lstrip('./') strips characters, not a prefix: ./.private -> private."""
        self.assertIsNone(bash("cat .privatenotes/scratch.txt"))


class TestAllowsLegitimateWork(unittest.TestCase):
    """False positives would make the tool unusable; pin the common cases."""

    def test_ordinary_commands(self):
        for cmd in ("ls -la",
                    "git status",
                    "python3 -m unittest discover tests",
                    "cat README.md",
                    "grep -n TOKEN_RE odt_text.py",
                    "unzip -o canon_resume.odt -d x"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(bash(cmd), "blocked legitimate work")

    def test_fill_is_allowed_to_run(self):
        """fill reads the map itself; it must not be mistaken for a reader."""
        cmd = "python3 odt_text.py fill canon_resume.odt -o Out_Resume_filled.odt"
        self.assertIsNone(bash(cmd))

    def test_fill_through_a_script_variable_is_allowed(self):
        """`S=odt_text.py; python3 $S fill` was judged a reader before variables were expanded."""
        for cmd in ("S=odt_text.py; python3 $S fill a.odt -o d/Out_Resume.pdf",
                    "S=odt_text.py d=app; python3 ${S} fill $d/a.odt -o $d/Out_Resume.pdf",
                    'S="odt_text.py"; python3 $S fill a.odt -o x/Out_Resume.pdf'):
            with self.subTest(cmd=cmd):
                self.assertIsNone(bash(cmd))

    def test_assigned_variable_naming_the_map_is_denied(self):
        self.assertEqual(bash("p=pii; cat $p.txt"), g.PII_REASON)

    def test_assigned_variable_naming_a_filled_resume_is_denied(self):
        self.assertEqual(bash("f=Out_Resume.pdf; cat $f"), g.FILLED_REASON)

    def test_writing_a_filled_output_is_allowed(self):
        self.assertIsNone(tool("Write", file_path="notes.md"))

    def test_prose_mentioning_the_map_is_allowed(self):
        """Docs have to be able to describe the map without tripping the guard."""
        self.assertIsNone(tool("Write", file_path="README.md",
                               content="Write values into pii.txt, one per line."))


class TestFailsClosed(unittest.TestCase):
    def test_unparseable_payload_is_denied(self):
        self.assertIsNotNone(g.decide("not a dict"))

    def test_non_dict_tool_input_is_denied(self):
        self.assertIsNotNone(g.decide({"tool_name": "Bash", "tool_input": "oops"}))

    def test_non_string_command_is_denied(self):
        self.assertIsNotNone(
            g.decide({"tool_name": "Bash", "tool_input": {"command": 42}}))

    def test_missing_fields_are_denied(self):
        self.assertIsNotNone(g.decide({}))


class TestAcknowledgedGaps(unittest.TestCase):
    """Limits of a string matcher, pinned so they are not mistaken for coverage.

    Each of these reaches the map. They are documented in the guard's docstring
    rather than fixed, because no filename matcher can close them.
    """

    def test_directory_wide_sweep_is_not_caught(self):
        self.assertIsNone(bash("grep -r . ."))

    def test_variable_set_outside_the_command_is_not_caught(self):
        """Only assignments visible in the same command are expanded."""
        self.assertIsNone(bash("cat $MAPFILE"))


if __name__ == "__main__":
    unittest.main()
