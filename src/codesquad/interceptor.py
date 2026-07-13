"""Interception layer: every model call, shell command, and handoff appends one
JSONL record to the run's log. Model calls carry accounting only (model, tokens,
cost); the decision trail — what was done, how, why — lives in the handoff,
shell, git and compress records.

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
    echo: bool = False       # --verbose: mirror each record to stderr as it lands

    @classmethod
    def start(cls, logs_dir: Path, run_id: str | None = None, echo: bool = False) -> "RunLog":
        run_id = run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log = cls(run_id, logs_dir / f"{run_id}.jsonl", echo=echo)
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
        if self.echo:
            self._echo(rec)

    def _echo(self, rec: dict) -> None:
        """One human line per record on stderr: the meaningful text, not raw JSON.
        click strips the colors when stderr is not a tty."""
        import click

        p = rec["payload"] or {}
        head = p.get("task") or p.get("result") or p.get("command") or p.get("model") \
            or (json.dumps(p, default=str) if p else "")
        head = " ".join(str(head).split())  # newlines/indent → single spaces
        if p.get("verdict"):
            head += f" [{p['verdict']}]"
        tok = rec["tokens"] or {}
        meta = f" [{tok.get('in')}→{tok.get('out')} tok, ${rec['cost_usd']:.4f}]" if tok else ""
        arrow = {"in": " →", "out": " ←"}.get(rec["direction"] or "", "")
        click.echo(
            click.style(rec["ts"][11:19], dim=True) + " "
            + click.style(f"{rec['role']:<10}", fg="cyan")
            + click.style(f" {rec['kind']:<10}{arrow}", dim=True)
            + click.style(meta, fg="yellow")
            + f" {head[:160]}",
            err=True,
        )


from langchain_litellm import ChatLiteLLM  # noqa: E402  (after slots: avoids cycle)


class LoggedChat(ChatLiteLLM):
    """ChatLiteLLM that logs every call inline, in the calling thread/task.

    Not litellm callbacks: those run on a background logging worker, so
    records could land after the run ended and the cost breaker would lag.
    Inline = the record exists before the model's answer is used. The role
    rides on the instance, so concurrent delegations attribute correctly."""

    squad_role: str = "?"
    effort: str | None = None  # reasoning effort for the role

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        self._log(messages, result)
        return result

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        self._log(messages, result)
        return result

    def _log(self, messages, result) -> None:
        log = current_log.get()
        if log is None:
            return
        msg = result.generations[0].message if result.generations else None
        usage = getattr(msg, "usage_metadata", None) or {}
        tok_in, tok_out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        try:
            in_cost, out_cost = litellm.cost_per_token(
                model=self.model, prompt_tokens=tok_in, completion_tokens=tok_out)
            cost = in_cost + out_cost
        except Exception:
            cost = 0.0  # unknown/local models have no price entry
        # accounting only — full histories made the log grow O(N²) per run;
        # the handoff/shell/git records already tell what was decided and why
        log.write(
            "model_call",
            role=self.squad_role,
            payload={"model": self.model, "effort": self.effort},
            tokens={"in": tok_in, "out": tok_out},
            cost_usd=cost,
        )


def read_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_totals(path: Path) -> dict[str, dict]:
    """role → {calls, in, out, cost_usd} for one run — the end-of-run report."""
    totals: dict[str, dict] = {}
    for rec in read_run(path):
        if rec["kind"] != "model_call":
            continue
        t = totals.setdefault(rec["role"], {"calls": 0, "in": 0, "out": 0, "cost_usd": 0.0})
        t["calls"] += 1
        t["in"] += (rec.get("tokens") or {}).get("in", 0)
        t["out"] += (rec.get("tokens") or {}).get("out", 0)
        t["cost_usd"] = round(t["cost_usd"] + (rec.get("cost_usd") or 0.0), 6)
    return totals


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
