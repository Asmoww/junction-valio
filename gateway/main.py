"""Trade Gateway web app: the open /v1 API for external purchasing agents plus the demo UI."""
import hashlib
import hmac
import secrets
from pathlib import Path

from fastapi import Body, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import agent, catalog, policy, signing, store
from .config import DEMO_PASSWORD
from .valio import ValioClient

app = FastAPI(
    title="Valio Aimo Trade Gateway (demo)",
    version="0.1.0",
    description="Open interface for external purchasing agents: lookup → signed fixed-price offer → order → "
                "mandate check → human approval. Every decision is logged in a hash-chained audit trail.",
)
TEMPLATES = Path(__file__).parent / "templates"
_COOKIE = "tg_session"
_token = hmac.new(DEMO_PASSWORD.encode(), b"tg-session", hashlib.sha256).hexdigest() if DEMO_PASSWORD else ""
OPEN_PATHS = {"/login", "/healthz", "/.well-known/jwks.json"}


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """Whole site behind one demo password: browser cookie, or `Authorization: Bearer <password>` for agents."""
    if not DEMO_PASSWORD or request.url.path in OPEN_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if secrets.compare_digest(request.cookies.get(_COOKIE, ""), _token) or \
            (auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], DEMO_PASSWORD)):
        return await call_next(request)
    if request.url.path.startswith(("/v1", "/api")):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


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
    return LOGIN_HTML.replace("{next}", next if next.startswith("/") else "/").replace(
        "{err}", "<p>Wrong password</p>" if err else "")


@app.post("/login", include_in_schema=False)
def login(password: str = Form(...), next: str = Form("/")):
    if not secrets.compare_digest(password, DEMO_PASSWORD):
        return RedirectResponse(f"/login?err=1&next={next}", status_code=303)
    r = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
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
    overrides = {}
    if body.get("auto_limit") is not None:
        overrides["auto_approve.max_order_total_eur"] = float(body["auto_limit"])
    return agent.run(body["request"], body.get("mandate_id", "mnd_hotel_helsinki"), overrides,
                     simulate_price_change=bool(body.get("simulate_price_change")))


@app.get("/api/audit", tags=["demo"])
def audit(ref: str | None = None):
    return {"chain": store.verify_chain(), "events": store.events(ref)}


# ---- Open interface for external purchasing agents ---------------------------------------------

@app.get("/v1/products/search", tags=["v1: lookup"])
def search(q: str, attributes: str = "", limit: int = 10):
    """Structured product search. Attributes: FREE_FROM_LACTOSE, FREE_FROM_GLUTEN, VEGAN, ORGANIC, FINNISH."""
    attrs = [a for a in attributes.split(",") if a]
    found, trace = catalog.search(q, attrs, min(limit, 20))
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
    mandate = policy.load(body["mandate_id"])
    ids = [l["productId"] for l in body["lines"]]
    c = ValioClient()
    prices = c.prices(mandate["customer"], body["deliver_by"], ids)
    _, avail = c.availability(mandate["customer"], body["deliver_by"], ids)
    lines = []
    for l in body["lines"]:
        pr, av, p = prices.get(l["productId"]), avail.get(l["productId"]) or {}, catalog.get(l["productId"])
        if not p or not pr or not pr.get("catalogPrice"):
            raise HTTPException(422, f"no price for {l['productId']}")
        sched = [s for s in av.get("scheduleLines", []) if not s.get("salesBlock") and s.get("availableDate")
                 and s["availableDate"] <= body["deliver_by"]]
        if not sched or sum(s["availableAmount"] for s in sched) < l["units"]:
            raise HTTPException(409, f"{l['productId']} not available in that quantity by {body['deliver_by']}")
        up = pr["catalogPrice"]["value"]
        lines.append({"productId": p["id"], "name": p["name"], "units": l["units"], "salesUnit": p["salesUnit"],
                      "unitPrice": up, "lineTotal": round(up * l["units"], 2), "perUnit": 1,
                      "deliveryDate": min(s["availableDate"] for s in sched), "reliability": p["reliability"],
                      "substitution": l.get("substitution")})
    option = {"lines": lines, "total_eur": round(sum(x["lineTotal"] for x in lines), 2)}
    payload, jws = agent.create_offer(mandate, option, {"deliver_by": body["deliver_by"]})
    return {"offer": payload, "jws": jws, "api_calls": c.calls}


@app.post("/v1/orders", tags=["v1: order"])
def order(body: dict = Body(...), idempotency_key: str = Header(...)):
    """Commit a signed offer. The gateway verifies the signature, re-checks prices and applies the mandate."""
    ok, payload = signing.verify(body["jws"])
    if not ok:
        raise HTTPException(400, payload)
    mandate = policy.load(payload["mandate_id"])
    ctx = {"deliver_by": payload["deliver_by"], "delivery_date": max(l["deliveryDate"] for l in payload["lines"]),
           "rationale": body.get("rationale", "")}
    o, err = agent.place_order(body["jws"], idempotency_key, mandate, ctx, actor=f"agent:{mandate['agent']}")
    if err and not o:
        raise HTTPException(409, err)
    return o


@app.get("/v1/orders/{order_id}", tags=["v1: order"])
def get_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        raise HTTPException(404, "unknown order")
    return o


@app.post("/v1/approvals/{order_id}", tags=["v1: approval"])
def approve(order_id: str, body: dict = Body(..., examples=[{"decision": "approve", "approver": "Aino Virtanen"}])):
    o, err = agent.decide(order_id, body["decision"], body["approver"], body.get("comment", ""))
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
    return {"valid": ok, "payload": payload if ok else None, "error": None if ok else payload}


@app.get("/.well-known/jwks.json", tags=["v1: trust"])
def well_known_jwks():
    """Public key for verifying offer signatures."""
    return signing.jwks()
