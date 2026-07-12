"""squad CLI — run / ping / log / cost / clean."""

import json
import os
import shutil
from pathlib import Path

import typer
from dotenv import load_dotenv

from codesquad.config import SquadConfig, load_config

app = typer.Typer(no_args_is_help=True, help="Heterogeneous multi-agent squads with full traffic interception.")

# Bundled defaults, shipped inside the wheel (installs unzipped → __file__-relative path is reliable).
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_CONFIG = Path("codesquad.yaml")
CONFIG_OPT = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Path to codesquad.yaml (falls back to the bundled default)")
LOGS_DIR = Path("logs")
OVERRIDE_OPT = typer.Option(
    None, "--override", "-o",
    help="Route ALL roles to this LiteLLM model (e.g. ollama/gemma3n) — keyless dev. Env: SQUAD_MODEL_OVERRIDE",
)


def _apply_override(model: str | None) -> None:
    if model:
        os.environ["SQUAD_MODEL_OVERRIDE"] = model


def _resolve_config(config: Path) -> Path:
    """Local codesquad.yaml wins; if the default name is absent, use the bundled template
    (so an installed tool — or the repo before `squad init` — works out of the box)."""
    if not config.exists() and config == DEFAULT_CONFIG:
        return TEMPLATES_DIR / "codesquad.yaml"
    return config


def _load(config: Path) -> SquadConfig:
    load_dotenv()
    try:
        return load_config(_resolve_config(config))
    except (ValueError, FileNotFoundError) as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        typer.secho("run 'squad init' to scaffold a codesquad.yaml you can edit", fg="yellow", err=True)
        raise typer.Exit(1) from e


