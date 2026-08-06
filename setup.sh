#!/usr/bin/env bash
# setup.sh — one-time install for the Agent Team MVP. Idempotent; safe to re-run.
#
# What it does (all LOCAL — no network services, no credentials):
#   1. creates a Python venv + installs the portal's deps (fastapi, uvicorn, pytest)
#   2. autodetects the `claude` CLI (the job worker spawns headless `claude -p`)
#   3. writes .env from .env.example (if absent)
#   4. generates the two job-gate config files from their templates, baking in THIS
#      install's absolute python + control/ paths (the gate hook command needs them)
#   5. IF demo-app/ is present, turns it into a local git repo with a LOCAL bare `origin` (a
#      file path — no GitHub/network) so the portal's worktree-based code-task flow runs
#      offline. demo-app is an optional sample: deleting it skips this step, never fails.
#   6. creates the runtime state dirs the control plane writes to
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENG="$ROOT/standup"
CONTROL="$ENG/control"

echo "==> Agent Team MVP setup"
echo "    root:    $ROOT"

# --- 1. venv + deps -----------------------------------------------------------
if [[ ! -x "$ENG/.venv/bin/python" ]]; then
  echo "==> creating venv at standup/.venv"
  python3 -m venv "$ENG/.venv"
fi
PY="$("$ENG/.venv/bin/python" -c 'import sys; print(sys.executable)')"
echo "==> installing portal deps (fastapi, uvicorn, pytest)"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$ENG/portal/requirements.txt"

# --- 2. resolve the claude CLI ------------------------------------------------
CLAUDE_BIN="${STANDUP_CLAUDE_BIN:-$(command -v claude || true)}"
if [[ -z "$CLAUDE_BIN" ]]; then
  echo "    !! 'claude' CLI not found on PATH. The job worker needs it to run code tasks."
  echo "       Install Claude Code, then set STANDUP_CLAUDE_BIN in .env to its path."
else
  echo "==> found claude CLI: $CLAUDE_BIN"
fi

# --- 3. .env from example -----------------------------------------------------
if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  # bake the resolved claude path into .env so the worker finds it
  if [[ -n "$CLAUDE_BIN" ]]; then
    "$PY" - "$ROOT/.env" "$CLAUDE_BIN" <<'PYEOF'
import sys, pathlib
env, claude = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = env.read_text().splitlines()
out = [(f"STANDUP_CLAUDE_BIN={claude}" if l.startswith("STANDUP_CLAUDE_BIN=") else l) for l in lines]
env.write_text("\n".join(out) + "\n")
PYEOF
  fi
  echo "==> wrote .env (edit it to change the port / toggles)"
fi

# --- 4. generate the job-gate configs from templates --------------------------
echo "==> generating job-gate configs (baking in $PY + $CONTROL)"
STANDUP_PY="$PY" STANDUP_CONTROL="$CONTROL" "$PY" - <<'PYEOF'
import os, pathlib
control = pathlib.Path(os.environ["STANDUP_CONTROL"])
py = os.environ["STANDUP_PY"]
for name in ("job_code_gate", "job_readonly_gate"):
    tmpl = (control / f"{name}.json.template").read_text()
    out = tmpl.replace("__PYTHON__", py).replace("__CONTROL_DIR__", str(control))
    (control / f"{name}.json").write_text(out)
    print(f"    wrote control/{name}.json")
PYEOF

# --- 5. demo-app: local git + local bare origin (offline) ---------------------
# demo-app/ is a SAMPLE, and deleting it once you have your own repo is a supported path — the
# README says so. This whole section is therefore conditional on it still being there. It did not
# used to be: under `set -euo pipefail`, `git -C "$ROOT/demo-app" init` on a deleted directory is
# `fatal: cannot change to .../demo-app` and exit 128, which aborted the installer with nothing
# installed. Note the guard opens AFTER the two assignments: `set -u` makes a $DEMO referenced
# outside its own guarded block an unbound-variable abort, which is the same closed door one line
# further down. Judge: standup/control/tests/test_setup_guard.sh
DEMO="$ROOT/demo-app"
ORIGIN="$ROOT/.demo-app-origin.git"
if [[ -d "$DEMO" ]]; then
  if [[ ! -d "$DEMO/.git" ]]; then
    echo "==> git-init demo-app + a LOCAL bare origin (offline, no network)"
    git -C "$DEMO" init -q -b main
    git -C "$DEMO" add -A
    git -C "$DEMO" -c user.name="demo" -c user.email="demo@local" commit -q -m "demo-app: initial import"
  fi
  if [[ ! -d "$ORIGIN" ]]; then
    git init -q --bare -b main "$ORIGIN" 2>/dev/null || git init -q --bare "$ORIGIN"
  fi
  # (re)point origin at the local bare repo + push main so origin/main resolves
  if git -C "$DEMO" remote | grep -qx origin; then
    git -C "$DEMO" remote set-url origin "$ORIGIN"
  else
    git -C "$DEMO" remote add origin "$ORIGIN"
  fi
  git -C "$DEMO" push -q -u origin main
  # point origin's HEAD at main so the worktree flow resolves origin/main (a local bare
  # repo created without -b main may otherwise default HEAD to master and break resolution)
  git -C "$ORIGIN" symbolic-ref HEAD refs/heads/main 2>/dev/null || true
  git -C "$DEMO" remote set-head origin main >/dev/null 2>&1 || true
else
  echo "==> no demo-app/ here — skipping the sample repo setup (this is fine)"
  echo "    demo-app is an optional sample. Everything else above is installed."
fi

# --- 6. runtime state dirs ----------------------------------------------------
mkdir -p "$CONTROL/runs" "$CONTROL/requests" "$CONTROL/results" "$CONTROL/worktrees"
mkdir -p "$ENG/log" "$ENG/portal/.standup"

echo ""
echo "==> done. start the portal:"
echo "      cd standup/portal && ./run_local.sh"
if [[ -d "$DEMO" ]]; then
  echo "    then open http://127.0.0.1:8770 and submit a code task targeting 'project:demo-app'."
else
  echo "    then open http://127.0.0.1:8770 and submit a code task targeting one of the"
  echo "    projects in standup/team.json (a developer's 'folder')."
fi
