"""Supervisor graph — delegate handoffs are logged, budget breaker halts, roster built from config."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from squad.config import load_config
from squad.graph import BudgetExceeded, build_delegate, build_squad
from squad.interceptor import RunLog, current_role

CONFIG = Path(__file__).parent.parent / "squad.yaml"


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


def test_build_squad_constructs_supervisor_with_delegate(cfg, tmp_path):
    RunLog.start(tmp_path)
    squad = build_squad(cfg, jail=tmp_path, confirm=lambda c: False, max_cost=1.0)
    tools = {t.name for t in squad.nodes["tools"].bound._tools_by_name.values()}
    assert "delegate" in tools
    assert "shell" not in tools  # supervisor delegates only — no direct tools
