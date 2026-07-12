# SQUAD

AI deepagents orchestration project for coding purposes.
Point of the project is to apply the best coding practices and reduce the cost by delegating to cheaper models and using less context.

## The loop

These rules are like a law in the system. They are the best programming practices:

- **Test driven development**, assume that the user cares only about the tests and will read tests primarily. Test everything that can be tested.
- **Focus before features**, or YAGNI, each task should be done with a focus to the task, each function should perform only the one job. Functions should be short, around 20 lines.
- **Self explanatory names, consistent style, readability**, for classes, interfaces, functions and variables.
- **No premature optimisation** - optimisation only if the task requires it or we are dealing with a system that is user-facing and with simple optimising we can gain on resource reduction thus cost reduction.
- **Prefer readability and composition over optimisation**
- **Prefer standard library and native code**
- **Declarative programming is preferred**, where applicable.
- **Immutability by default**

### Cost control

Cost reduction is the point of the project, so it is enforced, not hoped for:

- **Cost breaker.** A running USD total (`--max-cost`, default $1) is checked before every `delegate` handoff; crossing it halts the run. Stops a runaway loop from burning budget.
- **Turn cap.** Each role has a `max_turns` limit (a recursion cap) so no single agent spins forever.
- **Disable.** `--max-cost 0` (any value ≤ 0) turns the cost breaker off for unbounded runs — use when the task is trusted and no ceiling is wanted. The turn cap stays as a hard safety rail against infinite recursion; raise it in config rather than removing it.

### Model routing

Each role runs on the cheapest model that can do its job — the routing *is* the cost strategy. All of it is pure config in `squad.yaml`; code never hardcodes a role's model.

| Role | Tier | Model (example) | Why |
|------|------|-----------------|-----|
| scout | cheapest | gemini flash-lite | high-volume browsing, shallow reasoning |
| compressor | local / free | Ollama qwen3:8b | runs constantly — keep it off the paid meter, and private |
| scribe | cheap-mid, **thinking** | gemini flash (thinking) | curates prompt / report / subtask context — relevance judgment, so a reasoning-capable model |
| supervisor | cheap-mid | gemini flash | only routes and decides, no deep thought |
| coder | mid | gemini pro | writes code — needs competence, not genius |
| planner | frontier | opus | runs once per task; deep reasoning pays off here |
| reviewer | frontier | opus | catching bugs is where a strong model earns its cost |

Principle: **cheap browses, local compresses, a thinking model curates, mid codes, frontier plans and reviews.** Spend big only where a mistake is expensive (planning, review) or where the model runs just once (planner). Models named are examples — the point is the tiering, not the specific SKUs.

#### Compression vs curation — two roles, not one

The concept originally lumped both under "compressor." Split them, because they want different model tiers:

- **compressor** shrinks a *given* string — mechanical, faithful token reduction, facts preserved. Fires automatically at every `delegate` handoff over `trigger_tokens`; originals always land in the JSONL log, only the live context shrinks. Local / free model — it runs constantly.
- **scribe** decides what a string *should contain* — editorial and relevance judgment: fix typos and tighten the incoming prompt, give a one-sentence summary, shrink the discovery report to only what's prompt-relevant, pick which report bits align with each subtask. Deliberate, at named pipeline points. A cheap-mid **thinking** model — a botched judgment corrupts the task spec or feeds the coder wrong context, so it must reason, not just squeeze bytes.

Rule of thumb: **byte-count → compressor; relevance / quality → scribe.**

### Handoffs and capabilities

Two mechanics underpin every phase below:

- **Handoffs go through one `delegate` tool.** Whenever the text says "through the supervisor," it means a single `delegate(role, task, context)` call — the one interception point. It logs task + context in and the result out, attributes the model spend to the receiving role, and checks the cost breaker *before* each handoff. There is no ad-hoc agent-to-agent messaging; the relay is the audit and cost boundary.
- **Capability is tool binding, not prose.** A role can only do what its bound tools allow. A tool absent from a role's list is never constructed, so the model physically cannot call it — the prompt carries specialization, the binding carries security. Only the coder holds `shell`; planner and reviewer are read-only (`fs_read`); the supervisor holds nothing but `delegate`. The filesystem tool is jailed so `..`/absolute paths can't escape the repo.

