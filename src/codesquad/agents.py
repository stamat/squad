"""Build deepagents agents from config roles. Tool list = capability boundary:
a tool not in the role's list is never bound — the agent physically cannot call it."""

from pathlib import Path
from typing import Callable

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend, StateBackend
from langchain_core.tools import tool

from codesquad.config import SquadConfig
from codesquad.router import chat_model
from codesquad.tools.docs import save_doc
from codesquad.tools.git import make_git_commit
from codesquad.tools.profile import make_profile
from codesquad.tools.shell import run_shell
from codesquad.tools.subtasks import complete_subtask, next_subtask, set_subtasks

_NAMED_TOOLS = {"set_subtasks": set_subtasks, "next_subtask": next_subtask,
                "complete_subtask": complete_subtask, "save_doc": save_doc}


def history_middleware(cfg: SquadConfig, role: str) -> list:
    """Wires max_context: when a role's live message history crosses it, older
    messages are summarized by the local compressor model (free, private) and
    the last keep_last_messages stay verbatim. AI/tool-call pairs are kept
    together by the middleware. This is what stops supervisor history from
    growing O(N²) across delegations."""
    from langchain.agents.middleware import SummarizationMiddleware  # lazy: heavy

    from codesquad.interceptor import LoggedChat

    class SquadHistoryCompressor(SummarizationMiddleware):
        """Distinct name so it coexists with deepagents' built-in summarizer,
        which triggers at a fraction of the model's full window (~850k on 1M
        models) — far past our budgets. Ours fires at the role's max_context."""

    return [SquadHistoryCompressor(
        model=LoggedChat(model=cfg.compressor.model, squad_role="compressor"),
        trigger=("tokens", cfg.roles[role].max_context),
        keep=("messages", cfg.compressor.keep_last_messages),
    )]


def fs_permissions(role_tools: list[str]) -> list[FilesystemPermission] | None:
    """fs_read without fs = read-only, enforced: writes denied on every path.
    Prompt says "never edits"; this makes it physics, not a request."""
    if "fs_read" in role_tools and "fs" not in role_tools:
        return [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    return None


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
        from codesquad.tools import mcp  # lazy: may spawn MCP server processes
        tools += mcp.tools_for_role(r.tools, cfg.mcp_servers)

    for name, fn in _NAMED_TOOLS.items():  # planner pushes, coder pulls, scout saves docs
        if name in r.tools:
            tools.append(fn)

    if "profile" in r.tools:  # linguist-style repo profile, jailed like shell
        tools.append(make_profile(jail))

    if "shell" in r.tools:
        @tool
        def shell(cmd: str) -> str:
            """Run a shell command in the working directory. Dangerous commands are
            denied or require human confirmation; the result string tells you which."""
            return run_shell(cmd, cfg.shell_rules, jail, confirm)

        tools.append(shell)

    # fs / fs_read → deepagents file tools on the real FS, rooted at the jail.
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
        permissions=fs_permissions(r.tools),
        middleware=history_middleware(cfg, role),
    )
