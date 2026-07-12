# squad

Personal deep-agent orchestrator: heterogeneous models per role (cheap model
browses, local model compresses, mid model codes, frontier models plan and
review), full interception of inter-agent traffic, direct local shell with a
safety gate. Multiple squads can work the same repo concurrently via git
worktrees.

**Status: pre-alpha, built phase by phase.** Done: config, model router,
gated shell, interception log with per-role token/cost accounting, the
supervisor graph (multi-agent relay, logged handoffs, cost circuit breaker),
per-run git worktrees with a gated commit tool + run-end PR step, scout
browsing (`fetch` → trafilatura markdown extraction + Playwright MCP via the MCP loader), and local-model
compression at handoff boundaries (oversized context is digested by Ollama
before crossing between agents; originals stay in the log — a live check
shrank 449 tokens to 96 with every fact intact).
All planned v1 phases are built; see [PLAN.md](PLAN.md) and
[DECISIONS.md](DECISIONS.md) for why things are the way they are.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- API keys for the providers in your `squad.yaml` (OpenAI, Google, Anthropic) — **or none**, see keyless mode below
- [Ollama](https://ollama.com) for the local compression model and keyless runs

## Start

```bash
git clone <this repo> && cd squad
uv sync

# keys (skip for keyless mode)
cp .env.example .env   # fill in the keys you have

# validate config + see the roster
uv run squad check

# smoke-test every configured model (latency + cost per role)
uv run squad ping
```

## Keyless mode (no API keys)

Two options, combinable:

```bash
# 1. mock: exercises routing, contacts no provider at all
uv run squad ping --mock

# 2. local: route ALL roles to one Ollama model (real inference, free)
ollama pull qwen3:8b
uv run squad ping --override ollama_chat/qwen3:8b
uv run squad run --override ollama_chat/qwen3:8b "create hello.py that prints hi, then run it"
```

Use the `ollama_chat/` prefix for agent runs — `ollama/` lacks native tool
calling (see DECISIONS.md). Local 8B-class models handle single-role runs
(`--role coder`) fine but drive the full supervisor relay unreliably — for
multi-agent runs use real provider keys.

`--override` (or env `SQUAD_MODEL_OVERRIDE`) reroutes every role to the given
LiteLLM model string — dev/testing shim, never production.

## Use

```bash
# run a task with the full squad: supervisor delegates to planner/scout/coder/reviewer,
# every handoff is intercepted and logged
uv run squad run "add input validation to parse_user()"

# circuit breaker: halt the run when total model spend crosses the cap
uv run squad run --max-cost 0.50 "refactor the config loader"

# single role, no supervisor — cheaper for simple jobs
uv run squad run --role coder "create hello.py that prints hi, then run it"

# another repo
uv run squad run --repo ~/code/myproject --role reviewer "assess test coverage"

# unattended: never prompts. Dangerous shell commands are DECLINED (not approved);
# at run end the branch is pushed and a PR opens automatically (Phase 5) —
# you decide at merge time instead of during the run.
uv run squad run --auto "fix the failing test"
```

## Logs, tokens, cost

Every run writes an append-only JSONL to `logs/<run-id>.jsonl`: each model
call (with the full task context sent and the response), every shell command
and its verdict, every handoff — with token counts and cost per record.

```bash
uv run squad log            # pretty-print the latest run (--full for whole payloads)
uv run squad log 20260712   # or a specific run by id prefix
uv run squad cost           # per role/model: calls, tokens in/out, cost — across all runs
```

**Worktrees:** pointing `--repo` at a git repo gives the run its own worktree
(`~/.squad/worktrees/<repo>/<run-id>`) and branch (`squad/<run-id>`) — your
checkout is never touched, and concurrent squads on one repo can't collide.
Coder commits there via the gated `git_commit` tool (run-id trailer on every
commit). At run end you get branch + diffstat, then the PR step per
`git.pr` config: `confirm` asks, `auto` pushes + opens the PR unattended,
`never` keeps it local. `uv run squad clean` removes worktrees whose branches
you've merged. A non-git directory just runs in place, no worktree.

Shell commands from agents pass a safety gate (`squad.yaml → shell_rules`):
deny patterns are refused outright (rm -rf /, forkbombs, worktree removal);
confirm patterns (sudo, git push, pipe-to-shell, rm -rf) pause and ask you.
Everything else runs cwd-jailed with a timeout, output truncated.

Roles, models, tools, and rules live in [squad.yaml](squad.yaml) — edit it, own it.

## .env — keys and endpoints

`.env` (gitignored, loaded automatically at CLI start) holds only secrets and
endpoints — never model choices, those live in squad.yaml:

```bash
OPENAI_API_KEY=...          # only the providers your squad.yaml actually uses
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
# OLLAMA_API_BASE=http://localhost:11434   # only if Ollama runs elsewhere
```

**How Ollama is wired:** there is no key and no special code path. A model
string like `ollama_chat/qwen3:8b` in squad.yaml (or `--override`) makes
LiteLLM call your local Ollama HTTP API (`localhost:11434` by default,
`OLLAMA_API_BASE` to change). Which model — compressor, a role, everything —
is config in squad.yaml like any other provider.

## squad.yaml reference

One file, five sections:

```yaml
roles:                  # a role = model + prompt + tools. Add a block = add a role.
  coder:
    model: gemini/gemini-3-pro   # any LiteLLM model string; swap providers by editing this line
    prompt: prompts/coder.md     # the role's specialization, relative to this file
    tools: [shell, fs, git_commit]  # capability boundary: unlisted tool = never bound = uncallable
    max_context: 120000          # compression kicks in above this (Phase 7)
    max_turns: 20                # per-delegation loop cap

compressor:             # local model that squeezes context between agent handoffs
  model: ollama/qwen3:8b
  trigger_tokens: 50000          # compress when crossing an agent boundary above this
  keep_last_messages: 6          # working tail is never compressed

git:
  worktrees_dir: ~/.squad/worktrees  # each run works in its own worktree + branch
  branch_prefix: squad/
  commit_roles: [coder]          # who may call git_commit
  push: confirm                  # push NEVER happens without a human yes
  pr: confirm                    # run end: offer push + gh pr create (never | confirm)

shell_rules:            # gate for roles that have `shell`
  deny_patterns: [...]           # refused outright, agent is told why
  confirm_patterns: [...]        # pause, ask you in the terminal
  timeout_seconds: 120
  max_output_bytes: 100000       # output truncated beyond this

mcp_servers: {}         # your own tool servers, see below
```

Built-in tools: `shell` (gated), `fs` (read/write, jailed), `fs_read`,
`browse` (scout's `search` + `fetch`), `render` (opt-in Playwright MCP),
`git_commit`. Every name in a role's `tools` must be a built-in or an
`mcp_servers` key — config validation fails otherwise (`squad check` tells you).

## MCP servers (your own tools)

Any [MCP](https://modelcontextprotocol.io) server becomes a tool an agent can
use. Define it, then bind it by name in a role's `tools`:

```yaml
mcp_servers:
  postgres:                      # name = the tool name roles bind
    command: npx
    args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"]
    transport: stdio

roles:
  analyst:
    model: anthropic/claude-opus-4-8
    prompt: prompts/analyst.md
    tools: [fs_read, postgres]   # this role can query the DB; nobody else can
```

The binding is the security model: a role without `postgres` in its list
never gets the tool handle — nothing to jailbreak.

Same pattern for issue trackers — GitHub and Linear ship official MCP servers:

```yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]   # reads GITHUB_TOKEN from env
    transport: stdio
  linear:
    url: https://mcp.linear.app/sse                       # Linear's hosted MCP
    transport: sse

roles:
  planner:
    tools: [fs_read, github, linear]   # planner reads issues to plan from them
```

(Coder can also just `gh issue view 123` through the gated shell — no config
at all if `gh` is logged in.)

Built-in `browse` (scout's toolset) = two cheap tools
([src/squad/tools/mcp.py](src/squad/tools/mcp.py)):
`search(query)` (DuckDuckGo via `ddgs`, no API key) returns compact
title/url/snippet results instead of a raw SERP; `fetch(url)` runs the page
through trafilatura → main content as clean markdown (scripts/nav/styles
stripped, links kept), ~8–10× fewer tokens than raw HTML — and the cheap scout
model reasons better without the noise. Heavy `render` (Playwright MCP,
`npx @playwright/mcp`, spawned on demand) is a separate opt-in tool for
JS-rendered pages, so a role only pays its cold start + tool-schema tax by
listing `render` explicitly.

## Test

```bash
uv run pytest            # whole suite, offline, no keys needed
uv run pytest tests/test_rules.py -v   # just the shell-gate security tests
```

## Layout

| Path | What |
|---|---|
| `squad.yaml` | roles, models, rules, git, MCP servers — the whole product surface |
| `prompts/*.md` | role specializations |
| `src/squad/config.py` | config load + validation |
| `src/squad/router.py` | role → LiteLLM model (incl. override) |
| `src/squad/rules.py` | shell command gate: deny → confirm → allow |
| `src/squad/tools/shell.py` | gated executor: jail, timeout, truncation |
| `src/squad/agents.py` | role config → deepagents agent (tool binding = capability boundary) |
| `src/squad/graph.py` | supervisor + `delegate` handoff tool + cost breaker |
| `src/squad/interceptor.py` | JSONL run log: model calls, shell, git, handoffs |
| `src/squad/worktree.py` | per-run worktree/branch lifecycle, PR step, clean |
| `src/squad/tools/git.py` | `git_commit` tool (commit_roles only, run-id trailer) |
| `src/squad/cli.py` | `squad check / ping / run / log / cost / clean` |
