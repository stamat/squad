"""Interceptor — every model call and shell command lands in the run's JSONL.
Model calls carry accounting only (model, tokens, cost); the decision trail —
what was done, how, why — lives in handoff/shell/git/compress records. Offline."""

import json
from pathlib import Path

from codesquad.config import ShellRules, load_config
from codesquad.interceptor import RunLog, aggregate, current_log, current_role, read_run
from codesquad.router import chat_model
from codesquad.tools.shell import run_shell

CONFIG = Path(__file__).parent.parent / "squad.yaml"


def records(log: RunLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_write_creates_valid_jsonl(tmp_path):
    log = RunLog.start(tmp_path)
    log.write("handoff", role="supervisor", payload={"task": "do x"})
    (rec,) = records(log)
    assert rec["run_id"] == log.run_id
    assert rec["kind"] == "handoff"
    assert rec["role"] == "supervisor"
    assert rec["payload"] == {"task": "do x"}
    assert rec["ts"]


def test_model_call_logged_accounting_only(tmp_path):
    # decisions live in handoff/shell/git records; model_call is pure accounting —
    # embedding full message history made the log grow O(N²)
    cfg = load_config(CONFIG)
    log = RunLog.start(tmp_path)
    m = chat_model(cfg, "planner")
    m.model_kwargs["mock_response"] = "a plan"  # offline; flows through to litellm
    m.invoke("plan something")
    (rec,) = [r for r in records(log) if r["kind"] == "model_call"]
    assert rec["role"] == "planner"
    assert rec["payload"]["model"]
    assert "messages" not in rec["payload"] and "response" not in rec["payload"]
    assert rec["tokens"]["in"] > 0 and rec["tokens"]["out"] > 0


def test_shell_command_logged(tmp_path):
    log = RunLog.start(tmp_path)
    current_role.set("coder")
    rules = ShellRules(deny_patterns=[r"\bforbidden\b"])
    run_shell("echo logged", rules, tmp_path, lambda c: True)
    run_shell("forbidden thing", rules, tmp_path, lambda c: True)
    recs = [r for r in records(log) if r["kind"] == "shell"]
    assert len(recs) == 2
    assert recs[0]["payload"]["cmd"] == "echo logged"
    assert "logged" in recs[0]["payload"]["result"]
    assert recs[1]["payload"]["verdict"] == "deny"


def test_model_call_role_rides_instance_not_global(tmp_path):
    # concurrent delegations clobber the current_role global; the role on the
    # model instance travels with the individual call and must win
    cfg = load_config(CONFIG)
    log = RunLog.start(tmp_path)
    current_role.set("reviewer")  # the "wrong" concurrent value
    m = chat_model(cfg, "coder")
    m.model_kwargs["mock_response"] = "ok"
    m.invoke("hi")
    (rec,) = [r for r in records(log) if r["kind"] == "model_call"]
    assert rec["role"] == "coder"


def test_no_active_log_is_fine(tmp_path):
    current_log.set(None)
    out = run_shell("echo quiet", ShellRules(), tmp_path, lambda c: True)  # must not raise
    assert "quiet" in out


def test_read_run_and_aggregate(tmp_path):
    log = RunLog.start(tmp_path)
    log.write("model_call", role="coder", payload={"model": "m1"},
              tokens={"in": 100, "out": 50}, cost_usd=0.002)
    log.write("model_call", role="coder", payload={"model": "m1"},
              tokens={"in": 10, "out": 5}, cost_usd=0.001)
    log.write("model_call", role="reviewer", payload={"model": "m2"},
              tokens={"in": 7, "out": 3}, cost_usd=0.0005)
    assert len(read_run(log.path)) == 3
    totals = aggregate(tmp_path)
    assert totals[("coder", "m1")] == {"calls": 2, "in": 110, "out": 55, "cost_usd": 0.003}
    assert totals[("reviewer", "m2")]["calls"] == 1
