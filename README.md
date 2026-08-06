# Agent Team

An AI engineering team you clone and run from Claude Code, on your own machine.

You get a paired-squad roster, a pipeline that plans, tests and reviews its own work behind
gates before it commits, and a local Mission Control portal to watch it in the browser. Beyond
Claude Code itself it needs no third-party service account and none of your credentials: the
team, the portal and the sample project it works on all live on your machine. You are the
approval gate for anything irreversible.

## Run it

1. Get the folder — unzip the package, or `git clone` the repo.
2. Open it in Claude Code. CLI: `cd agent-team && claude`. Desktop app: Code tab, new
   session, open this folder.
3. Type `/portal`. The first run installs dependencies, then starts Mission Control; open
   the URL it prints (http://127.0.0.1:8770).
4. Type `/standup`, or just say *run the agent team*. The board, the squads and each
   developer's progress update live in the portal while the top task goes through the gates.

Prerequisites: Claude Code with the Workflow tool and sub-agents (that is what runs the
squad); Python 3.9+, `git`, and the `claude` CLI on `PATH` (the portal's code-task worker
spawns a headless `claude -p`).

## What a run does

`/standup` polls every active developer, merges their reports into per-squad boards, runs a
design pass over the live product surface, and ranks the result into one board. The top item is
then worked through the gated pipeline, and *if it survives every gate* it commits to a feature
branch — never pushed, never merged.

Often it will not survive, and that is the point. The engine names ten terminal states a task
can end in ([`standup/standup.workflow.js`](standup/standup.workflow.js) `:220-222`). One is
`committed`. Six are a gate refusing — at intake, investigate, plan challenge, the test gate,
review, or the supervisor's final read — and a run that ends in one of those is the pipeline
working. The last three are not: a change that went green and produced no commit, an agent that
came back empty, and a task that threw. Those say something went wrong with the run rather than
with the work, and reading them as gates doing their job is how a real fault gets filed as
normal — [`ARCHITECTURE.md`](ARCHITECTURE.md) names all ten with the line that emits each.
Promising commits would mean hiding the nine from you, and that kind of hiding is the failure
this repo exists to remove.

`/work <task>` runs the same pipeline on one named task, skipping the roster-wide phases it has
no use for. A third path, `/team <task>`, runs the roster as a Claude Code native agent team.
Native supplies the team mechanics; three lifecycle hooks put this system's guardrails, kill
switch and secret scan on them in code, and the lead is pointed at the same canonical pipeline.
Worth knowing which is which: on that path the hooks are machine-enforced while the SDLC steps
are instructed, where `/standup` binds both.

The pipeline's steps have exactly one *definition*:
[`standup/team.json`](standup/team.json) → `manager.policy.sdlc_pipeline`. Other surfaces may
restate it — [`CLAUDE.md`](CLAUDE.md) does, and so does the engine's own metadata — but the
rule the roster states is that they change only after that array changes. The roster records
why: before the rule existed the sequence "was written out nine times in three different arrow
glyphs with five different step counts". A second definition is not documentation, it is a
fork. This file deliberately keeps no copy at all.

The portal is not a second source of truth either, and structurally cannot become one. It
reads the files the team writes — the roster, the backlog, the daily log, each developer's
progress file — and never writes them, while owning a separate store of its own: the job
database, the run history, and the generated teammate definitions.

Which file owns which beat, and what each gate stops: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## The team

The squads:

| squad | the pair | works on |
|---|---|---|
| Demo Dev Squad | `dev_a` (builder) and `dev_b` (reviewer and tests) | `demo-app/` |
| Team Portal Squad | `portal_backend` and `portal_frontend` | `standup/portal/` — Mission Control itself |

The staff:

| role | what it does | state |
|---|---|---|
| `pm_agent` | owns intake (raw ask into an outcome contract) and the board; keeps the backlog prioritized and challenges plans for direction and scope | active |
| `design_lead` | owns clarity and craft of the portal UI: screenshots the live product, critiques it against the rulebook, files ranked design tasks | active |
| `product_qa` | uses the product as a user every tick — picks a real user task, runs it end to end on the live instance, records where it got stuck | active |
| `comms_triage` | optional triage over a local inbox folder; the sample roster ships it switched off | inactive (`active: false`) |

`product_qa` is the newest role and the roster explains why it had to exist: every other line
of defence looks somewhere else. Developers are scoped to their own lane, reviewers only read
the diff, the design lead judges expert standards rather than usability, and the product
manager judges keep or kill. A team of many agents caught nothing, because no role was using
the product.

The supervisor is Claude Code itself, acting as engineering manager: it gates each phase and
writes no product code. The approval gate is you — the portal's code-task commit, and every
push, merge or deploy.

Four documents, four readers, and they do not overlap. This one is for the human deciding
whether to adopt this and then running it. [`CLAUDE.md`](CLAUDE.md) is the operating
instruction Claude reads when it opens the folder.
[`standup/team.json`](standup/team.json) is canonical policy — the roster, the doctrine, the
pipeline — and it is where the other three point when they need to be right.
[`ARCHITECTURE.md`](ARCHITECTURE.md) describes the wiring underneath all of it.

## Commands

Installed as a plugin, every command is namespaced: `/agent-team:standup`. In a checkout you
opened directly, `/standup` and `/portal` are the two that work unprefixed — they are the only
two files in `.claude/commands/`. The rest of this table needs the plugin installed.

Run the team:

| command | what it does |
|---|---|
| `/standup` | the whole roster reports, the EM builds the board, the top task goes through the gated pipeline |
| `/work <task>` | one named task, end to end through the same pipeline |
| `/team <task>` | run the roster as a Claude Code native agent team instead of through the Workflow tool |

Watch it and control spend:

| command | what it does |
|---|---|
| `/portal` | start Mission Control — the status board plus the code-task approval queue |
| `/runs` | run history — jobs and ticks with status, cost, duration, denied tools, commits |
| `/costs` | spend today against the daily cap; set the cap, or throw the kill switch |

Shape the roster:

| command | what it does |
|---|---|
| `/team-structure` | show the org — squads, pairs, staff, supervisor |
| `/add-team <id> — <mission>` | add a squad |
| `/add-role <squad> <id> <role> [folder]` | add a developer, or a staff role with `--staff` |
| `/sync-roster` | regenerate the native teammate definitions in `.claude/agents/` from the roster |

Schedule it:

| command | what it does |
|---|---|
| `/daily-standup [hours]` | start a recurring standup on an interval |
| `/stop-daily-standup` | stop the recurring standup |
| `/standup-status` | is one scheduled, when does it next run, what ran recently |

Set up and verify:

| command | what it does |
|---|---|
| `/init` | scaffold a team project into the current directory — engine, starter roster, sample app |
| `/eval` | run the regression suite in `evals/cases.json` and score pass or fail |
| `/help` | list every command with what it does |

Those sixteen are every entry in `skills/`; nothing is omitted.

---

## Install as a plugin

This repo is also a Claude Code plugin with its own marketplace, so other people can install
the commands and run them in any project — the same flow in the CLI, the desktop app, and
claude.ai.

```
/plugin marketplace add Kain1989/agent-team
/plugin install agent-team@agent-team-marketplace
/reload-plugins
```

Verify with `/plugin list`, which should show `agent-team`; then try `/agent-team:standup`, or
`/agent-team:init` to scaffold a team project into the current folder. Uninstall with
`/plugin uninstall agent-team@agent-team-marketplace`.

Which release you have is declared in exactly one place: `.claude-plugin/plugin.json` →
`version`. Read it there rather than trusting a number written into prose, where it rots. The
marketplace manifest, `.claude-plugin/marketplace.json`, carries its own separate `version`
field for the same plugin, so read both if you need to know which release an install served
you.

First-time notes:

- Claude Code may ask you to trust the folder, and the plugin's hooks may ask permission to
  run their scripts. Approve them to let it load.
- The three session hooks are cwd-gated: the charter, the route reminder and the PreToolUse
  supervisor gate each walk up for `standup/team.json` and exit silently if they do not find
  one, so installing this plugin does not touch your unrelated projects
  ([`hooks/supervisor_gate.py`](hooks/supervisor_gate.py)). The three native-team lifecycle
  hooks are not cwd-gated — they govern any native agent team you run while the plugin is
  enabled, applying this install's kill switch and guardrails, and they fail open if they
  cannot load rather than wedging your team.

## Layout

```
agent-team/
├── CLAUDE.md           how Claude Code runs the team (read on open)
├── ARCHITECTURE.md     how the parts connect — which file owns which beat
├── DESIGN_RULEBOOK.md  the numbered design rules the design gate judges against
├── .claude/commands/   /standup and /portal — the two unprefixed commands
├── skills/             the sixteen plugin commands
├── hooks/              the supervisor charter, the route reminder, the PreToolUse gate
├── standup/            the engine
│   ├── team.json       the roster, and the canonical policy
│   ├── standup.workflow.js   the gated pipeline the Workflow tool runs
│   ├── portal/         Mission Control: a FastAPI API and a no-build static UI
│   └── control/        the job queue, the git-worktree lifecycle, the locked-down job gate,
│                       verify_design_quality.js and check_workflow_parse.js
├── evals/              the regression gold-set + resolve_cases.py (which cases can run here)
├── demo-app/           an OPTIONAL sample project (a small Python library) — safe to delete
└── setup.sh            installs the portal (venv, deps, gate config, demo-app git if present)
```

## The gates

Each of these is bound in code rather than requested in a prompt. That is the whole difference
between a gate and a wish.

**The design gate.** A design-quality lens joins the review ring whenever a change has an
observable surface, so that some lens in the ring is responsible for whether the screen is any
good. It judges against [`DESIGN_RULEBOOK.md`](DESIGN_RULEBOOK.md) — numbered, citable rules,
where a finding without a rule id is inadmissible (`E-01`) — and it runs a deterministic judge
first, whose exit code is the verdict.

```bash
node standup/control/verify_design_quality.js <url>
node standup/control/verify_design_quality.js --self-test   # prove the judge can still fail
node standup/control/verify_design_quality.js --rule-ids    # the citable rule ids
```

Every code it can return — including 3, which is what a failing `--self-test` above hands you:

| exit code | what it means |
|---|---|
| 0 | no violations |
| 1 | violations |
| 2 | the judge ran, but the page could not be loaded — and, from `--rule-ids`, that the rulebook itself could not be read |
| 3 | `--self-test` failed: a rule did not fire on the deliberately broken fixture, or the fixture is gone |
| 4 | the judge itself could not run — the gate is broken, not the page |
| 64 | usage: no URL given, or `--rules` naming an id the rulebook or the judge does not have |

Exit 4 is the one to know when adopting this: the judge needs Playwright
(`npm i -D playwright && npx playwright install chromium`), and without it the gate fails
loudly instead of passing quietly. Exit 0 is a floor and not a verdict (`E-07`): the judge
catches "looks wrong" and is blind to "looks right, is lying". The gate reads the current
state of the whole surface rather than the diff (`E-05`), and one rule cited twice is a
shared-component fix rather than N per-file tickets (`E-02`). The target URL is a parameter
(`args.designUrl`), never a baked-in default; the judge itself ships a deliberately broken
fixture so `--self-test` proves it can fail (`E-03`).

**A reservation is not a veto.** When a reviewer sets `approve=false` (or `approved=false`) it
must also answer `blocking`: is this a real blocker — would work done against it have to be
thrown away rather than amended? A wording fix, an optional hardening, a "one amendment away"
are all `blocking=false`, and the run continues carrying the reviewer's `must_fix`. A missing
`blocking` still stops, because silence is not consent. Either way the reservation earns its
revision round, so the objection is absorbed rather than dropped. Without this, the gate got
*less* permissive the more diligent the reviewer: three consecutive runs on one task, roughly
4.6M tokens and zero lines of code, while the supervisor's own verdicts said go three times
([`CHANGELOG.md`](CHANGELOG.md), `[0.3.7] — 2026-08-03`).

**Bad input throws instead of degrading.** Unparseable `args` used to become `null`, which
silently turned a single-task dispatch into a whole-roster standup that was structurally
incapable of producing code. One unescaped double-quote was enough, and nothing errored. It
now throws, quoting the parse position and the text around it.

**The code-task gate.** A portal code task runs its agent under five independent layers
([`standup/portal/parsers/agent_run.py`](standup/portal/parsers/agent_run.py)): a tool
allow-list, the default permission mode, a deny-by-default hook confining every read and write
to the job's own git worktree, an empty MCP config with a credential-stripped environment, and
the worktree as the agent's whole filesystem scope. That agent *can* write — it has to, or
there would be no diff for you to review — so what confines it is the worktree, not
read-only-ness. It gets no shell at all, which is why it cannot run the tests, commit, push or
merge; the trusted worker does that, and only after you approve. Nothing is ever pushed.

## Point it at your repo

Four preconditions, then the team is working on your own code instead of the sample:

1. Add a developer to a squad in `standup/team.json` whose `folder` is your repo.
2. Give that squad a `review_surface` — the pipeline refuses to run against an assignee, a
   pair, a folder or a review surface nobody declared.
3. Make sure the repo has an `origin` remote.
4. Run `/standup`, or submit a code task targeting `project:<your-folder>`.

## Tests

```bash
cd standup/portal && ../.venv/bin/python -m pytest -q                     # the portal engine
cd demo-app && python3 -m pytest -q                                       # the sample library
node standup/control/verify_design_quality.js --self-test                 # the design judge can still fail
node standup/control/check_workflow_parse.js standup/standup.workflow.js  # the engine still loads
node standup/control/tests/test_sdlc_routing.js                           # both entry paths still reach intake
bash standup/control/tests/test_setup_guard.sh                           # the installer survives a deleted demo-app
bash standup/control/tests/test_precondition_parity.sh                   # every doc states the demo-app precondition the same way
bash standup/control/tests/test_eval_resolver.sh                         # /eval says which cases it skipped, and why
```

Each of those takes `--self-test`, which deliberately breaks the thing being judged and requires the
judge to go red. Run it before trusting a green.

Pass and failure counts are deliberately not printed here. A number copied into prose rots
exactly the way a version number does; run the commands and read what they print.

## Platform and project

Supported: macOS and Linux — bash, Python 3.9+, git, the `claude` CLI, and the Workflow tool
for `/standup`, with a Task-tool fallback. Windows is not currently supported. The security
model and the platform notes are in [`SECURITY.md`](SECURITY.md).

- License [MIT](LICENSE) · changes [`CHANGELOG.md`](CHANGELOG.md)
- CI runs the portal and demo-app suites on pushes to `main` and on every pull request
  (`.github/workflows/ci.yml`).

---

*Distilled from a larger round-the-clock multi-squad system. The research-backed design —
divide by responsibility, pairs critique rather than debate, deterministic test gates, name
experts only for alignment tasks, human gates on irreversible actions — carries over. The
host-specific plumbing it grew around (a data warehouse, a chat tool, deploy hosts, mail
intake) was stripped so this one runs self-contained.*
