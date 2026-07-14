---
name: help
description: List all the agent-team plugin commands with what each does. Use when the user asks what commands are available, what can I do, help, or list commands.
allowedTools: Read, Bash
---

List the agent-team commands (do NOT run any of them — just show what's available).

1. Read every `SKILL.md` under the plugin's skills (each defines a `/agent-team:<folder>`
   command). Use `${CLAUDE_PLUGIN_ROOT}/skills/*/SKILL.md` when installed as a plugin, else
   `./skills/*/SKILL.md` in a cloned project. Parse each file's frontmatter `name` +
   `description`.
2. Print a clean table grouped by purpose:
   - **Run** — standup, work
   - **Native teams** — team, sync-roster
   - **Monitor** — portal
   - **Roster** — team-structure, add-team, add-role
   - **Schedule** — daily-standup, stop-daily-standup, standup-status
   - **Daily ops** — costs, runs, eval
   - **Setup / help** — init, help
   Each row: `/agent-team:<name>` — <the first sentence of its description>. Put any command
   not in the groups above under "Other" so nothing is dropped.
3. End with: full docs in `CLAUDE.md` + `README.md`; the roster is `standup/team.json`.
