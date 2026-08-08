"""The gate must be RUNNABLE, not merely present (fail-open regression suite).

WHAT THIS COVERS THAT test_readonly_gate.py DOES NOT
---------------------------------------------------
test_readonly_gate.py asserts the hook SCRIPT decides correctly and that the launched
command carries the right flags. Both were true the whole time the boundary was gone.
Nothing asserted the one thing in between: that Claude Code can actually RUN the hook
named in the config.

"Can run" is not "the path resolves". A resolvable path is necessary and NOT sufficient:
a script that exists but crashes, and `/usr/bin/python3` after Command Line Tools is
removed, both satisfy `isfile` + `X_OK` and both still exit non-zero — so both fail open
while a path-only checker certifies them green. A dead boundary showing a green light is
worse than no checker at all, because the green light is what stops anyone looking. The
gate is therefore SMOKE-EXECUTED on a synthetic event and must exit 0 with a parseable
decision (or exit 2, the documented block). One mechanism covers all five failures:
missing interpreter, non-executable interpreter, missing script, crashing script, shim.

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
import os
import re
import subprocess

import pytest

from parsers import agent_run, gate_check, paths
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


DECIDING_HOOK = (
    "import json, sys\n"
    "sys.stdin.read()\n"
    "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',\n"
    "  'permissionDecision': 'deny', 'permissionDecisionReason': 'test hook'}}))\n"
)


def _healthy_command(tmp_path, name="hook.py"):
    """A gate command that really works: this interpreter + a script that DECIDES.

    Note what "healthy" has to mean now — a script that merely runs is not enough, it
    has to answer. A hook that exits 0 and prints nothing has decided nothing, which is
    the same as not being there.
    """
    import sys
    script = tmp_path / name
    script.write_text(DECIDING_HOOK, encoding="utf-8")
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


# --------------------------------------------------------------------------- #
# A RESOLVABLE path is not a WORKING hook. These two pass every static check.
# --------------------------------------------------------------------------- #
def test_crashing_hook_script_is_rejected(tmp_path):
    """ARM E: interpreter exists, script exists, script CRASHES (a bad edit).

    Every path in the config resolves. `isfile` and `X_OK` are both satisfied. The hook
    exits 1, so Claude Code fails open and the gate is gone.
    """
    import sys
    script = tmp_path / "crash.py"
    script.write_text("raise ImportError('simulated bad edit: a deleted import')\n",
                      encoding="utf-8")
    command = f"{sys.executable} {script}"

    # Precondition: the STATIC check is happy — that is the whole point of this test.
    from parsers.gate_check import _interpreter_problem
    assert _interpreter_problem(command) == "", "static path check should pass here"

    problems = gate_config_problems(_write_gate(tmp_path, command))
    assert problems, "a hook that crashes must not be certified healthy"
    assert any("EXITS 1" in p for p in problems), problems
    assert any("ImportError" in p for p in problems), "surface the hook's own error"


def test_shim_interpreter_that_exits_nonzero_is_rejected(tmp_path):
    """ARM F: the macOS /usr/bin/python3 shape — present, executable, exits non-zero.

    /usr/bin/python3 is a Command Line Tools shim. Remove CLT (an ordinary OS-upgrade
    consequence) and the file is STILL there and STILL executable, but every invocation
    fails with `xcrun: error: invalid active developer path`. A checker built on
    isfile+X_OK certifies it green while the boundary is wide open — which is worse than
    having no checker, because a green light stops anyone from looking.
    """
    shim = tmp_path / "clt_shim_python3"
    shim.write_text(
        "#!/bin/bash\n"
        "echo 'xcrun: error: invalid active developer path "
        "(/Library/Developer/CommandLineTools)' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    hook = tmp_path / "job_gate_hook.py"
    hook.write_text(DECIDING_HOOK, encoding="utf-8")
    command = f"{shim} {hook}"

    from parsers.gate_check import _interpreter_problem
    assert _interpreter_problem(command) == "", "static path check should pass here"

    problems = gate_config_problems(_write_gate(tmp_path, command))
    assert problems, "a shim that always fails must not be certified healthy"
    assert any("EXITS 1" in p for p in problems), problems
    assert any("invalid active developer path" in p for p in problems), problems


def test_hook_that_decides_nothing_is_rejected(tmp_path):
    """Exit 0 with no output is not a decision — it is silence wearing a green light."""
    import sys
    script = tmp_path / "silent.py"
    script.write_text("import sys; sys.stdin.read()\n", encoding="utf-8")
    problems = gate_config_problems(_write_gate(tmp_path, f"{sys.executable} {script}"))
    assert any("no parseable permissionDecision" in p for p in problems), problems


def test_hook_that_blocks_via_exit_2_is_accepted(tmp_path):
    """Exit 2 is Claude Code's documented block; such a hook demonstrably RAN.

    Guards the smoke check against over-reach — it must reject hooks that do not run,
    not hooks that block in the other supported way.
    """
    script = tmp_path / "block.sh"
    script.write_text("#!/bin/bash\ncat >/dev/null\necho blocked >&2\nexit 2\n", encoding="utf-8")
    script.chmod(0o755)
    assert gate_config_problems(_write_gate(tmp_path, str(script))) == []


def test_hanging_hook_is_rejected(tmp_path, monkeypatch):
    """A gate that never answers cannot be relied on to block."""
    from parsers import gate_check
    monkeypatch.setattr(gate_check, "_SMOKE_TIMEOUT_S", 0.5)
    script = tmp_path / "hang.sh"
    script.write_text("#!/bin/bash\nsleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    problems = gate_check.gate_config_problems(_write_gate(tmp_path, str(script)))
    assert any("did not answer" in p for p in problems), problems


def test_smoke_run_cannot_be_stubbed_out(tmp_path, monkeypatch):
    """A stub on subprocess.run must not be able to certify an unexecuted gate.

    Found while writing these: the existing agent_run tests patch subprocess.run globally
    to capture the launch argv, and that stub was answering the smoke probe — a broken
    gate would have been certified healthy by a test helper. The verification binds the
    real runner at import so nothing installed later can answer for the hook.
    """
    class _Liar:
        returncode = 0
        stdout = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}})
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Liar())
    import sys
    crash = tmp_path / "crash.py"
    crash.write_text("raise SystemExit(1)\n", encoding="utf-8")
    problems = gate_config_problems(_write_gate(tmp_path, f"{sys.executable} {crash}"))
    assert problems, "a stubbed subprocess.run must not certify a hook that exits 1"


# --------------------------------------------------------------------------------------- #
# WHAT THE PROBE HANDS THE HOOK.
#
# `_smoke_problem` EXECUTES the command named in an on-disk config. It used to do so under
# the FULL parent environment — so the one place this repo runs a config-named command was
# also the one place handing it every token the portal process holds. Captured from inside
# a probe on this box: SNOWFLAKE_TOKEN, ATLASSIAN_API_TOKEN, SLACK_BOT_TOKEN. The same
# command, run as a REAL hook, runs under agent_run._child_env(), which strips exactly those.
#
# Not a privilege escalation — whoever can write control/job_*_gate.json can already install
# a three-line hook that allows everything, which is strictly easier and more useful — but it
# is a real exposure with no reason to exist, and "the easier attack is worse" is not an
# argument for leaving the harder one open.
# --------------------------------------------------------------------------------------- #
_CREDENTIALISH = re.compile(
    r"(SNOWFLAKE|SLACK|ATLASSIAN|BITBUCKET|JIRA|GITHUB|OPENAI|ANTHROPIC|AWS|AZURE|GCP|NPM"
    r"|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API[_-]?KEY|PRIVATE[_-]?KEY|_KEY$|^KEY$)",
    re.IGNORECASE,
)

PLANTED_CREDENTIALS = {
    # The three actually observed leaking, plus one that no strip-list named after a secret
    # would catch — which is why the probe uses an ALLOW-list.
    "SNOWFLAKE_TOKEN": "sf-probe-canary",
    "ATLASSIAN_API_TOKEN": "atl-probe-canary",
    "SLACK_BOT_TOKEN": "xoxb-probe-canary",
    "DATABASE_URL": "postgres://user:probe-canary@host/db",
}


def _credential_vars(env):
    """Every variable name in `env` that looks like it carries a credential."""
    return sorted(k for k in env if _CREDENTIALISH.search(k))


def _reporting_gate(tmp_path):
    """A healthy gate whose hook DUMPS the environment + cwd it was given.

    It answers correctly (exit 0, parseable decision) so the gate is still certified
    healthy — the point is what the probe handed it, not whether it passed.
    """
    import sys
    report = tmp_path / "probe_report.json"
    script = tmp_path / "reporting_hook.py"
    script.write_text(
        "import json, os, sys\n"
        "sys.stdin.read()\n"
        "json.dump({'env': dict(os.environ), 'cwd': os.getcwd(),\n"
        "           'cwd_entries': sorted(os.listdir('.'))},\n"
        "          open(%r, 'w'))\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',\n"
        "  'permissionDecision': 'deny', 'permissionDecisionReason': 'probe'}}))\n"
        % str(report),
        encoding="utf-8",
    )
    return _write_gate(tmp_path, f"{sys.executable} {script}"), report


@pytest.fixture
def planted_credentials(monkeypatch):
    for name, value in PLANTED_CREDENTIALS.items():
        monkeypatch.setenv(name, value)
    return PLANTED_CREDENTIALS


def test_smoke_probe_is_handed_no_credentials(tmp_path, planted_credentials):
    gate, report = _reporting_gate(tmp_path)
    assert gate_config_problems(gate) == [], "precondition: the reporting hook is healthy"

    seen = json.loads(report.read_text(encoding="utf-8"))["env"]
    assert _credential_vars(seen) == [], (
        "the smoke probe was handed credential-bearing variables: %s"
        % _credential_vars(seen))
    for name, value in planted_credentials.items():
        assert name not in seen, name
    assert not any(value in v for v in seen.values() for value in planted_credentials.values()), (
        "a planted secret reached the probe under a different variable name")
    assert "PATH" in seen, "stripping must not break a bare-name interpreter"


def test_probe_env_check_rejects_the_inherited_environment(tmp_path, monkeypatch,
                                                           planted_credentials):
    """E-03 for the assertion above: restore the pre-fix env and it must go red.

    A test that only ever sees a clean environment cannot tell "the fix works" from "this
    box exports no secrets". Putting the old behaviour back — the full parent environment —
    has to make `_credential_vars` non-empty, or the check above is decoration.
    """
    monkeypatch.setattr(gate_check, "_probe_env", lambda: dict(os.environ))
    gate, report = _reporting_gate(tmp_path)
    assert gate_config_problems(gate) == []

    seen = json.loads(report.read_text(encoding="utf-8"))["env"]
    leaked = _credential_vars(seen)
    assert leaked, "the pre-fix environment leaked nothing — the canaries did not apply"
    for name in planted_credentials:
        assert name in seen, f"{name} should be visible to the pre-fix probe"


def test_smoke_probe_runs_in_a_pinned_empty_directory(tmp_path):
    """The hook must not be handed the caller's cwd.

    A hook that resolves a relative path would otherwise operate on whatever directory the
    portal happened to be started from. It gets a fresh empty one, removed afterwards.
    """
    gate, report = _reporting_gate(tmp_path)
    assert gate_config_problems(gate) == []

    dump = json.loads(report.read_text(encoding="utf-8"))
    assert os.path.realpath(dump["cwd"]) != os.path.realpath(os.getcwd()), dump["cwd"]
    assert dump["cwd_entries"] == [], f"the probe cwd was not empty: {dump['cwd_entries']}"
    assert not os.path.exists(dump["cwd"]), "the probe directory must be cleaned up"


def test_probe_cwd_check_rejects_an_inherited_cwd(tmp_path, monkeypatch):
    """E-03 for the cwd assertion: make the probe inherit the caller's cwd again."""
    real_run = gate_check._RUN
    monkeypatch.setattr(gate_check, "_RUN",
                        lambda *a, **kw: real_run(*a, **{**kw, "cwd": None}))
    gate, report = _reporting_gate(tmp_path)
    assert gate_config_problems(gate) == []

    dump = json.loads(report.read_text(encoding="utf-8"))
    assert os.path.realpath(dump["cwd"]) == os.path.realpath(os.getcwd()), (
        "the mutation did not reproduce the inherited cwd, so the check above proves nothing")


