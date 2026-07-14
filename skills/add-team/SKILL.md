---
name: add-team
description: Add a new squad to the team roster (standup/team.json). Use when the user wants to add a team, create a squad, or onboard a new group to the agent team.
allowedTools: Read, Bash, Edit
---

Add a new squad to `standup/team.json`. Do it safely (validate the JSON before and after).

INPUT: $ARGUMENTS  — expected as `<squad_id> — <one-line mission>` (e.g. `payments — own the billing service`). If the id or mission is missing, ask for it.

1. Read `standup/team.json`. Confirm the `<squad_id>` is not already a `teams[].id` (refuse duplicates).
2. Append to `teams[]` a new entry matching the existing schema:
   ```json
   { "id": "<squad_id>", "name": "<Title-cased name>", "mission": "<mission>",
     "coordination": "Two paired developer-agents who challenge each other's plans and diffs in a fresh context.",
     "developers": [] }
   ```
   (A squad with no developers will not run until you add at least one — that is expected.)
3. Write `standup/team.json` back (preserve formatting + the rest of the file exactly; only insert the new entry). Re-parse it to confirm it is valid JSON.
4. Confirm: "Added squad `<id>`. Add developers with `/agent-team:add-role <id> <dev_id> <role>` (a squad needs ≥1 active developer to run)." Show the updated roster via the same render as `/agent-team:team-structure`.
