"""git_commit tool: stage + commit inside the run's worktree, commit_roles only.
Push is not a tool — it stays a confirm-gated shell pattern + the run-end PR step."""

import subprocess
from pathlib import Path

from langchain_core.tools import tool

from codesquad.config import SquadConfig
from codesquad.interceptor import current_log


def make_git_commit(cfg: SquadConfig, role: str, worktree: Path, run_id: str):
    """The tool, or None when the role may not commit (then it's simply never bound)."""
    if role not in cfg.git.commit_roles:
        return None

    @tool
    def git_commit(message: str) -> str:
        """Stage all changes in the working directory and commit them on the run's
        branch. Message: what + why, one coherent unit of work per commit."""
        def git(*args: str):
            return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True)

        git("add", "-A")
        if not git("status", "--porcelain").stdout.strip():
            return "nothing to commit — working tree clean"
        full = f"{message}\n\nSquad-Run: {run_id}"
        proc = git("commit", "-m", full)
        result = ("committed:\n" + git("show", "--stat", "--format=%h %s", "HEAD").stdout
                  if proc.returncode == 0 else f"commit failed: {proc.stderr.strip()}")
        if log := current_log.get():
            log.write("git", role=role, payload={"message": message, "result": result[:500]})
        return result

    return git_commit
