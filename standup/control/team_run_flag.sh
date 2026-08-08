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
# rather than overwriting. `clear <run-id>` removes THAT RUN'S OWN record and unlinks the file only
# when the last record goes; while any other run's record remains it keeps the file and exits
# non-zero. Rule of thumb: if any run may still be alive, do not delete the FILE.
#
# WHY `clear` HAD TO CHANGE. There was no per-record delete at all: it refused outright whenever any
# other record was present, so the caller's own record was never removed and the file only ever
# grew. In a tree that has seen two overlapping runs that made `clear` PERMANENTLY unreachable,
# leaving `--force` — which unlinks the flag under whatever else is still running — as the only exit.
# That is the one outcome this command exists to prevent, so the safe path has to be the reachable
# one. The install this was distilled from had accumulated 30+ records exactly that way.
#
# Records are matched on the run-id FIELD, trimmed and compared exactly. The old test was
# `grep -v -- "$RUN"`, wrong twice over: it matched a SUBSTRING anywhere on the line, and it read
# the id as a REGEX. A run whose id contains another's (`nightly` vs `nightly-2`) could therefore
# hide the other's record from the "is anyone else here" question and then delete the flag out from
# under a live run — the precise accident the refusal exists to stop.
#
# A run-id is now REQUIRED. `clear` with no id used to fall through to `rm -f`: a silent `--force`
# for anyone who mistyped the command.
#
# The rewrite is tmp+rename, so a concurrent reader never sees a half-written file, and it RESTORES
# the original mtime — the gate expires the flag 6h after mtime and a teardown must not push that
# deadline out. Known and accepted: a `set` landing between the read and the rename is lost. It
# costs that run its LISTING, not its exemption (the gate keys on the file existing, not on the
# records), and the 6h TTL, not `clear`, is the real backstop either way.
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

# Split the flag's records by whether they belong to <run-id>.
#
# A record is `<utc-timestamp> | <run-id> | <note>`, so the id is FIELD 2 — trimmed, and compared
# for EQUALITY. `-F'|'` is a single literal character on purpose: a multi-character -F is an ERE and
# the backslash escaping needed to express one differs between awk implementations, which is not a
# thing a safety check should depend on.
#
# A line carrying no `|` at all (a hand-edited or truncated file) yields an empty field 2, so it is
# never equal to a real run-id and always counts as SOMEONE ELSE'S. That is deliberate: the failure
# this whole command guards is deleting a record you did not understand.
_records() { # <run-id> mine|others  -> prints the matching records
  awk -F'|' -v run="$1" -v want="$2" '
    {
      f = $2
      gsub(/^[ \t]+/, "", f)
      gsub(/[ \t]+$/, "", f)
      if ((f == run) == (want == "mine")) print
    }
  ' "$FLAG"
}

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
    if [ -z "$RUN" ] || [ "${RUN#--}" != "$RUN" ]; then
      echo "usage: team_run_flag.sh clear <run-id> [--force]" >&2
      echo "  A run-id is REQUIRED: clear removes the CALLER'S OWN record, and without one there is" >&2
      echo "  no such record to remove. Omitting it used to delete the whole file — a silent --force." >&2
      exit 2
    fi
    if [ "${3:-}" = "--force" ]; then
      rm -f "$FLAG"
      echo "team_run_active CLEARED (--force: the whole file, including every other run's record)"
      exit 0
    fi
    mine="$(_records "$RUN" mine)"
    others="$(_records "$RUN" others)"
    if [ -z "$others" ]; then
      rm -f "$FLAG"
      echo "team_run_active CLEARED (last record was $RUN)"
      exit 0
    fi
    # Other runs are still holding the exemption, so the FILE stays. The caller's own record does
    # not: leaving it is what made this command unreachable in every shared tree.
    if [ -n "$mine" ]; then
      tmp="$(mktemp "${FLAG}.clear.XXXXXX")"
      if _records "$RUN" others > "$tmp"; then
        touch -r "$FLAG" "$tmp"   # keep the ORIGINAL mtime: a teardown must not extend the 6h TTL
        mv "$tmp" "$FLAG"
        echo "released this run's record ($RUN)"
      else
        rm -f "$tmp"
        echo "could not rewrite $FLAG — left exactly as it was" >&2
        exit 1
      fi
    else
      echo "no record for '$RUN' in team_run_active — nothing of yours to release" >&2
    fi
    echo "REFUSING to delete the flag — $(printf '%s\n' "$others" | wc -l | tr -d ' ') other record(s) still hold the exemption:" >&2
    printf '%s\n' "$others" | sed 's/^/  /' >&2
    echo "(deleting it would switch the gate back ON mid-run and block every write those runs still" >&2
    echo " have to make. The gate's own 6h TTL is the backstop; pass --force only once you have" >&2
    echo " confirmed they are dead.)" >&2
    exit 1
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
