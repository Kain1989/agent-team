#!/usr/bin/env bash
# setup.sh — one-time install for the Agent Team MVP. Idempotent; safe to re-run.
#
# What it does (all LOCAL — no network services, no credentials):
#   1. creates a Python venv + installs the portal's deps (fastapi, uvicorn, pytest)
#   2. autodetects the `claude` CLI (the job worker spawns headless `claude -p`)
#   3. writes .env from .env.example (if absent)
#   4. generates the two job-gate config files from their templates, baking in THIS
#      install's absolute python + control/ paths (the gate hook command needs them)
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

# --- 6. runtime state dirs ----------------------------------------------------
mkdir -p "$CONTROL/runs" "$CONTROL/requests" "$CONTROL/results" "$CONTROL/worktrees"
mkdir -p "$ENG/log" "$ENG/portal/.standup"

echo ""
echo "==> done. start the portal:"
echo "      cd standup/portal && ./run_local.sh"
echo "    then open http://127.0.0.1:8770. There is no project yet — add one with"
echo "      /add-project clone <git-url>   |   new <name>   |   adopt <name>" 
