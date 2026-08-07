"""The gate must be RUNNABLE, not merely present (fail-open regression suite).

WHAT THIS COVERS THAT test_readonly_gate.py DOES NOT
---------------------------------------------------
test_readonly_gate.py asserts the hook SCRIPT decides correctly and that the launched
command carries the right flags. Both were true the whole time the boundary was gone.
Nothing asserted the one thing in between: that Claude Code can actually *exec* the
hook named in the config.

Measured on Claude Code 2.1.222 (3/3 replication, one variable changed):
a PreToolUse hook whose interpreter does not exist **fails OPEN** — the tool runs,
`permission_denials` is empty, stderr is empty, and the result JSON is shaped exactly
like a run with no hook at all. Nothing errors. Nothing logs. The gate is simply not
there. A real install was found in exactly that state — a Homebrew python baked into
the config and later uninstalled — so the job queue's last-line boundary had silently
evaporated.

Because the failure is upstream and silent, the enforcement has to happen BEFORE
launch, in the trusted parent: `agent_run` refuses to spawn `claude` at all when the
gate config is not runnable. These tests pin that refusal, and pin that the SHIPPED
configs stay runnable.

FIXTURE LOCATION NOTE: these use pytest's `tmp_path`. The `/tmp`, `/private/tmp/claude-`,
`/var/folders/` allow-list that could make a fixture vacuous belongs to the *supervisor*
gate (hooks/supervisor_gate.py), which governs who may edit what — a different hook
from the *job* gate under test here. These assertions are on pure-Python
return values and on whether `subprocess.run` was reached, so no path allow-list anywhere
can make them pass vacuously.
"""

import json
import subprocess

import pytest

from parsers import agent_run, paths
from parsers.gate_check import (
    GateConfigError,
    gate_config_problems,
    repair_gate_config,
    verify_gate_config,
)

DEAD_INTERPRETER = "/nonexistent/bin/python-3141592"


def _write_gate(tmp_path, command, name="gate.json", matcher="*"):
    p = tmp_path / name
    p.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": matcher, "hooks": [{"type": "command", "command": command}]},
        ]}
    }), encoding="utf-8")
    return p


def _healthy_command(tmp_path):
    """A gate command that really can run: this interpreter + a real script."""
    import sys
    script = tmp_path / "hook.py"
    script.write_text("import sys; sys.stdin.read()\n", encoding="utf-8")
    return f"{sys.executable} {script}"


# --------------------------------------------------------------------------- #
# The SHIPPED configs must be runnable. This is the test that was red.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("which", ["readonly", "code"])
def test_shipped_gate_config_is_runnable(which):
    """The gate configs the portal actually launches jobs with must be exec'able.

    RED whenever control/job_*_gate.json names an interpreter that is not installed:
    every job would then run with NO gate at all.
    """
    path = paths.job_readonly_gate() if which == "readonly" else paths.job_code_gate()
    problems = gate_config_problems(path)
    assert problems == [], f"shipped {which} gate config is not runnable: {problems}"


# --------------------------------------------------------------------------- #
# gate_check itself catches the specific ways a gate stops being a gate
# --------------------------------------------------------------------------- #
def test_dead_interpreter_is_rejected(tmp_path):
    gate = _write_gate(tmp_path, f"{DEAD_INTERPRETER} /some/hook.py")
    problems = gate_config_problems(gate)
    assert problems, "a config naming a nonexistent interpreter must be reported"
    assert any("does not exist" in p and DEAD_INTERPRETER in p for p in problems), problems
    with pytest.raises(GateConfigError):
        verify_gate_config(gate)


def test_missing_hook_script_is_rejected(tmp_path):
    """A live interpreter pointed at a missing script exits non-zero -> same fail-open."""
    import sys
    gate = _write_gate(tmp_path, f"{sys.executable} {tmp_path}/nope.py")
    problems = gate_config_problems(gate)
    assert any("hook script does not exist" in p for p in problems), problems


def test_non_executable_interpreter_is_rejected(tmp_path):
    fake = tmp_path / "not-executable-python"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o644)
    problems = gate_config_problems(_write_gate(tmp_path, f"{fake} /x/hook.py"))
    assert any("not executable" in p for p in problems), problems


def test_missing_catchall_matcher_is_rejected(tmp_path):
    """Without '*' the hook only fires for the matchers listed — the rest are ungated."""
    gate = _write_gate(tmp_path, _healthy_command(tmp_path), matcher="mcp__.*")
    problems = gate_config_problems(gate)
    assert any("catch-all" in p for p in problems), problems


@pytest.mark.parametrize("body", ["", "not json", "[]", "null", '{"hooks": {}}',
                                  '{"hooks": {"PreToolUse": []}}'])
def test_malformed_gate_config_is_rejected(tmp_path, body):
    p = tmp_path / "gate.json"
    p.write_text(body, encoding="utf-8")
    assert gate_config_problems(p), f"malformed config accepted: {body!r}"


def test_absent_gate_config_is_rejected(tmp_path):
    assert gate_config_problems(tmp_path / "nope.json")


