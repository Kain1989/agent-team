---
name: add-project
description: Bring a project repo into the agent team's workspace — clone a git URL, start a new repo, or adopt a folder already under workspace/. Use when the user wants the team to work on a repo, or types /add-project.
---

# /add-project

Run exactly one of these (the user's words decide which):

    python .agentteam/bin/project.py clone <git-url> [--name <name>]
    python .agentteam/bin/project.py new <name>
    python .agentteam/bin/project.py adopt <name>

It guarantees the project is its own git repo with a baseline commit, an `origin` (a local bare repo
when it has none, so story branches never reach a real remote by accident), a `constitution.md`, and
a unit suite that actually runs on the baseline.

If it reports that no unit suite was detected, or that the suite fails on the baseline, relay that
plainly and stop. Every QA gate compares against the baseline, so a red baseline makes every later
verdict meaningless. Ask the user how the project's tests are run, and have them declare it in
`workspace/<name>/agentteam.json`:

    {"suites": {"unit": {"argv": ["{python}", "-m", "pytest", "-q"]},
                "perf": {"na": "no performance requirement"}},
     "visual_url": "http://127.0.0.1:5173"}

Then point the user at `constitution.md` — it holds the project's non-negotiables, and the dev plan is
checked against it. Next step: `/story <title>`.
