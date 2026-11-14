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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import agent, ai, catalog, gates, policy, security, signing, standards, store
from .config import AGENT_TOKEN as AGENT_TOKEN_ENV, DEMO_CLOCK, DEMO_PASSWORD
from .mcp_server import app as mcp_app
from .mcp_server import mcp


@asynccontextmanager
async def lifespan(_):
    for mid in standards.sign_all(policy.mandates().values()):
        store.log(mid, "buyer", "mandate.signed", {"issuer": standards.buyer_did(mid)})
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
# Agents and people have different secrets: an agent's token is not the sign-in password, so a hijacked agent
# can't swap its token for a browser session and approve its own order.
AGENT_TOKEN = AGENT_TOKEN_ENV or (hmac.new(DEMO_PASSWORD.encode(), b"tg-agent", hashlib.sha256).hexdigest()[:32]
                                     if DEMO_PASSWORD else "")
if AGENT_TOKEN and AGENT_TOKEN == DEMO_PASSWORD:
    raise SystemExit("AGENT_TOKEN must differ from DEMO_PASSWORD")
OPEN_PATHS = {"/login", "/healthz", "/.well-known/jwks.json", "/v1/mandates/verify"}
if not DEMO_PASSWORD:
    logging.getLogger("uvicorn.error").warning(
        "DEMO_PASSWORD is not set: the whole gateway, including approvals, is open. Local development only.")


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """Whole site behind sign-in: a browser cookie for people (DEMO_PASSWORD), `Authorization: Bearer <agent token>` for agents.

    The two are different principals. A signed-in browser is a human; a Bearer token is an agent. Agents
    may look up, quote and order, but only a human session may approve or reject an order, so a hijacked
    agent can't sign off its own order."""
    request.state.principal = "human" if not DEMO_PASSWORD else None
    if not DEMO_PASSWORD or request.url.path in OPEN_PATHS or request.url.path.startswith(("/approve/", "/buyers/")):
        # /approve/... is reachable without signing in: it only works with a signed, single-use approval link.
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if secrets.compare_digest(request.cookies.get(_COOKIE, ""), _token):
        request.state.principal = "human"
        return await call_next(request)
    if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], AGENT_TOKEN):
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
def run_agent(request: Request, body: dict = Body(...)):
    """Run the buyer agent end to end on a natural-language request."""
    if not str(body.get("request") or "").strip():
        raise HTTPException(422, "request text is required")
    mandate_id = body.get("mandate_id", "mnd_hotel_helsinki")
    if mandate_id not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    overrides = {}
    if body.get("auto_limit") is not None:
        # Demo knob for the buyer's own settings. A signed-in person may set it up to the hard limit; an agent can
        # only lower its own authority, never raise it.
        m = policy.mandates()[mandate_id]
        ceiling = m["hard_limits"]["max_order_total_eur"] if getattr(request.state, "principal", None) == "human" \
            else m["auto_approve"]["max_order_total_eur"]
        try:
            overrides["auto_approve.max_order_total_eur"] = min(max(float(body["auto_limit"]), 0.0), ceiling)
        except (TypeError, ValueError):
            raise HTTPException(422, "auto_limit must be a number")
    return agent.run(body["request"], mandate_id, overrides,
                     simulate_price_change=bool(body.get("simulate_price_change")), inject=bool(body.get("inject")),
                     real_clock=bool(body.get("real_clock")))


@app.get("/api/info", tags=["demo"])
def info():
    return {"ai_parser": ai.enabled(), "model": ai.MODEL if ai.enabled() else None, "demo_clock": DEMO_CLOCK,
            "ladder": gates.GATES}


@app.get("/api/readiness", tags=["demo"])
def readiness_report():
    """How much of the catalog each buyer's agent may decide on alone, and which missing data blocks it."""
    from . import readiness
    return readiness.report()


@app.get("/api/traps", tags=["demo"])
def traps():
    """Latest trap-suite results (scripts/run_traps.py): real-data cases where buying agents go wrong."""
    import json
    from .config import STATE_DIR
    f = STATE_DIR / "trap_results.json"
    return json.loads(f.read_text()) if f.exists() else {"results": []}