# --------------------------------------------------------------------------------------- #
# A MISSING HOOK IS NAMED, whatever it is written in.
#
# The check read `arg.endswith(".py")`, so a config naming a missing `hook.sh`/`hook.js`/
# extensionless hook was reported as having no path problem. Still fail-CLOSED (the smoke run
# catches it) but only as "hook EXITS 2" — which points the reader at the hook's logic instead
# of at the path that is not there.
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["hook.sh", "hook.js", "hook.rb", "gatehook"])
def test_missing_hook_script_is_named_whatever_its_extension(tmp_path, name):
    import sys
    problem = gate_check._interpreter_problem(f"{sys.executable} {tmp_path}/{name}")
    assert "hook script does not exist" in problem, problem
    assert name in problem, problem


# --------------------------------------------------------------------------------------- #
# ...AND WHEREVER IT SITS: a BARE filename was checked by nothing at all.
#
# Two individually-correct rules met and left a hole between them. `looks_like_path` demanded a
# separator or a `./ ../ ~` prefix, so a config naming a plain `hook.py` was never path-checked;
# and the smoke run does not cover for it, because CPython exits **2** when it cannot open the
# script and exit 2 is the documented "the hook ran and blocked". Measured on this file's own
# helpers: a config naming a MISSING bare `hook.py` came back with NO PROBLEMS — certified
# healthy, boundary gone. That is the exact direction this module exists to make impossible.
#
# The two halves live in ONE test deliberately. "The bare name goes red" is satisfiable by
# simply refusing exit 2, which would redden every hook that blocks the way Claude Code
# documents — so proving that half alone proves the wrong thing.
# --------------------------------------------------------------------------------------- #
BARE_MISSING = "__no_such_gate_hook__.py"


