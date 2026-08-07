---
name: add-role
description: Add a developer or staff role to a squad in the team roster (standup/team.json). Use when the user wants to add a role, add a developer, add an engineer, hire, or staff a squad.
allowedTools: Read, Bash, Edit
---

Add a developer (or staff) to `standup/team.json`. Validate JSON before + after.

INPUT: $ARGUMENTS — expected as `<squad_id> <dev_id> <role title> [folder]` (e.g. `payments billing_api "Backend Engineer" services/billing`). Add `--staff` to add to `staff[]` instead of a squad. If a required field is missing, ask.

1. Read `standup/team.json`. Resolve `<squad_id>` in `teams[]` (or target `staff[]` if `--staff`). Refuse a duplicate `<dev_id>`.
   Also refuse a `<dev_id>` containing a space, `/`, `\`, or that is `.`/`..`: the id is both the
   agent-type name and the FILENAME `/sync-roster` writes to `.claude/agents/<dev_id>.md`, and
   `/sync-roster` refuses ids that are not filenames — so an id accepted here would break the very
   next required step. `[folder]` is a different field and MAY contain `/` (`services/billing`,
   `standup/portal`); that is the normal way for a role to own a subdirectory.
2. Build the entry matching the schema of existing developers:
   ```json
   { "id": "<dev_id>", "folder": "<folder or the squad's folder>", "role": "<role title>",
     "stack": "<infer or 'unspecified'>", "git": true, "active": true,
     "pair": "<the existing lone dev in the squad, if exactly one — to form a pair>",
     "focus": "<one line; ask if unclear>", "tests": "<the squad's test gate>" }
   ```
   If the squad already has exactly one developer, set BOTH devs' `pair` to each other (pairs critique each other). For `--staff`, drop `pair`/`tests` and set `git:false` unless told otherwise.
3. Write `standup/team.json` back (only insert the entry; preserve everything else). Re-parse to confirm valid JSON.
4. Confirm + render the updated squad (like `/agent-team:team-structure`). Note that the new role takes part in the next `/agent-team:standup`.
