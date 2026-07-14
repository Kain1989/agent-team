---
name: team-structure
description: Show the current team org structure / roster — squads, paired developers, staff, and the supervisor. Use when the user wants to see the team, the roster, the org chart, who is on the team, or the team structure.
allowedTools: Read, Bash
---

Show the current agent-team org structure. Read `standup/team.json` and render it clearly:

1. Parse `standup/team.json`. For each entry in `teams[]`: the squad **id + name + mission**, and each developer (`id`, `role`, `folder`, `pair`, active?). Then `staff[]` (id, role, active?) and `bench[]` if any. Note the manager/supervisor and the cadence from `manager`.
2. Render a compact tree, e.g.:
   ```
   EM (supervisor: Claude)
   ├─ <squad name> (<id>) — <mission, one line>
   │    ├─ <dev_id> · <role>  ⇄ pair <pair_id>   [folder]
   │    └─ ...
   ├─ ...
   Staff: <id> · <role> (active|inactive) ...
   ```
3. End with a one-line summary: N squads, M active developers, K staff, and what each squad works on. If `standup/team.json` is missing, say so and suggest `/agent-team:init`.

Read-only — do not modify anything.