def test_healthy_gate_config_passes(tmp_path):
    """Guard against the opposite failure: a checker that rejects everything."""
    gate = _write_gate(tmp_path, _healthy_command(tmp_path))
    assert gate_config_problems(gate) == []
    verify_gate_config(gate)  # must not raise


# --------------------------------------------------------------------------- #
# THE LOAD-BEARING ONE: a job whose gate cannot run must not be launched at all
# --------------------------------------------------------------------------- #
def _launch_attempt(monkeypatch, fn, **kwargs):
    """Call `fn` with subprocess.run tripwired; report whether claude was spawned."""
    spawned = {"yes": False}

    def _fake_run(cmd, **kw):
        spawned["yes"] = True
        raise AssertionError(f"claude was LAUNCHED despite an unusable gate: {cmd[:3]}")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = fn(**kwargs)
    return spawned["yes"], result


@pytest.mark.parametrize("kind", ["readonly", "code"])
def test_refuses_to_launch_when_gate_is_dead(monkeypatch, tmp_path, kind):
    """The whole point: fail CLOSED where Claude Code fails OPEN.

    A dead gate must stop the job. Launching it ungated is the failure this exists to
    prevent, and it is worse than the job not running, because it is invisible.
    """
    gate = _write_gate(tmp_path, f"{DEAD_INTERPRETER} /some/hook.py")
    if kind == "readonly":
        spawned, result = _launch_attempt(
            monkeypatch, agent_run.run_readonly,
            prompt="inspect the thing", settings_path=str(gate),
        )
    else:
        spawned, result = _launch_attempt(
            monkeypatch, agent_run.run_code_task,
            prompt="change the thing", worktree=str(tmp_path), settings_path=str(gate),
        )
    assert not spawned, "claude must not be spawned with an unusable gate"
    assert result["ok"] is False
    assert result["exit_code"] == -1
    assert "REFUSING TO LAUNCH" in (result.get("error") or ""), result.get("error")
    assert DEAD_INTERPRETER in (result.get("error") or ""), result.get("error")


@pytest.mark.parametrize("kind", ["readonly", "code"])
def test_still_launches_when_gate_is_healthy(monkeypatch, tmp_path, kind):
    """Positive control — the refusal must be about the gate, not about everything."""
    captured = {}

    class _FakeProc:
        returncode = 0
        stdout = json.dumps({"result": "ok", "is_error": False, "num_turns": 1,
                             "permission_denials": []})
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    gate = _write_gate(tmp_path, _healthy_command(tmp_path))
    if kind == "readonly":
        result = agent_run.run_readonly("inspect", settings_path=str(gate), cwd=str(tmp_path))
    else:
        result = agent_run.run_code_task("change", worktree=str(tmp_path),
                                         settings_path=str(gate))
    assert captured.get("cmd"), "a healthy gate must still launch the job"
    assert "--settings" in captured["cmd"]
    assert result["ok"] is True


# --------------------------------------------------------------------------- #
# The conftest gap: `if not j.exists()` never inspected the file it kept
# --------------------------------------------------------------------------- #
def test_stale_gate_config_is_regenerated_not_kept(tmp_path, monkeypatch):
    """A config that EXISTS but names a dead interpreter must be rewritten.

    The old guard was `if not j.exists()` — idempotent, and permanently blind to a file
    whose contents had rotted. Existence was standing in for correctness.
    """
    import sys

    control = tmp_path / "control"
    control.mkdir()
    tmpl = control / "job_readonly_gate.json.template"
    tmpl.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command",
                                        "command": "__PYTHON__ __CONTROL_DIR__/job_gate_hook.py"}]},
        ]}
    }), encoding="utf-8")
    (control / "job_gate_hook.py").write_text("import sys; sys.stdin.read()\n", encoding="utf-8")

    stale = control / "job_readonly_gate.json"
    stale.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command",
                                        "command": f"{DEAD_INTERPRETER} /x/job_gate_hook.py"}]},
        ]}
    }), encoding="utf-8")

    # Exercise the REAL repair the conftest fixture calls — not a re-implementation of
    # it, or this test would keep passing while the fixture regressed.
    assert gate_config_problems(stale), "precondition: the stale config is unhealthy"
    rewrote = repair_gate_config(stale, tmpl, sys.executable)

    assert rewrote is True, "an unhealthy config must be regenerated, not kept"
    assert DEAD_INTERPRETER not in stale.read_text(encoding="utf-8")
    assert gate_config_problems(stale) == [], "regenerated config must be runnable"


def test_healthy_gate_config_is_left_alone(tmp_path):
    """Repair must be a repair, not an unconditional rewrite (idempotence preserved)."""
    import sys

    control = tmp_path / "control"
    control.mkdir()
    (control / "job_gate_hook.py").write_text("import sys; sys.stdin.read()\n", encoding="utf-8")
    tmpl = control / "g.json.template"
    tmpl.write_text("{}", encoding="utf-8")  # a template that would produce a BROKEN config
    good = _write_gate(control, _healthy_command(control), name="g.json")
    before = good.read_text(encoding="utf-8")

    assert repair_gate_config(good, tmpl, sys.executable) is False
    assert good.read_text(encoding="utf-8") == before
