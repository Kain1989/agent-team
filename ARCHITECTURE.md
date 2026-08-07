# Agent Team — how the parts connect

This file describes what can **stop** a run, and which file owns each beat of it. What the run
*does* — the pipeline steps, the review rules, the role definitions, the budget semantics — is
policy, and policy has one home: [`standup/team.json`](standup/team.json). This file keeps no
copy of that step list. What it does list is *gates*, derived from the engine and cited row by
row, which is a different thing from the roster's array and is allowed to differ from it — when
it does, the roster is what changes. The roster gives the reason at
`manager.policy._sdlc_pipeline_note`: before that rule, the sequence "was written out nine
times in three different arrow glyphs with five different step counts".

Read [`README.md`](README.md) first if you are still deciding whether to adopt this. This file
assumes you have.

Every claim below carries the `file:line` that makes it true, and the citation is meant to be
read: it points at the line that *entails* the sentence, not merely at the neighbourhood. Where
a table's subject is itself a file, column one carries it; otherwise the citation lives in the
last column, labelled `file`. One convention, six tables.

## What happens when you type /standup

Beats 2 through 8 are the engine's own `meta.phases` array in order
([`standup/standup.workflow.js`](standup/standup.workflow.js) `:10-18` — seven entries, which is
why the numbering below is not an index into it). Beats 0, 1 and 9 bracket that array: they
happen on either side of the phase list and are cited to their own lines. One beat per line; the
file after the dash owns it.

0. Claude Code reads the roster and calls the engine through the Workflow tool —
   [`CLAUDE.md`](CLAUDE.md)
1. `args` are parsed, and an args string that will not parse throws rather than becoming
   `null` — `standup.workflow.js:37-38` catches, `:40-45` throws
2. Comms: an optional staff agent triages a local `messages/inbox/`, skipped unless an active
   `comms_triage` exists — `standup.workflow.js:612-613`, phase tag at `:610`
3. Standup: one read-only agent per active developer, each resuming from
   `<folder>/.standup/<dev>.md` — `standup.workflow.js:637`, `:639`, phase tag at `:632`
4. Team Sync: each squad's reports merge into a squad board (`standup.workflow.js:680`), and
   the squad's declared review surface is printed with its inspect command verbatim (`:689`)
5. Design: the design lead runs the deterministic judge over the live surface (`:796`), then
   the judgment rules — and it runs before the board, so its findings become items on this
   tick rather than notes in a file — phase tag at `:713`, which precedes Synthesize at `:846`
6. Synthesize: the EM merges the squad boards into one ranked board —
   `standup.workflow.js:846`
7. Staff Pulse: the product manager, product QA and the design lead each take a light real
   lens over that board — `standup.workflow.js:885` selects exactly those three
8. Work: the gated pipeline runs on the top task, and the next section is what can stop it —
   `standup.workflow.js:925`
9. The run prints a closing line whose statuses are enumerated from the records, so a status
   added later cannot become invisible — `standup.workflow.js:218`, `:224`; the fixed `order`
   array at `:220-222` only sorts what is present, and `:215` says why

Said out loud: the team reports, the reports become one ranked board, the board's top item is
built and reviewed behind gates, and if it survives all of them it commits to a feature branch.
The `order` array at [`standup/standup.workflow.js`](standup/standup.workflow.js) `:220-222`
names ten terminal states, and they do not all mean the same thing:

- status `committed` — every gate passed and the change is on a feature branch (`:1272`)
- status `escalated-intake` — the outcome contract was still unclear after one revision (`:989`)
- status `blocked-investigate` — the investigator judged the task infeasible as written (`:1007`)
- status `escalated-plan-rejected` — the pair's challenge blocked the plan (`:1048`)
- status `test-gate-failed` — the suite, the design judge or the live check did not satisfy the gate (`:1113`)
- status `review-failed` — not every planned review lens passed (`:1273`)
- status `supervisor-rejected` — the final read rejected the committed diff (`:1271`)
- status `green-not-committed` — the change went green and no commit was made (`:1273`)
- status `blocked` — the plan agent returned nothing (`:1019`); status `blocked` again — the implement agent returned nothing (`:1063`). One status name, two call sites
- status `work-error` — the task threw, and the catch records the message (`:1284`)

One success, six gate stops, one commit no-op, one dead agent, one thrown error: ten. The
partition is the point. A run that ends in one of the six is the pipeline working. The last
three are not — nothing judged the change and refused it — and reading them as ordinary is
how a real fault gets filed as normal.

Nothing is pushed — no phase in the engine pushes, merges or deploys, the commit step is told
so in as many words (`:1257`), and `meta` records it as the boundary of the pipeline (`:17`).

## The other entry point

