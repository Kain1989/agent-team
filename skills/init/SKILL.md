---
name: init
description: Scaffold a new agent-team project into the current directory (the engine, a starter roster, and a sample demo-app). Use when the user wants to set up, initialize, scaffold, or create a new agent team project, or is starting from an installed plugin with no team yet.
allowedTools: Read, Bash, Write
---

Scaffold an agent-team project into the current directory. Do it now.

1. If `standup/team.json` already exists here, STOP and tell the user a team project is already present (don't overwrite) — point them at `/agent-team:team-structure`.
2. Otherwise copy the engine + starter files from the installed plugin into the current directory. The plugin root is `${CLAUDE_PLUGIN_ROOT}`:
   ```
   cp -R "${CLAUDE_PLUGIN_ROOT}/standup" ./standup
   cp -R "${CLAUDE_PLUGIN_ROOT}/demo-app" ./demo-app
   cp "${CLAUDE_PLUGIN_ROOT}/setup.sh" ./setup.sh && chmod +x ./setup.sh
   cp "${CLAUDE_PLUGIN_ROOT}/.env.example" ./.env.example
   ```
   Then remove any copied runtime state: `rm -rf standup/.venv standup/control/jobs.db* standup/control/runs/* standup/control/worktrees/* demo-app/.git` (these are regenerated). If `${CLAUDE_PLUGIN_ROOT}` is unset (you're in a cloned repo, not an install), the files are likely already here — skip the copy.
3. Run `./setup.sh` (venv, deps, gate config, demo-app local git + offline origin).
4. Confirm and tell the user the next steps: `/agent-team:team-structure` to see the roster, `/agent-team:portal` to open Mission Control, `/agent-team:standup` to run the team, `/agent-team:add-team` + `/agent-team:add-role` to grow it, and edit `standup/team.json` to point a squad at your own repo (give that repo an `origin` remote).