@app.get("/api/scoreboard", tags=["demo"])
def scoreboard():
    """Latest scripts/scoreboard.py run: the same requests through an AI agent with and without the buyer's rules
    in its prompt, on Valio's raw API and through this gateway."""
    import json
    from .config import STATE_DIR
    f = STATE_DIR / "scoreboard.json"
    return json.loads(f.read_text()) if f.exists() else {"rows": []}


@app.get("/concepts", response_class=HTMLResponse, include_in_schema=False)
def concepts_page():
    """Design mockups of the process visualisation (docs/visuals), for the team."""
    from .config import ROOT
    return (ROOT / "docs" / "visuals" / "index.html").read_text()


@app.get("/concepts/{name}", include_in_schema=False)
def concept_image(name: str):
    from fastapi.responses import FileResponse
    from .config import ROOT
    if name not in {f"concept_{k}.png" for k in "ABCDEFGHIJKLM"}:
        raise HTTPException(404, "not found")
    return FileResponse(ROOT / "docs" / "visuals" / name, media_type="image/png")


@app.get("/connect", response_class=HTMLResponse, include_in_schema=False)
def connect_page(request: Request):
    page = (TEMPLATES / "connect.html").read_text()
    if getattr(request.state, "principal", None) == "human" and AGENT_TOKEN:
        page = page.replace("&lt;agent token&gt;", escape(AGENT_TOKEN))  # people hand this to their agent
    return page


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
                    "failed": [r["rule"] for r in o["policy"]["rules"] if not r["ok"]],
                    "buyer": mandates.get(o["mandate_id"], {}).get("buyer", "").replace(" (demo)", ""),
                    "approver": (o["policy"].get("approver") or {}).get("name")} for o in orders[:25]],
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
    offer, jws, err = agent.quote(body["mandate_id"], body["deliver_by"], body.get("lines") or [],
                                  requested_quantity=body.get("requested_quantity"), requested_unit=body.get("requested_unit"))
    if err:
        raise HTTPException(409, err)
    return {"offer": offer, "jws": jws}


@app.post("/v1/orders", tags=["v1: order"])
def order(body: dict = Body(...), idempotency_key: str = Header(...)):
    """Commit a signed offer, by `offer_id` or the full `jws`. The gateway verifies the signature, re-checks
    prices and applies the mandate."""
    jws = body.get("jws") or (store.get_offer(body.get("offer_id") or "") or {}).get("jws")
    if not jws:
        raise HTTPException(422, "give offer_id or jws")
    o, err = agent.order_from_jws(jws, idempotency_key, body.get("rationale", ""), body.get("requested_units"))
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


# ---- Approval on your own phone: signed, single-use links (approval_links.py) --------------------------------

def _public_base(request: Request):
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    proto = request.headers.get("x-forwarded-proto") or ("http" if host.startswith(("localhost", "127.")) else "https")
    return f"{proto}://{host}"


@app.get("/api/approval-link/{order_id}", tags=["demo"])
def approval_link(request: Request, order_id: str, approver: str):
    """A signed approval link and its QR code, for the approver's phone. Only a signed-in person can create one."""
    from urllib.parse import urlencode
    import segno
    from . import approval_links
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "only a person can hand out an approval link")
    o = store.get_order(order_id)
    if not o or o["status"] != "pending_approval":
        raise HTTPException(409, "order is not waiting for approval")
    if approver not in [a["name"] for a in policy.load(o["mandate_id"])["approvers"]]:
        raise HTTPException(422, "not an approver in this buyer's mandate")
    url = f"{_public_base(request)}/approve/{order_id}?{urlencode(approval_links.make(order_id, approver))}"
    store.log(order_id, "gateway", "approval.link_issued", {"approver": approver})
    return {"url": url, "svg": segno.make(url, error="m").svg_inline(scale=5, dark="#0a3d91", border=2)}


def _approve_page(order, approver, body, title):
    from html import escape as h
    page = (TEMPLATES / "approve.html").read_text()
    buyer = policy.load(order["mandate_id"])["buyer"].replace(" (demo)", "") if order else "Valio Aimo"
    return HTMLResponse(page.replace("__BUYER__", h(buyer)).replace("__TITLE__", h(title))
                        .replace("__APPROVER__", h(approver or "")).replace("__BODY__", body))


