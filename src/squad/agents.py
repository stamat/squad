"""Build deepagents agents from config roles. Tool list = capability boundary:
a tool not in the role's list is never bound — the agent physically cannot call it."""

from pathlib import Path
from typing import Callable

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, StateBackend
from langchain_core.tools import tool

from squad.config import SquadConfig
from squad.router import chat_model
from squad.tools.git import make_git_commit
from squad.tools.shell import run_shell
from squad.tools.subtasks import complete_subtask, next_subtask, set_subtasks

_SUBTASK_TOOLS = {"set_subtasks": set_subtasks, "next_subtask": next_subtask,
                  "complete_subtask": complete_subtask}


def load_prompt(prompt_path: Path) -> str:
    """Role prompt with `{principles}` expanded to the shared coding law.
    Opt-in: a role gets the law by putting the token in its prompt file — no code
    change to add another role. Token absent → prompt returned verbatim."""
    text = prompt_path.read_text()
    if "{principles}" in text:
        text = text.replace("{principles}", (prompt_path.parent / "principles.md").read_text())
    return text


def build_agent(cfg: SquadConfig, role: str, jail: Path, confirm: Callable[[str], bool],
                run_id: str | None = None):
    """One deepagents agent for a role: its model, its prompt, only its tools.
    run_id present = the jail is a run worktree → git_commit may be bound."""
    r = cfg.roles[role]
    tools = []

    if "git_commit" in r.tools and run_id:
        if commit_tool := make_git_commit(cfg, role, jail, run_id):
            tools.append(commit_tool)

    if {"browse", "render"} & set(r.tools) or set(r.tools) & set(cfg.mcp_servers):
        from squad.tools import mcp  # lazy: may spawn MCP server processes
        tools += mcp.tools_for_role(r.tools, cfg.mcp_servers)

    for name, fn in _SUBTASK_TOOLS.items():  # planner pushes, coder pulls/completes
        if name in r.tools:
            tools.append(fn)

    if "shell" in r.tools:
        @tool
        def shell(cmd: str) -> str:
            """Run a shell command in the working directory. Dangerous commands are
            denied or require human confirmation; the result string tells you which."""
            return run_shell(cmd, cfg.shell_rules, jail, confirm)

        tools.append(shell)

    # fs / fs_read → deepagents file tools on the real FS, rooted at the jail.
    # ponytail: fs_read is not read-only yet — enforce via FilesystemPermission in Phase 4.
    if {"fs", "fs_read"} & set(r.tools):
        # virtual_mode=True: paths resolved inside root_dir; blocks '..' and absolute-path escapes
        backend = FilesystemBackend(root_dir=jail, virtual_mode=True)
    else:
        backend = StateBackend()  # virtual scratch only; role never touches disk

    return create_deep_agent(
        model=chat_model(cfg, role),
        tools=tools,
        system_prompt=load_prompt(r.prompt),
        backend=backend,
    )
