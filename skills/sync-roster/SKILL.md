---
name: sync-roster
description: Regenerate Claude Code NATIVE-team teammate definitions (.claude/agents/*.md) from standup/team.json, so native agent teams can spawn your roster's roles as teammates. Run after editing the roster (/add-team, /add-role) or before /agent-team:team.
allowedTools: Read, Bash, Write
---

Regenerate the native-team teammate definitions from the roster. Do it; don't just describe it. (If there is no `standup/team.json` in the current directory, tell the user to run `/agent-team:init` first.)

1. **Run the generator:**
   `python3 standup/portal/parsers/agents_gen.py .`
   It writes `.claude/agents/<role>.md` for every **active** developer + active staff member in `standup/team.json`, and prunes any previously-generated file whose role left the roster. (Read-only staff get read-only tools; developers get edit + bash. Each file carries a `generated from team.json` header.)

2. **Report** which roles were written (the generator prints them), and remind the user:
   - Claude Code's **native agent teams** pick these up automatically — the lead can "Spawn a teammate using the `<role>` agent type".
   - **`/agent-team:team`** uses exactly these definitions to launch the roster as a native team.

Notes: these files are **generated** — don't hand-edit them; change `standup/team.json` (or use `/agent-team:add-role` / `/agent-team:add-team`) and re-run this skill. The roster is the single source of truth.
