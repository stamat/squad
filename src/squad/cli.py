"""squad CLI — run / ping / log / cost / clean."""

import json
import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from squad.config import SquadConfig, load_config

app = typer.Typer(no_args_is_help=True, help="Heterogeneous multi-agent squads with full traffic interception.")

CONFIG_OPT = typer.Option(Path("squad.yaml"), "--config", "-c", help="Path to squad.yaml")
LOGS_DIR = Path("logs")
OVERRIDE_OPT = typer.Option(
    None, "--override", "-o",
    help="Route ALL roles to this LiteLLM model (e.g. ollama/gemma3n) — keyless dev. Env: SQUAD_MODEL_OVERRIDE",
)


def _apply_override(model: str | None) -> None:
    if model:
        os.environ["SQUAD_MODEL_OVERRIDE"] = model


def _load(config: Path) -> SquadConfig:
    load_dotenv()
    try:
        return load_config(config)
    except (ValueError, FileNotFoundError) as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(1) from e


@app.command()
def check(config: Path = CONFIG_OPT) -> None:
    """Validate config and print the roster."""
    cfg = _load(config)
    typer.secho(f"config OK — {len(cfg.roles)} roles", fg="green")
    for name, role in cfg.roles.items():
        tools = ", ".join(role.tools) or "—"
        typer.echo(f"  {name:<12} {role.model:<32} tools: {tools}")


@app.command()
def run(
    task: str,
    repo: Path = typer.Option(None, "--repo", help="Target repo (worktree created per run)"),
    max_cost: float = typer.Option(1.0, "--max-cost", help="USD circuit breaker"),
    config: Path = CONFIG_OPT,
    override: str = OVERRIDE_OPT,
    role: str = typer.Option(None, "--role", help="Run a single role instead of the full squad"),
    auto: bool = typer.Option(False, "--auto", help="Unattended: never prompts. Confirm-gated shell commands are DECLINED (not approved); at run end push + PR happen automatically (Phase 5)."),
) -> None:
    """Run a squad on a task (supervisor graph; --role for a lone agent)."""
    from squad.agents import build_agent  # lazy: heavy imports
    from squad.graph import BudgetExceeded, build_squad
    from squad.interceptor import RunLog, current_role, install

    _apply_override(override)
    cfg = _load(config)
    if role and role not in cfg.roles:
        typer.secho(f"unknown role {role!r}; have: {', '.join(cfg.roles)}", fg="red", err=True)
        raise typer.Exit(1)
    log = RunLog.start(LOGS_DIR)
    install()
    entry = role or "supervisor"
    current_role.set(entry)
    log.write("handoff", direction="in", payload={"task": task})
    # ponytail: cwd-jailed until Phase 5 gives each run its own worktree.
    jail = (repo or Path.cwd()).resolve()
    # auto mode declines rather than approves: unattended runs must not self-authorize sudo/rm/pushes
    confirm = (lambda c: False) if auto else (lambda c: typer.confirm(f"allow shell command? {c}"))
    agent = (build_agent(cfg, role, jail, confirm) if role
             else build_squad(cfg, jail, confirm, max_cost))
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": task}]},
            config={"recursion_limit": 2 * cfg.roles[entry].max_turns},
        )
        answer = result["messages"][-1].text  # str even when content is block-list (thinking models)
    except BudgetExceeded as e:
        answer = f"HALTED: {e}"
        typer.secho(answer, fg="red", err=True)
    log.write("handoff", direction="out", payload={"result": answer})
    if not answer.startswith("HALTED"):
        typer.echo(answer)
    typer.secho(f"\nrun {log.run_id} — cost ${log.total_cost:.4f} — log: {log.path}", fg="cyan")
    if answer.startswith("HALTED"):
        raise typer.Exit(3)


@app.command()
def ping(
    config: Path = CONFIG_OPT,
    mock: bool = typer.Option(False, "--mock", help="Fake responses via LiteLLM mock — no keys, no network"),
    override: str = OVERRIDE_OPT,
) -> None:
    """Smoke-test every configured model."""
    from squad.router import ping_role  # lazy: litellm import is slow

    _apply_override(override)
    cfg = _load(config)
    failed = False
    for role in cfg.roles:
        r = ping_role(cfg, role, mock=mock)
        failed |= not r.ok
        mark = typer.style("✓", fg="green") if r.ok else typer.style("✗", fg="red")
        typer.echo(f"  {mark} {r.role:<12} {r.model:<32} {r.latency_s:6.2f}s  ${r.cost_usd:.4f}  {r.reply[:80]}")
    if mock:
        typer.secho("(mock mode — routing exercised, providers not contacted)", fg="yellow")
    raise typer.Exit(1 if failed else 0)


@app.command()
def log(
    run_id: str = typer.Argument(None, help="Run to display; latest if omitted"),
    full: bool = typer.Option(False, "--full", help="Print complete payloads (whole task context)"),
) -> None:
    """Pretty-print a run's interception log."""
    from squad.interceptor import read_run

    files = sorted(LOGS_DIR.glob(f"{run_id or ''}*.jsonl"))
    if not files:
        typer.secho(f"no run logs match {run_id or '*'} in {LOGS_DIR}/", fg="red", err=True)
        raise typer.Exit(1)
    path = files[-1]
    typer.secho(f"— {path}", fg="cyan")
    for rec in read_run(path):
        payload = json.dumps(rec["payload"], indent=2 if full else None, default=str) or ""
        if not full and len(payload) > 160:
            payload = payload[:160] + "…"
        tok = rec.get("tokens") or {}
        meta = f" [{tok.get('in')}→{tok.get('out')} tok, ${rec['cost_usd']:.4f}]" if tok else ""
        typer.echo(f"{rec['ts'][11:19]} {rec['role']:<10} {rec['kind']:<10}{meta} {payload}")


@app.command()
def cost() -> None:
    """Aggregate cost per role/model across runs."""
    from squad.interceptor import aggregate

    totals = aggregate(LOGS_DIR)
    if not totals:
        typer.secho(f"no model calls logged yet in {LOGS_DIR}/", fg="yellow")
        raise typer.Exit(0)
    typer.echo(f"{'role':<12} {'model':<36} {'calls':>5} {'in':>9} {'out':>8} {'cost $':>9}")
    for (role, model), t in sorted(totals.items()):
        typer.echo(f"{role:<12} {model:<36} {t['calls']:>5} {t['in']:>9} {t['out']:>8} {t['cost_usd']:>9.4f}")
    typer.echo(f"{'total':<12} {'':<36} {'':>5} {'':>9} {'':>8} {sum(t['cost_usd'] for t in totals.values()):>9.4f}")


@app.command()
def clean(config: Path = CONFIG_OPT) -> None:
    """Remove finished worktrees (merged or discarded branches)."""
    _load(config)
    typer.secho("not yet: worktrees land in Phase 5", fg="yellow")
    raise typer.Exit(2)


def main() -> None:
    app()
