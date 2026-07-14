"""Cost/budget accounting + the worker claim-gate (v0.2)."""
import importlib
import json


def _costs(tmp_path, monkeypatch):
    monkeypatch.setenv("STANDUP_ROOT", str(tmp_path))
    monkeypatch.delenv("STANDUP_DAILY_COST_CAP_USD", raising=False)
    (tmp_path / "control").mkdir(exist_ok=True)
    import parsers.paths as paths
    importlib.reload(paths)
    import parsers.costs as costs
    importlib.reload(costs)
    return costs, paths


def test_daily_total_sums_only_todays_jobs(tmp_path, monkeypatch):
    costs, _ = _costs(tmp_path, monkeypatch)
    today = costs._today()
    jobs = [
        {"finished_at": today + "T10:00:00", "result": {"cost_usd": 0.5}},
        {"finished_at": today + "T11:00:00", "result": {"cost_usd": 1.25}},
        {"finished_at": "2020-01-01T00:00:00", "result": {"cost_usd": 9.0}},  # old → excluded
        {"finished_at": today + "T12:00:00", "result": None},                 # no cost → 0
    ]
    monkeypatch.setattr(costs.db, "list_jobs", lambda **k: jobs)
    assert costs.daily_total() == 1.75
    s = costs.summary()
    assert s["spent_usd"] == 1.75 and s["jobs_today"] == 3 and s["blocked"] is False


def test_claim_gate_blocks_over_cap_and_on_kill_switch(tmp_path, monkeypatch):
    costs, paths = _costs(tmp_path, monkeypatch)
    today = costs._today()
    monkeypatch.setattr(costs.db, "list_jobs",
                        lambda **k: [{"finished_at": today + "T10:00:00", "result": {"cost_usd": 1.75}}])
    assert costs.claim_gate()["blocked"] is False                      # no cap yet
    (paths.control_dir() / "budget.json").write_text(json.dumps({"daily_cap_usd": 1.0}))
    g = costs.claim_gate()
    assert g["blocked"] is True and "cap" in g["reason"]               # 1.75 >= 1.0
    (paths.control_dir() / "budget.json").write_text(json.dumps({"daily_cap_usd": 100.0}))
    assert costs.claim_gate()["blocked"] is False                      # under the raised cap
    (paths.control_dir() / "kill_switch").write_text("")
    g = costs.claim_gate()
    assert g["blocked"] is True and "kill" in g["reason"]              # hard stop regardless of cap


def test_cap_usd_budget_json_beats_env(tmp_path, monkeypatch):
    costs, paths = _costs(tmp_path, monkeypatch)
    monkeypatch.setenv("STANDUP_DAILY_COST_CAP_USD", "9.0")
    assert costs.cap_usd() == 9.0
    (paths.control_dir() / "budget.json").write_text(json.dumps({"daily_cap_usd": 3.5}))
    assert costs.cap_usd() == 3.5   # runtime file wins