def _approve_card(order, q, note_error=""):
    from html import escape as h
    offer = order["record"]["offer"]
    rules = order["policy"]["rules"]
    gate = (order["policy"].get("ladder") or {}).get("decided_by")
    failed = [r for r in rules if not r["ok"]]
    ok_jws = signing.verify((store.get_offer(order["offer_id"]) or {}).get("jws", ""))[0]
    l0 = offer["lines"][0]
    trust = l0.get("trust") or {}
    mandate = policy.load(order["mandate_id"])
    short_ok = {"Hard spend limit": ("Within the hard limit", f'€{mandate["hard_limits"]["max_order_total_eur"]:,} per order'),
                "Forbidden allergens": ("No banned allergens", "checked in product data"),
                "Claims verified in product data": ("Claims checked", "in the manufacturer's data"),
                "Stock confirmed by Valio": ("In Valio's stock", "quantity confirmed"),
                "Matches the buyer's request": ("Matches the request", "no over-ordering"),
                "Delivery on time": ("Arrives in time", offer["deliver_by"])}
    stamps = [f'<div class="stamp"><b>✓ {h(short_ok[r["rule"]][0])}</b>{h(short_ok[r["rule"]][1])}</div>'
              for r in rules if r["ok"] and r["rule"] in short_ok]
    profile = (order["record"].get("context") or {}).get("from_profile") or []
    if profile:
        stamps.append(f'<div class="stamp warn"><b>From your profile, not the request</b>{h(" · ".join(profile))}</div>')
    need_note = gate == "proven"
    lines = "<br>".join(f'{l["units"]:,g} × {h(l["name"])}' for l in offer["lines"])
    return f"""<div class="card"><div class="top">
      <div style="color:var(--mute);font-size:13.5px">Valio Aimo · deliver {h(offer["deliver_by"])}</div>
      <div class="amount">€{order["total"]:,.2f}</div><div class="lines">{lines}</div>
      <div class="why"><b>Why you:</b> {h(_why_you(failed, order, mandate))}</div></div>
      <div class="perf"></div><div class="stamps">{"".join(stamps[:5])}</div>
      <div class="sig">{"✓" if ok_jws else "✕"} signed by Valio · {h(order["offer_id"])} · Ed25519 {"verified" if ok_jws else "INVALID"}</div>
      <form method="post"><input type="hidden" name="a" value="{h(q["a"])}"><input type="hidden" name="e" value="{h(q["e"])}">
        <input type="hidden" name="t" value="{h(q["t"])}">
        {f'<label>What did you check?</label><textarea name="note" required placeholder="e.g. Valio confirmed 1,000 in stock by phone"></textarea>' if need_note else '<input type="hidden" name="note" value="Approved on phone">'}
        {f'<div class="why" style="margin:0 0 10px">{h(note_error)}</div>' if note_error else ''}
        <div class="btns"><button class="rej" name="decision" value="reject">Reject</button>
        <button class="app" name="decision" value="approve">Approve</button></div></form></div>"""


def _why_you(failed, order, mandate):
    """The reasons an order waits for a person, in plain words."""
    out = []
    for r in failed:
        if r["rule"] == "Auto-approve spend limit":
            out.append(f'€{order["total"]:,.0f} is over the €{mandate["auto_approve"]["max_order_total_eur"]:,.0f} the agent may spend alone')
        elif r["rule"] == "Agent's own spend in 24 h":
            out.append("the agent's own spending today would pass its daily limit")
        elif r["rule"] == "Unstated details within tolerance":
            out.append("a detail the request didn't state changes the price")
        else:
            out.append(f'{r["rule"].lower()}: {str(r["detail"])[:80]}')
    return "; ".join(out[:2]) + "."


@app.get("/approve/{order_id}", response_class=HTMLResponse, include_in_schema=False)
def approve_page(order_id: str, a: str = "", e: str = "", t: str = ""):
    from html import escape as h
    from . import approval_links
    ok, why = approval_links.check(order_id, a, e, t)
    o = store.get_order(order_id)
    if not ok or not o:
        return _approve_page(o, a, f'<div class="card done bad"><h1>Link not valid</h1>{h(why or "unknown order")}</div>', "Approval")
    if o["status"] != "pending_approval":
        last = (o["record"].get("approvals") or [{}])[-1]
        return _approve_page(o, a, f'<div class="card done {"ok" if o["status"] == "placed" else "bad"}"><h1>Already decided</h1>'
                                   f'{h(o["status"].replace("_", " "))}{" by " + h(last.get("approver", "")) if last else ""}</div>', "Approval")
    return _approve_page(o, a, _approve_card(o, {"a": a, "e": e, "t": t}), "1 order needs you")


