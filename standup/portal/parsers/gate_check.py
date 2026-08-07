"""Pre-launch verification that a job gate's `--settings` config is actually RUNNABLE.

WHY THIS MODULE EXISTS
----------------------
`parsers/agent_run.py` calls the PreToolUse gate hook "the LAST-LINE boundary that
holds even if the --allowedTools flag were ever weakened". That claim carries one
unstated precondition: **Claude Code has to be able to execute the hook.**

Measured on Claude Code 2.1.222 (benign prompt, 3/3 replication, one
variable changed — the interpreter path — everything else byte-identical):

    PreToolUse hook `command`          Write tool   permission_denials   stderr
    ---------------------------------  -----------  -------------------  ------
    <real python> job_gate_hook.py     BLOCKED      1                    (none)
    <script that exits 2>              BLOCKED      1                    (none)
    <MISSING python> job_gate_hook.py  **RAN**      0                    (none)
    (no --settings at all)             RAN          0                    (none)

A hook whose interpreter cannot be exec'd **FAILS OPEN**, and does it *silently*:
zero bytes of stderr, an empty `permission_denials`, and a result JSON whose shape
is indistinguishable from a run with no hook configured at all. The boundary does
not degrade into something noisy — it disappears, and nothing anywhere says so.

That cannot be fixed inside the hook, because the hook is precisely the thing that
is not running. The only remaining place to enforce is BEFORE launch, in the
trusted parent process. So this module answers "is the gate executable?" and
`agent_run` REFUSES TO LAUNCH when the answer is no — converting an upstream
fail-OPEN into our fail-CLOSED.

The consequence is deliberate and is the entire point: a rotted interpreter path now
STOPS work instead of silently unprotecting it. Blocked work is visible and someone
fixes it; an evaporated boundary is invisible and nobody does.

Checking file *existence* is not enough — that is the specific hole this replaces.
`if not path.exists()` is idempotent but never inspects content, so a config that
exists while naming a dead interpreter survives forever. Both shipped configs were
found in exactly that state on a real install: a Homebrew python had been baked in
and later uninstalled. The question is never "is there a config" but "does the config
still describe something real".

Stdlib-only, no imports from the rest of the portal, so it can be used from a hook,
a conftest, setup, or a bare `python -m`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from typing import List, Union

PathLike = Union[str, "os.PathLike[str]"]


class GateConfigError(Exception):
    """The gate `--settings` config is missing, malformed, or not executable.

    Raised by :func:`verify_gate_config`. Callers must treat this as "do not launch":
    a job that cannot be gated must not run ungated.
    """


def _interpreter_problem(command: str) -> str:
    """Return a problem string if `command`'s executable cannot be run, else ""."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:  # unbalanced quotes
        return f"hook command is not parseable ({exc}): {command!r}"
    if not argv:
        return f"hook command is empty: {command!r}"

    exe = argv[0]
    if os.sep in exe or exe.startswith("."):
        # An absolute/relative path: it must exist AND be executable. This is the
        # check that was missing — an uninstalled interpreter's path is a perfectly
        # well-formed string that names nothing.
        if not os.path.isfile(exe):
            return (
                f"hook interpreter does not exist: {exe!r} — Claude Code FAILS OPEN "
                f"on an unlaunchable hook, so this gate would silently allow every tool"
            )
        if not os.access(exe, os.X_OK):
            return f"hook interpreter is not executable: {exe!r}"
    else:
        # A bare name resolved through PATH. Note the child's PATH may differ from
        # ours; an absolute path in the config is strongly preferred.
        if shutil.which(exe) is None:
            return f"hook interpreter {exe!r} is not on PATH"

    # The hook script itself must exist too — a valid interpreter pointed at a
    # missing script also produces a non-zero exit and the same silent fail-open.
    for arg in argv[1:]:
        if arg.endswith(".py") and not os.path.isfile(arg):
            return f"hook script does not exist: {arg!r}"
    return ""


