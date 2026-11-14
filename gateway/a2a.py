"""A2A (Agent2Agent protocol, v1.0, a2a-protocol.org): other agents discover and talk to Vahti.

- Agent Card at /.well-known/agent-card.json (public): skills, the JSON-RPC endpoint and its Bearer security.
- JSON-RPC at /a2a: SendMessage and GetTask (v1.0 names), plus message/send and tasks/get (v0.3 names).

Tasks belong to the buyer whose token created them: GetTask / tasks/get return a task only to that same caller
(a task id is not a credential), and "not found" otherwise, so another buyer can't even tell the task exists.

Safety: a text message is answered as an inquiry only (the built-in agent with purpose="inquiry": prices and
availability, never an order). Quoting and ordering take structured data parts ({"skill": "quote", ...} or
{"skill": "order", ...}) and go through exactly the same code, token binding and checks as REST and MCP.
"""
import secrets
import threading

from . import agent, ap2, policy

VERSION = "1.0"
_tasks, _lock = {}, threading.Lock()

SKILLS = [
    {"id": "inquiry", "name": "Ask about products", "tags": ["catalog", "price", "availability"],
     "description": "Plain text: what Valio Aimo has, prices and availability for a date. Never orders.",
     "examples": ["What does lactose-free milk cost for Monday?"]},
    {"id": "quote", "name": "Signed fixed-price offer", "tags": ["offer", "ap2"],
     "description": "Data part {skill: quote, mandate_id, deliver_by, lines: [{productId, units}], request_limits?}. "
                    "Returns an Ed25519-signed offer and an AP2-style ES256 merchant checkout JWT with its checkout_hash."},
    {"id": "order", "name": "Commit an offer", "tags": ["order"],
     "description": "Data part {skill: order, offer_id, idempotency_key}. Placed, sent to a person, or stopped by the "
                    "buyer's rules. Agents can never approve."},
]


def card(base):
    return {
        "name": "Vahti for Valio Aimo",
        "description": "The guard between buying agents and Valio Aimo: signed fixed-price offers, the buyer's signed "
                       "rules checked on every order, a person for what an agent may not decide alone.",
        "version": "0.3.0",
        "provider": {"organization": "Vahti (Junction 2026 demo for Valio Aimo)", "url": base},
        "documentationUrl": f"{base}/connect",
        "supportedInterfaces": [{"url": f"{base}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": VERSION}],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "securitySchemes": {"agentToken": {"type": "http", "scheme": "bearer",
                                           "description": "Per-buyer agent token from /connect; acts for one buyer only."}},
        "securityRequirements": [{"agentToken": []}],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": SKILLS,
    }


def _task(state, text=None, data=None, msg=None, owner=None):
    parts = ([{"text": text}] if text else []) + ([{"data": data}] if data is not None else [])
    t = {"id": "task_" + secrets.token_hex(6), "contextId": (msg or {}).get("contextId") or "ctx_" + secrets.token_hex(4),
         "status": {"state": state, "message": {"messageId": secrets.token_hex(6), "role": "ROLE_AGENT", "parts": parts}},
         "artifacts": [{"artifactId": "result", "parts": parts}] if parts else []}
    with _lock:
        _tasks[t["id"]] = (owner, t)  # owner: the caller's bound mandate (None = the all-buyer demo token)
        if len(_tasks) > 500:
            _tasks.pop(next(iter(_tasks)))
    return t


def _send(params, bound):
    msg = (params or {}).get("message") or {}
    parts = msg.get("parts") or []
    data = next((p.get("data") for p in parts if isinstance(p.get("data"), dict)), None)
    text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
    if data and data.get("skill") == "quote":
        mid = data.get("mandate_id") or bound
        offer, jws, err = agent.quote(mid, data.get("deliver_by"), data.get("lines") or [],
                                      requested_quantity=data.get("requested_quantity"),
                                      requested_unit=data.get("requested_unit"), limits=data.get("request_limits"))
        if err:
            return _task("TASK_STATE_REJECTED", text=err, msg=msg, owner=bound)
        return _task("TASK_STATE_COMPLETED", text=f"Signed offer {offer['offer_id']}: €{offer['total_eur']:,.2f}",
                     data={"offer": offer, "jws": jws, "ap2": ap2.checkout_jwt(offer)}, msg=msg, owner=bound)
    if data and data.get("skill") == "order":
        from . import store
        stored = store.get_offer(str(data.get("offer_id") or ""))
        if not stored:
            return _task("TASK_STATE_REJECTED", text="unknown offer_id", msg=msg, owner=bound)
        order, err = agent.order_from_jws(stored["jws"], str(data.get("idempotency_key") or ""), "via A2A")
        if not order:
            return _task("TASK_STATE_REJECTED", text=err, msg=msg, owner=bound)
        state = {"placed": "TASK_STATE_COMPLETED", "pending_approval": "TASK_STATE_INPUT_REQUIRED"}.get(order["status"], "TASK_STATE_REJECTED")
        return _task(state, text=f"Order {order['id']}: {order['status']}",
                     data={"order_id": order["id"], "status": order["status"], "total_eur": order["total"],
                           "failed_rules": [r for r in order["policy"]["rules"] if not r["ok"]]}, msg=msg, owner=bound)
    if text:
        mid = bound or "mnd_hotel_helsinki"
        res = agent.run(text, mid, purpose="inquiry")  # inquiry: can never create an offer or order
        return _task("TASK_STATE_COMPLETED", text=(res.get("ladder") or {}).get("reason") or res["result"].get("error") or "Answered.",
                     data={"answered": True, "creates_order": False, "status": res["result"]["status"]}, msg=msg, owner=bound)
    return _task("TASK_STATE_REJECTED", text="send a text question, or a data part with skill quote or order", msg=msg, owner=bound)


def handle(body, bound):
    """One JSON-RPC 2.0 request. Returns the response object."""
    rid, method, params = body.get("id"), body.get("method"), body.get("params") or {}
    try:
        if method in ("SendMessage", "message/send"):
            return {"jsonrpc": "2.0", "id": rid, "result": _send(params, bound)}
        if method in ("GetTask", "tasks/get"):
            with _lock:
                owner, t = _tasks.get(str(params.get("id") or str(params.get("name") or "").removeprefix("tasks/")), (None, None))
            if not t or owner != bound:  # same answer for "someone else's" and "doesn't exist"
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": "task not found"}}
            return {"jsonrpc": "2.0", "id": rid, "result": t}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except PermissionError as e:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32003, "message": str(e)}}
    except (ValueError, KeyError, TypeError) as e:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"invalid params: {e}"}}


def known_buyer(mid):
    return mid in policy.mandates()
