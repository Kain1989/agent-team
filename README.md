# Agent Team Harness

**Software delivery, run by agents, governed by checks.** Seven teams of Claude Code agents take a story
from request to deployment through eight phases and nine gates. Nothing advances because an agent said
it was done: a hook stops forbidden tool calls before they run, deterministic checks read every phase's
artifacts off disk, and four decisions stay with a person.

```
Supervisor (you) ── four human gates
  └─ Claude (main session) ── manages seven leads, produces nothing itself
       product · ux · dev · qa · review · visual · devops   (7 leads on opus, 19 ICs on sonnet)

1 PRD ─► 2 UX ─► 3 Dev ─► 4a Unit+Int ─► 4b Sys+Reg+Perf ─► 5 Review ─► 6 Visual ─► 7 Deploy
   ▲human          ▲human (plan)                               ▲human                  ▲human (beyond dev)
                    ◄────── code / test / visual defects, capped rounds ──────┘
```

- [ARCHITECTURE.md](ARCHITECTURE.md) — teams, privileges, pipeline, every check, budgets (generated from the live config)
- [DESIGN.md](DESIGN.md) — why: what was kept from v1, what the sources disagreed on, which outside ideas were adopted
- [SECURITY.md](SECURITY.md) — what each layer guarantees and what it does not

## Quick start

Requirements: Claude Code, Python 3.9+, git. Optional: Node + Playwright (phase 6), `gh` (real PRs).
macOS, Linux and Windows.

```bash
git clone https://github.com/Kain1989/agent-team.git
cd agent-team
python .agentteam/bin/setup.py          # wires the hooks with this machine's interpreter, smoke-tests them
python .agentteam/bin/selftest.py       # proves every gate check still goes red when broken
```

Then, in Claude Code inside this folder:

```
/add-project clone https://github.com/you/your-app.git
/story Add CSV export to the reports page
```

The story stops at each human gate and tells you which file to review and what to run, in your own
terminal:

```bash
python .agentteam/bin/story.py human-gate STORY-001 prd-signoff approve --by "Your Name"
```

`python .agentteam/bin/story.py board` shows every story; `story.py show STORY-001` shows one.

## What makes a gate mean something

| | |
|---|---|
| **Least privilege by role** | readonly roles run an allow-list; QA writes only tests; dev can't touch acceptance tests; review writes nothing |
| **Evidence, not claims** | test suites are run by `story.py run`, which records the exit code against HEAD; a pass at an older HEAD is stale |
| **Test integrity** | `integrity.py` refuses deleted/skipped/always-true/commented assertions, loosened comparisons, edited expected values and snapshots |
| **Traceability** | every REQ → TASK → test → PR; QA sign-off is blocked while any Tests cell is empty, and every cited test must exist on disk |
| **Clean context** | reviewers get a code-built packet (PRD, diff, trace, QA reports), never the plan or the author's reasoning |
| **Anti-thrash** | findings are fingerprinted by file + symbol + category; the same finding twice is an escalation, not another loop |
| **Budgets** | per-phase agent and token ceilings, measured by a hook; a breach fails the gate |
| **E-03** | every one of the checks has a fixture that turns it red; a check that has never failed is not allowed |

## Coming from v1 (≤ 0.5.2)

v2 is a rebuild, not an upgrade: the unit of work is a story rather than a board tick, roles are
lead + ICs rather than pairs, and gates are deterministic checks rather than model verdicts. The
Mission Control portal is not in v2.0 (story state is plain files; see DESIGN.md's roadmap).

v1 stays available: its final state is tagged **`v1-final`** in this repository and is archived as its
own repository (**`Kain1989/agent-team-v1`**). If you installed v1 as a Claude Code plugin, pin it to that
repository — v2 is a project you clone and run `setup.py` in, not a marketplace plugin.

## Layout

```
.agentteam/config/   roster.json · pipeline.json · policy.json · budgets.json   ← the single source of truth
.agentteam/bin/      story.py · gate.py · guard.py · integrity.py · env.py · project.py
                     roster.py · architecture.py · setup.py · selftest.py · usage_hook.py · visual/
.claude/agents/      26 generated agent definitions        .claude/skills/   /story /add-project /architecture /selftest
workspace/           your project repos + story worktrees (git-ignored)
.agentteam/stories/  one directory per story: artifacts, gate records, runs, findings, human records (git-ignored)
```

## License

MIT — see [LICENSE](LICENSE).
