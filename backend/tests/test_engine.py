import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import rules as X
from app.engine import Engine
from app.store import Store


def eng(): return Engine(Store(":memory:"), delay=0)


def test_restricted_fails_and_baseline_paths_valid():
    assert X.tot(dict(LS=1, MED=1, FOOD=1, ENG=1)) == [83, 51, 60, 30, 18]
    for sel in (dict(LS=2, MED=1, FOOD=1, ENG=1), dict(LS=1, MED=1, FOOD=1, ENG=2)):
        p = dict(sel=sel, ver=1, commits=[dict(c, ok=True) for c in X.make_commits(sel)], votes={})
        assert X.ok_all(X.validate(p, [79, 52, 59, 26, 17], 24, 1))


def test_validator_blocks_missing_returns_and_stale_votes():
    sel = dict(LS=2, MED=1, FOOD=1, ENG=1); pool = [79, 52, 59, 26, 17]
    p = dict(sel=sel, ver=1, commits=X.make_commits(sel), votes={})
    assert not X.ok_all(X.validate(p, pool, 24, 1))
    p["commits"] = [dict(c, ok=True) for c in p["commits"]]; p["votes"] = {k: dict(v="ACCEPT", ver=0) for k in X.K}
    assert not X.ok_all(X.validate(p, pool, 24, 1, True))


def test_baseline_run_and_event():
    e = eng(); e.start([79, 52, 59, 26, 17], "bal", sync=True)
    s = e.snapshot(); assert s["status"] == "APPROVED" and s["round"] >= 3
    msgs = e.store.messages(); types = [m["type"] for m in msgs]
    assert "Objection" in types and types.count("Commitment") >= 2 and types.count("Vote-ACCEPT") == 4
    assert sum(1 for m in msgs if "REFUSE" in m["text"]) >= 1
    e.inject("Solar aftershock", [-4, 0, 0, 0, 0], True, sync=True)
    s = e.snapshot(); assert s["status"] == "APPROVED" and s["plan"]["ver"] > 2


def test_infeasible_and_inject_guard():
    e = eng(); e.start([10, 10, 10, 10, 10], "bal", sync=True)
    assert e.snapshot()["status"] == "INFEASIBLE"
    try: e.inject("x", [-1, 0, 0, 0, 0], sync=True); assert False
    except RuntimeError: pass