`/work <task>` runs the same pipeline on one named task. It is not a different engine and it
does not skip the gates — it enters at beat 8. `SINGLE` is set only when `args.task` is present
(`standup.workflow.js:604`), and six of the seven phases are skipped when it is, by two
different mechanisms. Five are `phase()` calls behind an `if (!SINGLE)` guard: Comms (`:610`),
Standup (`:631-632`), Design (`:713`), Synthesize (`:846`) and Staff Pulse (`:879`). Team Sync
is not a `phase()` call at all — `meta` says so at `:13` — and is skipped because the per-squad
loop it lives in is `SINGLE ? [] : …` (`:636`), so its agent (`:680`) never runs. Work is the
exception: it runs when either `args.work` or `args.task` is set (`:925`).

That single Work loop is the system's central structural claim. Because there is physically
one of it, an improvement to a gate cannot land on one dispatch path and quietly miss the
other. The engine states the cost of that choice itself at `:601-603`: the upstream phases are
skipped by guard rather than by structure, so each guard is a place a future edit could
reintroduce divergence — which is why the routing judge asserts that *both* paths reach intake
instead of asserting it once.

## What stops the work phase

Each row is a gate: who owns it, and what a stop looks like in the run record. The steps
themselves are policy and live in the roster.

| gate | what a stop looks like | file |
|---|---|---|
| Intake — the supervisor gates the outcome contract, with one revision and one recheck | status `escalated-intake` (`:989`) — nothing is investigated, planned or built | `standup.workflow.js:959`, `:968`, `:984` |
| Investigate — read-only evidence first, and a feasibility judgment | status `blocked-investigate` (`:1007`) | `standup.workflow.js:1004` |
| Plan challenge — the pair critiques in fresh context | status `escalated-plan-rejected` (`:1048`) | `standup.workflow.js:1028` |
| Test gate — the suite must actually run, and the supervisor reads the report for honesty | status `test-gate-failed` (`:1113`) | `standup.workflow.js:1096`, `:1100` |
| Review — fresh-context lenses on the real diff, plus a design lens when the change has an observable surface | status `review-failed` (`:1273`) | `standup.workflow.js:1188`, `:1195`, `:1203` |
| Commit — only on green, feature branch, only the task's own files | status `green-not-committed` when there is nothing committable (`:1273`) | `standup.workflow.js:1251` |
| Supervisor final — sign-off on the committed diff | status `supervisor-rejected` (`:1271`) | `standup.workflow.js:1262`, `:1266` |

Who *owns* each of those gates is not one answer, and the roster's rule that a writer never
grades their own work is why it cannot be. The supervisor — Claude Code itself — owns intake
outright ([`standup/standup.workflow.js`](standup/standup.workflow.js) `:968`, `:984`) and the
final read (`:1266`). It shares the test gate, which passes only if the runner's own report and
the supervisor's honesty read both hold (`:1113`). The other four it does not own at all: the
plan challenge is authored by the lanemate (`:1028`) and stops the run through `challengeBlocks`
(`:1048`), review green is the lenses' own arithmetic (`:1244`), commit is a plain conditional
with no agent judging anything (`:1251`), and investigate turns on the investigator's own
`evidence.feasible` (`:1007`) — a feasibility report about the task, not a verdict on their work.

Two predicates decide whether a reservation stops the run, and they differ on the case that
matters. `challengeBlocks` (`:415`) — the pair's plan challenge — guards null itself: it opens
`!v ||`, so a dead agent stops the run. `isBlocking` (`:496`) does **not**: it opens `!!v &&`,
so `isBlocking(null)` is *false*. The null-stop for supervisor verdicts is supplied by the call
site, `if (!intakeOk || isBlocking(intakeOk))` (`:988`), and the engine flags that leading
`!intakeOk ||` as load-bearing at `:986`. It is not redundant, and deleting it would reopen
exactly the silent-gate class this release closed. Both predicates treat `blocking=false` as
"continue, carrying the objection", and both treat a *missing* `blocking` field as a stop.

`isBlocking` has that one call site, the intake recheck. The test gate (`:1113`) and the
supervisor's final review (`:1271`) read `.approve` directly and never consult `blocking`.

The review lens set is not a fixed number. Green requires every lens *planned for that task*
to have returned and passed (`:1244`), and the engine's header at `:5-8` says why it refuses to
print a count: advertising one is how an added lens gets silently ignored.

## What each control file gates

