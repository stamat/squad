"""Build deepagents agents from config roles. Tool list = capability boundary:
a tool not in the role's list is never bound — the agent physically cannot call it."""

from pathlib import Path
from typing import Callable

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, StateBackend
from langchain_core.tools import tool

from squad.config import SquadConfig
from squad.router import chat_model
from squad.tools.shell import run_shell


def build_agent(cfg: SquadConfig, role: str, jail: Path, confirm: Callable[[str], bool]):
    """One deepagents agent for a role: its model, its prompt, only its tools."""
    r = cfg.roles[role]
    tools = []

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
        system_prompt=r.prompt.read_text(),
        backend=backend,
    )
