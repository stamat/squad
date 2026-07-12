"""git_commit tool — commit_roles gating, worktree jail, run-id trailer."""

import subprocess

import pytest

from codesquad.config import load_config
from codesquad.tools.git import make_git_commit
from tests.test_worktree import git, gitcfg, repo  # fixtures  # noqa: F401

from pathlib import Path

CONFIG = Path(__file__).parent.parent / "squad.yaml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


def test_commit_role_gets_tool_others_none(cfg, tmp_path):
    assert make_git_commit(cfg, "coder", tmp_path, "run1") is not None
    assert make_git_commit(cfg, "reviewer", tmp_path, "run1") is None
    assert make_git_commit(cfg, "scout", tmp_path, "run1") is None


def test_commit_stages_commits_with_trailer(cfg, repo, gitcfg):  # noqa: F811
    from codesquad.worktree import create

    wt = create(repo, "run1", gitcfg)
    tool = make_git_commit(cfg, "coder", wt.path, "run1")
    (wt.path / "new.py").write_text("x = 1\n")

    out = tool.invoke({"message": "add new.py"})

    assert "committed" in out.lower()
    log = git("log", "-1", "--format=%B", cwd=wt.path)
    assert "add new.py" in log
    assert "Squad-Run: run1" in log  # traceability trailer
    assert "new.py" in git("show", "--stat", "HEAD", cwd=wt.path)


def test_commit_nothing_to_commit(cfg, repo, gitcfg):  # noqa: F811
    from codesquad.worktree import create

    wt = create(repo, "run1", gitcfg)
    tool = make_git_commit(cfg, "coder", wt.path, "run1")
    out = tool.invoke({"message": "empty"})
    assert "nothing to commit" in out.lower()