Beat 5 calls the design judge. The rest of this table is not the engine's machinery — grep
`standup/standup.workflow.js` for `job_gate`, `job_readonly`, `job_code_gate`, `run_lock`,
`budget.json` and `kill_switch` and it returns nothing; the engine names none of them. The job
gates are the portal's, handed to its two agent subprocesses: `run_readonly` for a read-only
board job ([`standup/portal/parsers/agent_run.py`](standup/portal/parsers/agent_run.py) `:159`,
gate config read at `:184`–`:185`) and `run_code_task` for a code task (`:268`, at `:292`–`:293`),
the dispatch path the last section of this file describes. The budget and the kill switch are
the portal's too, but they sit further out — they stop the worker claiming new work at all
rather than fencing a subprocess already running. The run lock is no one component's:
`standup/control/drain.py:321` takes it, and so does the portal, on the two paths the
`run_lock.py` row below sets out.

| file | what it gates | what happens when it fires |
|---|---|---|
| `standup/control/job_readonly_gate.json` with `job_gate_hook.py` | every tool call a read-only board job makes | deny-by-default: only the read tools are allowed, everything else is denied, and malformed input fails closed |
| `standup/control/job_code_gate.json` with `job_code_gate_hook.py` | every read and write a code-task agent makes | allowed inside the job's git worktree only; shell, sub-agents, network fetch and MCP tools are denied |
| `standup/control/job_empty_mcp.json` | which MCP servers a job may load | none — it is an empty config passed with `--strict-mcp-config`, so no configured server is reachable |
| `standup/control/verify_design_quality.js` | whether an observable change may go green | the exit code is the verdict. This file keeps no copy of what each code means: `README.md` carries that table once, in "The gates", at `:224` through `:231` |
| `standup/control/check_workflow_parse.js` | whether an edited engine still loads | it wraps the script the way the harness does and hands it to the real parser; `node --check` passes on an unescaped backtick that truncates a prompt template, so it is not sufficient |
| `standup/control/run_lock.py` | whether two ticks can run at once | one holder at a time, and a second launch defers rather than runs. The portal both reads it and takes it: `parsers/actions.py:92` reads the holder without acquiring, while `parsers/jobworker.py:85` builds the lock for a code task and takes it at `parsers/jobworker.py:87`, called from `parsers/jobworker.py:288`, and `parsers/scheduler.py:343` does the same for a scheduled tick, taking it at `parsers/scheduler.py:345` |
| `standup/control/budget.json` and `standup/control/kill_switch` | whether the worker may claim new work | over the daily cap, or with the kill-switch file present, no new job is claimed (`standup/portal/parsers/costs.py:44`, `:95`) |
| `standup/control/team_task_created_hook.py` | a native teammate task at creation | the input guardrail plus a kill-switch check; exit 2 blocks creation |
| `standup/control/team_task_completed_hook.py` | a native teammate task at completion | the produced diff is secret-scanned; exit 2 blocks completion, and a scan error on a non-empty diff fails closed |
| `standup/control/team_teammate_idle_hook.py` | a native teammate about to go idle | with the kill switch set, the teammate is stopped rather than idled |

## The portal's boundary

The portal cannot become a second source of truth, and the reason is structural rather than a
promise. The files it reads it does not write, and the files it writes are its own — with one
shared exception, `control/run.lock`, which it takes as well as reads (the row above), because a
lock only works if every writer holds it.

| reads, never writes | owns and writes |
|---|---|
| `standup/team.json` — `parsers/team.py:37` | `control/jobs.db` — `parsers/db.py:240`, `api_jobs.py:423` |
| `standup/BACKLOG.md` — `parsers/backlog.py:362` | `control/runs/<run-id>.json` — `parsers/runs.py:66` |
| `standup/log/<date>.md` — `parsers/log.py:143` | `control/requests/` and `control/results/` — `parsers/actions.py:576`, `:608` |
| `<folder>/.standup/<dev>.md` — `parsers/devlog.py:88` | `control/control.log` and `control/notifications.log` — `parsers/actions.py:195`, `parsers/notify.py:31` |
| | `control/worktrees/<job-id>/` — `parsers/worktree.py:154` runs `git worktree add`, destination from `parsers/jobworker.py:306` |
| | `.claude/agents/<role>.md` — `parsers/agents_gen.py:166`, output directory at `:193` |

Every non-test reference to a left-column file was resolved one by one. All but one are reads.
The exception is neither: `parsers/scheduler.py:240` names the daily log, and
`parsers/scheduler.py:242` names `standup/BACKLOG.md`, inside a prompt string that tells the
launcher agent to write them — the portal names those files, an agent outside it does the
writing. The API's cached wrappers
(`app.py:249`, `:254`, `:262`) call the parsers above and use the file's modification time only
as a cache signature; `app.py:462` and `:463` read a modification time and nothing else.
`parsers/jobworker.py` looks like a writer to a grep — its module docstring at `:18` and `:20`
spells out the SQL transitions — but every write it makes goes through `parsers/db.py`.

Paths are resolved through `parsers/paths.py`, which is why an alternate checkout can be
pointed somewhere else with `STANDUP_ROOT` without any of the above moving.