def test_bare_missing_script_is_named_and_a_real_exit_2_block_still_passes(tmp_path):
    import sys

    assert not os.path.exists(BARE_MISSING), "precondition: the bare name must not resolve"

    # HALF 1 — the fail-open. A bare filename naming nothing must be reported, and reported as
    # the missing PATH: "EXITS 2" is the message that hid this, because it sends the reader to
    # the hook's logic instead of to the file that is not there.
    problems = gate_config_problems(
        _write_gate(tmp_path, f"{sys.executable} {BARE_MISSING}", name="bare.json"))
    assert problems, "a config naming a MISSING bare hook filename was certified healthy"
    assert any("hook script does not exist" in p and BARE_MISSING in p for p in problems), problems
    assert not any("EXITS 2" in p for p in problems), problems

    # HALF 2 — the over-reach. Same interpreter, same exit code, script PRESENT: this hook ran
    # and blocked, which is a supported answer and must stay green. The only difference between
    # the two halves is whether the script is on disk, which is precisely the distinction the
    # accepted exit code cannot make on its own.
    blocker = tmp_path / "blocking_hook.py"
    blocker.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "sys.stderr.write('blocked by policy\\n')\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    assert gate_config_problems(
        _write_gate(tmp_path, f"{sys.executable} {blocker}", name="blocker.json")) == []


