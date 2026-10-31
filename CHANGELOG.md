# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). This project adheres to
[Semantic Versioning](https://semver.org/). The v1 history (0.1.0 – 0.5.2) is in the `v1-final` tag and
the [Kain1989/agent-team-v1](https://github.com/Kain1989/agent-team-v1) archive
([v1 CHANGELOG](https://github.com/Kain1989/agent-team-v1/blob/v1-final/CHANGELOG.md)).

## [2.0.0] — 2026-10-30

A rebuild. The version jumps from 0.5.2 to 2.0.0 so that "v2" means one thing everywhere. For a
side-by-side comparison with v1, see [V2-VS-V1.md](V2-VS-V1.md).

### Changed — everything that defines the product
- **Unit of work:** a *story* moving through eight phases (PRD, UX, Dev, Unit+Int, Sys+Reg+Perf, Review,
  Visual, Deploy) replaces the v1 board tick.
- **Roles:** seven teams of lead (opus) + ICs (sonnet), 26 agents in total, generated from
  `.agentteam/config/roster.json`. They replace the v1 developer pairs and staff roles.
- **Gates:** 9 gates, 91 deterministic checks in `gate.py`, reading artifacts off disk. They replace the
  v1 supervisor-model verdicts. Four human gates: prd-signoff, dev-plan-signoff, pre-merge, deploy-beyond-dev.
- **Identity:** the guard reads the PreToolUse `agent_type` instead of inferring the caller from cwd, so the
  v1 `team_run_active` flag is gone.
- **Install:** a project you clone and run `setup.py` in, not a Claude Code plugin.

### Added
- `integrity.py` — test-tampering detection (assertions deleted, skipped, always-true, commented,
  loosened, re-valued; snapshots and golden files), with human waivers that are reported, not absorbed.
- Traceability matrix (REQ → TASK → test → PR) with on-disk resolution of every cited test.
- Code-built review packets with hashed manifests; the review gate refuses a contaminated or stale packet.
- Finding fingerprints and escalation on repeats; round caps per looping phase.
- Per-phase agent/token budgets, measured by a SubagentStop hook.
- `env.py` — toolchain detection with absolute-path argv lists.
- `selftest.py` — every check must be shown to fail (E-03), and a check without a breaking fixture fails the selftest.
- CI on Linux, macOS and Windows, Python 3.9 and 3.12.

### Kept from v1
- `DESIGN_RULEBOOK.md` and the design judge (now `.agentteam/bin/visual/verify_design_quality.js`), driving phase 6.
- Secret scanning of produced diffs, the empty-diff diagnosis, and smoke-running the configured hook
  command so a hook that cannot execute is caught (0.5.2).

### Removed
- The Mission Control portal, the Workflow-tool engine (`standup.workflow.js`), `/standup` and its tick
  cadence, the code-task queue, and the v1 skills. All of them are in `v1-final`.

### Fixed (relative to v1)
- Windows: v1 parsed Windows paths with POSIX `shlex` and failed 124 portal tests there; `pip install`
  failed on a GBK locale. v2 uses argv lists, explicit UTF-8, and retries atomic replaces on transient
  Windows file locks.
- Reviewers were told they saw "only the diff" while the engine pasted the approved plan and the
  implementation report into their prompts. Packets are now built by code and checked by the gate.
