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
