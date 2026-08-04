# Agent Team

A **clone-and-run AI engineering team you drive from Claude Code.** Open this folder in
Claude Code, type a prompt, and a paired-squad team runs a full gated SDLC —
intake → investigate → plan → pair-challenge → implement → test gate → review → commit →
supervisor sign-off — on a bundled sample project. (That sequence has one canonical
definition: `standup/team.json` → `manager.policy.sdlc_pipeline`.) A local **Mission Control** portal
runs alongside so you can watch and manage the squads in the browser, and **you are the
approval gate** for anything irreversible (merge / deploy).

You (Claude Code) run it as the **Engineering Manager**: a supervisor who orchestrates the
squads and gates the work but **produces no code itself** — every change is done by the team
through the gated pipeline. No external services, no credentials, no network — everything
runs locally.

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
install the commands and run them in any project — same flow in the Claude Code **CLI**, the
**desktop app**, and **claude.ai**:

```
/plugin marketplace add Kain1989/agent-team
/plugin install agent-team@agent-team-marketplace
/reload-plugins                       # activate in the current session (no restart needed)
```

**Verify:** `/plugin list` should show `agent-team`. Then try `/agent-team:standup`, or
`/agent-team:init` to scaffold a team project in the current folder.

Installed, the commands are namespaced (`/agent-team:standup`, `/agent-team:portal`, …). If you
just clone + open the folder, the two headline commands also work unprefixed (`/standup`, `/portal`).

**First-time notes:**
- Claude Code may ask you to **trust** the folder, and the plugin's hooks may prompt for
  **permission** to run their scripts — approve them to let it load.
- The plugin ships **hooks** (a SessionStart charter + a per-prompt reminder + a PreToolUse gate
  that keep the EM in *supervisor mode*). They are **cwd-gated** — they only fire inside an
  agent-team checkout, so installing the plugin does **not** touch your other, unrelated projects.
- **Uninstall:** `/plugin uninstall agent-team@agent-team-marketplace`.

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

## The design gate — someone is responsible for whether the screen is any good

Every other lens in the review ring is an *engineering-correctness* lens: pair-diff, correctness,
conventions-and-tests. All three ask whether the code is right. None asks whether the screen is
any good — so UI quality was never a condition of green, and a design critique that ran after the
commit could not block anything anyway.

So the pipeline carries a **fourth lens**. Whenever a change has an **observable surface**, it does
not go green until `design-quality` passes:

```bash
# the deterministic judge — the exit code IS the verdict, not an opinion
node standup/control/verify_design_quality.js http://127.0.0.1:8770
#   0 = no violations   1 = violations   2 = could not run (never a silent pass)

node standup/control/verify_design_quality.js --self-test   # prove the judge can FAIL
node standup/control/verify_design_quality.js --rule-ids    # the citable rule ids
```

- **[`DESIGN_RULEBOOK.md`](DESIGN_RULEBOOK.md) is the criterion** — numbered rules, not prose. A
  rubric is a lens; a rulebook is a language. **Every finding must cite a rule id, and the id must
  exist in the file** (`E-01`) — otherwise a finding can't become a queue item, and the same
  defects get re-found every tick and never land.
- **`[MACHINE]` rules are decided by the script**, `[JUDGMENT]` rules by the design lens reading a
  real screenshot. `pass` is bound to the exit code **in code**, not merely requested in a prompt.
- **The judge is itself tested** (`E-03`): `--self-test` runs it against a deliberately broken
  fixture and fails if any rule stays silent. A judge that can't catch breakage isn't a judge.
- **`E-07`: a machine PASS proves nothing.** A non-zero exit always fails, but exit 0 is not a
  pass — the judge catches "looks wrong" and is blind to "looks right, is lying". (A page whose
  small-multiple charts are each normalized *per card* renders flawlessly and inverts the real
  ranking; every machine rule was silent on it.)
- **`E-02`: one rule cited twice is a shared-component fix**, never N per-file tickets.

The design pass runs **before** the board is synthesized, so its findings become ranked queue items
for this tick instead of notes in a file nobody reads. The gate applies on **every** dispatch path
— `/standup`, `/work` and `/team` — because an improvement that lands on one path while the others
run the old shape is the exact failure the rulebook exists to stop.

**Adopting it:** the judge needs Playwright (`npm i -D playwright && npx playwright install
chromium`); if it can't run it exits 2, never 0. Pass the target as `args.designUrl` — the URL is a
parameter, never a baked-in default. Keep the rule ids, replace the examples with your own.

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
├── DESIGN_RULEBOOK.md  the numbered design rules the design gate judges against
├── .claude/commands/   /standup (run the squad) · /portal (start Mission Control)
├── standup/            the engine
│   ├── team.json       the roster — 2 squads + lean staff
│   ├── standup.workflow.js   the gated SDLC standup the Workflow tool runs
│   ├── portal/         Mission Control: FastAPI API + a no-build static UI
│   └── control/        the job queue, git-worktree lifecycle, the locked-down gate,
│                       verify_design_quality.js (the design judge + its self-test),
│                       and check_workflow_parse.js
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
cd standup/portal && ../.venv/bin/python -m pytest -q     # portal engine: 185 passing
cd demo-app && python3 -m pytest -q                       # the sample lib
node standup/control/verify_design_quality.js --self-test # the design judge can still FAIL (E-03)
node standup/control/check_workflow_parse.js standup/standup.workflow.js   # the workflow still loads
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
