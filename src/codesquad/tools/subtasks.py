"""Per-run subtask stack: the planner pushes an ordered list, the coder pulls
one subtask at a time and marks each done after review sign-off. Backed by a
JSON file next to the run log so it survives across delegate calls, and lets
each subtask be worked in its own fresh context."""

import json
from pathlib import Path

from langchain_core.tools import tool

from codesquad.interceptor import current_log


def _store() -> Path:
    log = current_log.get()
    if log is None:  # tools only run inside a squad run
        raise RuntimeError("no active run — subtask stack needs a RunLog")
    return log.path.with_suffix(".subtasks.json")


def _load(p: Path) -> list[dict]:
    return json.loads(p.read_text()) if p.exists() else []


@tool
def set_subtasks(subtasks: list[str]) -> str:
    """Store the ordered subtask stack for this run. Overwrites any existing
    stack. Each entry is a self-contained subtask prompt the coder will pull."""
    p = _store()
    p.write_text(json.dumps([{"task": t, "done": False} for t in subtasks]))
    return f"stacked {len(subtasks)} subtasks"


@tool
def next_subtask() -> str:
    """Return the next unfinished subtask without removing it. Returns
    'all subtasks done' when the stack is exhausted."""
    items = _load(_store())
    for i, it in enumerate(items):
        if not it["done"]:
            return f"subtask {i + 1}/{len(items)}: {it['task']}"
    return "all subtasks done"


@tool
def complete_subtask() -> str:
    """Mark the current (first unfinished) subtask done, after review sign-off,
    and advance the stack."""
    p = _store()
    items = _load(p)
    for it in items:
        if not it["done"]:
            it["done"] = True
            p.write_text(json.dumps(items))
            left = sum(1 for x in items if not x["done"])
            return f"marked done; {left} subtask(s) left"
    return "no subtask to complete"
