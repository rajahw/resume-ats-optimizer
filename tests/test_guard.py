"""Tests for the PreToolUse guard in hooks/block-pii-access.py.

These exercise the matcher on synthetic strings. Nothing here opens a protected
file; the strings are inputs to a pure function.

The guard is a guardrail against accidental access, not a sandbox. The
"acknowledged gaps" case at the bottom pins the limits that a string matcher
cannot close, so nobody mistakes them for coverage.
"""

import pytest

import helpers

g = helpers.load_guard()

MAP = "pii.txt"
FILLED = "Someone_Resume_filled.odt"


def bash(command):
    return g.decide({"tool_name": "Bash", "tool_input": {"command": command}})


def tool(name, **kwargs):
    return g.decide({"tool_name": name, "tool_input": kwargs})


class TestDeniesTheMap:
    @pytest.mark.parametrize("cmd", [
        "cat " + MAP,
        "head -c 50 ./" + MAP,
        "grep Name " + MAP,
        "python3 -c \"open('" + MAP + "')\"",
        "cp " + MAP + " /tmp/x",
    ])
    def test_plain_reads(self, cmd):
        assert bash(cmd) is not None

    @pytest.mark.parametrize("cmd", ["cat < " + MAP, "cat <" + MAP, "cat<" + MAP])
    def test_redirection_with_and_without_a_space(self, cmd):
        """`<` was missing from the separator class, so the no-space form passed."""
        assert bash(cmd) is not None, "redirect form slipped past"

    @pytest.mark.parametrize("cmd", ["echo $(<" + MAP + ")", "echo `cat " + MAP + "`"])
    def test_command_substitution_forms(self, cmd):
        assert bash(cmd) is not None

    @pytest.mark.parametrize("cmd", ["cat pii-old.txt", "cat pii.bak", "cat backup/pii.txt"])
    def test_variants_of_the_name(self, cmd):
        assert bash(cmd) is not None

    @pytest.mark.parametrize("name", ["Read", "Edit", "Write"])
    def test_path_fields_on_other_tools(self, name):
        assert tool(name, file_path=MAP) is not None


class TestDeniesFilledResumes:
    def test_direct_read(self):
        assert bash("cat " + FILLED) is not None

    @pytest.mark.parametrize("prefix", ["timeout 5 ", "stdbuf -o0 ", "nice ", "env "])
    def test_through_a_wrapper(self, prefix):
        """First-token-only reader detection let `timeout` through."""
        assert bash(prefix + "cat " + FILLED) is not None

    @pytest.mark.parametrize("name", ["Someone_Resume_final.odt",
                                      "Someone_Resume_submitted.odt"])
    def test_other_gitignored_filled_shapes(self, name):
        """.gitignore also treats _final and _submitted as filled outputs."""
        assert bash("cat " + name) is not None

    @pytest.mark.parametrize("name", ["Someone_Resume.odt", "Resume.odt"])
    def test_filled_resume_is_the_plain_resume_name(self, name):
        assert bash("cat " + name) is not None
        assert tool("Read", file_path=name) is not None

    def test_tokenized_resume_is_readable(self):
        assert bash("cat Someone_Resume_tokenized.odt") is None

    def test_private_directory(self):
        assert bash("cat private/resume.odt") is not None
        assert tool("Read", file_path="private/resume.odt") is not None

    def test_a_dotfile_directory_is_not_confused_for_private(self):
        """lstrip('./') strips characters, not a prefix: ./.private -> private."""
        assert bash("cat .privatenotes/scratch.txt") is None

    @pytest.mark.parametrize("name", ["ada_resume.pdf", "ADA_RESUME.PDF", "resume.odt",
                                      "Someone_FILLED.odt", "PRIVATE/resume.odt"])
    def test_case_variants_are_denied_on_every_platform(self, name):
        """Windows and macOS open Ada_Resume.pdf for any casing of its name."""
        assert bash("cat " + name) is not None
        assert tool("Read", file_path=name) is not None

    @pytest.mark.parametrize("name", ["private/canon_resume.odt", "canon_resume_filled.odt"])
    def test_canon_exemption_does_not_leak(self, name):
        assert bash("cat " + name) is not None


class TestCanonIsNeverAFilledResume:
    """canon_resume.* differs from *_Resume.* only in case; it must stay readable."""

    @pytest.mark.parametrize("name", ["canon_resume.odt", "canon_resume.pdf",
                                      "CANON_RESUME.ODT", "./canon_resume.odt",
                                      "dir/canon_resume.odt"])
    @pytest.mark.parametrize("cmd", ["cat {}", "unzip -o {} -d x"])
    def test_readers_are_allowed(self, cmd, name):
        assert bash(cmd.format(name)) is None

    @pytest.mark.parametrize("name", ["canon_resume.odt", "dir/canon_resume.odt"])
    def test_read_tool_is_allowed(self, name):
        assert tool("Read", file_path=name) is None


class TestAllowsLegitimateWork:
    """False positives would make the tool unusable; pin the common cases."""

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "git status",
        "python3 -m pytest",
        "cat README.md",
        "grep -n TOKEN_RE odt_text.py",
        "unzip -o canon_resume.odt -d x",
    ])
    def test_ordinary_commands(self, cmd):
        assert bash(cmd) is None, "blocked legitimate work"

    def test_fill_is_allowed_to_run(self):
        """fill reads the map itself; it must not be mistaken for a reader."""
        cmd = "python3 odt_text.py fill canon_resume.odt -o Out_Resume_filled.odt"
        assert bash(cmd) is None

    @pytest.mark.parametrize("cmd", [
        "S=odt_text.py; python3 $S fill a.odt -o d/Out_Resume.pdf",
        "S=odt_text.py d=app; python3 ${S} fill $d/a.odt -o $d/Out_Resume.pdf",
        'S="odt_text.py"; python3 $S fill a.odt -o x/Out_Resume.pdf',
    ])
    def test_fill_through_a_script_variable_is_allowed(self, cmd):
        """`S=odt_text.py; python3 $S fill` was judged a reader before variables were expanded."""
        assert bash(cmd) is None

    def test_assigned_variable_naming_the_map_is_denied(self):
        assert bash("p=pii; cat $p.txt") == g.PII_REASON

    def test_assigned_variable_naming_a_filled_resume_is_denied(self):
        assert bash("f=Out_Resume.pdf; cat $f") == g.FILLED_REASON

    def test_writing_a_filled_output_is_allowed(self):
        assert tool("Write", file_path="notes.md") is None

    def test_prose_mentioning_the_map_is_allowed(self):
        """Docs have to be able to describe the map without tripping the guard."""
        assert tool("Write", file_path="README.md",
                    content="Write values into pii.txt, one per line.") is None


class TestFailsClosed:
    @pytest.mark.parametrize("payload", [
        "not a dict",
        {"tool_name": "Bash", "tool_input": "oops"},
        {"tool_name": "Bash", "tool_input": {"command": 42}},
        {},
    ], ids=["unparseable", "non-dict-input", "non-string-command", "missing-fields"])
    def test_malformed_payload_is_denied(self, payload):
        assert g.decide(payload) is not None


class TestAcknowledgedGaps:
    """Limits of a string matcher, pinned so they are not mistaken for coverage.

    Each of these reaches the map. They are documented in the guard's docstring
    rather than fixed, because no filename matcher can close them.
    """

    def test_directory_wide_sweep_is_not_caught(self):
        assert bash("grep -r . .") is None

    def test_variable_set_outside_the_command_is_not_caught(self):
        """Only assignments visible in the same command are expanded."""
        assert bash("cat $MAPFILE") is None
