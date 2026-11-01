"""Shared fixtures. Every one works in a fresh tmp_path, never in the real repo."""

import os
import subprocess
import sys

import pytest

import helpers

m = helpers.load_odt_text()
SCRIPT = os.path.join(helpers.SCRIPTS, "odt_text.py")
HOOK = os.path.join(helpers.HOOKS, "pre-commit.py")
SCAN = os.path.join(helpers.HOOKS, "scan-history.py")

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
    GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid",
)


class Workspace:
    """A scratch directory for odt_text.py, with the CLI pointed at it."""

    def __init__(self, tmp):
        self.tmp = tmp

    def path(self, name):
        return os.path.join(self.tmp, name)

    def odt(self, name="canon.odt", content=None, styles=None):
        return helpers.make_odt(self.path(name), content, styles)

    def env(self):
        """Renders and the canon geometry cache stay inside this test's tmp dir."""
        return dict(os.environ, ODT_TEXT_CACHE=self.path("cache"))

    def run_cli(self, *args):
        # cwd is the tmp dir, so a default path (canon_resume.odt, pii.txt,
        # applications/) never reaches the real repo's files.
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, env=self.env(),
                              cwd=self.tmp)

    def run_stubbed(self, patch, *args):
        """Run the CLI with one line of monkeypatching applied first."""
        stub = self.path("stub.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport odt_text\n%s\n"
                     "sys.argv = ['odt_text'] + %r\nodt_text.main()\n"
                     % (helpers.SCRIPTS, patch, list(args)))
        return subprocess.run([sys.executable, stub], capture_output=True, text=True,
                              env=self.env(), cwd=self.tmp)


class GitRepo:
    """A throwaway git repo with a fake identity, for the commit guard."""

    def __init__(self, root):
        self.root = root
        self.git("init", "-q")

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.root, env=GIT_ENV,
                              capture_output=True, text=True)

    def path(self, name):
        return os.path.join(self.root, name)

    def write_map(self, values=None):
        helpers.write_map(self.path("pii.txt"),
                          values if values is not None else helpers.FAKE_VALUES)

    def stage_resume(self, content, name="resume.odt"):
        helpers.make_odt(self.path(name), content)
        self.git("add", "-f", name)
        return name

    def commit(self, name, text, message="add"):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def run_hook(self):
        return subprocess.run([sys.executable, HOOK], cwd=self.root,
                              env=GIT_ENV, capture_output=True, text=True)

    def scan(self):
        return subprocess.run([sys.executable, SCAN], cwd=self.root,
                              env=GIT_ENV, capture_output=True, text=True)


@pytest.fixture
def ws(tmp_path):
    return Workspace(str(tmp_path))


@pytest.fixture
def render_cache(ws, monkeypatch):
    """Point odt_text's render cache into the workspace for one test."""
    cache = ws.path("cache")
    monkeypatch.setattr(m, "RENDER_CACHE", cache)
    return cache


@pytest.fixture
def repo(tmp_path):
    return GitRepo(str(tmp_path))
