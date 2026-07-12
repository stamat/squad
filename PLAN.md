# squad — Plan

A personal, open-source deep-agent orchestrator. Heterogeneous models per role
(cheap model browses, local model compresses, mid model codes, frontier models
plan and review), full interception of inter-agent traffic, user-owned tool
layer, direct local shell execution (no Docker). Multiple squads can work the
same repo concurrently via git worktrees.

**Stack:** Python 3.12+ / uv. LangGraph (loop) + deepagents (agent harness) +
LiteLLM (model routing) + MCP (custom tools) + Playwright MCP (browsing) +
Ollama (local compression model).

**One-sentence scope:** Routes deep-agent work across models by task type,
with full traffic interception. Everything outside that sentence gets a "no".

---

## 1. Architecture

```
                        ┌─────────────────────────────┐
                        │        CLI (squad run)      │
                        └──────────────┬──────────────┘
                                       │ task prompt
                        ┌──────────────▼──────────────┐
                        │   Supervisor (LangGraph)    │  ← the loop
                        │   routes, delegates, decides│
                        └─┬───────┬───────┬───────┬───┘
             handoff msgs │       │       │       │   (all handoffs pass
                      ┌───▼───┐ ┌─▼───┐ ┌─▼───┐ ┌─▼────┐  through interceptor)
                      │planner│ │scout│ │coder│ │review│
                      └───┬───┘ └──┬──┘ └──┬──┘ └──┬───┘
 models (via LiteLLM):  opus-4.8 flash-lt gemini  opus-4.8
 tools:                 read-fs  browse  shell+fs read-fs
                                         +git
                                       │
                        ┌──────────────▼──────────────┐
                        │  compressor (Ollama, local) │  ← squeezes context
                        │  runs between handoffs when │     before it crosses
                        │  state exceeds threshold    │     agent boundaries
                        └─────────────────────────────┘

   Cross-cutting layers (not agents):
   • Interceptor  — middleware on every model call & handoff → JSONL log + cost meter
   • Rule engine  — per-role tool allowlist + shell command gate
   • MCP registry — user-defined tool servers, attached to roles by config
   • Worktrees    — each run gets its own git worktree + branch; squads never collide
```

### Core design decisions

| Decision | Choice | Why |
|---|---|---|
| Topology | Supervisor (hub-and-spoke), one level deep | 2026 production default; loops stay debuggable; no agent-to-agent spaghetti |
| Agent runtime | `deepagents` `SubAgent` per role | Planning, virtual FS, per-subagent model/tools/prompt for free |
| Communication | LangGraph shared state (message list) + handoff tool calls | Messages ARE state → interception is reading state, not patching frameworks |
| Model access | Everything through LiteLLM | One config maps role → model; provider churn isolated to one file |
| Shell | Direct local exec with regex gate + role allowlist | Personal tool; Docker cut deliberately. Gate lives in our executor. |
| Concurrency | One squad = one run = one git worktree + branch | N squads on one repo, zero collisions; merging is a human decision |
| Config | Single `squad.yaml` | Roles, models, rules, git, MCP servers — one file the user owns |
| Interception log | Append-only JSONL per run | Greppable, diffable, no DB |

### Repo layout (target)

```
squad/
├── PLAN.md
├── README.md
├── pyproject.toml            # uv-managed
├── squad.yaml                # user config: roles, models, rules, git, mcp servers
├── prompts/                  # role specializations (markdown)
│   ├── supervisor.md
│   ├── planner.md
│   ├── scout.md
│   ├── coder.md
│   └── reviewer.md
├── src/squad/
│   ├── __init__.py
│   ├── cli.py                # squad run / ping / log / cost / clean
│   ├── config.py             # load + validate squad.yaml (pydantic)
│   ├── router.py             # role → LiteLLM model string
│   ├── graph.py              # LangGraph supervisor graph assembly
│   ├── agents.py             # deepagents SubAgent definitions from config
│   ├── interceptor.py        # decision log: handoffs/shell/git/compress + model-call accounting
│   ├── intake.py             # task router: gh:123 / linear:ABC-123 / plain prompt
│   ├── rules.py              # tool allowlist + shell command gate
│   ├── worktree.py           # worktree lifecycle: create at run start, list, clean
│   ├── tools/
│   │   ├── shell.py          # gated local shell tool
│   │   ├── git.py            # commit tool (gated); push always human-confirmed
│   │   ├── docs.py           # save_doc: run documents (report, code style, PR notes)
│   │   └── mcp.py            # MCP client loader (incl. Playwright MCP)
│   └── compress.py           # compression node (local model)
├── logs/                     # <run-id>.jsonl + <run-id>/ run docs (gitignored)
└── tests/
    ├── test_rules.py         # security-relevant — real tests
    ├── test_worktree.py      # isolation between concurrent squads
    ├── test_router.py
    └── test_interceptor.py
```

