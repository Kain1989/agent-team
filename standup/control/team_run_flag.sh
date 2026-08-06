#!/usr/bin/env bash
# team_run_flag.sh — set / clear the supervisor-gate TEAM-RUN EXEMPTION.
#
# WHY THIS EXISTS
#   hooks/supervisor_gate.py identifies the supervisor (EM) by the SESSION cwd. The Task/agent
#   tool has no `cwd` parameter (upstream: anthropics/claude-code#12748), so every subagent
#   inherits the EM's cwd — including the dev agents this team dispatches to work on a project
#   folder. The gate therefore classifies them as the EM and HARD-BLOCKS their Edit/Write on the
#   very folder they were sent to.
#
#   The roster gives each developer a `folder`, but a folder string cannot become a process cwd.
#   It can only be interpolated into a prompt, and a prompt cannot govern a hook.
#
#   The failure is SILENT and EXPENSIVE: the dev agent plans, investigates, writes its patch,
#   passes its own test gate — and then the fresh-context reviewer correctly FAILS it for an empty
#   diff. The run reports "review-failed", which reads as a code-quality problem. It is not.
#
#   The gate has always read standup/control/team_run_active as an exemption, and its docstring
#   has always said "the EM creates it before a team run". Nothing ever created it — which is why
#   this script now exists. standup.workflow.js calls it automatically at the start of any run
#   that writes code, so it no longer depends on anyone remembering.
#
# The gate expires the flag 6h after its mtime, so a forgotten one cannot leave the gate off
# indefinitely. That TTL — not `clear` — is the real safety mechanism: a crashed run never reaches
# its teardown.
#
# CONCURRENCY: several runs may share one flag. `set` APPENDS a record and refreshes the mtime
# rather than overwriting, and `clear` REFUSES to delete while another run's record is present.
# Rule of thumb: if any run may still be alive, do not delete it.
#
# Usage:
#   standup/control/team_run_flag.sh set   <run-id> [note]
#   standup/control/team_run_flag.sh clear <run-id> [--force]
#   standup/control/team_run_flag.sh status
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAG="$HERE/team_run_active"
CMD="${1:-status}"
RUN="${2:-}"

case "$CMD" in
  set)
    [ -n "$RUN" ] || { echo "usage: team_run_flag.sh set <run-id> [note]" >&2; exit 2; }
    shift 2 || true
    NOTE="${*:-team run}"
    printf '%s | %s | %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN" "$NOTE" >> "$FLAG"
    touch "$FLAG"
    echo "team_run_active SET for $RUN"
    sed 's/^/  /' "$FLAG"
    ;;
  clear)
    [ -f "$FLAG" ] || { echo "team_run_active already absent"; exit 0; }
    if [ -n "$RUN" ] && [ "${3:-}" != "--force" ]; then
      others=$(grep -v -- "$RUN" "$FLAG" || true)
      if [ -n "$others" ]; then
        echo "REFUSING to clear — another run still holds the exemption:" >&2
        echo "$others" | sed 's/^/  /' >&2
        echo "(clearing it would switch the gate back ON mid-run and block every write that run" >&2
        echo " still has to make. Pass --force only once you have confirmed those runs are dead.)" >&2
        exit 1
      fi
    fi
    rm -f "$FLAG"
    echo "team_run_active CLEARED"
    ;;
  status)
    if [ -f "$FLAG" ]; then
      echo "team_run_active PRESENT (gate OFF; the hook expires it 6h after mtime):"
      sed 's/^/  /' "$FLAG"
      ls -l "$FLAG"
      echo "NOTE: record timestamps are UTC while \`ls\` is local — do not judge staleness by eye."
    else
      echo "team_run_active ABSENT — supervisor gate is ON; dispatched dev agents CANNOT write"
      echo "their project folder. A code-writing run must arm this first."
    fi
    ;;
  *)
    echo "usage: team_run_flag.sh set|clear|status" >&2; exit 2 ;;
esac
