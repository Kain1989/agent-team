---
name: selftest
description: Prove the agent team's gates still block — runs the guard cases, breaks a golden story once per gate check and requires each check to go red, and smoke-runs the configured hook. Use after any change under .agentteam/, when the user asks whether the harness works, or types /selftest.
---

# /selftest

    python .agentteam/bin/selftest.py

Report the summary line verbatim. If anything failed, list each FAIL line and stop: a check that no
longer goes red is a gate that no longer blocks, so no story should advance on it until it's fixed.

Sections can be run alone (`selftest.py guard gates`). `hook` fails when `.claude/settings.local.json`
is missing or its interpreter moved — the fix is `python .agentteam/bin/setup.py`, run by the user or in
maintainer mode.