---

## 2. Roles

Roles are **pure config**, not code. Adding a role = adding a YAML block.
The five defaults:

```yaml
# squad.yaml (excerpt)
roles:
  supervisor:
    model: gemini/gemini-3-flash        # cheap-mid; it only routes & decides
    prompt: prompts/supervisor.md
    tools: []                            # delegates only, no direct tools

  planner:
    model: anthropic/claude-opus-4-8     # runs once per task; strong model justified
    prompt: prompts/planner.md
    tools: [fs_read]                     # reads the repo to plan. Never edits.
    max_context: 100000

  scout:
    model: gemini/gemini-3.1-flash-lite
    prompt: prompts/scout.md
    tools: [browse]                      # Playwright MCP + fetch. NO shell. NO fs write.
    max_context: 60000

  coder:
    model: gemini/gemini-3-pro
    prompt: prompts/coder.md
    tools: [shell, fs, git_commit]       # gated shell + files + commit (own branch only)
    max_context: 120000

  reviewer:
    model: anthropic/claude-opus-4-8
    prompt: prompts/reviewer.md
    tools: [fs_read]                     # read-only. Reviews, never edits.
    max_context: 100000

compressor:
  model: ollama/qwen3:8b                 # local, free, private
  trigger_tokens: 50000                  # compress state crossing agent boundary above this
  keep_last_messages: 6                  # never compress the working tail

git:
  worktrees_dir: ~/.squad/worktrees      # <repo-name>/<run-id>/
  branch_prefix: squad/                  # branch per run: squad/<run-id>
  commit_roles: [coder]                  # who may call git_commit
  push: confirm                          # push NEVER happens without a human yes
  pr: confirm                            # run end: push + gh pr create; confirm | auto | never
```

**Role anatomy** (what a role block resolves to at runtime):
1. **Model** — LiteLLM string, resolved by `router.py`. Swap providers by editing one line.
2. **Prompt** — markdown file defining specialization: what it does, what it must hand back, what it must refuse.
3. **Tools** — names resolved against the tool registry (built-ins `shell`, `fs`,
   `fs_read`, `browse`, `git_commit` + any MCP server from config). Unlisted
   tool = agent physically cannot call it (not "asked not to" — not bound).
4. **Limits** — context threshold, max turns per delegation, max shell calls per run.

The prompt files carry the *specialization*; the tool list carries the
*capability boundary*. Both per-role, both in the repo, both diffable.

**The default relay for a coding task:**
supervisor → planner (plan from repo state) → coder (implement per plan,
commit) → reviewer (review the diff) → coder (fix findings, commit) → done.
Supervisor may skip planner for trivial tasks and pull scout in for research —
the roster lives in its prompt; routing stays its judgment call.

---

## 3. Squads & worktrees

A **squad** = one run of the graph = one supervisor + its team. Multiple
squads on the same repo work because runs share nothing:

