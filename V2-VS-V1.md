# v2 vs v1

v2 (2.0.0) is a rebuild of agent-team v1 (0.1.0 – 0.5.2). This page lists what changed, why, and what you
give up by moving. The reasoning behind each decision is in [DESIGN.md](DESIGN.md).

v1 is still available, frozen at its last release:

- **Archive repository:** [Kain1989/agent-team-v1](https://github.com/Kain1989/agent-team-v1)
- **Tag in this repository:** [`v1-final`](https://github.com/Kain1989/agent-team/tree/v1-final)

## At a glance

| | v1 (≤ 0.5.2) | v2 (2.0.0) |
|---|---|---|
| Unit of work | a board **tick** (`/standup` polls every developer, ranks one board, works the top task) | a **story** moving through 8 phases, from PRD to deploy |
| Roles | developer **pairs** per project squad, plus staff roles (PM, design lead, product QA) | **7 teams**, each a lead (opus) with ICs (sonnet): 26 agents, generated from one roster |
| Who decides a phase is done | mostly **model verdicts** (`sup:*` supervisor agents) | **91 deterministic checks** in `gate.py`, reading artifacts off disk |
| Human decisions | commit approval in the portal; merge/push | **4 human gates**: PRD sign-off, dev-plan sign-off, pre-merge, deploy beyond dev |
| Enforcement | a PreToolUse hook that identified the supervisor **by cwd** | a PreToolUse guard that identifies **each role** by the hook's `agent_type` |
| Engine | the Workflow tool running `standup.workflow.js` (~1,650 lines) | plain Python CLIs (`story.py`, `gate.py`) driven by the `/story` skill |
| UI | the **Mission Control portal** (FastAPI, code-task queue, approvals) | none in 2.0; `story.py board` / `show` print the state |
| Install | Claude Code **plugin** (`/plugin marketplace add`) or a checkout | a checkout + `python .agentteam/bin/setup.py` |
| Windows | unsupported (124 portal tests failed there; `pip install` failed on a GBK locale) | supported and tested in CI, as are Linux and macOS |

## What got better

**Gates that can't be talked past.** In v1 a supervisor model read each phase and decided whether it
was honest. In v2 each gate is a list of checks with exit codes. They check that the PRD has numbered
REQs with EARS `SHALL` criteria, that every TASK cites a REQ, that the diff is non-empty and has no
secrets, that tests were run by the harness at the current HEAD, that every REQ traces to a test that
exists on disk, and that the review packet is clean. A gate either passes or names the check that
failed.

**Each check is shown to fail.** v1's E-03 rule ("a judge that can't catch breakage isn't a judge")
applied to the design judge. In v2 it applies to every gate check: `selftest.py` breaks a golden story
once per check and requires that check to go red. A check without a breaking fixture fails the
selftest.

**Tests are evidence, not claims.** v1 asked a model to verify the test gate "for honesty". In v2,
`story.py run` executes the suite itself and records the exit code, the HEAD sha and an output digest.
A pass the harness didn't run doesn't count, and a pass at an older HEAD is stale.

**Test tampering is detected.** `integrity.py` diffs test files against the branch base and refuses:

- deleted, skipped, always-true or commented-out assertions;
- loosened comparisons and changed expected values;
- edited snapshots and golden files.

It is also structural: QA may write only tests, dev may not write acceptance tests, and a dev who thinks
a test is wrong files a `test-flaw` finding instead of editing it. v1 had none of this.

**Review really is clean-context.** v1 told reviewers they saw "only the diff" while the engine pasted
the approved plan and the implementation report into their prompts. In v2 the review packet is built by
code with a hashed manifest. The review gate refuses a packet that is contaminated or stale, and the
guard denies reviewers reads of withheld paths.

**Least privilege per role, not per session.** v1 could only tell "the EM session" from "everyone else".
That is why it needed the `team_run_active` flag, which is gone in v2. Every role now has its own Bash
profile and write roots:

| Role | Bash | Writes |
|---|---|---|
| readonly roles | allow-list | story files and state only |
| review | allow-list | nothing |
| devops | deploy commands; beyond dev only after the human gate | story files and state |

Some commands are hard-denied for everyone, Claude's main session included: force-push, push to a
default branch, merging a PR and bypassing hooks.

**Loops end.** In v2 review and visual defects flow back to dev or QA through `story.py reopen`, which
counts rounds against a cap. Findings are fingerprinted by file, symbol and category: the second
sighting escalates to the supervisor, and the third to you. v1 had one revision cycle for the plan
challenge, and nothing that recognised the same finding coming back.

**Budgets are measured.** v1 tracked costs per run. v2 sets agent and token ceilings per phase. A
`SubagentStop` hook measures actual usage, and a breach fails the gate.

**Spec quality up front.** These are ideas from Spec Kit, Kiro and BMAD:

- a per-project `constitution.md` that the dev plan must check itself against;
- EARS acceptance criteria;
- self-contained story directories.

v1 started each task from a backlog line.

**One source of truth.** `roster.json`, `pipeline.json`, `policy.json` and `budgets.json` generate
the agent definitions and [ARCHITECTURE.md](ARCHITECTURE.md). CI fails if either drifts from the
config. In v1 the policy lived in `team.json` but was restated across the engine, the skills and the
docs.

**Runs on Windows.** v2 uses stdlib Python with argv lists instead of shell strings, explicit UTF-8,
and retries for Windows file locks. CI covers Linux, macOS and Windows on Python 3.9 and 3.12.

## What v2 kept from v1

- [`DESIGN_RULEBOOK.md`](DESIGN_RULEBOOK.md) and the machine design judge. v2 runs them in phase 6;
  exit 0 still proves nothing (E-07).
- Secret scanning of every produced diff.
- An empty diff fails with a message that names it as an empty diff, not as a quality failure.
- `setup.py` bakes in the absolute interpreter path and smoke-runs the configured hook command. 0.5.2
  learned that a hook which can't execute is a boundary that silently isn't there.
- Every agent works on a feature branch, and push and merge stay with a person.

## What you give up (for now)

- **The Mission Control portal.** v2 has no web UI yet. Story state is plain files, so one can be built
  on top of them later ([roadmap](DESIGN.md#not-in-v20-roadmap)).
- **Recurring standups.** v2 runs one story at a time, on request. It has no tick cadence and no
  board-wide polling of every developer.
- **Plugin install.** v2 is a project you clone, not a marketplace plugin.
- **v1's skills.** `/standup`, `/work`, `/team`, `/portal`, `/add-team`, `/add-role`, `/costs`,
  `/runs` and the rest are not in v2. Its skills are `/story`, `/add-project`, `/architecture` and
  `/selftest`.

If you rely on any of these, stay on v1 (the next section explains how). v1 is frozen and will not get
fixes.

## v1 resources

All links are pinned to the `v1-final` tag in the archive repository:

- [v1 README](https://github.com/Kain1989/agent-team-v1/blob/v1-final/README.md): install, `/standup`,
  `/work`, `/team`, plugin setup
- [v1 ARCHITECTURE.md](https://github.com/Kain1989/agent-team-v1/blob/v1-final/ARCHITECTURE.md):
  what can stop a run, cited line by line
- [Mission Control portal](https://github.com/Kain1989/agent-team-v1/blob/v1-final/standup/portal/README.md):
  the web UI, code-task queue and approvals
- [The v1 engine](https://github.com/Kain1989/agent-team-v1/blob/v1-final/standup/standup.workflow.js)
  and [roster/policy](https://github.com/Kain1989/agent-team-v1/blob/v1-final/standup/team.json)
- [v1 skills](https://github.com/Kain1989/agent-team-v1/tree/v1-final/skills) and
  [hooks](https://github.com/Kain1989/agent-team-v1/tree/v1-final/hooks)
- [v1 CHANGELOG](https://github.com/Kain1989/agent-team-v1/blob/v1-final/CHANGELOG.md) (0.1.0 – 0.5.2)

To keep using v1 as a plugin:

```
/plugin marketplace add Kain1989/agent-team-v1
```

To use it as a checkout:

```bash
git clone https://github.com/Kain1989/agent-team-v1.git
```
