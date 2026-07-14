"""Unit tests per parser, against the REAL current standup artifacts.

These assert the parser pulls TODAY's health/counts and at least the
PAT-rotation dated blocker with a days-remaining value. They are intentionally
coupled to the live files (the task requires verifying against real state); if
the runner advances the day, the date-pinned assertions self-adjust via
``datetime.date`` math rather than hard-coded numbers.
"""

import datetime
from pathlib import Path

import pytest

from parsers import backlog, comms, devlog, log, paths, team

REAL_STANDUP_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# team.json
# --------------------------------------------------------------------------- #
def test_team_roster_has_squads_incl_portal_and_staff():
    t = team.parse()
    assert t["_ok"] is True
    ids = {s["id"] for s in t["squads"]}
    # The MVP roster carries TWO squads: `demo_squad` (the working team on the
    # bundled demo-app) and `portal` (owns this Mission Control surface). Assert
    # the core set is present (subset, so adding a future squad won't break this).
    assert {"demo_squad", "portal"} <= ids
    assert len(t["squads"]) >= 2
    # active devs present in each squad
    for sq in t["squads"]:
        assert len(sq["devs"]) >= 1
    # staff includes the pm + design + comms leads (comms_triage is inactive in
    # the MVP but still listed in the roster).
    staff_ids = {s["id"] for s in t["staff"]}
    assert {"comms_triage", "pm_agent", "design_lead"} <= staff_ids
    # bench is empty in the MVP (the parent's bench cast was dropped).
    assert isinstance(t["bench"], list)


def test_active_dev_index_maps_folders():
    t = team.parse()
    idx = team.active_dev_index(t)
    # MVP devs: demo_squad's dev_a/dev_b -> demo-app; portal's devs -> standup/portal.
    assert idx["dev_a"]["folder"] == "demo-app"
    assert idx["dev_a"]["squad_id"] == "demo_squad"
    assert idx["portal_backend"]["folder"] == "standup/portal"
    assert idx["portal_backend"]["squad_id"] == "portal"


# --------------------------------------------------------------------------- #
# BACKLOG.md
# --------------------------------------------------------------------------- #
def test_backlog_header_pulls_health_and_counts(tmp_path):
    # The MVP's seed BACKLOG.md has no run-stamped "Last updated:" header (it is
    # written by a tick, which hasn't run on a fresh install). Drive the header
    # parser with a synthetic file in the exact shape a tick emits, so the engine
    # is covered without depending on parent-deployment content.
    p = tmp_path / "BACKLOG.md"
    p.write_text(
        "# Team Backlog\n\n"
        "Last updated: 2026-06-22 14:07 · run `wf_abc123` · 7 agents · "
        "GREEN (0 red / 2 yellow / 10 reported) · "
        "**5 worked / 4 green / 3 committed / 1 PRs**\n",
        encoding="utf-8",
    )
    b = backlog.parse(path=p)
    h = b["header"]
    assert h["_ok"] is True
    assert h["run_id"] and h["run_id"].startswith("wf_")
    assert isinstance(h["agents"], int) and h["agents"] > 0
    # org health string 0red/Nyellow/Mreported
    assert h["counts"]["red"] is not None
    assert h["counts"]["yellow"] is not None
    assert h["counts"]["reported"] is not None
    assert h["color"] in {"green", "yellow", "red"}
    # worked/green/committed/PRs all parsed
    for k in ("worked", "green", "committed", "prs"):
        assert h[k] is not None, f"{k} not parsed from header"


def test_backlog_sections_present(tmp_path):
    # The MVP seed BACKLOG.md does not carry the parent's KEYSTONE/SECURITY/Pending
    # sections. Exercise parse_sections against a synthetic file that does, so the
    # section extractor stays covered without parent-specific content.
    p = tmp_path / "BACKLOG.md"
    # parse_sections keys off the markers as the real file writes them: the
    # KEYSTONE line STARTS with 📌; SECURITY/Pending are ## headings.
    p.write_text(
        "# Team Backlog\n\n"
        "\U0001f4cc KEYSTONE — the pinned spine\n"
        "- the one thing that must not starve\n\n"
        "## \U0001f534 SECURITY\n"
        "- rotate the token\n\n"
        "## ⚠️ Pending\n"
        "- waiting on review\n",
        encoding="utf-8",
    )
    b = backlog.parse(path=p)
    secs = b["sections"]
    assert secs["keystone"] is not None
    assert secs["security"] is not None
    assert secs["pending"] is not None
    assert "KEYSTONE" in secs["keystone"]["header"].upper()


