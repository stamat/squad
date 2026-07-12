"""Worktree lifecycle: each run gets its own worktree + branch; squads never collide.
Creation/removal belongs to the CLI — agents are denied `git worktree` commands."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from codesquad.config import GitConfig


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass
class Worktree:
    repo: Path
    run_id: str
    path: Path
    branch: str


def create(repo: Path, run_id: str, cfg: GitConfig, slug: str | None = None) -> Worktree:
    # slug (issue number / task summary) names the branch; the run-id tail keeps
    # it unique. Dir name == branch suffix so `clean` can map one to the other.
    name = f"{slug}-{run_id[-6:]}" if slug else run_id
    branch = cfg.branch_prefix + name
    path = cfg.worktrees_dir.expanduser() / repo.name / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", str(path), "-b", branch, cwd=repo)
    return Worktree(repo=repo, run_id=run_id, path=path, branch=branch)


def summary(wt: Worktree) -> str:
    """Branch name + diffstat of what the run produced."""
    base = _git("merge-base", "HEAD", wt.branch, cwd=wt.repo).strip()
    stat = _git("diff", "--stat", base, wt.branch, cwd=wt.repo).strip() or "(no changes)"
    return f"branch {wt.branch}\n{stat}"


def push_and_pr(wt: Worktree, title: str, body: str | None = None) -> str:
    """Push the run branch and open a PR. CLI-only — never an agent capability.
    body: the run's PR notes (what was done and why); falls back to the task."""
    if not _git("rev-list", f"HEAD..{wt.branch}", cwd=wt.repo).strip():
        return f"no commits on {wt.branch} — nothing to push, branch stays local"
    try:
        _git("push", "-u", "origin", wt.branch, cwd=wt.path)
    except RuntimeError as e:
        return f"push failed ({str(e)[:200]}) — branch {wt.branch} stays local"
    proc = subprocess.run(
        ["gh", "pr", "create", "--head", wt.branch, "--title", title.splitlines()[0][:70],
         "--body", f"{body or f'Task: {title}'}\n\nSquad-Run: {wt.run_id}"],
        cwd=wt.path, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return f"PR: {proc.stdout.strip()}"
    # gh missing or failed → hand the human a compare URL
    remote = _git("remote", "get-url", "origin", cwd=wt.repo).strip()
    return (f"pushed {wt.branch}; open a PR manually: {remote} (compare {wt.branch})\n"
            f"gh said: {proc.stderr.strip()[:200]}")


def clean(repo: Path, cfg: GitConfig) -> list[Path]:
    """Remove worktrees whose branches are merged into the repo's HEAD. Returns removed paths."""
    merged = {
        b.strip().lstrip("*+ ")  # '*' = current, '+' = checked out in a worktree
        for b in _git("branch", "--merged", cwd=repo).splitlines()
    }
    removed: list[Path] = []
    root = cfg.worktrees_dir.expanduser() / repo.name
    if not root.is_dir():
        return removed
    for wt_dir in sorted(root.iterdir()):
        branch = cfg.branch_prefix + wt_dir.name
        if branch not in merged:
            continue
        _git("worktree", "remove", "--force", str(wt_dir), cwd=repo)
        _git("branch", "-D", branch, cwd=repo)
        removed.append(wt_dir)
    return removed