NOTHING = re.compile(r"(?!)")  # a regex that matches nothing — the standard neutraliser here


def test_bare_script_check_rejects_the_shape_only_rule(tmp_path, monkeypatch):
    """E-03 for the half above: restore the pre-fix rule and the lie must come back.

    The old rule was "path-shaped or not a path at all", under which a bare filename is never a
    candidate. The SAME config must then be certified healthy again — otherwise the assertion
    above is only proving that this box happens to have no `__no_such_gate_hook__.py` lying
    around.

    TWO mechanisms have to be neutralised now, and the reason is worth keeping: blanking the
    filename-suffix signal alone no longer restores the pre-fix classification, because the
    interpreter-identity rule claims the bare token on POSITION regardless of its shape. That
    is not the mutation going stale — it is the identity rule being load-bearing, which
    `test_bare_extensionless_script_is_named_under_a_known_interpreter` pins from the other
    side. A mutation that stops reproducing the bug must be widened, never deleted.
    """
    import sys

    command = f"{sys.executable} {BARE_MISSING}"
    monkeypatch.setattr(gate_check, "_FILE_SUFFIX", NOTHING)
    assert gate_check._interpreter_problem(command) != "", (
        "blanking the suffix signal alone stopped reporting it — the identity rule below is "
        "what covers this now, and this assertion is how we notice if it stops")

    monkeypatch.setattr(gate_check, "_SCRIPT_TAKING_INTERPRETER", NOTHING)
    assert gate_check._interpreter_problem(command) == "", (
        "the mutation did not restore the pre-fix classification")
    assert gate_config_problems(_write_gate(tmp_path, command, name="bare.json")) == [], (
        "the pre-fix rule did not reproduce the fail-open, so the case above proves nothing")


