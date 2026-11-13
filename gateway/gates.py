"""The decision ladder: every request ends in exactly one action, decided by the first gate it fails.

    0  Is it a purchase?             no  -> ANSWER (a question about products) or DECLINE (anything else)
    1  Clear: do we know exactly what is wanted?           no -> ASK BACK
    2  Possible: can Valio supply it at a fixed price?     no -> ASK BACK, with the nearest alternative
    3  Allowed: does it keep every hard rule?               no -> STOP
    4  Proven: is every fact it relies on backed by data?   no -> A PERSON CHECKS
    5  Agent's call: is it within the agent's authority?    no -> A PERSON APPROVES
       all yes                                                  -> ORDER

Why it is mutually exclusive and complete: the gates are asked in a fixed order and the first "no" ends the
run, so every request leaves through exactly one exit (or passes them all and is ordered). The order is the
order of dependency: you can't look for a product before you know what is wanted, can't apply rules before
there is an offer, and authority only matters for an offer that is allowed and proven.

Each exit names who acts next: the requester (1, 2), nobody (3), a person checking facts (4), a person with
authority (5), or the agent itself (order). An order with several lines takes the earliest "no" of any line.

Gates 1-2 run in the buyer agent (it holds the request text). Gates 3-5 run in Valio's gateway on every
order, whatever agent sent it, so a fooled or hostile agent can't skip them.
"""

GATES = [
    {"n": 0, "key": "purchase", "question": "Is it a purchase?", "no": "answer_or_decline"},
    {"n": 1, "key": "clear", "question": "Do we know exactly what is wanted?", "no": "ask_back"},
    {"n": 2, "key": "possible", "question": "Can Valio supply it at a fixed price?", "no": "ask_back"},
    {"n": 3, "key": "allowed", "question": "Does it keep every hard rule?", "no": "stop"},
    {"n": 4, "key": "proven", "question": "Is every fact it relies on backed by data?", "no": "person_checks"},
    {"n": 5, "key": "authority", "question": "Is it within the agent's own authority?", "no": "person_approves"},
]
BY_KEY = {g["key"]: g for g in GATES}

ACTIONS = {
    "answer": "Answer the question (nothing is ordered)",
    "decline": "Decline: not a purchasing request",
    "ask_back": "Ask the requester (nothing is ordered)",
    "stop": "Stop: a hard rule forbids it",
    "person_checks": "A person checks the facts before it is placed",
    "person_approves": "A person with authority approves it",
    "order": "Order placed by the agent",
}

# Every policy rule belongs to exactly one gate.
RULE_GATE = {
    # 3 Allowed: hard rules. Breaking one stops the order; no person can approve it.
    "Mandate valid": "allowed",
    "Hard spend limit": "allowed",
    "Forbidden allergens": "allowed",
    "Required claims": "allowed",
    "Substitutions allowed": "allowed",
    "Matches the buyer's request": "allowed",
    # 4 Proven: the order relies on something the data can't confirm.
    "Allergens verifiable from data": "proven",
    "Claims verified in product data": "proven",
    "Stock confirmed by Valio": "proven",
    "Delivery history known": "proven",
    # 5 Authority: allowed and proven, but a compromise or a size the agent may not decide alone.
    "Substitutions within preferences": "authority",
    "Delivery on time": "authority",
    "Full quantity": "authority",
    "Delivery reliability": "authority",
    "Unstated details within tolerance": "authority",
    "Auto-approve spend limit": "authority",
}


def gate_of(rule_name):
    return RULE_GATE.get(rule_name, "authority")  # an unknown rule can never make an order more autonomous


def ladder(failed_gate=None, action=None, reason=""):
    """The full ladder for display: which gates passed, which one decided, which were never reached."""
    order = [g["key"] for g in GATES]
    stop_at = order.index(failed_gate) if failed_gate else len(order)
    steps = []
    for i, g in enumerate(GATES):
        if g["key"] == "purchase" and failed_gate != "purchase" and i < stop_at:
            state = "pass"
        else:
            state = "pass" if i < stop_at else ("fail" if i == stop_at else "not_reached")
        steps.append({"n": g["n"], "key": g["key"], "question": g["question"], "state": state})
    action = action or (BY_KEY[failed_gate]["no"] if failed_gate else "order")
    return {"gates": steps, "decided_by": failed_gate, "action": action, "action_text": ACTIONS.get(action, action),
            "reason": reason}


def from_rules(rules):
    """Turn the policy engine's rule results into the ladder verdict (gates 3-5)."""
    for key in ("allowed", "proven", "authority"):
        failed = [r for r in rules if not r["ok"] and gate_of(r["rule"]) == key]
        if failed:
            return ladder(key, reason="; ".join(f"{r['rule']}: {r['detail']}" for r in failed))
    return ladder(None, "order", "every gate passed")
