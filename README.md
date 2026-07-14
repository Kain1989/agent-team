# Agent Team MVP

A **clone-and-run AI engineering team you drive from Claude Code.** Open this folder in
Claude Code, type a prompt, and a paired-squad team runs a full gated standup — plan →
challenge → implement → test → review → commit — on a bundled sample project. A local
**Mission Control** portal runs alongside so you can watch and manage the squads in the
browser, and **you are the approval gate**.

No external services, no credentials, no network — everything runs locally.

---

## Run it (the main way)

1. **Get the folder** — unzip the package, or `git clone` the repo.
2. **Open it in Claude Code** — either:
   - **CLI:** `cd agent-team && claude`
   - **Desktop app:** Code tab → new session → open this folder.
3. **Bring up the monitor** — type **`/portal`** (first time it installs deps, then starts the
   Mission Control portal). Open the URL it prints (http://127.0.0.1:8770).
4. **Run the team** — type **`/standup`** (or just say *"run the agent team"*). The whole squad
   runs: per-dev standup → squad sync → EM board → gated work on a task in `demo-app/`. Watch it
   land as reviewed commits, and watch the squads/board/progress update live in the portal.

That's it. The team is Claude Code sub-agents; Claude Code (as the EM) orchestrates them.

> **Prerequisites:** Claude Code with the **Workflow** tool + sub-agents (this is what runs the
> squad) · **Python 3.9+**, **git**, and the **`claude` CLI** on `PATH` (the portal's code-task
> worker spawns a headless `claude -p`).

---

## Install as a plugin (share it with a team)

This repo is also a **Claude Code plugin** (`agent-team`) with its own marketplace, so others can
install the commands and run them in any project:

```
/plugin marketplace add Kain1989/agent-team
/plugin install agent-team@agent-team-marketplace
/agent-team:init          # scaffold a team project here, then /agent-team:standup
```

Installed, the commands are namespaced (`/agent-team:standup`, `/agent-team:portal`, …). If you
just clone + open the folder, the two headline commands also work unprefixed (`/standup`, `/portal`).

## Commands

| command | what it does |
|---|---|
| `/agent-team:init` | scaffold a team project (engine + starter roster + demo-app) into the current dir |
| `/agent-team:standup` | run the whole squad — standup → board → gated work on the top task |
| `/agent-team:work <task>` | run ONE task end-to-end through the gated SDLC (paired sub-agents) |
| `/agent-team:portal` | start Mission Control (status board + the code-task approval queue) |
| `/agent-team:team-structure` | show the org / roster — squads, pairs, staff |
| `/agent-team:add-team <id> — <mission>` | add a squad to the roster |
| `/agent-team:add-role <squad> <id> <role> [folder]` | add a developer (or `--staff`) to a squad |
| `/agent-team:daily-standup [hours]` | start a recurring standup (every N hours, or the daily cadence) |
| `/agent-team:stop-daily-standup` | pause the recurring standup (runtime — no restart) |
| `/agent-team:standup-status` | is a standup scheduled? when does it run next? recent runs |
| `/agent-team:costs` | spend today vs the daily cap; set the cap / kill switch (budget control) |
| `/agent-team:runs` | run history / timeline — jobs + ticks, cost, denials, commits, notifications |
| `/agent-team:eval` | run the regression eval suite (evals/cases.json) and score pass/fail |
| `/agent-team:help` | list all the agent-team commands with what each does |

**Daily-ops controls (v0.2):** a budget cap + kill switch enforced in the worker
(`/costs`), run-history/observability (`/runs`), content guardrails that block secrets
from a diff before it reaches approval, optional approver≠creator separation, and a
regression suite (`/eval`). See [ROADMAP.md](ROADMAP.md).

---

## The three ways work cuts — and the portal is always your window in

| | drive from | what runs |
|---|---|---|
| **Standup** (`/standup`) | the Claude Code chat | the whole squad runs the gated SDLC on `demo-app` via the Workflow tool |
| **Code-task queue** | the **portal** browser UI | submit one task → isolated git worktree → review the diff → **Approve** → commit |
| **Native team** (`/team`) | the Claude Code chat | the roster spawns as a Claude Code **native agent team** (lead + teammates, shared task list, peer mailbox) and self-organizes through the same gates |

Either way, the **Mission Control portal is your visualization + management surface** — keep it
open. It reads the same files the team writes (`standup/team.json`, `standup/BACKLOG.md`,
`standup/log/`, per-dev `.standup/*.md`), so a standup you run from the chat shows up there live,
and the job queue lets you assign + approve work in the browser.

**Native agent teams** (experimental, Claude Code's built-in feature) are wired in via four
bridges: `/sync-roster` turns `team.json` into native teammate definitions (`.claude/agents/`);
`/team <task>` spawns them as a real native team; the plugin's hooks put the **same guardrails +
kill switch** on the native task lifecycle (a secret in a task's diff **blocks** its completion);
and the portal observes live native teams at `GET /api/native-teams`. Enable native teams with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `~/.claude/settings.json`. So native gives the team
*mechanics*, our system keeps the *governance*.

---

## The team

| | who | works on |
|---|---|---|
| **Demo Dev Squad** | `dev_a` (builder) + `dev_b` (reviewer) — a pair who challenge each other | `demo-app/` |
| **Team Portal Squad** *(the exception)* | `portal_backend` + `portal_frontend` | `standup/portal/` — it owns Mission Control itself |
| **Staff** | `pm_agent` (Jobs-grounded scope/say-no) · `design_lead` (Apple-HIG UI lens) | cross-cutting |
| **Supervisor** | Claude Code, as EM — autonomous gates | every run |
| **Approval gate** | **you** — review the diff, Approve/Reject | the one irreversible write (the commit) |

Full roster + the gated-SDLC doctrine: [`standup/team.json`](standup/team.json). How Claude should
run the team: [`CLAUDE.md`](CLAUDE.md).

## What's in the box

```
agent-team/
├── CLAUDE.md           how Claude Code runs the team (read on open)
├── .claude/commands/   /standup (run the squad) · /portal (start Mission Control)
├── standup/            the engine
│   ├── team.json       the roster — 2 squads + lean staff
│   ├── standup.workflow.js   the gated SDLC standup the Workflow tool runs
│   ├── portal/         Mission Control: FastAPI API + a no-build static UI
│   └── control/        the job queue, git-worktree lifecycle, and the locked-down gate
├── demo-app/           the sample project the team works on (textkit; local git on first run)
└── setup.sh            installs the portal (venv, deps, gate config, demo-app git)
```

## How the code-task gate stays safe

A portal code task runs its agent under four belt-and-suspenders layers (see
[`standup/portal/parsers/agent_run.py`](standup/portal/parsers/agent_run.py)): an allow-list
(read + worktree-scoped edit only — no Bash/sub-agents/WebFetch/MCP), default permission mode, a
deny-by-default hook confining every read+write to the job's worktree, and an empty MCP config +
credential-stripped env. The agent never runs shell, commits, pushes, or merges — the trusted
worker commits, and only after **you** approve. Nothing is ever pushed.

## Point it at your own repo

Add a developer to a squad in `standup/team.json` whose `folder` is your repo, make sure that repo
has an `origin` remote, then run `/standup` or submit a code task targeting `project:<your-folder>`.

## Tests

```bash
cd standup/portal && ../.venv/bin/python -m pytest -q     # portal engine: 180 passing
cd demo-app && python3 -m pytest -q                       # the sample lib
```

## Platform & project

**Supported:** macOS / Linux (bash + Python 3.9+ + git + the `claude` CLI; the Workflow tool
for `/standup`, with a Task-tool fallback). Windows is not currently supported. The security
model + platform notes are in [SECURITY.md](SECURITY.md).

- License [MIT](LICENSE) · changes [CHANGELOG.md](CHANGELOG.md) · roadmap [ROADMAP.md](ROADMAP.md)
- CI runs the portal + demo-app test suites on every push (`.github/workflows/ci.yml`).

---

*Distilled from a larger 24/7 multi-squad system. The research-backed design — divide by
responsibility, pairs critique (never debate), deterministic test gates, name experts only for
alignment tasks, human gates on irreversible actions — carries over; the host-specific plumbing
(Snowflake, Slack, deploy boxes, email intake) was stripped for a self-contained MVP.*