def _fake_launcher(tmp_path, name):
    """An executable that only has to EXIST — argv[0] is checked, never run, by these tests.

    Needed because the wrapper shapes have to be judged with a WRAPPER at argv[0]. They used
    to be written with `{py}` standing in for `uv`, which was fine while shape was the only
    signal and became wrong the moment the interpreter's IDENTITY became one: `python run
    hook.py` does not mean what `uv run hook.py` means — CPython has no `run` subcommand, so
    there `run` really is the script, and reporting it missing is correct rather than
    cry-wolf. A stand-in that no longer stands for the same thing tests the wrong command.
    """
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.mark.parametrize("command", [
    "{py} -c 'import sys; sys.stdout.write(\"/no/such/path\")'",   # code, not a path
    "{py} -m json.tool",                                           # a module, not a path
    "{py} -u {script}",                                            # a flag that takes no value
    "{py} {script} --verbose",                                     # a valueless flag after it
    "{uv} run {script}",            # a wrapper's SUBCOMMAND before the script (`uv run hook.py`)
    "/usr/bin/env python3 {script}",              # a nested INTERPRETER before it
    "{py} -W ignore {script}",      # a SHORT flag that DOES take a value, then the script
    "{py} -X faulthandler {script}",
])
def test_interpreter_check_does_not_cry_wolf(tmp_path, command):
    """The other half: a checker that flags well-formed commands gets switched off.

    This repo threw away a backtick linter for exactly that once. `-c` and `-m` carry
    arbitrary text that can contain a `/`, so their values are skipped rather than read as
    paths.

    The wrapper and `env` shapes guard the rule above from the obvious over-correction. "The
    first non-flag token is the script" would report `run` and `python3` as missing scripts and
    refuse to launch a working gate — and a false BROKEN stops the queue, which is its own
    outage. A token that cannot name a file is stepped over, not consumed; under `env` the
    interpreter is identified and the walk starts after it.

    The last two are the price of the interpreter-identity rule: under a RECOGNISED interpreter
    the first positional is claimed whatever its shape, so a short flag's value (`-W ignore`,
    `-X faulthandler`) would be convicted as a missing script unless a flag's value is barred
    from claiming the role. `ignore` naming nothing is not a broken gate.
    """
    import sys
    script = tmp_path / "hook.py"
    script.write_text(DECIDING_HOOK, encoding="utf-8")
    resolved = command.format(py=sys.executable, script=script,
                              uv=_fake_launcher(tmp_path, "uv"))
    assert gate_check._interpreter_problem(resolved) == "", resolved


# --------------------------------------------------------------------------------------- #
# WHICH TOKEN IS THE SCRIPT: three rules, and each one exists to stop the next over-reaching.
#
#   1. IDENTITY — under an interpreter we RECOGNISE, the first positional is the script by that
#      interpreter's own grammar, whatever its shape. This is the one that closes a bare
#      EXTENSIONLESS name; shape alone can never, because `gatehook` and a wrapper's
#      subcommand are the same string.
#   2. A bare `--long-flag`'s VALUE never claims the role. Rule 1 without this convicts
#      `--env-file .env` and `--project pyproject.toml` — two commands that WORK — and a false
#      RED refuses to launch the queue, which is an outage rather than a safety win.
#   3. A bare SHORT flag's value may claim it, but only on a file EXTENSION. Rule 2 extended to
#      short flags would un-check `python -u hook.py`, reopening the hole Finding A closed; but
#      a SEPARATOR must not be enough either, because that is what the values which do exist
#      carry (`-r ts-node/register`).
#
# Every case below is judged with the right thing at argv[0]: a wrapper for the wrapper shapes,
# a real interpreter for the interpreter shapes. Judging a wrapper shape with python at argv[0]
# is what the fixture above stopped doing, and is now itself a different command.
# --------------------------------------------------------------------------------------- #
BARE_MISSING_EXTENSIONLESS = "__no_such_gatehook__"


def test_bare_extensionless_script_is_named_under_a_known_interpreter(tmp_path):
    """RULE 1. `python gatehook` can only mean the FILE `gatehook` — CPython has no subcommands.

    Documented as a residual until now, and correctly so while shape was the only signal. What
    changed is not the tolerance for guessing but the amount that has to be guessed: POSITION
    under a NAMED interpreter is not a guess. The message matters as much as the verdict —
    without the path check the smoke run reports this as `EXITS 2`, which is the documented
    "the hook ran and blocked" and sends the reader to the hook's logic instead of to the file
    that is not there.
    """
    import sys

    assert not os.path.exists(BARE_MISSING_EXTENSIONLESS), "precondition: it must not resolve"
    problem = gate_check._interpreter_problem(
        f"{sys.executable} {BARE_MISSING_EXTENSIONLESS}")
    assert "hook script does not exist" in problem, problem
    assert BARE_MISSING_EXTENSIONLESS in problem, problem

    problems = gate_config_problems(_write_gate(
        tmp_path, f"{sys.executable} {BARE_MISSING_EXTENSIONLESS}", name="extless.json"))
    assert any("hook script does not exist" in p for p in problems), problems
    assert not any("EXITS 2" in p for p in problems), problems


def test_bare_extensionless_check_rejects_the_pre_identity_rule(monkeypatch):
    """E-03 for rule 1: with no interpreter recognised, the documented fail-open comes back."""
    import sys

    monkeypatch.setattr(gate_check, "_SCRIPT_TAKING_INTERPRETER", NOTHING)
    assert gate_check._interpreter_problem(
        f"{sys.executable} {BARE_MISSING_EXTENSIONLESS}") == "", (
        "the pre-fix rule did not reproduce the fail-open, so the case above proves nothing")


