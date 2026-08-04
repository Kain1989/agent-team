#!/usr/bin/env bash
# The `portal` squad's review-surface INSPECT: start the Mission Control portal and prove it answers.
#
#     bash standup/control/inspect_portal.sh          # from the repo root
#
# WHY THIS IS A SCRIPT AND NOT A curl ONE-LINER. The obvious form —
#     ./setup.sh && (run_local.sh &) && sleep 8 && curl -sf http://127.0.0.1:8770/healthz
# passes on a machine where ANYTHING is already listening on that port, without ever starting this
# portal. It was written that way first and caught in review on a box that already had another
# `team-status-portal` on :8770: /healthz answered `{"ok":true,"service":"team-status-portal",...}`,
# which is byte-indistinguishable from what this portal returns. A green from a server you did not
# start is the same false promise the review_surface field exists to retire, wearing the opposite
# mask — an inspect that only appears to work BECAUSE of pre-existing local state.
#
# The port is also not knowable in advance: run_local.sh sources the repo's .env with `set -a`
# AFTER the caller's environment, so a local `PORT=` in .env wins over an exported one. Guessing
# 8770 can therefore be wrong even when nothing else is running.
#
# So this script never guesses and never trusts a port:
#   1. it starts run_local.sh itself and keeps that PID;
#   2. it reads the URL that process PRINTS about itself, rather than assuming one;
#   3. it curls only while that PID is still alive — if uvicorn failed to bind (because something
#      else holds the port) the child is gone, and this reports FAILED instead of grading the
#      stranger that answered.
#
# Prerequisite (network, once): ./setup.sh creates standup/.venv and installs fastapi+uvicorn.
# run_local.sh hard-exits "portal deps missing" without it. Pass --no-setup to skip that step.
#
# Exit: 0 = the portal this script started answered /healthz · 1 = it did not.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="${TMPDIR:-/tmp}/agent-team-portal.log"
TIMEOUT="${INSPECT_TIMEOUT:-40}"

if [[ "${1:-}" != "--no-setup" ]]; then
  echo "==> ./setup.sh (venv + deps; needs network the first time)"
  ./setup.sh >/dev/null 2>&1 || { echo "inspect FAILED — ./setup.sh did not complete" >&2; exit 1; }
fi

: > "$LOG"
( cd standup/portal && exec ./run_local.sh ) >"$LOG" 2>&1 &
PID=$!
# Stop the server we started, whatever the outcome — an inspect that leaves a process behind is a
# side effect, and this command is supposed to be safe to run from a review lens.
cleanup() { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
trap cleanup EXIT

URL=""
for _ in $(seq "$TIMEOUT"); do
  sleep 1
  kill -0 "$PID" 2>/dev/null || break                     # it died: do NOT fall through to a curl
  [[ -n "$URL" ]] || URL="$(sed -n 's|.*portal \(http://[^ ]*\).*|\1|p' "$LOG" | head -1)"
  [[ -n "$URL" ]] || continue
  if OUT="$(curl -sS -f -m 5 "$URL/healthz" 2>/dev/null)"; then
    echo "INSPECT PASS — the portal started by this script answered at $URL"
    echo "$OUT" | head -c 400; echo
    exit 0
  fi
done

echo "INSPECT FAILED — the portal this script started never answered${URL:+ at $URL}." >&2
if ! kill -0 "$PID" 2>/dev/null; then
  echo "  The server process exited. Most likely another process already holds that port," >&2
  echo "  or deps are missing. This is reported as a FAILURE rather than curl'ing the port" >&2
  echo "  anyway, because a green from a server this script did not start is a false green." >&2
fi
echo "  Last lines of $LOG:" >&2
tail -5 "$LOG" >&2
exit 1
