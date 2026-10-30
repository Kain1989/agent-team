# Security

## What the harness defends against

Agents that are trying to finish, not attackers: a dev agent that edits the test instead of the code,
a reviewer that reads the author's reasoning and agrees with it, a run that pushes to `main`, deploys to
production or leaks `.env` into a commit. All of these have been observed in coding agents (see the
references in [DESIGN.md](DESIGN.md)).

## Layers, and what each one does NOT guarantee

| Layer | Guarantees | Does not guarantee |
|---|---|---|
| `guard.py` (hard) | the listed tool calls never run; readonly roles cannot run anything off their allow-list; path tools respect write roots and withheld reads | **code-executing roles** (dev, qa, visual, devops) run tests — that is arbitrary code, and `python -c` can write anywhere the OS lets it. For those roles the Bash rules are best-effort pattern checks, backed by the audited layer |
| `gate.py` (audited) | a phase advances only when its artifacts, runs and records pass every check | that an artifact is *good* — only that it is complete, consistent and evidenced |
| human gates | four decisions are recorded as made by a person, from an interactive terminal | that the person is who `--by` says, or that a determined user with a shell didn't forge a record |

## Fail-open, audited
If `guard.py` itself crashes it allows the call (a guard bug must not brick a session), writes the
error to `.agentteam/state/audit/guard-errors.jsonl`, and **every gate fails** while an error is
recorded inside its phase. `setup.py` and `selftest.py` smoke-run the configured hook command; Claude Code
does not fail closed on a hook it cannot execute, so a moved interpreter would otherwise remove the
boundary silently. Re-run `setup.py` after any Python reinstall.

## Identity
Roles come from the PreToolUse `agent_type` field. A Claude Code build that does not send it makes every
subagent look like the main session (no product writes) — work fails safe, it doesn't widen. An
unrecognised `agent_type` gets the `unknown` role: readonly, no write roots.

## Reporting
Open a GitHub issue for harness bugs. For a bypass of `guard.py` or a way to make a gate pass on a broken
story, please open a private security advisory on the repository instead.