def test_the_identity_rule_sees_through_the_env_launcher(tmp_path):
    """RULE 1 again, one layer down: `env` is a launcher, so argv[0] is not the interpreter.

    Without stepping through it the recognised name is `env`, nothing is asserted, and the
    shape rule decides — which is the same fail-open, reached by a different road. Worse, a
    walk that started at argv[1] regardless would read `python3` itself as the script and
    report a WORKING command broken.
    """
    problem = gate_check._interpreter_problem(
        f"/usr/bin/env python3 {BARE_MISSING_EXTENSIONLESS}")
    assert "hook script does not exist" in problem, problem
    assert BARE_MISSING_EXTENSIONLESS in problem, problem
    assert "python3" not in problem, f"the interpreter was mistaken for the script: {problem}"


def test_env_step_through_check_rejects_the_unstepped_launcher(monkeypatch):
    """E-03 for the step-through: stop recognising `env` and the case above must go quiet."""
    monkeypatch.setattr(gate_check, "_ENV_LAUNCHER", "__not_env__")
    assert gate_check._interpreter_problem(
        f"/usr/bin/env python3 {BARE_MISSING_EXTENSIONLESS}") == "", (
        "the mutation did not reproduce the unstepped launcher")


MISSING_ASSIGNMENT = "PYTHONPATH=/no/such/lib"


def test_the_identity_rule_sees_through_variable_assignments(tmp_path):
    """RULE 1, two layers down: `env VAR=1 python3 <hook>` still runs python.

    Separate from the `env` case above and not folded into it: they are neutralised by
    different constants, and a case whose only covering mutation also reddens something else
    is a case nobody has actually watched fail on its own.
    """
    problem = gate_check._interpreter_problem(
        f"/usr/bin/env {MISSING_ASSIGNMENT} python3 {BARE_MISSING_EXTENSIONLESS}")
    assert "hook script does not exist" in problem, problem
    assert BARE_MISSING_EXTENSIONLESS in problem, problem


def test_assignment_step_through_check_rejects_the_pre_fix_rule(monkeypatch):
    """E-03: stop recognising assignments and the interpreter behind them is lost again."""
    monkeypatch.setattr(gate_check, "_ASSIGNMENT", NOTHING)
    assert gate_check._interpreter_problem(
        f"/usr/bin/env {MISSING_ASSIGNMENT} python3 {BARE_MISSING_EXTENSIONLESS}") == "", (
        "the mutation did not reproduce the pre-fix classification")


def test_a_variable_assignment_is_not_read_as_a_missing_script(tmp_path):
    """`env PYTHONPATH=/x python3 <hook>` is a working command, and was reported broken.

    A separator inside an ASSIGNMENT is not a separator inside a filename, but the walk saw
    only the slash — so it convicted the assignment, named it as the hook script, and refused
    to launch. Cry-wolf on a shape a hook that lives in a venv genuinely uses.
    """
    script = tmp_path / "hook.py"
    script.write_text(DECIDING_HOOK, encoding="utf-8")
    command = f"/usr/bin/env {MISSING_ASSIGNMENT} python3 {script}"
    assert not os.path.exists("/no/such/lib"), "precondition: the value must not resolve"
    assert gate_check._interpreter_problem(command) == "", command


def test_assignment_not_convicted_check_rejects_the_pre_fix_rule(tmp_path, monkeypatch):
    """E-03: walk from argv[1] regardless — the pre-fix shape — and it is convicted again."""
    script = tmp_path / "hook.py"
    script.write_text(DECIDING_HOOK, encoding="utf-8")
    monkeypatch.setattr(gate_check, "_ENV_LAUNCHER", "__not_env__")
    problem = gate_check._interpreter_problem(
        f"/usr/bin/env {MISSING_ASSIGNMENT} python3 {script}")
    assert "hook script does not exist" in problem, (
        "the pre-fix rule flagged nothing, so the case above proves nothing")
    assert MISSING_ASSIGNMENT in problem, problem


# The two shapes the merge gate measured going newly RED, plus the one that was red already.
# Each value is RELATIVE or a module specifier, which is the whole difficulty: it is resolved
# against the CALLER's cwd, not the hook's, so it legitimately names nothing from here.
_FLAG_VALUES = {
    "env-file": ("--env-file", ".env"),
    "project": ("--project", "pyproject.toml"),
    "require": ("--require", "ts-node/register"),
}


def _wrapper_command(tmp_path, flag, value):
    """`<launcher> run <flag> <value> <script>` — the script is real and resolves."""
    script = tmp_path / "hook.py"
    script.write_text(DECIDING_HOOK, encoding="utf-8")
    return f"{_fake_launcher(tmp_path, 'uv')} run {flag} {value} {script}"