### Initial phase

Agent: supervisor

A task is given

Task can be performed over a repo GIT or if there is no repo a new project directory is created and the git repo is initialised.

Task can be a prompt, GitHub issue or a Linear issue. In order not to waste tokens we need a notation that can be parsed telling us whether it is an issue and what tool to reach — a small router over the input (`gh:123` / `linear:ABC-123` / plain prompt), a few lines of regex, no more.

We fetch through existing tools, not a bespoke API client:

- **GitHub**: `gh issue view <n> --json title,body,labels` through the gated shell. `--json` returns exactly the fields we ask for — API-grade token control with zero code to maintain.
- **Linear**: its official MCP server, bound to whichever role needs it. Drop to a ~15-line GraphQL fetch only if the MCP responses ever prove token-bloated — measured, not speculative.

No hand-rolled two-provider API layer: `gh` and MCP own auth, rate limits and pagination for us. This keeps the "MCP is the plugin system, build nothing bespoke" rule.

Here is where the loop breaks into two possible loops, based on if the repo exists and we are doing work on existing code or we are creating a new project.

Prompt is then passed to the **scribe** (not the compressor — this is judgment work, see *Compression vs curation* above). Scribe reviews the textual prompt/issue: fixes typos, makes it to the point, and — for a long prompt — gives a one-sentence summary. Compression here is a side effect of curation, not the goal.

After the scribe has tidied the prompt the supervisor creates a git branch, names it based on the issue title or number, or the one-sentence summary, prefixed by `squad/`, and starts the discovery phase.

### Discovery phase

Agent: scout

Basic data collection about the project: Languages used (we should use a GitHub Linguist like tool to save on tokens), testing and linting tools, Readme (passed through the scribe — to make it to the point and reduce token count)

We can store this data under `logs/` (per-run job documents; `~/.squad` is reserved for worktrees), but we should make sure it’s up to date before and after every loop is complete. This should be a job for scout.

Scout should then investigate the prompt and scout the codebase for the files that need updating, making a list.

If it is the new project, supervisor already initialised the repo. Scout performs web search.

#### Web search (scouting loop)

Scout's browsing is two cheap tools plus one heavy opt-in:

- **`search(query)`** — DuckDuckGo, no API key. Returns a compact list of results (title, url, snippet), not a 40KB HTML search page. This is how scout runs a search, gets results, and builds its stack of links to read.
- **`fetch(url)`** — pulls a page and runs it through trafilatura, returning the **main content as clean markdown** (scripts, nav and styles stripped; text, lists and links kept). ~8–10× fewer tokens than raw HTML, which also lets the cheap scout model reason better. Output is capped on the extracted markdown, not on raw bytes.
- **`render` (opt-in)** — Playwright via MCP, for JS-rendered pages that need a real browser. Off by default: it carries a heavy cold start and a big tool schema, so a role only pays for it by listing `render` in its tools. `browse` (search + fetch) stays cheap for the common static-page case.

No bespoke Google API, no raw-HTML dumps. Stack is fetched one by one; oversized pages are digested by the compressor and handed back to the scout, which decides if the data is useful or follows more links, looping again.

If we are starting a new project we want to answer the following questions:

- What are the competitors and what could we do better?
- Is there a value in starting a project like this?
- Does the benefit match or exceeds the effort.

Scout then compiles a report combining all the data. Report is then sent to the **scribe**, which shrinks it to only what's relevant to the prompt — keeping every prompt-related fact, dropping the rest. (Relevance is a judgment call, hence scribe not compressor.)

Report is then stored as an `.md` file and logged in the filesystem. The `logs/` directory is the job database. We need a tool to log the prompt (issue) and assign a directory name to it. This is where each job's documents are stored. If it’s a GitHub or a Linear issue we need to post the report as a comment as well. **Reports need to be in markdown notation.** Report should contain the language of the project, the most prominent language, and other minor languages. Testing and lint pipeline.