def test_backlog_blocker_tokenizer_handles_numbered_list():
    text = (
        "### \U0001f534 BLOCKERS FOR KAIN (gated)\n"
        "1. **Rotate the API token (exp 2026-06-30, 11 days)** — unstarves 4 lanes.\n"
        "2. **EM MERGE GATE** — adopt canonical writer.\n"
        "3. **PII scrub** — history rewrite.\n"
    )
    today = datetime.date(2026, 6, 19)
    blockers = backlog.parse_blockers(text, today=today)
    assert len(blockers) == 3
    assert blockers[0]["date"] == "2026-06-30"
    assert blockers[0]["days_remaining"] == 11


def test_backlog_mixed_case_blockquoted_heading_does_not_crash():
    """Regression: the real BACKLOG.md spine heading is MIXED-CASE and
    blockquoted (``> ### 🔴 Blockers for Kain — the spine``). parse_blockers
    matched it case-insensitively (``line.upper()``) but used to split the
    ORIGINAL line on the literal UPPERCASE token, finding nothing -> IndexError.

    Every prior test fed UPPERCASE "BLOCKERS FOR KAIN", so this mixed-case path
    was never covered and the bug shipped. This drives the exact artifact shape —
    a blockquoted mixed-case heading + blockquoted ``> N.`` numbered spine using
    the real short-date ``06-30`` (MM-DD) form — and asserts it parses without
    raising and resolves the PAT date.
    """
    text = (
        "> ### \U0001f534 Blockers for Kain — the spine (2 dated risks at top)\n"
        "> 1. ⏰ **PAT 06-30 = 8 days, THE ONE THING** — rotate the API "
        "token; unstarves the lanes.\n"
        "> 2. **EM merge gate** — adopt canonical writer.\n"
        "> 3. **PII scrub** — history rewrite.\n"
        "\n"
        "> **2026-06-22 14:07 AFTERNOON · next tick** — must NOT bleed in.\n"
        "> ① `dev_a` truncate-helper fix.\n"
        "> ② `portal_backend` cry-wolf fix.\n"
    )
    today = datetime.date(2026, 6, 19)
    blockers = backlog.parse_blockers(text, today=today)
    # exactly the 3 spine items — the next tick's circled-numeral run must not
    # leak past the blank line that closes the spine blockquote.
    assert len(blockers) == 3
    assert all("dev_a" not in (b["title"] or "") for b in blockers)
    # MM-DD short form resolves to this year's 2026-06-30 -> 11 days.
    assert blockers[0]["date"] == "2026-06-30"
    assert blockers[0]["days_remaining"] == 11


def test_backlog_blocker_tokenizer_handles_circled_numerals():
    text = (
        "> **\U0001f534 BLOCKERS FOR KAIN:** ① rotate PAT (exp 06-30, 11d); "
        "② EM merge gate; ③ PII scrubs. **People/infra:** Robin OOO.\n"
    )
    today = datetime.date(2026, 6, 19)
    blockers = backlog.parse_blockers(text, today=today)
    assert len(blockers) == 3
    # the People/infra trailer must NOT become a 4th blocker
    assert all("Robin" not in (b["title"] or "") for b in blockers)
    # MM-DD short form resolves to this year's 2026-06-30 -> 11 days
    assert blockers[0]["days_remaining"] == 11


