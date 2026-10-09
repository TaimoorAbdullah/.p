"""Orchestrator: runs the negotiation loop, plan versioning, event injection, timeouts. Messages go through the bus (say)."""
import os, threading, time
from . import rules as X
from .agents import build_agents
from .llm import phrase
from .store import Store

MAX_ROUNDS = int(os.environ.get("ARES_MAX_ROUNDS", "8"))
TIMEOUT_S = int(os.environ.get("ARES_TIMEOUT_S", "180"))


def fresh():
    return dict(pool=[79, 52, 59, 26, 17], events=[], ver=0, plan=None, last=None, status="IDLE", scen="Baseline", round=0,
                lim=X.BASE_LIM, max_sac=X.BASE_SAC, vr=[], baselines=0, agents={}, running=False, policy="bal")


class Engine:
    def __init__(self, store: Store, delay=None):
        self.store, self.lock = store, threading.Lock()
        self.delay = float(os.environ.get("ARES_DELAY", "0.3")) if delay is None else delay
        self.S = store.get_state() or fresh(); self.S["running"] = False
        self.agents = build_agents(self.S.get("agents")); self.t0 = time.time()

    # ---- bus ----
    def _save(self):
        self.S["agents"] = {k: dict(cur=a.cur, refusals=a.refusals) for k, a in self.agents.items() if k != "CMD"}
        self.store.set_state(self.S)

    def say(self, frm, typ, text):
        if time.time() - self.t0 > TIMEOUT_S: raise TimeoutError(f"negotiation exceeded {TIMEOUT_S}s")
        a = self.agents.get(frm)
        text, src = phrase(a.name, a.goal, text) if a and typ in ("Proposal", "Counteroffer", "Objection", "Commitment") else (text, "rule")
        m = self.store.add(self.S["scen"], self.S["round"], self.S["plan"]["ver"] if self.S["plan"] else self.S["ver"], frm, typ, text, src)
        for ag in self.agents.values(): ag.observe(m)
        self._save(); time.sleep(self.delay)

    def rnd(self, n):
        if n > MAX_ROUNDS: raise RuntimeError("maximum round limit reached")
        self.S["round"] = n

    # ---- public actions ----
    def snapshot(self): return {**self.S, "agents": {k: dict(cur=a.cur, refusals=a.refusals, goal=a.goal) for k, a in self.agents.items() if k != "CMD"}}

    def start(self, pool, policy="bal", sync=False):
        with self.lock:
            if self.S["running"]: raise RuntimeError("already running")
            self.S.update(pool=list(pool), policy=policy, baselines=self.S["baselines"] + 1, plan=None, last=None, vr=[], events=[], running=True)
            self.S["scen"] = f"Baseline #{self.S['baselines']}"
            for a in self.agents.values():
                if hasattr(a, "cur"): a.cur = 0
        self._launch(lambda: self.negotiate(False, policy, False), sync)

    def inject(self, name, delta, allow_override=True, sync=False):
        with self.lock:
            if self.S["running"] or self.S["status"] != "APPROVED": raise RuntimeError("an approved plan is required before injecting an event")
            self.S["running"] = True
            old = self.S["plan"]
            self.S["pool"] = [max(0, a + b) for a, b in zip(self.S["pool"], delta)]
            self.S["events"].append(dict(name=name, delta=delta)); self.S["scen"] = f"Event: {name}"
        def job():
            self.t0 = time.time(); self.S["round"] = 0
            chk = X.validate(old, self.S["pool"], X.BASE_LIM, X.BASE_SAC); old["status"] = "STALE"; self.S["status"] = "STALE"
            why = "still fits but must be re-voted" if X.ok_all(chk) else "; ".join(c["r"] for c in chk if not c["ok"])
            self.say("CMD", "Event", f"EVENT '{name}' recorded: change {delta} -> pool {self.S['pool']}. Plan v{old['ver']} marked STALE ({why}). Voiding {len(old['commits'])} return commitment(s); owners must reconfirm.")
            self.negotiate(True, self.S.get("policy", "bal"), allow_override)
        self._launch(job, sync)

    def reset(self):
        with self.lock:
            self.store.reset(); self.S = fresh(); self.agents = build_agents()

    def _launch(self, fn, sync):
        def wrap():
            try: fn()
            finally: self.S["running"] = False; self._save()
        wrap() if sync else threading.Thread(target=wrap, daemon=True).start()

    # ---- negotiation ----
    def negotiate(self, event, policy, allow):
        S, ag = self.S, self.agents
        self.t0 = time.time(); S["status"] = "NEGOTIATING"; S["round"] = 0; S["lim"], S["max_sac"] = X.BASE_LIM, X.BASE_SAC
        sel = dict(S["last"]) if event and S["last"] else {k: 0 for k in X.K}
        for k in X.K: ag[k].cur = sel[k]
        try:
            self.rnd(1); self.say("CMD", "Event", f"Crisis published. Scenario: {S['scen']}. Pool {S['pool']}. Round limit {MAX_ROUNDS}, risk limit {S['lim']}, max sacrifices {S['max_sac']}.")
            for k in X.K: self.say(k, "Proposal", ag[k].proposal())
            over = self._over(sel)
            self.say("CMD", "Objection", f"Resource conflict: {', '.join(over)}. Risk {X.risk(sel)}." if over else f"Totals {X.tot(sel)} fit the pool; confirming.")
            self.rnd(2)
            for k in X.K:
                self.say(k, "Counteroffer", ag[k].counteroffer(bool(over))); sel[k] = ag[k].cur
            over = self._over(sel)
            self.say("CMD", "Objection", f"Still over the pool: {', '.join(over)}. Restricted is not enough." if over else "Combined packages fit the pool.")
            self.rnd(3)
            res = X.search(S["pool"], S["lim"], S["max_sac"], policy)
            if not res and event and allow:
                S["lim"], S["max_sac"] = X.OVERRIDE_LIM, X.OVERRIDE_SAC
                self.say("CMD", "Event", f"No plan under risk {X.BASE_LIM} / {X.BASE_SAC} sacrifice. Invoking Crisis Override: risk {X.OVERRIDE_LIM}, up to {X.OVERRIDE_SAC} Sacrifice modes (full returns required).")
                res = X.search(S["pool"], S["lim"], S["max_sac"], policy)
            if not res:
                why, need = X.infeasible(S["pool"]); S["status"], S["plan"], S["vr"] = "INFEASIBLE", None, [dict(n="Feasibility", ok=False, r=why)]
                return self.say("CMD", "Approval", f"INFEASIBLE. Blocking constraint: {why} Needed: {need}.")
            best = res[0]; sc = X.sacs(best); minr = X.risk(best)
            elig = [k for k in X.K if any(X.risk(s) == minr and s[k] == 2 for s in res)]
            if sc:
                self.say("CMD", "Objection", f"Requesting Sacrifice from: {', '.join(X.nm(k) for k in elig)}. Each needs a fair return.")
                for k in elig: self.say(k, "Objection", ag[k].refuse_sacrifice())
                self.say("CMD", "Counteroffer", f"Search of 81 package combos: lowest-risk valid plan (risk {minr}) needs {' + '.join(X.nm(k) for k in sc)} in Sacrifice. Policy: {policy}.")
            p = self._draft(best, X.make_commits(best))
            self.say("CMD", "Proposal", f"Plan v{p['ver']}: {' + '.join(X.pkg(k, best[k])[0] for k in X.K)}. Totals {X.tot(best)}, risk {X.risk(best)}.")
            self.say("Validator", "Approval", f"Plan v{p['ver']}: {'PASS' if X.ok_all(S['vr']) else 'FAIL'}. " + "; ".join(f"{c['n']}: {c['r']}" for c in S["vr"] if not c["ok"]))
            self.rnd(4)
            if not X.ok_all(S["vr"]):
                for c in p["commits"]:
                    c["ok"] = True; self.say(c["owner"], "Commitment", f"{X.nm(c['owner'])} -> {X.nm(c['ben'])}: {c['text']}. Expires: {c['exp']}.")
                p = self._draft(best, [dict(c) for c in p["commits"]])  # new version clears votes
                self.say("CMD", "Proposal", f"Revised plan v{p['ver']} with {len(p['commits'])} return commitments. Earlier votes cleared.")
                self.say("Validator", "Approval", f"Plan v{p['ver']}: {'PASS' if X.ok_all(S['vr']) else 'FAIL'}.")
            for k in X.K:
                vote, why = ag[k].vote(p, X.ok_all(X.validate(p, S["pool"], S["lim"], S["max_sac"])))
                p["votes"][k] = vote; self.say(k, "Vote-" + vote["v"], f"{vote['v']} plan v{p['ver']}: {why}")
            S["vr"] = X.validate(p, S["pool"], S["lim"], S["max_sac"], True)
            if ag["CMD"].can_approve(S["vr"]):
                p["status"], S["status"], S["last"] = "APPROVED", "APPROVED", dict(p["sel"])
                self.say("CMD", "Approval", f"APPROVED plan v{p['ver']}. Four ACCEPT votes and validation PASS. Reserve {[S['pool'][i] - X.tot(p['sel'])[i] for i in range(5)]}.")
            else:
                S["status"] = "DEADLOCK"; self.say("CMD", "Approval", f"DEADLOCK on v{p['ver']}: validation or votes failed.")
        except Exception as e:  # timeout / round limit / unexpected failure -> safe, labelled fallback
            S["status"] = "DEADLOCK"; self.store.add(S["scen"], S["round"], S["ver"], "CMD", "Approval", f"DEADLOCK (fallback): {e}", "rule-fallback")
        finally: self._save()

    def _over(self, sel):
        t = X.tot(sel); return [f"{X.R[i]} +{t[i] - self.S['pool'][i]}" for i in range(5) if t[i] > self.S["pool"][i]]

    def _draft(self, sel, commits):
        self.S["ver"] += 1
        p = dict(ver=self.S["ver"], sel=dict(sel), commits=commits, votes={}, status="DRAFT"); self.S["plan"] = p
        self.S["vr"] = X.validate(p, self.S["pool"], self.S["lim"], self.S["max_sac"]); return p
