"""Five agents. Each has its own goal, state and message history and decides independently."""
from .rules import K, M, GOAL, LOSS, pkg, mode_txt, ok_all, nm


class Agent:
    def __init__(self, id, cur=0, refusals=0):
        self.id, self.name, self.goal = id, M[id][0], GOAL[id]
        self.cur, self.refusals, self.history = cur, refusals, []

    def observe(self, msg): self.history.append(msg["id"])

    def proposal(self):
        m = pkg(self.id, self.cur)
        return f"Requesting {m[0]} (P{m[1][0]} W{m[1][1]} O{m[1][2]} R{m[1][3]} B{m[1][4]}). {mode_txt(self.id, self.cur)}. Goal: {self.goal}."

    def counteroffer(self, conflict):
        if conflict and self.cur < 1:
            self.cur = 1
            return f"Switching to {pkg(self.id, 1)[0]} (risk 5). {mode_txt(self.id, 1)}."
        return f"Holding {pkg(self.id, self.cur)[0]}; cannot reduce further without serious damage."

    def refuse_sacrifice(self):
        self.refusals += 1
        return f"REFUSE Sacrifice ({pkg(self.id, 2)[0]}): {LOSS[self.id]}. Only with two return commitments from different agents."

    def vote(self, plan, validation_ok):
        """Independent vote on one exact plan version."""
        returns = sum(1 for c in plan["commits"] if c["ben"] == self.id and c["ok"])
        ok = validation_ok and (plan["sel"][self.id] != 2 or returns >= 2)
        why = ("Returns recorded; accepting the loss." if plan["sel"][self.id] == 2 else "My mode is protected within limits.") if ok else "Validator or returns not satisfied."
        return dict(v="ACCEPT" if ok else "REJECT", ver=plan["ver"]), why


class Commander:
    """Owns plan versions. Cannot approve without validator PASS and four matching ACCEPT votes."""
    id, name, goal = "CMD", "Commander", "Coordinate and approve only validated, fully-voted plans"

    def __init__(self): self.history = []
    def observe(self, msg): self.history.append(msg["id"])

    @staticmethod
    def can_approve(checks_with_votes): return ok_all(checks_with_votes)


def build_agents(saved=None):
    saved = saved or {}
    a = {k: Agent(k, **saved.get(k, {})) for k in K}
    a["CMD"] = Commander()
    return a
