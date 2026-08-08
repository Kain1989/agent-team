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

**That runs the team on the bundled sample.** To put it on YOUR code, install this as a plugin
(below) and run `/agent-team:add-project <git-url>` — see [Point it at your repo](#point-it-at-your-repo).
Worth knowing before you pick a path: in a checkout you opened directly, **only `/standup` and
`/portal` work**, because they are the two files in `.claude/commands/`. Every other command in this
README — `add-project` included — needs the plugin installed, and is namespaced `/agent-team:…`
there. The quick start above and the headline feature are on two different install paths.

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

The squads: **none ship**. `standup/team.json` has `teams: []`, so the first `/standup` stops and
tells you to run `/add-project`. That command creates the squad — a pair of developers pointed at
the repo you clone, create, or adopt.

This includes the portal: **no squad ships that owns `standup/portal/`**, so Mission Control is not
maintained by the bundled roster. The supervisor gate still classifies `standup/portal/` as project
territory and blocks hand-editing it, so changing the portal means creating a squad for it first.

`/add-project` is the wrong tool for this one. It exists to bring a repo IN, and every guarantee it
makes — its own git repo, a baseline commit, its own `origin`, ignored by the root `.gitignore` — is
wrong for the portal, which is not a project you added but part of what this repo ships. It also
refuses the name outright, because `name` is the directory, the squad id and the developer-id prefix
at once and `standup/portal` contains a `/`. Create the squad directly instead:

```
/add-team portal — own the local Mission Control portal --kind web --inspect "bash standup/control/inspect_portal.sh"
/add-role portal portal_backend  "Portal Dev — Backend & Jobs" standup/portal
/add-role portal portal_frontend "Portal Dev — Frontend"       standup/portal
/sync-roster
```

`/add-role`'s fourth positional argument is the `folder`, and a folder may contain `/` — that is how
a role owns a subdirectory. `--kind` and `--inspect` are not decoration: the engine refuses to run a
squad whose product face nobody declared, so `/add-team` asks for them before it writes anything.

**Do not check this shape with `standup/control/verify_project.py`.** That checker enforces
`/add-project`'s invariants, which assume the directory, the squad id and every developer's `folder`
are one and the same string. Measured against a roster built from exactly the commands above, every
failure it reports is a false one: a missing `<root>/portal/` directory, a `folder` that "should" be
`portal`, and a missing `/portal/` line in `.gitignore` — none of which apply to code that already
lives here and stays where it is. Its one check that would matter, `review_surface`, passes, because
`/add-team` refuses to create a squad without one.

Two roles already point at `standup/portal/` before you do any of this: `design_lead` and
`product_qa` (below) both scope it. Until this squad exists they have somewhere to look and nobody
to hand findings to — the commands above are what closes that loop.

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
| `/add-team <id> — <mission> --kind K [--inspect CMD]` | add a squad (`--kind`/`--inspect` declare its product surface; the engine refuses a squad without one) |
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
| `/add-project <git-url> [name] [--kind K] [--inspect CMD]` | clone your repo in and give it a squad — the one command that points the team at your own code. `name` defaults to the repo basename and is also how you resolve a collision |
| `/remove-project <name>` | remove a project's squad from the roster (never deletes your code) |
| `/eval` | run the regression suite in `evals/cases.json` and score pass or fail |
| `/help` | list every command with what it does |

That table is every entry in `skills/`; nothing is omitted. (Counting them here would be one
more number to rot — `ls skills/` is authoritative.)

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
├── skills/             the plugin commands (one directory each — `ls skills/` is the count)
├── hooks/              the supervisor charter, the route reminder, the PreToolUse gate
├── standup/            the engine
│   ├── team.json       the roster, and the canonical policy
│   ├── standup.workflow.js   the gated pipeline the Workflow tool runs
│   ├── portal/         Mission Control: a FastAPI API and a no-build static UI
│   └── control/        the job queue, the git-worktree lifecycle, the locked-down job gate,
│                       verify_design_quality.js and check_workflow_parse.js
├── evals/              the regression gold-set + resolve_cases.py (which cases can run here)
└── setup.sh            installs the portal (venv, deps, gate config, runtime dirs)
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

<a id="point-it-at-your-repo"></a>

## Point it at your repo

```
/add-project clone https://github.com/you/your-repo --kind web --inspect "(npm start >/tmp/app.log 2>&1 &) && sleep 8 && curl -sS -f http://localhost:3000"
/add-project new   my-idea    --kind none
/add-project adopt already-here --kind cli --inspect "pytest -q"
```

Then `/sync-roster`, then `/work "the first thing you want done"`.

Three sources, one outcome. **clone** brings a repo in; **new** starts an empty project; **adopt**
registers a folder you already put here. All three create a squad with a pair of developers pointed
at it, record the `review_surface` (`kind` + `inspect`) the pipeline refuses to run without, add the
directory to `.gitignore` so `git add -A` here does not record it as a gitlink, and leave the
project as **its own git repo with a baseline commit and an `origin`**.

That last part is load-bearing, not tidiness. A project directory that is not its own repo is not
merely unreviewable — `git -C` resolves to the *enclosing* repo, so a run against it moves **your
installation's** HEAD and stages unrelated work. A repo with no commit leaves every file untracked
and `git diff` blind. No `origin` disables the portal's approve-then-commit loop. `new` and `adopt`
therefore get a **local bare origin** (offline, no account); an existing `origin` is never touched.
`adopt` scans for secrets before it commits anything and refuses on a hit. Those four used to be manual and
order-dependent; three of them were documented and the fourth was not, which is how people ended up
with a team aimed at a directory that was not there.

`name` (optional, defaults to the repo's basename) is used in **three** places at once: the
directory under this project root, the squad id, and the prefix of the two developer ids — with
`-` replaced by `_`, so `my-app` gives `my_app_a` / `my_app_b`. It has to be free in all three.

`--kind` is one of `web report agent api cli none`. **With `--kind none`, omit `--inspect`
entirely** — `none` declares "this has no inspectable face", which is a legitimate and honest
answer; there is no command to give. (`--inspect none` is not a magic value: it is stored as the
literal string, which is not what you meant.) **`--inspect` is the load-bearing one** — the
command a stranger runs to actually SEE the surface — and it must TERMINATE. A bare `npm start` is
a foreground server, so anything chained after it never runs; background it, wait, then probe (that
is the shape the bundled portal squad uses). `none` is an honest answer for something
genuinely faceless; an invented `inspect` that does not run is worse, because the review gate keeps
trying to cash it. Run `/sync-roster` afterwards: the new developers do not exist as agent types
until you do.

`/remove-project <name>` is the inverse. It never deletes your code — it prints the path and leaves
the directory alone.

## Tests

```bash
cd standup/portal && ../.venv/bin/python -m pytest -q                     # the portal engine
node standup/portal/tests/contract.frontend.test.js                       # the page still renders what the API sends
node standup/control/verify_design_quality.js --self-test                 # the design judge can still fail
node standup/control/check_workflow_parse.js standup/standup.workflow.js  # the engine still loads
node standup/control/tests/test_sdlc_routing.js                           # both entry paths still reach intake
bash standup/control/tests/test_arm_path.sh                              # the exemption is armed in THIS install, not a neighbour
bash standup/control/tests/test_eval_resolver.sh                         # /eval says which cases it skipped, and why
bash standup/control/tests/test_add_project.sh                          # /add-project's four invariants are checkable
bash standup/control/tests/test_remove_project.sh                       # /remove-project edits surgically and keeps your code
bash standup/control/tests/test_supervisor_gate.sh                      # the gate still blocks product work and allows management
bash standup/control/tests/test_release_invariants.sh                   # what ships is consistent with itself
```

**Run `--self-test` before trusting a green** — it deliberately breaks the thing being judged and
requires the judge to go red. Some commands above do not take the flag, each for its own reason,
and each carries the same proof by another route: the portal pytest suite has `*_rejects_*` tests
that mutate what they check; `check_workflow_parse.js` takes a **filename** and would read
`--self-test` as one, reporting a missing file as if the engine were broken; and
`contract.frontend.test.js` proves it in-band on every run — its last section renders the same
payload through the pre-fix producer shape and requires the card to break. (This line used to carry
a count and a hand-written list of the judges that DO take the flag; both went stale the first time
a judge was added, which is the same rot the paragraph below is about.)

The last two are newer and worth saying what they are for. `test_supervisor_gate.sh` covers
`hooks/supervisor_gate.py` — the one mechanism separating "the EM supervises" from "the EM writes
the product" — which had no test of any kind, while `verify_project.py` derives its deny list from
that file's constants. `test_release_invariants.sh` judges the CONTENT of the release rather than
its behaviour: that no instructional document prints an `/add-project` invocation the command
refuses, and that the `.claude/agents/*.md`, the roster and the portal's off-disk mock this repo
**ships** are consistent with each other. Two of those shipped broken in 0.5.0 — a documented
`/add-project` the command refuses, and tracked teammate definitions `/sync-roster` would have
pruned — and no other judge could see either. Every case that judges the release reads the
**committed** blob,
never your working tree — customising your own install must never redden the shipped suite — and
when there is no committed blob to read (a tarball install with no `.git`) the run says so in its
summary rather than reporting a clean pass on a question it never asked.

Pass and failure counts are deliberately not printed here. A number copied into prose rots
exactly the way a version number does; run the commands and read what they print.

## Platform and project

Supported: macOS and Linux — bash, Python 3.9+, git, the `claude` CLI, and the Workflow tool
for `/standup`, with a Task-tool fallback. Windows is not currently supported. The security
model and the platform notes are in [`SECURITY.md`](SECURITY.md).

- License [MIT](LICENSE) · changes [`CHANGELOG.md`](CHANGELOG.md)
- CI runs the portal suite, the workflow parse check, and every judge above, on
  pushes to `main` and on every pull request (`.github/workflows/ci.yml`). Each judge that takes
  `--self-test` runs it first, except `verify_design_quality.js`, which CI runs **only** as
  `--self-test` — proving it can still fail without needing a live URL in CI.

---

*Distilled from a larger round-the-clock multi-squad system. The research-backed design —
divide by responsibility, pairs critique rather than debate, deterministic test gates, name
experts only for alignment tasks, human gates on irreversible actions — carries over. The
host-specific plumbing it grew around (a data warehouse, a chat tool, deploy hosts, mail
intake) was stripped so this one runs self-contained.*