@pytest.mark.parametrize("shape", list(_FLAG_VALUES))
def test_a_long_flags_value_does_not_claim_the_script_role(tmp_path, monkeypatch, shape):
    """RULE 2. Three WORKING commands that this module refused to launch.

    `cwd` is repointed at `tmp_path` deliberately: these values are relative, so whether they
    resolve depends on where the check runs from. Measured from this repo's root, which ships a
    `.env`, the `env-file` case comes back green for a reason that has nothing to do with the
    rule under test — a fixture that passes because of the developer's directory is not
    evidence, so the precondition below pins it.
    """
    monkeypatch.chdir(tmp_path)
    flag, value = _FLAG_VALUES[shape]
    assert not os.path.exists(value), f"precondition: {value!r} must not resolve from here"
    command = _wrapper_command(tmp_path, flag, value)
    assert gate_check._interpreter_problem(command) == "", command


@pytest.mark.parametrize("shape", list(_FLAG_VALUES))
def test_flag_value_check_rejects_the_pre_fix_rule(tmp_path, monkeypatch, shape):
    """E-03 for rule 2, once per shape: with no token shielded, each is convicted again.

    Split per shape rather than proven once, for the reason the stale-name checks next door are
    also split: one mutation that reddens the parametrisation leaves the other two assertions
    unwatched, and an assertion nobody has seen fail is decoration.
    """
    monkeypatch.chdir(tmp_path)
    flag, value = _FLAG_VALUES[shape]
    command = _wrapper_command(tmp_path, flag, value)
    monkeypatch.setattr(gate_check, "_BARE_FLAG", NOTHING)  # nothing is ever a flag's value
    problem = gate_check._interpreter_problem(command)
    assert "hook script does not exist" in problem, (
        f"the pre-fix rule flagged nothing for {shape!r}, so the case above proves nothing")
    assert value in problem, problem


def test_a_short_flags_value_may_still_be_the_script(tmp_path, monkeypatch):
    """RULE 3, and the BOUNDARY of rule 2 — extending it to short flags reopens Finding A.

    `python -u hook.py` is an ordinary hand-written config and `-u` takes no value, so the
    token after it is the script and a missing one must still be named. Proving rule 2 without
    this is proving the wrong thing: "no token after a flag is ever the script" satisfies every
    assertion above while quietly un-checking the commonest shape there is.
    """
    import sys

    monkeypatch.chdir(tmp_path)
    problem = gate_check._interpreter_problem(f"{sys.executable} -u {BARE_MISSING}")
    assert "hook script does not exist" in problem, problem
    assert BARE_MISSING in problem, problem


def test_short_flag_boundary_rejects_treating_every_flag_as_long(tmp_path, monkeypatch):
    """E-03 for rule 3: widen rule 2 to every flag and the fail-open above comes straight back."""
    import sys

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate_check, "_BARE_LONG_FLAG", re.compile(r"^-"))
    assert gate_check._interpreter_problem(f"{sys.executable} -u {BARE_MISSING}") == "", (
        "the mutation did not reproduce the over-broad rule, so the case above proves nothing")


HOME_INTERPRETER = "~/venv/bin/python"


def _home_venv(tmp_path, monkeypatch):
    """A REAL interpreter at `~/venv/bin/python`, with HOME repointed at `tmp_path`.

    A symlink to this interpreter rather than a stub, so the whole config can be certified end
    to end — the smoke run has to be able to execute it, and a checker that only proves
    `isfile` is the exact shape of check this module exists to replace.
    """
    import sys

    monkeypatch.setenv("HOME", str(tmp_path))
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    os.symlink(sys.executable, venv_python)
    script = tmp_path / "hook.py"
    script.write_text(DECIDING_HOOK, encoding="utf-8")
    return f"{HOME_INTERPRETER} {script}"


def test_home_relative_interpreter_is_accepted_and_a_missing_one_still_named(tmp_path,
                                                                             monkeypatch):
    """The `~` fix landed on the ARGUMENT side and not on argv[0] — half of its own commit.

    `os.path.isfile` was asked about a literal directory named `~`, which is never there, so
    `~/venv/bin/python <hook>` could never pass however real the interpreter was. Both halves
    are here in one test: expanding `~` must not become a blanket pass, so a `~` path that
    genuinely names nothing must still be named — that is the only difference between fixing
    the check and removing it.
    """
    command = _home_venv(tmp_path, monkeypatch)
    assert gate_check._interpreter_problem(command) == "", command
    assert gate_config_problems(_write_gate(tmp_path, command, name="home.json")) == []

    problem = gate_check._interpreter_problem(f"~/nope/bin/python {tmp_path}/hook.py")
    assert "hook interpreter does not exist" in problem, problem
    assert "~/nope/bin/python" in problem, problem


