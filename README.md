# codesquad

> Installs as `codesquad`, driven by the `squad` command.

Personal deep-agent orchestrator: heterogeneous models per role (cheap model
browses, local model compresses, mid model codes, frontier models plan and
review), full interception of inter-agent traffic, direct local shell with a
safety gate. Multiple squads can work the same repo concurrently via git
worktrees.

**Status: pre-alpha, built phase by phase.** Done: config, model router,
gated shell, interception log with per-role token/cost accounting, the
supervisor graph (multi-agent relay, logged handoffs, cost circuit breaker),
per-run git worktrees with a gated commit tool + run-end PR step, scout
browsing (`fetch` → trafilatura markdown extraction + Playwright MCP via the MCP loader), task intake
(`gh:123` fetches the GitHub issue, `linear:ABC-123` routes to Linear's MCP,
and the run's report is posted back on the issue), run documents
(scout persists its report / code style note / PR notes to `logs/<run-id>/`;
the PR notes become the pull request body), and local-model
compression at handoff boundaries (oversized context is digested by Ollama
before crossing between agents, chunked to fit the local model's window — a
live check shrank 449 tokens to 96 with every fact intact), and in-loop
history compression (each role's live message list is summarized by the local
model when it crosses the role's `max_context`; the last `keep_last_messages`
stay verbatim).
All planned v1 phases are built; see [CONCEPT.md](CONCEPT.md), [PLAN.md](PLAN.md) and
[DECISIONS.md](DECISIONS.md) for why things are the way they are.

## Quick start

```bash
uv tool install codesquad       # puts `squad` on your PATH
cd ~/code/myproject
squad init                      # scaffold codesquad.yaml + prompts/ + .env
$EDITOR .env                    # add provider keys (or skip — see keyless mode)
squad check                     # validate config + see the roster
squad run "add input validation to parse_user()"
```

No API keys? Run everything on a local Ollama model:

```bash
ollama pull qwen3:8b
squad run --override ollama_chat/qwen3:8b --role coder "create hello.py that prints hi, then run it"
```

Details: [Start](#start) (install on PATH, `squad init`), [Keyless mode](#keyless-mode-no-api-keys), [Use](#use).

## Flow

```
 squad run "gh:123"                       (or linear:ABC-123, or plain prompt)
        │
        ▼
 ┌──────────────┐   gh issue view / Linear MCP → task text, slug, closes-ref
 │    intake    │
 └──────┬───────┘
        ▼
 ┌──────────────┐   git repo? → own worktree + branch (squad/<slug>-<id>)
 │   worktree   │   plain dir → run in place
 └──────┬───────┘
        ▼
 ┌──────────────┐   frontier model; only tool: delegate(role, task, context)
 │  SUPERVISOR  │◄────────────────────────────────────────────┐
 └──────┬───────┘                                             │
        │ delegate = the single interception point:           │ result
        │  • handoff logged to logs/<run-id>.jsonl            │ (compressed
        │  • context compressed by local Ollama if oversized  │  if oversized)
        │  • cost breaker: total spend > --max-cost → HALT    │
        │  • review cap: >3 reviews per subtask → escalate    │
        ▼                                                     │
 ┌─────────┬─────────┬─────────┬──────────┐                   │
 │ planner │  scout  │  coder  │ reviewer │───────────────────┘
 └────┬────┴────┬────┴────┬────┴────┬─────┘
      │         │         │         │        each role = own model + prompt
   subtask   browse/    shell     fs_read    + tool list from codesquad.yaml;
   stack     fetch,     (gated),  (read-     unlisted tool = never bound
             save_doc   fs, git_  only)
                        commit
        │
        ▼
 ┌──────────────┐   branch + diffstat → push + PR (never | confirm | auto),
 │   run end    │   body from pr-notes.md; report posted back on the issue;
 └──────────────┘   run cost + log path printed
```

Every arrow through `delegate` is logged: task + context in, result out —
the whole decision trail replayable from one JSONL.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- API keys for the providers in your `codesquad.yaml` (OpenAI, Google, Anthropic) — **or none**, see keyless mode below
- [Ollama](https://ollama.com) for the local compression model and keyless runs

## Start

```bash
git clone https://github.com/stamat/codesquad.git && cd codesquad
uv sync

# scaffold an editable config into the cwd: codesquad.yaml + prompts/ + .env
uv run squad init
$EDITOR .env           # fill in the keys you have (skip for keyless mode)

# validate config + see the roster
uv run squad check

# smoke-test every configured model (latency + cost per role)
uv run squad ping
```

`squad init` is optional: with no local `codesquad.yaml` the CLI falls back to the
bundled defaults, so `check`/`run` work immediately. Run `init` when you want to edit
the roster, prompts, or rules — it writes `codesquad.yaml` + `prompts/` and, for `.env`,
appends any missing provider keys without touching values you've already set.

Install the `squad` (and `codesquad`) command on your PATH — run from any repo:

```bash
uv tool install .
cd ~/code/myproject
squad init                       # scaffold config here (or rely on the bundled defaults)
squad run "add input validation to parse_user()"
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

# a GitHub issue: fetched via `gh issue view --json` (exact fields, no token waste);
# the branch is named after it (squad/gh-123-…) and the run's report is posted
# back as an issue comment. linear:ABC-123 routes through Linear's MCP server.
uv run squad run "gh:123"

# unattended: never prompts. Dangerous shell commands are DECLINED (not approved);
# at run end the branch is pushed and a PR opens automatically (Phase 5) —
# you decide at merge time instead of during the run.
uv run squad run --auto "fix the failing test"
```

## Logs, tokens, cost

Every run writes an append-only JSONL to `logs/<run-id>.jsonl`. Model calls
are accounting records (model, tokens, cost); the decision trail — what was
done, how and why — lives in the handoff records (task + context in, result
out), shell commands with their verdicts, commits, and compression digests.
Run documents the scout saves (`report.md`, `code-style.md`, `pr-notes.md`)
land next to the log in `logs/<run-id>/`.

```bash
uv run squad log            # pretty-print the latest run (--full for whole payloads)
uv run squad log 20260712   # or a specific run by id prefix
uv run squad cost           # per role/model: calls, tokens in/out, cost — across all runs
```

**Worktrees:** pointing `--repo` at a git repo gives the run its own worktree
and branch, named from the task (`squad/<slug>-<id>`, e.g. `squad/gh-123-a1b2c3`
or `squad/fix-the-login-bug-a1b2c3`) — your
checkout is never touched, and concurrent squads on one repo can't collide.
Coder commits there via the gated `git_commit` tool (run-id trailer on every
commit). At run end you get branch + diffstat, then the PR step per
`git.pr` config: `confirm` asks, `auto` pushes + opens the PR unattended,
`never` keeps it local. `uv run squad clean` removes worktrees whose branches
you've merged. A non-git directory just runs in place, no worktree.

Shell commands from agents pass a safety gate (`codesquad.yaml → shell_rules`):
deny patterns are refused outright (rm -rf /, forkbombs, worktree removal);
confirm patterns (sudo, git push, pipe-to-shell, rm -rf) pause and ask you.
Everything else runs cwd-jailed with a timeout; long output is cut in the
middle (head + tail kept) so the agent sees the first error and the final
summary without drowning its context.

Roles, models, tools, and rules live in [codesquad.yaml](src/codesquad/templates/codesquad.yaml)
(the bundled default; `squad init` copies it into your cwd to edit) — own it.

## .env — keys and endpoints

`.env` (gitignored, loaded automatically at CLI start) holds only secrets and
endpoints — never model choices, those live in codesquad.yaml:

```bash
OPENAI_API_KEY=...          # only the providers your codesquad.yaml actually uses
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
# OLLAMA_API_BASE=http://localhost:11434   # only if Ollama runs elsewhere
```

**How Ollama is wired:** there is no key and no special code path. A model
string like `ollama_chat/qwen3:8b` in codesquad.yaml (or `--override`) makes
LiteLLM call your local Ollama HTTP API (`localhost:11434` by default,
`OLLAMA_API_BASE` to change). Which model — compressor, a role, everything —
is config in codesquad.yaml like any other provider.

Same goes for any other LiteLLM-supported provider — e.g. a GitHub Copilot
subscription: `model: github_copilot/gpt-4.1` on a role just works (first call
runs GitHub's device-flow auth; note Copilot is flat-rate, so those calls
show $0 in cost accounting and never trip `--max-cost`).

## codesquad.yaml reference

One file, five sections:

```yaml
roles: # a role = model + prompt + tools. Add a block = add a role.
  coder:
    model: gemini/gemini-3-pro # any LiteLLM model string; swap providers by editing this line
    prompt: prompts/coder.md # the role's specialization, relative to this file ({principles} expands here)
    # system: "You are …"    # OR an inline system message instead of a prompt file (exactly one of the two)
    tools: [shell, fs, git_commit] # capability boundary: unlisted tool = never bound = uncallable
    max_context: 120000 # live history above this is summarized by the local compressor
    max_turns: 20 # per-delegation loop cap

compressor: # local model that squeezes context between agent handoffs
  model: ollama/qwen3:8b
  trigger_tokens: 50000 # compress when crossing an agent boundary above this
  window_tokens: 8000 # the local model's context window; input is chunked to fit
  keep_last_messages: 6 # working tail is never compressed

git:
  worktrees_dir: ~/.squad/worktrees # each run works in its own worktree + branch
  branch_prefix: squad/
  commit_roles: [coder] # who may call git_commit
  push: confirm # push NEVER happens without a human yes
  pr: confirm # run end: offer push + gh pr create (never | confirm)

shell_rules: # gate for roles that have `shell`
  deny_patterns: [...] # refused outright, agent is told why
  confirm_patterns: [...] # pause, ask you in the terminal
  timeout_seconds: 120
  max_output_bytes: 10000 # agent-visible cap; head+tail kept, middle cut

mcp_servers: {} # your own tool servers, see below
```

### Agent tools

A role's `tools` list is its capability boundary — a tool not in the list is
never bound, so the agent physically cannot call it. Built-ins:

| Tool | What it does | Bound to (default roster) |
| ---- | ------------ | ------------------------- |
| `shell` | Run a shell command through the safety gate (deny → confirm → allow), cwd-jailed, timed out, output truncated head+tail | coder |
| `fs` | Read/write files, jailed to the run's worktree (`..` and absolute escapes blocked) | coder |
| `fs_read` | Read-only file access — writes denied by filesystem permission, not just by prompt | planner, scout, reviewer |
| `browse` | Scout's web pair: `search(query)` (DuckDuckGo via `ddgs`, no key) + `fetch(url)` (trafilatura → clean markdown) | scout |
| `render` | Playwright MCP for JS-rendered pages — opt-in, spawned on demand (pays its own cold-start + schema tax) | — |
| `git_commit` | Commit in the run worktree; `commit_roles` only, run-id trailer on every commit | coder |
| `save_doc` | Persist run documents (`report.md`, `code-style.md`, `pr-notes.md`) to `logs/<run-id>/` | scout |
| `profile` | Linguist-style repo profile: language shares + test/lint tooling, one deterministic call (no model turns) | scout |
| `set_subtasks` | Planner pushes the ordered plan onto the subtask stack | planner |
| `next_subtask` | Coder pulls the next subtask | coder |
| `complete_subtask` | Coder marks the current subtask done after review | coder |

Plus any `mcp_servers` you define — bound by name, same list (see below).
Every name in a role's `tools` must be a built-in or an `mcp_servers` key —
config validation fails otherwise (`squad check` tells you).

## MCP servers (your own tools)

Any [MCP](https://modelcontextprotocol.io) server becomes a tool an agent can
use. Define it, then bind it by name in a role's `tools`:

```yaml
mcp_servers:
  postgres: # name = the tool name roles bind
    command: npx
    args:
      [
        "-y",
        "@modelcontextprotocol/server-postgres",
        "postgresql://localhost/mydb",
      ]
    transport: stdio

roles:
  analyst:
    model: anthropic/claude-opus-4-8
    prompt: prompts/analyst.md
    tools: [fs_read, postgres] # this role can query the DB; nobody else can
```

The binding is the security model: a role without `postgres` in its list
never gets the tool handle — nothing to jailbreak.

Same pattern for issue trackers — GitHub and Linear ship official MCP servers:

```yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"] # reads GITHUB_TOKEN from env
    transport: stdio
  linear:
    url: https://mcp.linear.app/sse # Linear's hosted MCP
    transport: sse

roles:
  planner:
    tools: [fs_read, github, linear] # planner reads issues to plan from them
```

(Coder can also just `gh issue view 123` through the gated shell — no config
at all if `gh` is logged in.)

Built-in `browse` (scout's toolset) = two cheap tools
([src/codesquad/tools/mcp.py](src/codesquad/tools/mcp.py)):
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

| Path                         | What                                                                |
| ---------------------------- | ------------------------------------------------------------------- |
| `src/codesquad/templates/`   | bundled defaults shipped in the wheel: `codesquad.yaml`, `prompts/`, `env.example` |
| `src/codesquad/cli.py`       | `squad init / check / run / ping / log / cost / clean`              |
| `src/codesquad/config.py`        | config load + validation                                            |
| `src/codesquad/router.py`        | role → LiteLLM model (incl. override)                               |
| `src/codesquad/rules.py`         | shell command gate: deny → confirm → allow                          |
| `src/codesquad/tools/shell.py`   | gated executor: jail, timeout, truncation                           |
| `src/codesquad/agents.py`        | role config → deepagents agent (tool binding = capability boundary) |
| `src/codesquad/graph.py`         | supervisor + `delegate` handoff tool + cost breaker                 |
| `src/codesquad/interceptor.py`   | JSONL run log: model calls, shell, git, handoffs                    |
| `src/codesquad/worktree.py`      | per-run worktree/branch lifecycle, PR step, clean                   |
| `src/codesquad/tools/git.py`     | `git_commit` tool (commit_roles only, run-id trailer)               |
| `src/codesquad/intake.py`        | task router: `gh:123` / `linear:ABC-123` / plain prompt             |
| `src/codesquad/tools/docs.py`    | `save_doc`: run documents (report, code style, PR notes)            |
| `src/codesquad/tools/profile.py` | linguist-style repo profile: languages + tooling, zero model turns  |

---

Built on the coding practices I've distilled over my 20th year of professional
experience.

Made with :heart: by [@stamat](https://github.com/stamat)