@app.post("/approve/{order_id}", response_class=HTMLResponse, include_in_schema=False)
def approve_submit(order_id: str, a: str = Form(""), e: str = Form(""), t: str = Form(""), decision: str = Form(""),
                   note: str = Form("")):
    from html import escape as h
    from . import approval_links
    ok, why = approval_links.check(order_id, a, e, t)
    o = store.get_order(order_id)
    if not ok or not o:
        return _approve_page(o, a, f'<div class="card done bad"><h1>Link not valid</h1>{h(why or "unknown order")}</div>', "Approval")
    store.log(order_id, f"human:{a}", "approval.via_link", {"decision": decision})
    done, err = agent.decide(order_id, decision, a, note)
    if err:
        if o["status"] == "pending_approval" and "fact check" in err:
            return _approve_page(o, a, _approve_card(o, {"a": a, "e": e, "t": t}, err), "1 order needs you")
        return _approve_page(o, a, f'<div class="card done bad"><h1>Not done</h1>{h(err)}</div>', "Approval")
    placed = done["status"] == "placed"
    return _approve_page(done, a, f'<div class="card done {"ok" if placed else "bad"}"><h1>{"Approved · order placed" if placed else "Rejected"}</h1>'
                                  f'€{done["total"]:,.2f} · {h(done["id"])}<br><span style="color:var(--mute);font-size:14px">'
                                  f'Logged in the audit trail as {h(a)}.</span></div>', "Done")


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


@app.get("/v1/orders/{order_id}/peppol.xml", tags=["v1: standards"])
def peppol_xml(request: Request, order_id: str):
    """The placed order as a Peppol BIS Ordering 3 document (UBL 2.1 Order), ready for an ERP or access point."""
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(404, "unknown order")
    if o["status"] != "placed":
        raise HTTPException(409, f"order is {o['status']}: a Peppol order is only issued once it is placed")
    xml = standards.peppol_order(o, policy.mandates()[o["mandate_id"]], _public_base(request))
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'inline; filename="{order_id}.xml"'})


@app.get("/v1/mandates/{mandate_id}/credential", tags=["v1: standards"])
def mandate_credential(mandate_id: str):
    """The buyer-signed mandate as a W3C Verifiable Credential (vc+jwt), plus its decoded form."""
    m = policy.mandates().get(mandate_id)
    if not m:
        raise HTTPException(404, "unknown mandate")
    token = standards.credential(m)
    ok, vc = standards.verify(token, m)
    return {"credential": token, "verified": ok, "reason": None if ok else vc, "decoded": vc if ok else None}


@app.post("/v1/mandates/verify", tags=["v1: standards"])
def verify_mandate(body: dict = Body(..., examples=[{"credential": "eyJhbGciOiJFZERTQSIs..."}])):
    """Verify a mandate credential: buyer's signature (did:web), expiry, and that it matches the registered
    mandate. Open to anyone, like the offer key."""
    ok, vc = standards.verify(body.get("credential", ""))
    if ok:
        mid = vc["credentialSubject"]["mandate"].get("mandate_id")
        registered = policy.mandates().get(mid)
        if registered and not standards.verify(body["credential"], registered)[0]:
            ok, vc = False, "signed, but not the mandate registered with this gateway (outdated or altered)"
    if not ok:
        store.log("security", "verifier", "security.invalid_mandate_credential", {"error": vc})
    return {"valid": ok, "issuer": vc["issuer"]["id"] if ok else None, "error": None if ok else vc}


@app.get("/buyers/{slug}/did.json", tags=["v1: standards"], include_in_schema=False)
def buyer_did_document(slug: str):
    mid = f"mnd_{slug}"
    if mid not in policy.mandates():
        raise HTTPException(404, "unknown buyer")
    return standards.did_document(mid)


# MCP over streamable HTTP at /mcp. Mounted last so the routes above take precedence.
app.mount("/", mcp_app)