def merge_env(existing: str, template: str) -> tuple[str, list[str]]:
    """Append the template's `KEY=` lines whose key is absent from `existing`.
    Existing values are never touched; nothing is duplicated. Returns (text, added_keys)."""
    def keys(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                out.append(s.split("=", 1)[0].strip())
        return out

    have = set(keys(existing))
    add_lines, added = [], []
    for line in template.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key not in have:
                add_lines.append(line)
                added.append(key)
                have.add(key)
    if not add_lines:
        return existing, []
    sep = "" if existing.endswith("\n") else "\n"
    merged = f"{existing}{sep}\n# added by squad init\n" + "\n".join(add_lines) + "\n"
    return merged, added


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Where to scaffold (default: current dir)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing codesquad.yaml and prompt files"),
) -> None:
    """Scaffold codesquad.yaml + prompts/ + .env into a directory."""
    directory.mkdir(parents=True, exist_ok=True)

    def copy(src: Path, dst: Path) -> None:
        if dst.exists() and not force:
            typer.secho(f"skip {dst} (exists; --force to overwrite)", fg="yellow")
        else:
            shutil.copyfile(src, dst)
            typer.secho(f"wrote {dst}", fg="green")

    copy(TEMPLATES_DIR / "codesquad.yaml", directory / "codesquad.yaml")
    (directory / "prompts").mkdir(exist_ok=True)
    for src in sorted((TEMPLATES_DIR / "prompts").glob("*.md")):
        copy(src, directory / "prompts" / src.name)

    env_dst = directory / ".env"
    template = (TEMPLATES_DIR / "env.example").read_text()
    if env_dst.exists():
        merged, added = merge_env(env_dst.read_text(), template)
        if added:
            env_dst.write_text(merged)
            typer.secho(f"updated {env_dst}: added {', '.join(added)}", fg="green")
        else:
            typer.secho(f"{env_dst} already has every key", fg="yellow")
    else:
        env_dst.write_text(template)
        typer.secho(f"wrote {env_dst}", fg="green")

    typer.secho("done — edit codesquad.yaml + .env, then: squad check", fg="cyan")


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
    max_cost: float = typer.Option(1.0, "--max-cost", help="USD circuit breaker; <=0 disables it"),
    config: Path = CONFIG_OPT,
    override: str = OVERRIDE_OPT,
    role: str = typer.Option(None, "--role", help="Run a single role instead of the full squad"),
    auto: bool = typer.Option(False, "--auto", help="Unattended: never prompts. Confirm-gated shell commands are DECLINED (not approved); at run end push + PR happen automatically (Phase 5)."),
) -> None:
    """Run a squad on a task (supervisor graph; --role for a lone agent)."""
    from codesquad.agents import build_agent  # lazy: heavy imports
    from codesquad.graph import BudgetExceeded, build_squad
    from codesquad.intake import comment_on_issue, resolve_task
    from codesquad.interceptor import RunLog, current_role

    _apply_override(override)
    cfg = _load(config)
    if role and role not in cfg.roles:
        typer.secho(f"unknown role {role!r}; have: {', '.join(cfg.roles)}", fg="red", err=True)
        raise typer.Exit(1)
    from codesquad import worktree as wtree

    # input router: gh:123 fetches the issue, linear:ABC-123 tags it, else pass-through
    target = (repo or Path.cwd()).resolve()
    try:
        job = resolve_task(task, target)
    except RuntimeError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1) from e
    task = job.text
    # config validation guarantees a 'linear' tool name implies a configured server
    if job.linear_issue and not any("linear" in r.tools for r in cfg.roles.values()):
        typer.secho("warning: linear task, but no role binds a 'linear' MCP tool — "
                    "the squad cannot fetch the issue. Add mcp_servers.linear and list "
                    "it in a role's tools (see README).", fg="yellow", err=True)

    log = RunLog.start(LOGS_DIR)
    entry = role or "supervisor"
    current_role.set(entry)
    log.write("handoff", direction="in", payload={"task": task})
    # git repo → own worktree + branch per run; plain dir → work in place
    wt = (wtree.create(target, log.run_id, cfg.git, slug=job.slug)
          if (target / ".git").exists() else None)
    if wt:
        typer.secho(f"worktree {wt.path} (branch {wt.branch})", fg="cyan")
    jail = wt.path if wt else target
    # auto mode declines rather than approves: unattended runs must not self-authorize sudo/rm/pushes
    confirm = (lambda c: False) if auto else (lambda c: typer.confirm(f"allow shell command? {c}"))
    run_id = log.run_id if wt else None
    if not role and "scribe" in cfg.roles:  # CONCEPT: scribe tidies the prompt before discovery
        from codesquad.graph import tidy_task
        task = tidy_task(build_agent(cfg, "scribe", jail, confirm, run_id=run_id), task)
    agent = (build_agent(cfg, role, jail, confirm, run_id=run_id) if role
             else build_squad(cfg, jail, confirm, max_cost, run_id=run_id))
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
    from codesquad.tools.docs import docs_dir

    def doc_or(name: str, fallback: str) -> str:  # run doc if the scout wrote it
        p = docs_dir(log.path, log.run_id) / name
        return p.read_text() if p.exists() else fallback

    if wt:
        typer.echo(wtree.summary(wt))
        mode = "auto" if auto else cfg.git.pr
        if mode == "auto" or (mode == "confirm" and typer.confirm("push branch and open a PR?")):
            typer.echo(wtree.push_and_pr(wt, task, body=doc_or("pr-notes.md", answer),
                                         closes=job.closes))
    if job.gh_issue:  # report back where the task came from
        typer.echo(comment_on_issue(job.gh_issue, doc_or("report.md", answer), target))
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
    from codesquad.router import ping_role  # lazy: litellm import is slow

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
    from codesquad.interceptor import read_run

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
    from codesquad.interceptor import aggregate

    totals = aggregate(LOGS_DIR)
    if not totals:
        typer.secho(f"no model calls logged yet in {LOGS_DIR}/", fg="yellow")
        raise typer.Exit(0)
    typer.echo(f"{'role':<12} {'model':<36} {'calls':>5} {'in':>9} {'out':>8} {'cost $':>9}")
    for (role, model), t in sorted(totals.items()):
        typer.echo(f"{role:<12} {model:<36} {t['calls']:>5} {t['in']:>9} {t['out']:>8} {t['cost_usd']:>9.4f}")
    typer.echo(f"{'total':<12} {'':<36} {'':>5} {'':>9} {'':>8} {sum(t['cost_usd'] for t in totals.values()):>9.4f}")


@app.command()
def clean(
    repo: Path = typer.Option(None, "--repo", help="Repo whose run worktrees to clean (default: cwd)"),
    config: Path = CONFIG_OPT,
) -> None:
    """Remove finished worktrees (branches merged into HEAD)."""
    from codesquad.worktree import clean as clean_worktrees

    cfg = _load(config)
    target = (repo or Path.cwd()).resolve()
    if not (target / ".git").exists():
        typer.secho(f"not a git repo: {target}", fg="red", err=True)
        raise typer.Exit(1)
    removed = clean_worktrees(target, cfg.git)
    for p in removed:
        typer.echo(f"removed {p}")
    typer.secho(f"{len(removed)} worktree(s) removed", fg="green")


def main() -> None:
    app()