#### Note about the code style

Code style note should be a separate file. Based on the discovery we should manage it. It supplements the context. It contains the language, environment details, testing and linting pipelines types and execution info.

Report is then passed to the supervisor that initiates the planner.

### Planning phase

Agent: planner

Planner runs once per task. It reads the discovery report and the code style note, and reads the repo (read-only) to ground the plan. Strong model — this is the one place deep reasoning pays off, so we spend the frontier tokens here.

Task (initial compressed prompt or an issue) is broken into granular subtasks. Each subtask is a self-contained prompt (subprompt). **Eliminate contradiction** between subtasks here, before any code is written.

Planner produces the subtask stack — the tool that stacks subtasks so the coder can pull them one by one. Each subtask is a new context filled with only the info from the initial report that aligns with that subtask. The decision of what aligns is delegated to the **scribe** (relevance judgment, thinking model).

Planner never edits code. The stack goes back to the supervisor, which initiates the coder.

### Coding phase

Agent: coder

Coder should be able to perform the tasks of coding with the tools to execute terminal commands related to the job it is performing.

Initial research should be regarded as a context. Code style note as well. The planner's subtask stack drives the work — coder pulls subtasks one by one, does not re-derive them.

It should also be able to access other projects on the users machine and review the code if it’s useful. Try to find the functions that already solve the tasks, depending on the LICENSE.

Coder can issue another web search loop from the scout through the supervisor based on each step.

We want to know:

- If the functionality exists in the standard library?
- If the functionality exists as a quality maintained package?
- If we can do better - shorter localised code that follows **focus before features**, meaning if we can benefit from the localised simpler solution.
- Is there documentation of an existing solution? Read the code if there is no documentation or the documented solution doesn’t work.

The questions above are up to scout to answer and generate a subreport for each subtask. Passing it back to coder through the supervisor.

Coder does the code based on all subtasks. Upon completion of each subtask coder pings the supervisor to ping the reviewer.

### Reviewing phase

Agent: reviewer

Reviews the pending, not committed, code. Has access to the granular tasks of the coder and their initial context. Has access to the research docs and code notes.

Generates findings on what should be improved and passes them to the coder. Coder makes the update, reviewer compares it to the findings and signs it off.

Once the reviewer signs off, the coder commits the code via its `git_commit` tool. The commit message is a concise **what + why** the coder writes from the subtask — not the raw subtask prompt, which is too long and not commit-shaped. The supervisor tells the coder to resume the next subtask (via `next_subtask`), and so on until all of the subtasks are signed off. Committing is the coder's only write to git; the supervisor holds no tools and never touches the repo directly.

All communication can be compressed, but maybe shouldn’t be between the coder and reviewer.

Review loops are bounded three ways, so the coder↔reviewer cycle can't run away:

- **Hard: recursion cap.** The supervisor's `max_turns` sets a recursion limit on total delegations — the ping-pong physically cannot exceed it.
- **Hard: cost breaker.** `--max-cost` halts the run before each handoff once spend crosses the ceiling.
- **Soft: graceful review cap.** The supervisor stops re-reviewing a subtask after a few rounds (≈3) and escalates — reports "needs a human" rather than looping forever on the same finding.

The soft cap makes the stop *legible* (a clean escalation); the two hard caps guarantee termination regardless.

### Finishing phase

When all of the subtasks are done, coder reports to the supervisor.

Supervisor issues a scout to write the PR notes on what has been done and why, not compressed, it’s user facing.

PR is created. Loop is complete.

## Notes

- All the conversation must be logged in a big markdown file. Each role noted before the context.
- User should be updated through the CLI on what’s going on as well.
- Worktrees are the default, not optional. `squad run` creates a per-run worktree + branch before agents start; `squad clean` removes only merged ones; agents are denied `git worktree` by the shell gate. This buys both isolation (each run is sandboxed to its own checkout) and the ability to run several squads on the same codebase in parallel, touching different parts. A push failure (e.g. no origin) degrades to "branch stays local" — the run's work is never lost by the PR step failing.