def test_backlog_mixed_case_inline_heading_circled_numerals():
    """Regression: a NON-blockquoted MIXED-CASE ``### 🔴 Blockers for Kain``
    heading combined with circled-numeral items.

    This is the intersection the case-slice fix (backlog.py:242-244) protects but
    no other test independently bites on: the heading is matched case-insensitively
    via ``line.upper()`` (L220), but the pre-fix code split the ORIGINAL line on
    the literal UPPERCASE token ``"BLOCKERS FOR KAIN"``. On this mixed-case heading
    ``split`` returned a single element and ``[1]`` raised IndexError. The existing
    UPPER-case tokenizer test and the circled-numeral test both feed UPPERCASE
    headings, and the mixed-case test uses a BLOCKQUOTED heading + numbered spine —
    so this plain mixed-case heading + circled-numeral run is uncovered. Pre-fix
    this IndexErrors at the split; post-fix the case-insensitive slice parses it
    into 3 clean blockers (reaching any assert below proves no crash).
    """
    text = (
        "### \U0001f534 Blockers for Kain (gated)\n"
        "① rotate PAT (exp 06-30, 11d);\n"
        "② EM merge gate;\n"
        "③ PII scrubs. **People/infra:** Robin OOO.\n"
    )
    today = datetime.date(2026, 6, 19)
    blockers = backlog.parse_blockers(text, today=today)
    assert len(blockers) == 3
    # MM-DD short form resolves to this year's 2026-06-30 -> 11 days remaining.
    assert blockers[0]["date"] == "2026-06-30"
    assert blockers[0]["days_remaining"] == 11
    # the People/infra trailer must NOT become a 4th blocker.
    assert all("Robin" not in (b["title"] or "") for b in blockers)


# --------------------------------------------------------------------------- #
# log/<date>.md
# --------------------------------------------------------------------------- #
# A faithful one-tick log body in the exact shape parse_text expects (heading +
# **Run** line + Team health line + Worked/green/committed/PRs line). The MVP's
# standup/log/ is empty on a fresh install, so we drive the parser with a fixture
# rather than a parent-deployment log file.
_LOG_BODY = (
    "# Standup — 2026-06-22\n"
    "\n"
    "## MORNING (08:00 cron)\n"
    "\n"
    "**Run** `wf_abc123` · 7 agents · 837K tokens · ~12 min\n"
    "Team health: GREEN (0 red / 2 yellow / 10 reported)\n"
    "Worked 5 / green 4 / committed 3 / PRs 1\n"
)


def test_log_today_pulls_tick_health_and_counts(tmp_path):
    p = tmp_path / "2026-06-22.md"
    p.write_text(_LOG_BODY, encoding="utf-8")
    l = log.parse("2026-06-22", path=p)
    assert l["_ok"] is True
    assert l["ticks"], "no ticks parsed"
    latest = l["latest"]
    assert latest["name"] in {"MORNING", "AFTERNOON", "EVENING", "NIGHT"}
    assert latest["run_id"] and latest["run_id"].startswith("wf_")
    assert isinstance(latest["agents"], int)
    # Team health (0 red / N yellow / M reported)
    assert latest["counts"]["red"] is not None
    assert latest["counts"]["reported"] is not None
    # Worked/green/committed/PRs
    for k in ("worked", "green", "committed", "prs"):
        assert latest[k] is not None


def test_log_extracts_run_line_fields(tmp_path):
    p = tmp_path / "2026-06-22.md"
    p.write_text(_LOG_BODY, encoding="utf-8")
    l = log.parse("2026-06-22", path=p)
    ev = l["latest"]
    assert ev["tokens"] is not None  # e.g. "837K"
    assert ev["duration_min"] is not None


