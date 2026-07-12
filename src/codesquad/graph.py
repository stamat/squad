"""Supervisor graph: hub-and-spoke, one level deep. The supervisor's only real
tool is `delegate` — our handoff tool, which is the single interception point:
it logs task+context in, result out, and enforces the cost breaker."""

from pathlib import Path
from typing import Callable

from deepagents import create_deep_agent
from langchain_core.tools import tool

from codesquad.agents import build_agent, history_middleware
from codesquad.config import SquadConfig
from codesquad.interceptor import current_log, current_role
from codesquad.router import chat_model


class BudgetExceeded(RuntimeError):
    """Raised by delegate when the run's cost crosses --max-cost. Halts the graph."""


def build_delegate(subagents: dict, cfg: SquadConfig, max_cost: float):
    @tool
    def delegate(role: str, task: str, context: str = "") -> str:
        """Hand a task to a specialist role and return its result.

        Args:
            role: which specialist (see roster in your instructions).
            task: what it must do and what it must hand back.
            context: only the information it needs — it sees nothing else.
        """
        if role not in subagents:
            return f"unknown role {role!r}; available: {', '.join(subagents)}"
        from codesquad.compress import compress  # lazy: avoids import cycle at module load

        # compression checkpoint: both directions of the boundary (live message
        # lists are handled separately by history_middleware at max_context)
        context = compress(context, cfg.compressor)
        log = current_log.get()
        if log and max_cost > 0 and log.total_cost >= max_cost:  # max_cost <= 0 disables the breaker
            raise BudgetExceeded(f"run cost ${log.total_cost:.4f} reached --max-cost ${max_cost:.2f}")
        if log:
            log.write("handoff", role=role, direction="in",
                      payload={"task": task, "context": context})
        prev = current_role.get()
        current_role.set(role)  # subagent's model/shell calls attribute to it
        try:
            msg = f"{task}\n\n## Context from supervisor\n{context}" if context else task
            result = subagents[role].invoke(
                {"messages": [{"role": "user", "content": msg}]},
                config={"recursion_limit": 2 * cfg.roles[role].max_turns},
            )
        finally:
            current_role.set(prev)
        answer = result["messages"][-1].text  # str even when content is block-list (thinking models)
        answer = compress(answer, cfg.compressor)  # oversized results shrink before hitting supervisor
        if log:
            log.write("handoff", role=role, direction="out", payload={"result": answer[:4000]})
        return answer

    return delegate


def build_squad(cfg: SquadConfig, jail: Path, confirm: Callable[[str], bool], max_cost: float,
                run_id: str | None = None):
    """The full squad: supervisor + one subagent per configured role."""
    subagents = {name: build_agent(cfg, name, jail, confirm, run_id=run_id)
                 for name in cfg.roles if name != "supervisor"}
    roster = "\n".join(
        f"- {name} (tools: {', '.join(r.tools) or 'none'})"
        for name, r in cfg.roles.items() if name != "supervisor"
    )
    return create_deep_agent(
        model=chat_model(cfg, "supervisor"),
        tools=[build_delegate(subagents, cfg, max_cost)],
        system_prompt=cfg.roles["supervisor"].prompt.read_text()
        + f"\n\n## Configured roster (delegate by exact name)\n{roster}",
        # supervisor history is the run's accumulator — without this it grows
        # O(N²) input tokens across delegations; max_context now caps it
        middleware=history_middleware(cfg, "supervisor"),
    )