| Per-run resource | Isolation |
|---|---|
| Working directory | Own git worktree: `~/.squad/worktrees/<repo>/<run-id>/` |
| Branch | `squad/<run-id>`, created from repo's HEAD at run start |
| Shell jail | `workdir_jail` = that worktree path (security win: agents can't touch your checkout) |
| Log | `logs/<run-id>.jsonl` |
| Commits | On own branch only, via gated `git_commit` tool |

Lifecycle:
1. `squad run --repo ~/localhost/foo "task"` → `git worktree add <dir> -b squad/<run-id>`
2. Agents work + commit inside the worktree. Your checkout stays untouched.
3. Run ends → CLI prints branch name + diffstat, then offers a PR:
   confirm prompt → `git push -u origin squad/<run-id>` + `gh pr create`
   (title/body from the run's task + result summary). Decline → branch stays
   local. This is a **CLI step, not an agent tool** — agents can't push or
   open PRs; the confirm prompt is the human yes that gates the push.
   **Merging is still your job.** Squad never merges.
4. `squad clean` — removes finished worktrees whose branches are merged or
   explicitly discarded (`git worktree remove` + optional branch delete).

Not built: cross-squad communication, lock coordination, auto-merge.
Squads that must cooperate = one squad with a bigger task.

---

## 4. Communication

### How agents talk

1. User task enters the graph as the first message in **LangGraph state**.
2. **Supervisor** reads state, emits a *handoff tool call*:
   `delegate(role="planner", task="plan the auth refactor", context=[...])`.
3. Handoff spawns the subagent with: its role prompt + the delegated task +
   *only the context the supervisor chose to pass* (not the whole history —
   this is the context-access control point).
4. Subagent runs its own inner loop (deepagents), returns a **structured
   result message**: `{summary, artifacts, files_touched, commits, cost}`.
5. Result appends to supervisor state. Supervisor decides: delegate more,
   loop back, or finish.

Agents never talk peer-to-peer in v1. Everything relays through the
supervisor. Costs one extra hop; buys: single interception point, single
place to compress, no circular delegation. (Peer-to-peer is a v2 question,
only if the hop measurably hurts.)

### Where interception happens

Every arrow in the architecture diagram passes through `interceptor.py`:

- **Model calls** — accounting records only: model, token counts, cost per
  role. The decision trail (what was done, how, why) lives in the handoff,
  shell, git and compress records — full message histories made the log grow
  O(N²) per run and were dropped deliberately.
- **Handoffs** — the `delegate` tool is ours; it logs task, passed context,
  and returned result before/after the subagent runs.
- **Shell & git** — every command + exit code + truncated output; every commit.

One JSONL record schema for all:

```json
{"ts": "...", "run_id": "...", "kind": "model_call|handoff|shell|git|compress",
 "role": "coder", "direction": "in|out", "payload": {...},
 "tokens": {"in": 1200, "out": 300}, "cost_usd": 0.0021}
```

`squad log <run>` pretty-prints a run; `squad cost` sums per role/model. This
log is the killer demo: *what agents said to each other and what it cost.*

### Where compression happens

A **compression checkpoint** wraps every handoff. Before context crosses an
agent boundary, if it exceeds `trigger_tokens`: the local Ollama model
summarizes everything except the last `keep_last_messages` messages into a
single digest message. Original messages are preserved in the JSONL log
(nothing is ever lost, only removed from live context). Compression events
are themselves logged with before/after token counts — so the tool can
*prove* its own savings.

---

## 5. Rules

Two layers, both in `rules.py`, both driven by `squad.yaml`:

### Layer 1 — capability (hard boundary)
Tool binding per role, enforced at agent construction. Scout has no shell
tool bound → no prompt injection from a fetched webpage can make it run
commands. This is the primary defense; everything else is belt-and-braces.

### Layer 2 — shell gate (soft boundary, for roles that DO have shell)

```yaml
shell_rules:
  confirm_patterns:            # pause and ask the human
    - "\\brm\\s+(-\\w*[rf]\\w*\\s+)"
    - "\\bsudo\\b"
    - "git\\s+push\\b"             # ANY push → human confirms
    - "curl[^|]*\\|\\s*(ba)?sh"    # pipe-to-shell
    - "\\bchmod\\s+777\\b"
  deny_patterns:               # refuse outright, log, tell the agent why
    - "rm\\s+-rf\\s+[/~]\\s*$"
    - ":\\(\\)\\s*\\{.*\\};:"      # forkbomb
    - "git\\s+worktree\\s+remove"  # lifecycle belongs to the CLI, not agents
  workdir_jail: <run worktree>  # set per run; commands cwd-jailed to the worktree
  timeout_seconds: 120
  max_output_bytes: 10000      # agent-visible cap; head+tail kept, middle cut
```

Flow: agent calls `shell(cmd)` → gate checks deny → check confirm (blocking
prompt to the human in CLI) → execute with timeout + cwd jail → log → return
truncated output. ~80 lines total, and it gets real tests because it's the
one security-relevant surface.

### Git tool rules
`git_commit(message)` — stages + commits inside the run's worktree, on the
run's branch, only for roles in `commit_roles`. Refuses outside the worktree.
Push is not a tool; it's a confirm-gated shell pattern, so it always reaches
a human. Commit messages get a `run-id` trailer for traceability.

**Not built:** seccomp, VMs, network egress filtering. Revisit only if
agents ever run unattended.

---

## 6. Phases

Each phase ends with something runnable and a verification step. No phase
starts until the previous one's check passes.

### Phase 0 — Scaffold (½ day)
1. `uv init`, add deps: `deepagents`, `langgraph`, `litellm`, `pydantic`,
   `pyyaml`, `typer` (CLI), `langchain-mcp-adapters`, `python-dotenv`.
2. `config.py`: pydantic models for `squad.yaml`; fail loud on bad config.
3. Default `squad.yaml` + 5 prompt stubs.
4. `.env` handling for API keys (OpenAI, Google, Anthropic). Ollama needs none.
- ✅ **Check:** `squad --help` runs; config loads; bad config errors clearly.

### Phase 1 — Router + model smoke test (½ day)
1. `router.py`: role name → LiteLLM completion, ~30 lines.
2. `squad ping`: sends "reply with role name" through every configured role,
   prints model, latency, cost.
- ✅ **Check:** all 4 providers (OpenAI, Gemini, Anthropic, Ollama) answer.
  Proves keys, routing, and LiteLLM glue before any agent logic exists.

### Phase 2 — One agent, gated shell (1 day)
1. `tools/shell.py`: the gated executor (deny → confirm → jail → timeout → truncate).
2. `rules.py` + `tests/test_rules.py`: table-driven tests — every deny/confirm
   pattern, plus benign commands passing through.
3. Single deepagents agent (coder role) with shell + fs, no graph yet.
4. Run: "create hello.py in scratch dir and run it" end to end.
- ✅ **Check:** rule tests green; `rm -rf` triggers confirm prompt; task completes.

### Phase 3 — Interceptor (1 day)
1. `interceptor.py`: LiteLLM callbacks + wrapped tools → JSONL.
2. `squad log <run>` pretty-printer; `squad cost` aggregator.
3. Re-run Phase 2 task; inspect the full transcript.
- ✅ **Check:** every model call and shell command appears in JSONL with
  token counts and cost; totals match provider dashboards (roughly).

### Phase 4 — Supervisor + multi-agent (2 days) ← the heart
1. `agents.py`: build deepagents `SubAgent` list from config roles (incl. planner).
2. `graph.py`: supervisor loop with `delegate` handoff tool; max-turns and
   max-cost circuit breakers (runaway loops burn money — breaker first, not after).
3. Structured result contract (`{summary, artifacts, ...}`) between sub and super.
4. Test task: "plan a small refactor of file X, implement it, review it" —
   forces supervisor → planner → coder → reviewer relay.
- ✅ **Check:** JSONL shows the full relay: which role got what context, what
  each returned, what each cost. Kill-switch: `--max-cost 0.50` halts the run.

### Phase 5 — Worktrees + git tool (1 day)
1. `worktree.py`: create worktree + branch at run start; `squad clean` teardown;
   run-end summary (branch, diffstat).
2. `tools/git.py`: `git_commit` (stage + commit, `commit_roles` only, worktree-jailed,
   run-id trailer). Push stays a confirm-gated shell pattern.
3. Run-end PR step in the CLI: push branch + `gh pr create --fill` (falls back
   to printing the compare URL if `gh` is absent). Config `git.pr` / flag `--auto`:
   `confirm` (default) asks first; `auto` pushes + opens the PR unattended —
   the human decides at merge time instead; `never` keeps the branch local.
   Never an agent capability. In auto mode, confirm-gated *shell* commands
   (sudo, rm -rf, pipe-to-sh) are auto-DECLINED, not auto-approved — the agent
   is told the run is unattended; only the run-end push+PR is automated.
4. Point shell jail at the worktree.
4. Test: **two squads, same repo, simultaneously** — different branches, both
   commit, user checkout untouched.
- ✅ **Check:** `test_worktree.py` green; parallel runs produce two clean
  branches; `squad clean` removes only merged/discarded worktrees.

### Phase 6 — Scout + browsing (1 day)
1. `tools/mcp.py`: MCP client loader; wire Playwright MCP + a plain fetch tool.
2. Bind `browse` toolset to scout only; verify scout has no shell (test asserts it).
3. Test task: "find current LangGraph stable version and its release notes,
   hand findings to coder to pin in pyproject".
- ✅ **Check:** scout browses, hands off structured findings; a webpage
  containing "run rm -rf" prose cannot reach a shell (scout has none bound).

### Phase 7 — Compression (1 day)
1. `compress.py`: token-count check at handoff boundary; Ollama summarization;
   digest replaces old messages; originals stay in JSONL.
2. Compression events logged with before/after counts.
3. Test: long multi-handoff run that crosses `trigger_tokens`.
- ✅ **Check:** context stays under threshold across handoffs; final answer
  quality survives (manual judgment); log shows tokens saved.

### Phase 8 — Polish for OSS (1–2 days)
1. README: the one-sentence scope, quickstart, a real interception-log
   screenshot with per-role costs (the killer demo), config reference.
2. `squad cost` demo table: same task, all-Opus vs routed — the headline number.
3. MIT license, `.gitignore` (logs/, .env), `uv.lock` committed, tag v0.1.
- ✅ **Check:** stranger can clone → add keys → `squad run "task"` in under
  5 minutes.

**Total: ~9–10 working days.** Phases 0–3 already yield a useful single-agent
tool with cost visibility — if the project stalls there, it still earned its keep.

---

## 7. Explicitly out of scope (v1)

- Web UI / dashboard — `squad log` + JSONL is enough; a UI is a separate project.
- Peer-to-peer agent communication — supervisor relay only.
- Cross-squad coordination (locks, shared state, auto-merge) — squads are
  isolated by worktree; merging is a human decision.
- Docker / sandboxing — deliberate; revisit only for unattended runs.
- Persistence / memory across runs — each run is fresh; logs are the memory.
- Plugin system — MCP *is* the plugin system; build nothing bespoke.
- More than the 6 default roles — users add their own in YAML.
- Retry/fallback model chains — LiteLLM has fallbacks built in; config-only if wanted, no code.

## 8. Risks

| Risk | Mitigation |
|---|---|
| deepagents API churn (young library) | Pin versions; our code touches it only in `agents.py` + `graph.py` |
| Handoff context too thin → dumb subagents | Supervisor prompt engineering; log makes the failure visible immediately |
| Compression loses critical detail | `keep_last_messages` floor; originals in log; threshold tunable per role |
| Runaway supervisor loop burns money | Max-turns + max-cost breakers in Phase 4, before multi-agent exists |
| Weak model + shell + web content | Capability boundary: browsing role never gets shell bound. Tested, not promised. |
| Concurrent squads corrupt each other | Worktree isolation; agents denied `git worktree` commands; tested in Phase 5 |

## 9. Open items (backlog)

Deferred work and known soft spots — not out-of-scope, just not done yet.

- **Supervisor history still grows unbounded.** The compression checkpoint
  gates each handoff *string* at `trigger_tokens`; results below it return to
  the supervisor's message list uncompressed and accumulate — N delegations →
  O(N²) input tokens on the supervisor's model. `max_context` and
  `keep_last_messages` are declared in config but not wired to any live
  message list. Fix: trim/digest the supervisor history itself (deepagents
  middleware or a pre-model hook) once it crosses `max_context`.
- **Loop-limit soft cap is prompt-only.** The ≈3-round review cap lives in the
  supervisor prompt; hard termination relies on `max_turns` (recursion limit) +
  `--max-cost`. Upgrade path if the prompt proves unreliable: a per-subtask
  review counter (reuse the subtask store) that refuses past the cap.
- **Scribe not yet exercised end-to-end.** The role is wired and delegatable,
  but the three curation calls (prompt tidy, report shrink, subtask-context
  select) are supervisor-driven, not forced by code. Prove it in a live run;
  decide whether any call should be automatic rather than a delegation choice.
- **New-project viability questions** (competitors / value / effort, in
  [prompts/scout.md](prompts/scout.md)) are arguably scope-creep vs the YAGNI
  rule. Keep only if actually used; otherwise cut.
- **TDD-first not encoded in the coder flow.** The law says tests first, but the
  coder pulls a subtask and implements; the planner's "Verification" is guidance,
  not a test-before-code gate. Consider making test-first explicit per subtask.
Resolved since first written: `fs_read` is now write-enforced via
`FilesystemPermission` (deny-all-writes); scout persists run docs with
`save_doc`; task intake (`gh:` / `linear:`) routes issues; branches are named
from the task; PR bodies come from the scout's PR notes; model-call log
records are accounting-only; shell output is head+tail capped; compressor
input is chunked to the local model's window; roles default is 6 (§7 updated);
linguist-style `profile` tool computes language shares + tooling in one
deterministic call (scout no longer burns turns exploring by hand).
