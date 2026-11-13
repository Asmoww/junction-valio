"""Trade Gateway web app: the open /v1 API for external purchasing agents plus the demo UI."""
import hashlib
import hmac
import logging
import secrets
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import quote as urlquote

from fastapi import Body, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import agent, ai, catalog, policy, security, signing, store
from .config import DEMO_PASSWORD
from .mcp_server import app as mcp_app
from .mcp_server import mcp


@asynccontextmanager
async def lifespan(_):
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    lifespan=lifespan,
    title="Valio Aimo Trade Gateway (demo)",
    version="0.1.0",
    description="Open interface for external purchasing agents: lookup → signed fixed-price offer → order → "
                "mandate check → human approval. Every decision is logged in a hash-chained audit trail.",
)
TEMPLATES = Path(__file__).parent / "templates"
_COOKIE = "tg_session"
_token = hmac.new(DEMO_PASSWORD.encode(), b"tg-session", hashlib.sha256).hexdigest() if DEMO_PASSWORD else ""
OPEN_PATHS = {"/login", "/healthz", "/.well-known/jwks.json"}
if not DEMO_PASSWORD:
    logging.getLogger("uvicorn.error").warning(
        "DEMO_PASSWORD is not set: the whole gateway, including approvals, is open. Local development only.")


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """Whole site behind one demo password: browser cookie, or `Authorization: Bearer <password>` for agents.

    The two are different principals. A signed-in browser is a human; a Bearer token is an agent. Agents
    may look up, quote and order, but only a human session may approve or reject an order, so a hijacked
    agent can't sign off its own order."""
    request.state.principal = "human" if not DEMO_PASSWORD else None
    if not DEMO_PASSWORD or request.url.path in OPEN_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if secrets.compare_digest(request.cookies.get(_COOKIE, ""), _token):
        request.state.principal = "human"
        return await call_next(request)
    if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], DEMO_PASSWORD):
        request.state.principal = "agent"
        return await call_next(request)
    if request.url.path.startswith(("/v1", "/api", "/mcp")):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return RedirectResponse(f"/login?next={urlquote(request.url.path)}", status_code=303)


LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Sign in · Trade Gateway</title><style>body{font-family:system-ui,sans-serif;background:#0b2a5b;display:grid;place-items:center;
height:100vh;margin:0}form{background:#fff;padding:2rem;border-radius:12px;width:min(320px,85vw);box-shadow:0 10px 40px #0004}
h1{font-size:1.1rem;margin:0 0 1rem;color:#0b2a5b}input,button{width:100%;padding:.7rem;font-size:1rem;box-sizing:border-box;
border-radius:8px;border:1px solid #ccd}button{margin-top:.8rem;background:#f08a24;color:#fff;border:0;font-weight:600;cursor:pointer}
p{color:#b00020;font-size:.9rem}</style></head><body><form method=post action=/login><h1>Trusted Trade Agents · demo</h1>
<input type=hidden name=next value="{next}"><input type=password name=password placeholder=Password autofocus required>{err}
<button>Sign in</button></form></body></html>"""


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(next: str = "/", err: int = 0):
    return LOGIN_HTML.replace("{next}", escape(security.safe_next(next))).replace(
        "{err}", "<p>Wrong password</p>" if err else "")


@app.post("/login", include_in_schema=False)
def login(password: str = Form(...), next: str = Form("/")):
    if not secrets.compare_digest(password, DEMO_PASSWORD):
        return RedirectResponse(f"/login?err=1&next={urlquote(security.safe_next(next))}", status_code=303)
    r = RedirectResponse(security.safe_next(next), status_code=303)
    r.set_cookie(_COOKIE, _token, httponly=True, secure=True, samesite="lax", max_age=7 * 86400)
    return r


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    return (TEMPLATES / "index.html").read_text()


# ---- Demo API used by the UI -------------------------------------------------------------------

@app.get("/api/mandates", tags=["demo"])
def get_mandates():
    return list(policy.mandates().values())


@app.post("/api/run", tags=["demo"])
def run_agent(body: dict = Body(...)):
    """Run the buyer agent end to end on a natural-language request."""
    if not str(body.get("request") or "").strip():
        raise HTTPException(422, "request text is required")
    mandate_id = body.get("mandate_id", "mnd_hotel_helsinki")
    if mandate_id not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    overrides = {}
    if body.get("auto_limit") is not None:
        # Demo knob. Never above the mandate's hard limit, and never negative.
        hard = policy.mandates()[mandate_id]["hard_limits"]["max_order_total_eur"]
        overrides["auto_approve.max_order_total_eur"] = min(max(float(body["auto_limit"]), 0.0), hard)
    return agent.run(body["request"], mandate_id, overrides,
                     simulate_price_change=bool(body.get("simulate_price_change")), inject=bool(body.get("inject")))


@app.get("/api/info", tags=["demo"])
def info():
    return {"ai_parser": ai.enabled(), "model": ai.MODEL if ai.enabled() else None}


@app.get("/api/traps", tags=["demo"])
def traps():
    """Latest trap-suite results (scripts/run_traps.py): real-data cases where buying agents go wrong."""
    import json
    from .config import STATE_DIR
    f = STATE_DIR / "trap_results.json"
    return json.loads(f.read_text()) if f.exists() else {"results": []}


@app.get("/connect", response_class=HTMLResponse, include_in_schema=False)
def connect_page():
    return (TEMPLATES / "connect.html").read_text()


@app.get("/static/style.css", include_in_schema=False)
def stylesheet():
    from fastapi.responses import Response
    return Response((TEMPLATES / "style.css").read_text(), media_type="text/css",
                    headers={"Cache-Control": "no-cache"})


@app.get("/valio", response_class=HTMLResponse, include_in_schema=False)
def valio_page():
    return (TEMPLATES / "valio.html").read_text()


@app.get("/api/dashboard", tags=["demo"])
def dashboard():
    """Valio's view: which agents buy, under which mandates, what gets approved, escalated or blocked."""
    orders = store.list_orders(500)
    mandates = policy.mandates()
    agents = defaultdict(lambda: Counter())
    rules = Counter()
    for o in orders:
        a = o["record"].get("agent") or mandates.get(o["mandate_id"], {}).get("agent", "unknown")
        c = agents[a]
        c["orders"] += 1
        c[o["status"]] += 1
        if o["status"] == "placed":
            c["value_placed"] += o["total"]
        if o["policy"]["outcome"] == "auto_approve":
            c["auto"] += 1
        for r in o["policy"]["rules"]:
            if not r["ok"]:
                rules[(r["rule"], r["level"])] += 1
    events = store.events(limit=2000)
    runs = [e for e in events if e["type"] == "run.completed"]
    security = [e for e in events if e["type"].startswith("security.") or e["type"] == "order.rejected"]
    status = Counter(o["status"] for o in orders)
    return {
        "totals": {"orders": len(orders), "placed": status["placed"], "pending": status["pending_approval"],
                   "blocked": status["blocked"], "rejected": status["rejected"],
                   "value_placed": round(sum(o["total"] for o in orders if o["status"] == "placed"), 2),
                   "value_pending": round(sum(o["total"] for o in orders if o["status"] == "pending_approval"), 2),
                   "agent_runs": len(runs),
                   "avg_api_calls": round(sum(e["data"].get("api_calls", 0) for e in runs) / len(runs), 1) if runs else None,
                   "security_events": len(security)},
        "agents": [{"agent": a, "mandates": sorted({m["mandate_id"] for m in mandates.values() if m["agent"] == a}),
                    **{k: round(v, 2) for k, v in c.items()}} for a, c in agents.items()],
        "escalations": [{"rule": r, "level": l, "count": n} for (r, l), n in rules.most_common()],
        "security": [{"ts": e["ts"], "type": e["type"], "actor": e["actor"], "ref": e["ref"], "data": e["data"]}
                     for e in reversed(security[-30:])],
        "recent": [{"id": o["id"], "created": o["created"], "status": o["status"],
                    "agent": o["record"].get("agent") or mandates.get(o["mandate_id"], {}).get("agent"),
                    "total": o["total"], "lines": [f"{l['units']} × {l['name']}" for l in o["record"]["offer"]["lines"]],
                    "failed": [r["rule"] for r in o["policy"]["rules"] if not r["ok"]]} for o in orders[:25]],
        "chain": store.verify_chain(),
    }


@app.get("/api/audit", tags=["demo"])
def audit(ref: str | None = None):
    return {"chain": store.verify_chain(), "events": store.events(ref)}


# ---- Open interface for external purchasing agents ---------------------------------------------

@app.get("/v1/products/search", tags=["v1: lookup"])
def search(q: str, attributes: str = "", limit: int = 10):
    """Structured product search. Attributes: FREE_FROM_LACTOSE, FREE_FROM_GLUTEN, VEGAN, ORGANIC, FINNISH."""
    attrs = [a for a in attributes.split(",") if a]
    found, trace = catalog.search(q, attrs, min(limit, 20), type_check=ai.type_check if ai.enabled() else None)
    return {"trace": trace, "products": [catalog.describe(p) for p in found]}


@app.get("/v1/products/{product_id}", tags=["v1: lookup"])
def product(product_id: str):
    p = catalog.get(product_id)
    if not p:
        raise HTTPException(404, "unknown product")
    return catalog.describe(p)


@app.post("/v1/quotes", tags=["v1: offer"])
def quote(body: dict = Body(..., examples=[{"mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16",
                                            "lines": [{"productId": "463335", "units": 1000}]}])):
    """Price and availability for specific lines, returned as a signed fixed-price offer (valid 15 min)."""
    if body.get("mandate_id") not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    offer, jws, err = agent.quote(body["mandate_id"], body["deliver_by"], body.get("lines") or [])
    if err:
        raise HTTPException(409, err)
    return {"offer": offer, "jws": jws}


@app.post("/v1/orders", tags=["v1: order"])
def order(body: dict = Body(...), idempotency_key: str = Header(...)):
    """Commit a signed offer. The gateway verifies the signature, re-checks prices and applies the mandate."""
    o, err = agent.order_from_jws(body["jws"], idempotency_key, body.get("rationale", ""), body.get("requested_units"))
    if not o:
        raise HTTPException(409, err)
    return o


@app.get("/v1/orders/{order_id}", tags=["v1: order"])
def get_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(404, "unknown order")
    return o


@app.post("/v1/approvals/{order_id}", tags=["v1: approval"])
def approve(request: Request, order_id: str,
            body: dict = Body(..., examples=[{"decision": "approve", "approver": "Aino Virtanen"}])):
    """Human sign-off. Only a signed-in person can call this (agents' Bearer tokens get 403), and the
    approver must be named in the buyer's mandate with authority for the order total."""
    if getattr(request.state, "principal", None) != "human":
        store.log(order_id, "agent", "security.agent_tried_to_approve", {"decision": body.get("decision")})
        raise HTTPException(403, "approvals must come from a person, not an agent")
    if body.get("decision") not in ("approve", "reject"):
        raise HTTPException(422, "decision must be 'approve' or 'reject'")
    o, err = agent.decide(order_id, body["decision"], str(body.get("approver") or ""), body.get("comment", ""))
    if err:
        raise HTTPException(409, err)
    return o


@app.get("/v1/orders/{order_id}/decision", tags=["v1: audit"])
def decision(order_id: str):
    """Human-readable decision record plus the audit events for this order."""
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(404, "unknown order")
    return {"order": o, "events": store.events(o["id"]) + store.events(o["offer_id"]), "chain": store.verify_chain()}


@app.post("/v1/offers/verify", tags=["v1: trust"])
def verify_offer(body: dict = Body(...)):
    ok, payload = signing.verify(body["jws"])
    if not ok:
        store.log("security", "verifier", "security.invalid_signature", {"error": payload})
    return {"valid": ok, "payload": payload if ok else None, "error": None if ok else payload}


@app.get("/.well-known/jwks.json", tags=["v1: trust"])
def well_known_jwks():
    """Public key for verifying offer signatures."""
    return signing.jwks()


# MCP over streamable HTTP at /mcp. Mounted last so the routes above take precedence.
app.mount("/", mcp_app)
