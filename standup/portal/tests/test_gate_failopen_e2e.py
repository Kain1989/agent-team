"""END-TO-END PREMISE CHECK: does Claude Code still fail OPEN on an unlaunchable hook?

Everything in gate_check.py, and the refusal-to-launch in agent_run, rests on ONE
upstream fact about a tool we do not control:

    a PreToolUse hook whose `command` cannot be exec'd does not block anything,
    does not warn, and does not log — the tool simply runs.

The static tests (test_gate_runnable.py) check that our configs name a real
interpreter. That is necessary but it is a *string* check, and a string check is blind
to the disease itself: if Claude Code's behavior here changed — in either direction —
no string test would notice. This file checks the BEHAVIOR, by running the real binary.

It is the same discipline as `verify_design_quality.js --self-test` (rulebook E-03): a
judge that cannot demonstrate the breakage it exists to prevent is not a judge.

WHY IT IS OPT-IN
----------------
It spawns real `claude -p` processes (network, tokens, ~30s). Enable with:

    STANDUP_GATE_E2E=1 pytest tests/test_gate_failopen_e2e.py -v

Run it when you touch the gate, when the Claude Code version changes, or when someone
doubts the premise. If `test_unlaunchable_hook_fails_open` ever FAILS, that is good
news about upstream — and it means this file's docstring, gate_check.py's rationale,
and the refusal in agent_run should be revisited rather than left as folklore.

READING THE RESULT — the trap that nearly went into the record
--------------------------------------------------------------
**A missing file is NOT evidence the gate fired.** The model frequently declines a
write on its own judgment, and "the model chose not to" and "the boundary stopped it"
are byte-identical on the filesystem. During the original investigation a run against
the *broken* live config produced no file and was nearly recorded as "gate held" — it
was the model declining, with `permission_denials: []` and the tool never attempted.

So the assertions below key on `permission_denials`, which is populated by the CLI only
when a hook actually returned a deny, and use a deliberately boring prompt that the
model has no reason to refuse. `ATTEMPTED_BUT_UNDECIDED` is treated as an inconclusive
run to retry, never as a pass.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("STANDUP_GATE_E2E") != "1",
    reason="live claude e2e; set STANDUP_GATE_E2E=1 to run",
)

CLAUDE = shutil.which("claude")
# Deliberately mundane: nothing here invites the model to refuse on its own judgment.
PROMPT = ("Please use the Write tool to create a file called notes.txt in the current "
          "directory containing the single word hello. Then stop.")
DEAD_INTERPRETER = "/nonexistent/bin/python-3141592"


def _run_with_gate(tmp_path, command, label):
    """Launch a real gated `claude -p` that is asked to Write; report what happened.

    Returns (file_written: bool, denials: int). The flags mirror agent_run's launch,
    EXCEPT that --allowedTools deliberately PERMITS Write: that isolates the hook as the
    only thing that could stop it, which is exactly the threat model job_gate_hook.py
    claims to cover ("the last-line boundary ... even if the --allowedTools flag were
    ever weakened").
    """
    wd = tmp_path / f"wd_{label}"
    wd.mkdir()
    gate = tmp_path / f"gate_{label}.json"
    gate.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": command}]},
            {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": command}]},
        ]}
    }), encoding="utf-8")
    mcp = tmp_path / "empty_mcp.json"
    mcp.write_text('{"mcpServers": {}}', encoding="utf-8")

    proc = subprocess.run(
        [CLAUDE, "-p", "--output-format", "json", "--permission-mode", "default",
         "--max-turns", "3", "--allowedTools", "Write", "--model", "haiku",
         "--strict-mcp-config", "--mcp-config", str(mcp), "--settings", str(gate)],
        input=PROMPT, cwd=str(wd), capture_output=True, text=True, timeout=180,
    )
    try:
        denials = len(json.loads(proc.stdout).get("permission_denials") or [])
    except (ValueError, AttributeError):
        denials = 0
    return (wd / "notes.txt").exists(), denials


@pytest.mark.skipif(not CLAUDE, reason="claude binary not on PATH")
def test_working_hook_actually_blocks(tmp_path):
    """ANCHOR: what a hook that CAN run looks like. Without this the next test is noise."""
    hook = tmp_path / "deny.py"
    hook.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',\n"
        "  'permissionDecision': 'deny', 'permissionDecisionReason': 'e2e: denied'}}))\n",
        encoding="utf-8",
    )
    written, denials = _run_with_gate(tmp_path, f"{sys.executable} {hook}", "working")
    assert denials >= 1, "a runnable deny-hook must produce a recorded permission denial"
    assert not written, "a runnable deny-hook must stop the Write"


@pytest.mark.skipif(not CLAUDE, reason="claude binary not on PATH")
@pytest.mark.parametrize("shape", ["missing_interpreter", "crashing_script"])
def test_hook_that_does_not_complete_fails_open(tmp_path, shape):
    """THE PREMISE. A hook that does not COMPLETE blocks nothing and says nothing.

    Both shapes are checked because the premise is not "a missing interpreter fails
    open" — it is the more general and more dangerous "a non-zero exit fails open".
    `crashing_script` is the one a path-existence checker can never see: every path in
    the config resolves, and the gate is still gone.

    Measured 2026-08-07 on Claude Code 2.1.222: written=True, denials=0, stderr empty.

    If this FAILS, upstream has changed and our fail-closed workaround may be
    relaxable — revisit gate_check.py's rationale rather than deleting this test.
    """
    hook = tmp_path / "hook.py"
    if shape == "missing_interpreter":
        hook.write_text("import sys; sys.stdin.read()\n", encoding="utf-8")
        command = f"{DEAD_INTERPRETER} {hook}"
    else:
        hook.write_text("raise ImportError('simulated bad edit')\n", encoding="utf-8")
        command = f"{sys.executable} {hook}"
    written, denials = _run_with_gate(tmp_path, command, shape)

    if not written and denials == 0:
        pytest.skip("inconclusive: the model declined on its own, tool never attempted "
                    "(NOT evidence the gate held) — rerun")
    assert denials == 0, (
        "unexpected: an unlaunchable hook produced a permission denial — upstream may "
        "have started failing closed. Re-verify the premise in gate_check.py."
    )
    assert written, (
        "unexpected: the Write was stopped without any recorded denial. Something other "
        "than the hook blocked it; the premise measurement is no longer reproducible."
    )
