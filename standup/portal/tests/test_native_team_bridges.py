"""Native-agent-team integration bridges (v0.2):
  (1) roster -> .claude/agents/*.md generator   (parsers/agents_gen)
  (3) portal observes native teams              (parsers/native_teams)
  (2) governance hooks on the native lifecycle  (control/team_*_hook.py)
"""
import json
import subprocess
import sys
from pathlib import Path

PORTAL_ROOT = Path(__file__).resolve().parents[1]
CONTROL = PORTAL_ROOT.parent / "control"


# --------------------------------------------------------------------------- (1)
def _roster():
    return {
        "teams": [{
            "name": "Demo Squad", "mission": "ship textkit",
            "developers": [
                {"id": "dev_a", "role": "Builder", "focus": "features", "git": True,
                 "pair": "dev_b", "tests": "pytest", "active": True},
                {"id": "dev_b", "role": "Reviewer", "focus": "review", "git": True,
                 "pair": "dev_a", "active": True},
                {"id": "old_dev", "role": "gone", "active": False},
            ],
        }],
        "staff": [
            {"id": "pm_agent", "role": "PM", "focus": "scope", "git": False, "active": True},
            {"id": "sleeping", "role": "Inactive", "active": False},
        ],
    }


def test_agents_gen_writes_active_roles_with_right_tools(tmp_path):
    from parsers import agents_gen
    tj = tmp_path / "team.json"
    tj.write_text(json.dumps(_roster()), encoding="utf-8")
    out = tmp_path / ".claude" / "agents"
    written = agents_gen.generate(tj, out)

    assert set(written) == {"dev_a", "dev_b", "pm_agent"}          # only ACTIVE
    assert not (out / "old_dev.md").exists()
    assert not (out / "sleeping.md").exists()

    dev = (out / "dev_a.md").read_text(encoding="utf-8")
    assert dev.startswith("---\n") and "name: dev_a" in dev
    assert "Edit, Write, Bash" in dev                              # git dev -> edit tools
    assert "pair is" in dev and "dev_b" in dev                     # pair wired in

    pm = (out / "pm_agent.md").read_text(encoding="utf-8")
    assert "tools: Read, Grep, Glob, LS\n" in pm                   # non-git staff -> read-only


def test_agents_gen_prunes_stale_but_keeps_handwritten(tmp_path):
    from parsers import agents_gen
    tj = tmp_path / "team.json"
    tj.write_text(json.dumps(_roster()), encoding="utf-8")
    out = tmp_path / ".claude" / "agents"
    agents_gen.generate(tj, out)
    # a hand-written agent (no generated header) must survive a regen
    hand = out / "human_helper.md"
    hand.write_text("---\nname: human_helper\n---\nkeep me\n", encoding="utf-8")
    # drop dev_b from the roster, regenerate
    data = _roster()
    data["teams"][0]["developers"] = [d for d in data["teams"][0]["developers"] if d["id"] != "dev_b"]
    tj.write_text(json.dumps(data), encoding="utf-8")
    agents_gen.generate(tj, out)

    assert not (out / "dev_b.md").exists()    # generated + dropped -> pruned
    assert hand.exists()                      # hand-written -> kept


# --------------------------------------------------------------------------- (3)
def test_native_teams_reads_teams_and_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    import importlib
    import parsers.native_teams as nt
    importlib.reload(nt)

    assert nt.summary() == {"teams": [], "team_count": 0, "total_members": 0, "total_tasks": 0}

    team = "session-abc123"
    (tmp_path / "teams" / team).mkdir(parents=True)
    (tmp_path / "teams" / team / "config.json").write_text(json.dumps({
        "lead": "lead-session",
        "members": [{"name": "Dev A", "agent_type": "dev_a", "agent_id": "a1"},
                    {"name": "Dev B", "agent_type": "dev_b", "agent_id": "b1"}],
    }), encoding="utf-8")
    tdir = tmp_path / "tasks" / team
    tdir.mkdir(parents=True)
    (tdir / "t1.json").write_text(json.dumps(
        {"id": "t1", "status": "completed", "description": "build truncate",
         "assigned_teammate": {"agent_type": "dev_a"}}), encoding="utf-8")
    (tdir / "t2.json").write_text(json.dumps(
        {"id": "t2", "status": "in_progress", "description": "review it", "assignee": "dev_b"}),
        encoding="utf-8")

    s = nt.summary()
    assert s["team_count"] == 1 and s["total_members"] == 2 and s["total_tasks"] == 2
    t = s["teams"][0]
    assert t["team"] == team and t["member_count"] == 2
    assert t["task_counts"] == {"total": 2, "pending": 0, "in_progress": 1, "completed": 1}
    assert {tk["assignee"] for tk in t["tasks"]} == {"dev_a", "dev_b"}


# --------------------------------------------------------------------------- (2)
def _run_hook(name, payload, env=None):
    return subprocess.run(
        [sys.executable, str(CONTROL / name)],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )


def test_task_completed_hook_blocks_secret_allows_clean():
    clean = _run_hook("team_task_completed_hook.py",
                      {"task_diff": "+def f(x):\n+    return x[:3]\n"})
    assert clean.returncode == 0

    leak = _run_hook("team_task_completed_hook.py",
                     {"task_diff": "+KEY = 'AKIA" + "ABCDEFGHIJKLMNOP'\n"})
    assert leak.returncode == 2 and "guardrail" in leak.stderr.lower()

    empty = _run_hook("team_task_completed_hook.py", {"task_diff": ""})
    assert empty.returncode == 0          # nothing to scan -> allow


def test_task_created_and_idle_hooks_honor_kill_switch(tmp_path, monkeypatch):
    import os
    env = {**os.environ, "STANDUP_ROOT": str(tmp_path)}
    (tmp_path / "control").mkdir()

    # no kill switch -> a normal task is allowed, idle is a no-op
    assert _run_hook("team_task_created_hook.py", {"task_description": "add a helper"}, env).returncode == 0
    assert _run_hook("team_teammate_idle_hook.py", {"agent_type": "dev_a"}, env).returncode == 0

    # kill switch ON -> creation blocked, idle stops the teammate
    (tmp_path / "control" / "kill_switch").touch()
    created = _run_hook("team_task_created_hook.py", {"task_description": "add a helper"}, env)
    assert created.returncode == 2 and "kill switch" in created.stderr.lower()
    idle = _run_hook("team_teammate_idle_hook.py", {"agent_type": "dev_a"}, env)
    assert idle.returncode == 0 and json.loads(idle.stdout)["continue"] is False