def gate_config_problems(path: PathLike) -> List[str]:
    """Every reason `path` is unusable as a job gate `--settings` file.

    An empty list means the gate is structurally sound AND its hooks can actually be
    launched. Never raises — use this for reporting/repair (conftest, setup, a CLI);
    use :func:`verify_gate_config` at the point of launch.
    """
    p = os.fspath(path)
    if not os.path.isfile(p):
        return [f"gate config does not exist: {p!r}"]
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (ValueError, OSError) as exc:
        return [f"gate config is not readable JSON ({exc}): {p!r}"]
    if not isinstance(cfg, dict):
        return [f"gate config is not a JSON object: {p!r}"]

    problems: List[str] = []
    hooks = cfg.get("hooks")
    pretool = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    if not isinstance(pretool, list) or not pretool:
        return [f"gate config registers no PreToolUse hook: {p!r}"]

    matchers = {e.get("matcher") for e in pretool if isinstance(e, dict)}
    if "*" not in matchers:
        problems.append(
            f"gate config has no catch-all '*' matcher (matchers: {sorted(m for m in matchers if m)}) "
            f"— tools outside the listed matchers would be ungated"
        )

    commands = 0
    for entry in pretool:
        if not isinstance(entry, dict):
            problems.append(f"PreToolUse entry is not an object: {entry!r}")
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list) or not inner:
            problems.append(f"PreToolUse matcher {entry.get('matcher')!r} registers no hook")
            continue
        for hook in inner:
            if not isinstance(hook, dict) or hook.get("type") != "command":
                problems.append(f"unsupported hook entry (expected type 'command'): {hook!r}")
                continue
            command = hook.get("command")
            if not isinstance(command, str) or not command.strip():
                problems.append(f"hook has no command: {hook!r}")
                continue
            commands += 1
            bad = _interpreter_problem(command.strip())
            if bad:
                problems.append(bad)

    if not commands:
        problems.append(f"gate config declares no runnable hook command: {p!r}")
    return problems


def verify_gate_config(path: PathLike) -> None:
    """Raise :class:`GateConfigError` unless `path` is a usable, runnable job gate.

    Call this immediately before spawning a gated `claude -p`. If it raises, DO NOT
    LAUNCH: Claude Code would run the job with the gate silently absent.
    """
    problems = gate_config_problems(path)
    if problems:
        raise GateConfigError("; ".join(problems))


def repair_gate_config(config_path: PathLike, template_path: PathLike, python: str) -> bool:
    """Rewrite `config_path` from `template_path` iff the config is currently unusable.

    Returns True if it rewrote the file. The trigger is HEALTH, not existence — the bug
    this replaces was `if not path.exists()`, which is idempotent but never inspects what
    it is keeping, so a config naming a dead interpreter survived forever. Existence was
    standing in for correctness.

    No template, or a template that would produce an equally broken config, means we leave
    the file alone and report False: the caller (a conftest, setup) should surface the
    problems rather than silently install something that also does not work.
    """
    cfg, tmpl = os.fspath(config_path), os.fspath(template_path)
    if not gate_config_problems(cfg):
        return False
    if not os.path.isfile(tmpl):
        return False
    rendered = (
        open(tmpl, encoding="utf-8").read()
        .replace("__PYTHON__", python)
        .replace("__CONTROL_DIR__", os.path.dirname(os.path.abspath(cfg)))
    )
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return True


def main(argv: List[str]) -> int:
    """`python -m parsers.gate_check <config.json> [...]` — exit 1 if any gate is broken."""
    if not argv:
        print("usage: gate_check.py <gate-settings.json> [...]")
        return 2
    rc = 0
    for path in argv:
        problems = gate_config_problems(path)
        if problems:
            rc = 1
            print(f"BROKEN  {path}")
            for prob in problems:
                print(f"        - {prob}")
        else:
            print(f"ok      {path}")
    return rc


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(main(sys.argv[1:]))