## The five code-task confinement layers

A code task submitted in the portal is the one dispatch path that does not go through the spine
above: it runs a headless agent of its own. Unlike the read-only board job described in the
previous table, that agent **must write** — if it could not, there would be no diff for the
approval queue to review. So it is not confined by being read-only; it is confined by the
worktree. Five independent layers do that, and `run_code_task`
([`standup/portal/parsers/agent_run.py:268`](standup/portal/parsers/agent_run.py)) enumerates
them itself at `:283-289`.

| layer | what it stops | file |
|---|---|---|
| Tool allow-list | read plus `Edit`, `Write` and `MultiEdit` — and nothing else, so no shell, sub-agent, network fetch or MCP tool is reachable and a newly added or renamed tool is denied by omission | `agent_run.py:263-265`, passed at `:305` |
| Default permission mode | not `bypassPermissions`, which is what auto-ran every tool that had not been explicitly denied — the blacklist was the hole | `agent_run.py:302` |
| The code gate hook | deny-by-default `PreToolUse`, scoping every read and every write to the worktree named in `STANDUP_CODE_WORKTREE`; this is the machine boundary if a flag is ever weakened | `standup/control/job_code_gate_hook.py`, selected at `agent_run.py:292`, passed at `:312`, scoped at `:316` |
| Empty MCP config and stripped environment | no configured MCP server loads, and the child environment is stripped of credential-shaped variables, so even a leaked config path cannot authenticate | `agent_run.py:307-308`, `:314` |
| The worktree is the filesystem scope | `cwd` and `--add-dir` are both the worktree, so Claude's own scope matches the hook's rather than sitting wider than it | `agent_run.py:310`, `:319` |

The allow-list is the first fence, not the boundary: it has to stay in lockstep with `_ALLOWED`
in the hook (`job_code_gate_hook.py:61`), which is what actually decides, and both sides say so
(`agent_run.py:259-262`, `job_code_gate_hook.py:60`). The agent never runs a shell, runs the
tests or commits — the trusted worker does that (`agent_run.py:262`, `:278-279`), and only
after a human approves in the portal. Nothing is pushed or merged from here at all.

The read-only board job in the control-file table is a *different function*, `run_readonly`,
with its own allow-list (`READONLY_ALLOWED_TOOLS`, `agent_run.py:75`) and its own settings file
— which is why those two rows name different gate configs. Reading this section as a
description of that one is the mistake it is worded to prevent.

## The four native-team bridges

Claude Code's native agent teams supply the team mechanics — a lead, teammates, a shared task
list, a peer mailbox. Four bridges connect them to this system's governance.

| bridge | what it does | file |
|---|---|---|
| Roster becomes teammates | `/sync-roster` generates a definition for every active developer and staff role, and prunes definitions no longer in the roster | `standup/portal/parsers/agents_gen.py:139`, `:141` filter on `active`; `:151-162` refuses an id that is not a filename; `:166` writes; `:171-174` prunes |
| Governance on the task lifecycle | `TaskCreated`, `TaskCompleted` and `TeammateIdle` hooks put the same guardrails and kill switch on native tasks | `hooks/hooks.json` |
| The portal observes | `GET /api/native-teams` summarizes live native teams alongside the job queue | `standup/portal/app.py:731` |
| `/team` dispatches | the roster spawns as a native team on a task | `skills/team/SKILL.md` |

The two paths differ in who schedules the work: the Workflow path runs the phases above in a
fixed order, while a native team self-organizes over a shared task list. They also differ in
how much of the governance is machine-bound. The guardrails, the kill switch and the secret
scan are hooks on both. The SDLC gates are not: on the Workflow path they are the engine's
control flow, while on the native path the lead is *instructed* to run the same canonical
sequence (`skills/team/SKILL.md:24`). Enable native teams with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `~/.claude/settings.json`.

## Plugin versus plain checkout

Everything above is the same either way. What differs is how much of it is reachable, and from
where.

| | installed as a plugin | plain checkout |
|---|---|---|
| commands | all sixteen, namespaced `/agent-team:<name>`, loaded from the manifest's `skills` entry | `/standup` and `/portal` only — the two files in `.claude/commands/` |
| hooks | loaded from `hooks/hooks.json`, whose commands resolve through `${CLAUDE_PLUGIN_ROOT}` | not loaded, unless the checkout is itself registered as a plugin |
| scope | cwd-gated: the supervisor gate walks up for a directory holding `standup/team.json` and leaves anything outside a checkout alone — `hooks/supervisor_gate.py:65` | same gate, same rule |
| release | `.claude-plugin/plugin.json` → `version`; the marketplace manifest carries its own separate `version` field | `.claude-plugin/plugin.json` → `version` |
