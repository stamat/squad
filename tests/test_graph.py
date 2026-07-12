"""Supervisor graph — delegate handoffs are logged, budget breaker halts, roster built from config."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from codesquad.config import load_config
from codesquad.graph import BudgetExceeded, build_delegate, build_squad
from codesquad.interceptor import RunLog, current_role

from conftest import TEMPLATE_CONFIG as CONFIG


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


class FakeAgent:
    def __init__(self, reply="done: summary"):
        self.reply = reply
        self.calls = []

    def invoke(self, payload, config=None):
        self.calls.append((payload, current_role.get()))
        return {"messages": [AIMessage(content=self.reply)]}


def records(log):
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_total_cost_accumulates(tmp_path):
    log = RunLog.start(tmp_path)
    log.write("model_call", cost_usd=0.01)
    log.write("model_call", cost_usd=0.02)
    log.write("shell")  # no cost — must not break the sum
    assert log.total_cost == pytest.approx(0.03)


def test_delegate_invokes_logs_and_restores_role(cfg, tmp_path):
    log = RunLog.start(tmp_path)
    current_role.set("supervisor")
    fake = FakeAgent("planner says: do X then Y")
    delegate = build_delegate({"planner": fake}, cfg, max_cost=1.0)

    out = delegate.invoke({"role": "planner", "task": "plan the thing", "context": "repo is empty"})

    assert "do X then Y" in out
    (call_payload, role_during_call) = fake.calls[0]
    assert role_during_call == "planner"           # subagent's calls attribute to its role
    assert current_role.get() == "supervisor"      # restored after handoff
    handoffs = [r for r in records(log) if r["kind"] == "handoff"]
    assert [h["direction"] for h in handoffs] == ["in", "out"]
    assert handoffs[0]["payload"]["task"] == "plan the thing"
    assert handoffs[0]["payload"]["context"] == "repo is empty"
    assert "do X then Y" in handoffs[1]["payload"]["result"]


def test_delegate_unknown_role_returns_error_string(cfg, tmp_path):
    RunLog.start(tmp_path)
    delegate = build_delegate({"planner": FakeAgent()}, cfg, max_cost=1.0)
    out = delegate.invoke({"role": "chef", "task": "cook"})
    assert "unknown role" in out.lower() and "planner" in out


def test_budget_breaker_halts(cfg, tmp_path):
    log = RunLog.start(tmp_path)
    log.write("model_call", cost_usd=0.60)
    fake = FakeAgent()
    delegate = build_delegate({"planner": fake}, cfg, max_cost=0.50)
    with pytest.raises(BudgetExceeded):
        delegate.invoke({"role": "planner", "task": "plan"})
    assert fake.calls == []  # never reached the subagent


def test_budget_breaker_disabled_when_max_cost_not_positive(cfg, tmp_path):
    log = RunLog.start(tmp_path)
    log.write("model_call", cost_usd=99.0)  # way over any sane budget
    fake = FakeAgent()
    delegate = build_delegate({"planner": fake}, cfg, max_cost=0)  # <=0 = off
    delegate.invoke({"role": "planner", "task": "plan"})
    assert fake.calls != []  # reached the subagent despite the huge cost


def test_review_cap_refuses_past_limit(cfg, tmp_path):
    RunLog.start(tmp_path)
    from codesquad.tools import subtasks
    subtasks.set_subtasks.invoke({"subtasks": ["one"]})
    fake = FakeAgent("needs fixes")
    delegate = build_delegate({"reviewer": fake}, cfg, max_cost=1.0)

    for _ in range(subtasks.REVIEW_CAP):
        assert "needs fixes" in delegate.invoke({"role": "reviewer", "task": "review"})
    out = delegate.invoke({"role": "reviewer", "task": "review"})
    assert "review cap" in out.lower()
    assert len(fake.calls) == subtasks.REVIEW_CAP  # capped call never reached the agent


def test_review_cap_ignores_runs_without_subtasks(cfg, tmp_path):
    RunLog.start(tmp_path)  # trivial run, no subtask stack
    fake = FakeAgent("looks good")
    delegate = build_delegate({"reviewer": fake}, cfg, max_cost=1.0)
    for _ in range(10):
        assert "looks good" in delegate.invoke({"role": "reviewer", "task": "review"})


def test_subagent_turn_overflow_returns_error_not_crash(cfg, tmp_path):
    from langgraph.errors import GraphRecursionError

    class OverflowAgent:
        def invoke(self, payload, config=None):
            raise GraphRecursionError("Recursion limit of 40 reached")

    log = RunLog.start(tmp_path)
    delegate = build_delegate({"scout": OverflowAgent()}, cfg, max_cost=1.0)
    out = delegate.invoke({"role": "scout", "task": "profile the repo"})
    assert "turn limit" in out.lower()  # supervisor gets a result, run survives
    assert current_role.get() != "scout"  # role restored despite the failure
    handoffs = [r for r in records(log) if r["kind"] == "handoff"]
    assert [h["direction"] for h in handoffs] == ["in", "out"]  # overflow still leaves a full trail
    assert "turn limit" in handoffs[1]["payload"]["result"]


def test_oversized_context_routes_through_scribe(cfg, tmp_path):
    from codesquad.graph import SCRIBE_TRIGGER
    RunLog.start(tmp_path)
    scribe = FakeAgent("curated: only the relevant facts")
    coder = FakeAgent("done")
    delegate = build_delegate({"scribe": scribe, "coder": coder}, cfg, max_cost=1.0)

    delegate.invoke({"role": "coder", "task": "implement step 1",
                     "context": "x" * (SCRIBE_TRIGGER + 1)})

    assert len(scribe.calls) == 1  # scribe pass fired
    (scribe_payload, _) = scribe.calls[0]
    assert "implement step 1" in scribe_payload["messages"][0]["content"]  # scribe sees the task
    (coder_payload, _) = coder.calls[0]
    assert "curated: only the relevant facts" in coder_payload["messages"][0]["content"]
    assert "x" * 200 not in coder_payload["messages"][0]["content"]  # raw blob never reaches coder


def test_small_context_skips_scribe(cfg, tmp_path):
    RunLog.start(tmp_path)
    scribe = FakeAgent("should never run")
    coder = FakeAgent("done")
    delegate = build_delegate({"scribe": scribe, "coder": coder}, cfg, max_cost=1.0)
    delegate.invoke({"role": "coder", "task": "implement", "context": "short note"})
    assert scribe.calls == []


def test_scribe_delegation_never_recurses(cfg, tmp_path):
    from codesquad.graph import SCRIBE_TRIGGER
    RunLog.start(tmp_path)
    scribe = FakeAgent("curated")
    delegate = build_delegate({"scribe": scribe}, cfg, max_cost=1.0)
    delegate.invoke({"role": "scribe", "task": "tidy this", "context": "x" * (SCRIBE_TRIGGER + 1)})
    assert len(scribe.calls) == 1  # direct scribe delegation, no scribe-on-scribe pass


def test_no_scribe_configured_context_passes_through(cfg, tmp_path):
    from codesquad.graph import SCRIBE_TRIGGER
    RunLog.start(tmp_path)
    coder = FakeAgent("done")
    delegate = build_delegate({"coder": coder}, cfg, max_cost=1.0)
    big = "y" * (SCRIBE_TRIGGER + 1)
    delegate.invoke({"role": "coder", "task": "implement", "context": big})
    assert "y" * 200 in coder.calls[0][0]["messages"][0]["content"]  # untouched


def test_scout_report_shrunk_by_scribe(cfg, tmp_path):
    from codesquad.graph import SCRIBE_TRIGGER
    RunLog.start(tmp_path)
    scout = FakeAgent("finding " * (SCRIBE_TRIGGER // 7))  # oversized report
    scribe = FakeAgent("report: only the task-relevant facts")
    delegate = build_delegate({"scout": scout, "scribe": scribe}, cfg, max_cost=1.0)
    out = delegate.invoke({"role": "scout", "task": "profile the repo"})
    assert len(scribe.calls) == 1
    assert "profile the repo" in scribe.calls[0][0]["messages"][0]["content"]  # shrink is task-aware
    assert out == "report: only the task-relevant facts"


def test_short_scout_report_skips_scribe(cfg, tmp_path):
    RunLog.start(tmp_path)
    scout = FakeAgent("file found: users.py")
    scribe = FakeAgent("should never run")
    delegate = build_delegate({"scout": scout, "scribe": scribe}, cfg, max_cost=1.0)
    out = delegate.invoke({"role": "scout", "task": "profile the repo"})
    assert scribe.calls == []
    assert "users.py" in out


def test_tidy_task_routes_long_prompt_through_scribe(cfg, tmp_path):
    from codesquad.graph import TIDY_TRIGGER, tidy_task
    log = RunLog.start(tmp_path)
    scribe = FakeAgent("tidied: fix parse_user validation")
    out = tidy_task(scribe, "blah " * (TIDY_TRIGGER // 4))
    assert out == "tidied: fix parse_user validation"
    handoffs = [r for r in records(log) if r["kind"] == "handoff"]
    assert [h["direction"] for h in handoffs] == ["in", "out"]  # tidy pass is on the trail


def test_tidy_task_leaves_short_prompt_alone(cfg, tmp_path):
    from codesquad.graph import tidy_task
    RunLog.start(tmp_path)
    scribe = FakeAgent("should never run")
    assert tidy_task(scribe, "fix the bug") == "fix the bug"
    assert scribe.calls == []


def test_provider_error_returns_error_not_crash(cfg, tmp_path):
    import litellm

    class TimeoutAgent:
        def invoke(self, payload, config=None):
            raise litellm.Timeout("Connection timed out after 600.0 seconds.",
                                  model="anthropic/claude-sonnet-5", llm_provider="anthropic")

    log = RunLog.start(tmp_path)
    delegate = build_delegate({"coder": TimeoutAgent()}, cfg, max_cost=1.0)
    out = delegate.invoke({"role": "coder", "task": "implement"})
    assert "provider error" in out.lower()  # supervisor gets a result, run survives
    handoffs = [r for r in records(log) if r["kind"] == "handoff"]
    assert [h["direction"] for h in handoffs] == ["in", "out"]  # trail stays complete


def test_build_squad_constructs_supervisor_with_delegate(cfg, tmp_path):
    RunLog.start(tmp_path)
    squad = build_squad(cfg, jail=tmp_path, confirm=lambda c: False, max_cost=1.0)
    tools = {t.name for t in squad.nodes["tools"].bound._tools_by_name.values()}
    assert "delegate" in tools
    assert "shell" not in tools  # supervisor delegates only — no direct tools
