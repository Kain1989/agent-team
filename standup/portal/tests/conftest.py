"""Pytest config — make the portal package importable from the tests dir.

We add the portal root (parent of tests/) to sys.path so `import app` and
`from parsers import ...` resolve regardless of where pytest is invoked.
"""

import sys
from pathlib import Path

import pytest

PORTAL_ROOT = Path(__file__).resolve().parents[1]
if str(PORTAL_ROOT) not in sys.path:
    sys.path.insert(0, str(PORTAL_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _ensure_gate_configs():
    """Generate control/job_*_gate.json from their .template files if absent.

    setup.sh does this on a real install (baking in the host's python + control dir);
    CI and a fresh checkout don't run setup.sh, so the gate-config files (gitignored,
    since they hold machine-specific paths) are missing and the gate tests — plus any
    code path that opens the --settings file — fail with FileNotFoundError. We generate
    them here so the suite is hermetic. Idempotent (only when absent); RUNTIME stays
    strict — we never fall back to the placeholder template when actually launching a job.
    """
    control = PORTAL_ROOT.parent / "control"
    for name in ("job_code_gate", "job_readonly_gate"):
        j = control / f"{name}.json"
        tmpl = control / f"{name}.json.template"
        if not j.exists() and tmpl.exists():
            j.write_text(
                tmpl.read_text(encoding="utf-8")
                .replace("__PYTHON__", sys.executable)
                .replace("__CONTROL_DIR__", str(control)),
                encoding="utf-8",
            )
    yield


# A POPULATED roster for the tests that exercise parsing/serving a roster.
#
# standup/team.json now ships with `teams: []` on purpose — a fresh install has no project until
# /add-project creates one. Tests that assert on squads were reading that shipped file, so they
# began asserting against emptiness rather than against the parser. This fixture gives them a
# realistic roster in a tmp STANDUP_ROOT; tests that are ABOUT the shipped file (its shape, its
# canonical policy) deliberately do not use it.
@pytest.fixture
def populated_root(tmp_path, monkeypatch):
    import json
    real = PORTAL_ROOT.parent                      # the shipped standup/ dir
    root = tmp_path / "standup"
    root.mkdir()
    shipped = json.loads((real / "team.json").read_text(encoding="utf-8"))
    shipped["teams"] = [{
        "id": "portal", "name": "Team Portal Squad",
        "mission": "Owns the local Mission Control portal.",
        "coordination": "Two paired developer-agents who critique each other in fresh context.",
        "review_surface": {"kind": "web", "label": "Mission Control (local)",
                           "url": "http://127.0.0.1:8770",
                           "inspect": "bash standup/control/inspect_portal.sh",
                           "how": "Run from the project root."},
        "developers": [
            {"id": "portal_backend", "folder": "standup/portal", "role": "Backend",
             "stack": "python", "git": True, "active": True, "pair": "portal_frontend",
             "focus": "the read+job API", "tests": "pytest"},
            {"id": "portal_frontend", "folder": "standup/portal", "role": "Frontend",
             "stack": "js", "git": True, "active": True, "pair": "portal_backend",
             "focus": "the single-window page", "tests": "the API contract tests"},
        ]}]
    (root / "team.json").write_text(json.dumps(shipped, indent=2), encoding="utf-8")
    for sub in ("log", "control"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("STANDUP_ROOT", str(root))
    return root
