#!/usr/bin/env bash
# Run the Mission Control portal LOCALLY. Bound to 127.0.0.1 ONLY — never 0.0.0.0.
#
# This ONE process also hosts the job-queue WORKER (STANDUP_JOBWORKER=1): it claims
# submitted jobs, runs each code task in an isolated git worktree under the locked-down
# gate, and parks the diff at 'awaiting_approval' for you to approve in the UI. The
# in-process daily-tick SCHEDULER is OFF by default (on-demand MVP); set
# STANDUP_SCHEDULER=1 to enable it.
#
# Single-worker, single-instance, one host: the single-flight guard depends on it.
# Do NOT pass --workers N and do NOT start a 2nd instance.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # standup/portal
cd "$HERE"

# Load team-mvp/.env (two levels up) if present.
ENV_FILE="$HERE/../../.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a; . "$ENV_FILE"; set +a
fi

HOST="127.0.0.1"
PORT="${PORT:-8770}"

# Prefer the venv created by setup.sh at standup/.venv; else system python3.
PY="python3"
if [[ -x "$HERE/../.venv/bin/python" ]]; then
  PY="$HERE/../.venv/bin/python"
fi

if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "[run_local] portal deps missing — run ../../setup.sh first." >&2
  exit 1
fi

# MVP defaults: job worker ON (the headline). The scheduler LOOP runs (so
# /daily-standup works with no restart), but FIRING stays OFF until /daily-standup
# (control/schedule.json defaults off) — so the default posture is still on-demand.
export STANDUP_JOBWORKER="${STANDUP_JOBWORKER:-1}"
export STANDUP_SCHEDULER="${STANDUP_SCHEDULER:-1}"
export STANDUP_CLAUDE_BIN="${STANDUP_CLAUDE_BIN:-$(command -v claude || true)}"

echo "[run_local] portal http://${HOST}:${PORT}  (worker=${STANDUP_JOBWORKER}, scheduler=${STANDUP_SCHEDULER})"
exec "$PY" -m uvicorn app:app --host "$HOST" --port "$PORT" "$@"
