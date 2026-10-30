---
name: architecture
description: Regenerate ARCHITECTURE.md (teams, pipeline, gates, check counts, budgets) from the live harness config and check registry. Use when the user asks what the team looks like, after a roster/pipeline/policy change, or types /architecture.
---

# /architecture

    python .agentteam/bin/architecture.py

Every number in ARCHITECTURE.md comes from `.agentteam/config/*.json` and the `gate.py` check registry,
so the document can't drift from the code. If the roster changed, the agent files are stale too —
regenerating them is a harness change (maintainer mode, see README):

    python .agentteam/bin/roster.py

`python .agentteam/bin/architecture.py --check` and `roster.py --check` exit 1 when either is stale; the
selftest runs both. Summarise the headline counts for the user in one line and link ARCHITECTURE.md.
