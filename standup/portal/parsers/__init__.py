"""Tolerant parsers for the team-status portal (Phase 1, read-only).

Each module parses one artifact off disk. The hard rule across all of them:
NEVER crash on a parse miss. On any failure to find an expected marker we
degrade to returning whatever we have (often the raw block / raw text), plus a
``_parse_warnings`` list so the caller can surface a "stale/degraded" flag
instead of a 500.
"""

from . import paths  # noqa: F401
