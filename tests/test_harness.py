"""The harness test suite is its selftest: one test per section, so a failure names the section.

    python -m unittest discover -s tests        (or: python -m pytest tests)

`generated` and `hook` check THIS checkout (agent files, ARCHITECTURE.md, the installed hook),
so they need `python .agentteam/bin/setup.py` to have run once.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".agentteam" / "bin"))
import selftest  # noqa: E402


class Harness(unittest.TestCase):
    def section(self, name: str) -> None:
        selftest.RESULTS.clear()
        selftest.run([name])
        failed = [f"{n}: {d}" for s, n, ok, d in selftest.RESULTS if not ok]
        self.assertTrue(selftest.RESULTS, f"section {name} recorded nothing")
        self.assertEqual(failed, [], "\n".join(failed))

    def test_guard(self):
        self.section("guard")

    def test_gates(self):
        self.section("gates")

    def test_integrity(self):
        self.section("integrity")

    def test_thrash(self):
        self.section("thrash")

    def test_flow(self):
        self.section("flow")

    def test_generated(self):
        self.section("generated")

    def test_hook(self):
        self.section("hook")


if __name__ == "__main__":
    unittest.main()
