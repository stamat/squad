"""Interception layer: every model call, shell command, and handoff appends one
JSONL record to the run's log — full task context, token counts, cost.

Uses contextvars so tools and the LiteLLM callback log without plumbing:
whatever runs inside a run logs to that run.
"""

import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import litellm


class _Slot:
    """Process-global holder. Not a ContextVar: LiteLLM fires callbacks in a
    separate thread, where ContextVars set in the main thread are invisible.
    One squad run = one process, so a global is correct here."""

    def __init__(self, default):
        self._value = default

    def set(self, value) -> None:
        self._value = value

    def get(self):
        return self._value


current_log: _Slot = _Slot(None)   # RunLog | None
current_role: _Slot = _Slot("?")


@dataclass
class RunLog:
    run_id: str
    path: Path
    total_cost: float = 0.0  # running sum, feeds the --max-cost breaker

    @classmethod
    def start(cls, logs_dir: Path, run_id: str | None = None) -> "RunLog":
        run_id = run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log = cls(run_id, logs_dir / f"{run_id}.jsonl")
        current_log.set(log)
        return log

    def write(self, kind: str, *, role: str | None = None, direction: str | None = None,
              payload: dict | None = None, tokens: dict | None = None,
              cost_usd: float | None = None) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "kind": kind,  # model_call | handoff | shell | git | compress
            "role": role or current_role.get(),
            "direction": direction,
            "payload": payload,
            "tokens": tokens,
            "cost_usd": cost_usd,
        }
        self.total_cost += cost_usd or 0.0
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")


class _Interceptor(litellm.integrations.custom_logger.CustomLogger):
    """CustomLogger, not a plain success_callback function: those run in a
    separate thread, so records could land after the run already ended (and
    the total_cost breaker would lag). CustomLogger sync events run inline."""

    def log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        log = current_log.get()
        if log is None:
            return
        usage = getattr(response_obj, "usage", None)
        try:
            cost = litellm.completion_cost(completion_response=response_obj)
        except Exception:
            cost = 0.0  # unknown/local models have no price entry
        # per-call metadata beats the current_role global: concurrent delegations clobber the global
        meta = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        log.write(
            "model_call",
            role=meta.get("role"),  # None → falls back to current_role inside write()
            payload={
                "model": kwargs.get("model"),
                "messages": kwargs.get("messages"),  # the full task context sent
                "response": response_obj.choices[0].message.content if response_obj.choices else None,
            },
            tokens={
                "in": getattr(usage, "prompt_tokens", 0) or 0,
                "out": getattr(usage, "completion_tokens", 0) or 0,
            },
            cost_usd=cost,
        )

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)


_interceptor = _Interceptor()


def install() -> None:
    """Register the LiteLLM callback (idempotent)."""
    if _interceptor not in litellm.callbacks:
        litellm.callbacks.append(_interceptor)


def read_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def aggregate(logs_dir: Path) -> dict[tuple[str, str], dict]:
    """(role, model) → {calls, in, out, cost_usd} across all runs in logs_dir."""
    totals: dict[tuple[str, str], dict] = {}
    for f in sorted(logs_dir.glob("*.jsonl")):
        for rec in read_run(f):
            if rec["kind"] != "model_call":
                continue
            key = (rec["role"], (rec.get("payload") or {}).get("model", "?"))
            t = totals.setdefault(key, {"calls": 0, "in": 0, "out": 0, "cost_usd": 0.0})
            t["calls"] += 1
            t["in"] += (rec.get("tokens") or {}).get("in", 0)
            t["out"] += (rec.get("tokens") or {}).get("out", 0)
            t["cost_usd"] = round(t["cost_usd"] + (rec.get("cost_usd") or 0.0), 6)
    return totals