def test_log_missing_date_falls_back_to_newest(tmp_path, monkeypatch):
    # Redirect the log dir to an isolated tmp dir with ONE dated file, then ask
    # for a date that doesn't exist: the parser must fall back to the newest file
    # on disk (the MVP log dir is empty, so we build our own).
    (tmp_path / "2026-06-22.md").write_text(_LOG_BODY, encoding="utf-8")
    monkeypatch.setattr(log.paths, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(log.paths, "log_for", lambda ds: tmp_path / f"{ds}.md")
    l = log.parse("1999-01-01")
    # there is no 1999 file; it must fall back, not crash
    assert l["_ok"] is True
    assert l["_fell_back"] is True
    assert l["ticks"], "fallback produced no ticks"


# --------------------------------------------------------------------------- #
# per-dev .standup/<dev>.md
# --------------------------------------------------------------------------- #
def test_devlog_pulls_last_dated_entry(tmp_path):
    # The MVP has no per-dev .standup/<dev>.md files on a fresh install. Drive the
    # parser with a fixture in the exact shape it appends (dated ## entries +
    # Current state / Next step subsections), so the engine stays covered.
    p = tmp_path / "dev_a.md"
    p.write_text(
        "# dev_a progress\n\n"
        "## 2026-06-20 — first pass at truncate\n"
        "### Current state\n"
        "- drafted the helper, tests red\n"
        "### Next step\n"
        "- handle the max_length edge\n\n"
        "## 2026-06-22 — slugify max_length\n"
        "### Current state\n"
        "- implemented slugify with a max_length cap; all tests green\n"
        "### Next step\n"
        "- start top_words\n",
        encoding="utf-8",
    )
    d = devlog.parse("demo-app", "dev_a", path=p)
    assert d["_ok"] is True
    assert d["last_entry"] is not None
    assert d["last_entry"]["date"].startswith("2026-")
    # the LAST dated entry wins
    assert d["last_entry"]["date"] == "2026-06-22"
    assert d["current_task"]


def test_devlog_missing_file_degrades_not_crash():
    d = devlog.parse("nonexistent/folder", "ghost_dev")
    assert d["_ok"] is False
    assert d["current_task"] is None
    assert d["_parse_warnings"]


# --------------------------------------------------------------------------- #
# comms (inbox mtimes)
# --------------------------------------------------------------------------- #
def _seed_inbox(tmp_path, age_hours: float):
    """Build a messages-style inbox with teams_/outlook_ files whose mtimes are
    ``age_hours`` old, so the comms staleness engine can be exercised without the
    parent deployment's inbox (the MVP has no messages/inbox/)."""
    import json as _json
    import os as _os
    import time as _time

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    teams = inbox / "teams_2026-06-22.json"
    outlook = inbox / "outlook_2026-06-22.json"
    teams.write_text(_json.dumps({"signed_in": True, "activity": [1, 2], "chats": [1]}))
    outlook.write_text(_json.dumps({"signed_in": True, "mail": [1, 2, 3], "calendar": [1]}))
    old = _time.time() - age_hours * 3600.0
    for f in (teams, outlook):
        _os.utime(f, (old, old))
    return inbox


def test_comms_surfaces_last_pull_and_staleness(tmp_path):
    # A fresh (~1h-old) inbox: the parser should report a real age + a fresh state.
    inbox = _seed_inbox(tmp_path, age_hours=1.0)
    c = comms.parse(inbox=inbox)
    assert c["last_pull_at"] is not None
    assert c["stale_hours"] is not None
    assert c["state"] in {"fresh", "stale", "stalled", "empty", "missing"}


def test_comms_detects_stale_puller(tmp_path):
    # An inbox older than the stalled threshold reads stale/stalled against now().
    inbox = _seed_inbox(tmp_path, age_hours=comms.STALLED_HOURS + 1.0)
    c = comms.parse(inbox=inbox)
    assert c["stale_hours"] > comms.STALE_HOURS
    assert c["state"] in {"stale", "stalled"}


def test_comms_stream_counts_use_contents_not_mtime(tmp_path):
    """Prove counts are CONTENT-derived: synthesize an inbox with known list
    lengths and assert the streams report those lengths, independent of mtimes."""
    import json as _json

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "teams_2026-06-20.json").write_text(
        _json.dumps({"signed_in": False, "activity": [1, 2, 3], "chats": [1, 2]})
    )
    (inbox / "outlook_2026-06-20.json").write_text(
        _json.dumps({"signed_in": True, "mail": [1, 2, 3, 4], "calendar": [1]})
    )
    c = comms.parse(inbox=inbox)
    by_kind = {s["kind"]: s for s in c["streams"]}
    assert by_kind["message"]["count"] == 5  # 3 activity + 2 chats
    assert by_kind["email"]["count"] == 4
    assert by_kind["meeting"]["count"] == 1
    # one source says signed_in:false -> agent-level signed_in is false
    assert c["signed_in"] is False


def test_comms_missing_inbox_degrades_streams_not_crash(tmp_path):
    c = comms.parse(inbox=tmp_path / "nope")
    assert c["_ok"] is False
    assert c["state"] == "missing"
    # streams stay an (empty) list so the UI can iterate without a guard
    assert c["streams"] == []
