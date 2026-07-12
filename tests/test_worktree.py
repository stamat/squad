"""Worktree lifecycle — isolation between concurrent squads, clean teardown."""

import subprocess
from pathlib import Path

import pytest

from codesquad.config import GitConfig
from codesquad.worktree import clean, create, push_and_pr, summary


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "project"
    r.mkdir()
    git("init", "-b", "main", cwd=r)
    git("config", "user.email", "t@t", cwd=r)
    git("config", "user.name", "t", cwd=r)
    (r / "app.py").write_text("print('v1')\n")
    git("add", "-A", cwd=r)
    git("commit", "-m", "init", cwd=r)
    return r


@pytest.fixture
def gitcfg(tmp_path):
    return GitConfig(worktrees_dir=tmp_path / "worktrees")


def test_create_worktree_and_branch(repo, gitcfg):
    wt = create(repo, "run1", gitcfg)
    assert wt.path.is_dir() and (wt.path / "app.py").exists()
    assert wt.branch == "squad/run1"
    assert "squad/run1" in git("branch", "--list", "squad/run1", cwd=repo)


def test_slug_names_branch_and_dir(repo, gitcfg):
    wt = create(repo, "20260712-140000-abc123", gitcfg, slug="gh-42")
    assert wt.branch == "squad/gh-42-abc123"        # readable + unique tail
    assert wt.path.name == "gh-42-abc123"           # dir == branch suffix, clean() relies on it
    assert wt.run_id == "20260712-140000-abc123"    # log identity unchanged


def test_clean_maps_slugged_dirs_to_branches(repo, gitcfg):
    wt = create(repo, "20260712-140000-abc123", gitcfg, slug="fix-login")
    (wt.path / "app.py").write_text("print('done')\n")
    git("commit", "-am", "work", cwd=wt.path)
    git("merge", wt.branch, cwd=repo)
    assert wt.path in clean(repo, gitcfg)
    assert not wt.path.exists()


def test_two_squads_do_not_collide(repo, gitcfg):
    a, b = create(repo, "runA", gitcfg), create(repo, "runB", gitcfg)
    (a.path / "app.py").write_text("print('from A')\n")
    git("commit", "-am", "A change", cwd=a.path)
    (b.path / "app.py").write_text("print('from B')\n")
    git("commit", "-am", "B change", cwd=b.path)
    # user checkout untouched, branches independent
    assert (repo / "app.py").read_text() == "print('v1')\n"
    assert "from A" in git("show", "squad/runA:app.py", cwd=repo)
    assert "from B" in git("show", "squad/runB:app.py", cwd=repo)


def test_summary_shows_branch_and_diffstat(repo, gitcfg):
    wt = create(repo, "run1", gitcfg)
    (wt.path / "app.py").write_text("print('v2')\n")
    git("commit", "-am", "change", cwd=wt.path)
    s = summary(wt)
    assert "squad/run1" in s and "app.py" in s


def test_push_skipped_when_branch_has_no_commits(repo, gitcfg):
    wt = create(repo, "run1", gitcfg)
    out = push_and_pr(wt, "some task")
    assert "nothing to push" in out


def test_push_without_remote_degrades_gracefully(repo, gitcfg):
    wt = create(repo, "run1", gitcfg)
    (wt.path / "f.py").write_text("x=1\n")
    git("add", "-A", cwd=wt.path)
    git("commit", "-m", "work", cwd=wt.path)
    out = push_and_pr(wt, "some task")  # no origin configured
    assert "stays local" in out and "squad/run1" in out


def test_clean_removes_only_merged(repo, gitcfg):
    merged, active = create(repo, "runM", gitcfg), create(repo, "runX", gitcfg)
    (merged.path / "app.py").write_text("print('merged')\n")
    git("commit", "-am", "m", cwd=merged.path)
    git("merge", "squad/runM", cwd=repo)
    (active.path / "app.py").write_text("print('wip')\n")
    git("commit", "-am", "wip", cwd=active.path)

    removed = clean(repo, gitcfg)

    assert merged.path in removed and not merged.path.exists()
    assert active.path not in removed and active.path.exists()