def test_home_relative_interpreter_check_rejects_the_unexpanded_rule(tmp_path, monkeypatch):
    """E-03: put the unexpanded token back and the working interpreter is reported missing."""
    command = _home_venv(tmp_path, monkeypatch)
    monkeypatch.setattr(gate_check, "_interpreter_path", lambda exe: exe)
    problem = gate_check._interpreter_problem(command)
    assert "hook interpreter does not exist" in problem, (
        "the pre-fix rule flagged nothing, so the case above proves nothing")
    assert HOME_INTERPRETER in problem, problem


# --------------------------------------------------------------------------------------- #
# THE OTHER DIRECTION: arguments that carry a separator and name no file.
#
# `looks_like_path` accepts a `~` prefix and then tested the RAW token, so `os.path.exists`
# was asked about a literal directory named `~`, which is never there. A `~` argument could
# therefore never pass — reported missing while the real path sat on disk. A URL and a glob
# are the same mistake from the other end: both carry `os.sep` while naming nothing, and both
# started being flagged when the path test stopped requiring a `.py` suffix.
#
# Cry-wolf is not the harmless half of a checker. A false BROKEN refuses to launch a WORKING
# gate, so it stops the queue — and this repo has already thrown one checker away for exactly
# that. Today no shipped config carries such an argument, which is the only reason this was
# not red: a hole nobody has stepped in yet is still a hole.
# --------------------------------------------------------------------------------------- #
_NON_PATH_ARGUMENTS = {
    "home": "--data ~/artifacts",                 # expands to a real directory
    "url": "--endpoint https://example.com/x",    # a separator, but not a path
    "glob": "--exclude artifacts/*.json",         # matches files; is not one
}


def _hook_with_arguments(tmp_path, monkeypatch, *shapes):
    """A healthy hook command carrying each named argument shape.

    HOME is repointed at `tmp_path`, so the `~` case is decided by a directory this test
    created rather than by whatever the developer's home happens to contain.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    extras = " ".join(_NON_PATH_ARGUMENTS[s] for s in shapes)
    return f"{_healthy_command(tmp_path)} {extras}"


@pytest.mark.parametrize("shape", list(_NON_PATH_ARGUMENTS))
def test_non_path_arguments_are_not_reported_missing(tmp_path, monkeypatch, shape):
    command = _hook_with_arguments(tmp_path, monkeypatch, shape)
    assert gate_check._interpreter_problem(command) == "", command


def test_a_gate_carrying_all_three_shapes_is_certified_healthy(tmp_path, monkeypatch):
    """End to end, not just the static half — the whole config must come back clean."""
    command = _hook_with_arguments(tmp_path, monkeypatch, *_NON_PATH_ARGUMENTS)
    assert gate_config_problems(_write_gate(tmp_path, command)) == []


@pytest.mark.parametrize("shape,restore_pre_fix", [
    # `~` was tested unexpanded...
    ("home", lambda mp: mp.setattr(gate_check, "_resolves", os.path.exists)),
    # ...and neither exclusion existed, so a separator alone was enough to convict.
    ("url", lambda mp: mp.setattr(gate_check, "_URL_MARKER", "\x00")),
    ("glob", lambda mp: mp.setattr(gate_check, "_GLOB_CHARS", "")),
])
def test_non_path_argument_check_rejects_each_pre_fix_rule(tmp_path, monkeypatch, shape,
                                                           restore_pre_fix):
    """E-03, once per shape: restore each pre-fix rule and that shape must be flagged again.

    Three separate mutations rather than one, for the reason the stale-name checks next door
    are also split: a single mutation that reddens the check leaves the other two assertions
    unproven, and an assertion nobody has watched fail is decoration.
    """
    command = _hook_with_arguments(tmp_path, monkeypatch, shape)
    restore_pre_fix(monkeypatch)
    problem = gate_check._interpreter_problem(command)
    assert "hook script does not exist" in problem, (
        f"the pre-fix rule for {shape!r} flagged nothing, so the case above proves nothing")


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
    (control / "job_gate_hook.py").write_text(DECIDING_HOOK, encoding="utf-8")

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
    (control / "job_gate_hook.py").write_text(DECIDING_HOOK, encoding="utf-8")
    tmpl = control / "g.json.template"
    tmpl.write_text("{}", encoding="utf-8")  # a template that would produce a BROKEN config
    good = _write_gate(control, _healthy_command(control), name="g.json")
    before = good.read_text(encoding="utf-8")

    assert repair_gate_config(good, tmpl, sys.executable) is False
    assert good.read_text(encoding="utf-8") == before
