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

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, Security, UploadFile
from fastapi.security import HTTPBearer
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import learning, unlock, plans, recipe, agent, ai, catalog, gates, policy, security, recalls, signing, standards, store, vision, preplanner, game, receipt, trust, a2a, ap2, onboard, schemas
from .config import AGENT_TOKEN as AGENT_TOKEN_ENV, AGENT_TOKEN_SCOPE, DEMO_CLOCK, DEMO_PASSWORD
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
    title="Vahti for Valio Aimo (demo)",
    version="0.3.0",
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


def mandate_token(mandate_id, purpose="purchase"):
    """The agent token bound to one buyer's mandate (derived, so nothing new to store or leak). purpose "inquiry"
    gives a token that can look up and price under that mandate but never order."""
    salt = b"tg-agent:" if purpose == "purchase" else b"tg-agent-inquiry:"
    return hmac.new(AGENT_TOKEN.encode(), salt + mandate_id.encode(), hashlib.sha256).hexdigest()[:32]


def _bound_mandate(token):
    """(True, mandate it is bound to or None for unbound) for a valid agent token, else (False, None)."""
    ok, mid, _ = _token_scope(token)
    return ok, mid


def _token_scope(token):
    """(valid, mandate or None, read_only) for an agent token."""
    if not AGENT_TOKEN:
        return False, None, False
    if secrets.compare_digest(token, AGENT_TOKEN):
        return True, (None if AGENT_TOKEN_SCOPE == "all" else AGENT_TOKEN_SCOPE), False
    for mid in policy.mandates():
        if secrets.compare_digest(token, mandate_token(mid)):
            return True, mid, False
        if secrets.compare_digest(token, mandate_token(mid, "inquiry")):
            return True, mid, True
    return False, None, False


# MCP authorization spec 2026-07-28: an MCP server publishes Protected Resource Metadata (RFC 9728) and says in
# WWW-Authenticate where it is and which scopes an operation needs. Our tokens are issued per buyer on /connect
# (no OAuth server in the demo); the scopes describe what each token may do.
SCOPES = {"purchase": "catalog:read offers:quote orders:place", "inquiry": "catalog:read offers:quote"}
PRM_PATH = "/.well-known/oauth-protected-resource"


def _www_authenticate(request: Request, error=None, scope=None):
    base = str(request.base_url).rstrip("/")
    parts = [f'resource_metadata="{base}{PRM_PATH}"']
    if error:
        parts.insert(0, f'error="{error}"')
    if scope:
        parts.append(f'scope="{scope}"')
    return "Bearer " + ", ".join(parts)


OPEN_PATHS = {"/login", "/healthz", PRM_PATH, "/.well-known/jwks.json", "/v1/mandates/verify", "/static/style.css",
              "/.well-known/agent-card.json", "/openapi.json", "/docs"}  # the API description is public; calling it is not
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
    if not DEMO_PASSWORD or request.url.path in OPEN_PATHS or request.url.path.startswith(("/approve/", "/buyers/", "/r/")):
        # /approve/... is reachable without signing in: it only works with a signed, single-use approval link.
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if secrets.compare_digest(request.cookies.get(_COOKIE, ""), _token):
        request.state.principal = "human"
        return await call_next(request)
    ok, bound, read_only = _token_scope(auth[7:]) if auth.startswith("Bearer ") else (False, None, False)
    if ok and request.url.path.startswith("/api"):
        # The /api endpoints serve the demo pages to signed-in people (and list every buyer's orders).
        return JSONResponse({"detail": "agents use /v1 or /mcp"}, status_code=403)
    if ok:
        request.state.principal = "agent"
        request.state.mandate = bound
        request.state.read_only = read_only
        reset = agent.CALLER.set(bound)  # read by agent.quote/order_from_jws, so REST and MCP get the same check
        reset_ro = agent.READ_ONLY.set(read_only)  # inquiry-only token: agent.place_order refuses
        try:
            return await call_next(request)
        finally:
            agent.READ_ONLY.reset(reset_ro)
            agent.CALLER.reset(reset)
    if request.url.path.startswith(("/v1", "/api", "/mcp", "/a2a")):
        return JSONResponse({"detail": "authentication required"}, status_code=401,
                            headers={"WWW-Authenticate": _www_authenticate(request, scope=SCOPES["inquiry"])})
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
    purpose = body.get("purpose")
    if purpose not in (None, "purchase", "inquiry"):
        raise HTTPException(422, "purpose must be purchase or inquiry")
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
    token = recalls.TEST.set(bool(body.get("test_recall")))  # demo switch: a labelled test recall
    try:
        return agent.run(body["request"], mandate_id, overrides,
                         simulate_price_change=bool(body.get("simulate_price_change")), inject=bool(body.get("inject")),
                         real_clock=bool(body.get("real_clock")), purpose=purpose)
    finally:
        recalls.TEST.reset(token)


# ---- Week plan: the planner page (/plan) -----------------------------------------------------------------------

def _human(request: Request):
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "plans are made and signed by people, not agents")


@app.post("/v1/preplan", response_model=preplanner.PreplanResponse, tags=["v1: lookup"],
          dependencies=[Security(HTTPBearer(auto_error=False))])
def preplan_request(body: preplanner.PreplanRequest):
    """Prepare a purchase or inquiry and inspect buyer/agent limits. Never signs an offer or orders."""
    try:
        return preplanner.prepare(body)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/plan", response_class=HTMLResponse, include_in_schema=False)
def plan_page():
    return (TEMPLATES / "plan.html").read_text()


@app.get("/api/planner/search", tags=["demo"])
def planner_search(q: str, mandate_id: str = "mnd_hotel_helsinki", attributes: str = "", limit: int = 12):
    """Catalog search for the planner, with each product checked against the buyer's mandate up front."""
    if mandate_id not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    m = policy.load(mandate_id)
    found, trace = catalog.search(q, [a for a in attributes.split(",") if a], min(limit, 20))
    out = []
    for p in found:
        problem = policy.allergen_problem(m, p)
        missing = [c for c in m["hard_limits"].get("required_claims", [])
                   if c not in p["claims"] and not (c == "FREE_FROM_LACTOSE" and p.get("lactose") == 0)]
        out.append(dict(catalog.describe(p), mandate_check=(
            {"ok": False, "level": "block", "why": problem[0]} if problem and problem[1] == "block" else
            {"ok": False, "level": "block", "why": "not " + ", ".join(c.lower().replace("_", " ") for c in missing)} if missing else
            {"ok": True, "level": "check", "why": problem[0]} if problem else {"ok": True, "level": "ok", "why": "fits the mandate"})))
    return {"trace": trace, "products": out}


@app.post("/api/planner/prices", tags=["demo"])
def planner_prices(body: dict = Body(...)):
    """Live price and availability for the planned products (at most 20), so the budget is real."""
    ids = [str(i) for i in (body.get("product_ids") or [])][:20]
    mandate_id = body.get("mandate_id", "mnd_hotel_helsinki")
    if mandate_id not in policy.mandates() or not ids:
        raise HTTPException(422, "give mandate_id and product_ids")
    m = policy.load(mandate_id)
    c = agent.ValioClient()
    try:
        prices = c.prices(m["customer"], body.get("deliver_by") or "", ids)
        now_s, avail = c.availability(m["customer"], body.get("deliver_by") or "", ids)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Valio didn't answer ({type(e).__name__})")
    rows = {}
    for pid in ids:
        av = avail.get(pid) or {}
        rows[pid] = {"unitPrice": agent._price(prices.get(pid)), "salesBlock": bool(av.get("salesBlock")),
                     "stockConfirmed": av.get("logisticModel") == "Stock",
                     "available": sum(agent._amount(x) for x in av.get("scheduleLines", []) if not x.get("salesBlock")),
                     "deadlinePassed": agent._passed(av.get("closingTime"), now_s)}
    return {"serverTime": now_s, "products": rows}


@app.post("/api/planner/from-recipe", tags=["demo"])
def planner_from_recipe(request: Request, body: dict = Body(...)):
    """Turn a recipe + covers into candidate plan lines for the chef to edit. Nothing is ordered or signed."""
    _human(request)
    out, err = recipe.read(body.get("mandate_id"), body.get("text"), body.get("covers"), body.get("serves"),
                           str(body.get("deliver_by") or ""), type_check=ai.type_check if ai.enabled() else None)
    if err:
        raise HTTPException(409, err)
    return out


@app.get("/api/unlock", tags=["demo"])
def unlock_report(mandate_id: str = ""):
    """Which missing product data, if Valio added it, would let the most held orders through alone (what-if)."""
    return unlock.report(mandate_id or None)


@app.get("/api/learning", tags=["demo"])
def learned(mandate_id: str = "mnd_hotel_helsinki"):
    """Suggestions from past human decisions and ask-backs. Suggestions only: nothing is applied automatically."""
    if mandate_id not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    return learning.suggestions(mandate_id)


@app.get("/api/plans", tags=["demo"])
def list_plans(mandate_id: str = "mnd_hotel_helsinki"):
    return [plans.view(p) for p in store.plans_for(mandate_id)]


@app.post("/api/plans", tags=["demo"])
def create_plan(request: Request, body: dict = Body(...)):
    _human(request)
    plan, err = plans.create(body.get("mandate_id"), body.get("lines"), body.get("start"), body.get("end"),
                             body.get("max_total_eur"), str(body.get("created_by") or "buyer"), str(body.get("note") or ""))
    if err:
        raise HTTPException(409, err)
    return plans.view(plan)


@app.post("/api/plans/{plan_id}/approve", tags=["demo"])
def approve_plan(request: Request, plan_id: str, body: dict = Body(...)):
    _human(request)
    plan, err = plans.approve(plan_id, str(body.get("approver") or ""), str(body.get("comment") or ""))
    if err:
        raise HTTPException(409, err)
    return plans.view(plan)


@app.post("/api/plans/{plan_id}/revoke", tags=["demo"])
def revoke_plan(request: Request, plan_id: str, body: dict = Body(...)):
    _human(request)
    plan, err = plans.revoke(plan_id, str(body.get("by") or ""), str(body.get("reason") or ""))
    if err:
        raise HTTPException(409, err)
    return plans.view(plan)


@app.get("/v1/plans", tags=["v1: lookup"])
def agent_plans(request: Request, mandate_id: str):
    """The buyer's signed week plans (read-only): what the agent may order without asking, and what's left."""
    if mandate_id not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    if err := agent.caller_refusal(mandate_id):
        raise HTTPException(403, err)
    return [plans.view(p) for p in store.plans_for(mandate_id, "approved")]


@app.post("/api/photo", tags=["demo"])
async def read_photo(request: Request, photo: UploadFile = File(...), deliver_by: str = Form("")):
    """Read a photographed order list into request lines (Nemotron 3.5 Super VL, or Claude without an NVIDIA key).
    Nothing is ordered here: each line then runs through the six checks like a typed request."""
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "people only")
    data = await photo.read()
    import asyncio
    out = await asyncio.to_thread(vision.read_list, data, photo.content_type or "", deliver_by or None)
    if out.get("error"):
        raise HTTPException(422, out["error"])
    for line in out["lines"]:
        findings = security.scan_request(line["written"] + " " + line["product"])
        if findings:
            line["blocked"] = "instructions aimed at the agent: not read"
            store.log("security", "photo", "security.injection_in_request", {"findings": [f["why"] for f in findings][:3]})
    store.log("photo", "person", "photo.read", {"reader": out["reader"], "lines": len(out["lines"])})
    return out


@app.get("/play", response_class=HTMLResponse, include_in_schema=False)
def play_page():
    return (TEMPLATES / "play.html").read_text()


@app.get("/api/game", tags=["demo"])
def game_state():
    return {"missions": game.MISSIONS, "board": game.board()}


@app.post("/api/game/attempt", tags=["demo"])
def game_attempt(request: Request, body: dict = Body(...)):
    """One attempt to sneak an order past the gateway. Runs the real agent and gateway; people only."""
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "people only")
    try:
        return game.play(body.get("mission"), body.get("text"), body.get("nick"))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/receipt-link/{order_id}", tags=["demo"])
def receipt_link(request: Request, order_id: str):
    """Signed link and QR for an order's public receipt (people only)."""
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "people only")
    if not store.get_order(order_id):
        raise HTTPException(404, "unknown order")
    url = f"{_public_base(request)}/r/{order_id}?t={receipt.token(order_id)}"
    import segno
    return {"url": url, "svg": segno.make(url, error="m").svg_inline(scale=4, dark="#0047bb", border=1)}


@app.get("/r/{order_id}", response_class=HTMLResponse, include_in_schema=False)
def receipt_page(order_id: str, t: str = ""):
    """Public receipt: every claim behind the order re-checked live. Needs the signed link."""
    if not receipt.check_token(order_id, t):
        return HTMLResponse(_receipt_html("Not a valid receipt link", "<p class=muted>Scan the QR code on the order again.</p>"), 404)
    r = receipt.verify(order_id)
    if not r:
        return HTMLResponse(_receipt_html("No such order", ""), 404)
    o = r["order"]
    status = {"placed": "Ordered", "pending_approval": "Waiting for a person", "blocked": "Stopped", "rejected": "Rejected"}.get(o["status"], o["status"])
    lines = "".join(f"<div class=line><span>{escape(str(l['units']))} × {escape(l['name'])}</span><b>€{l['total']:,.2f}</b></div>" for l in o["lines"])
    checks = "".join(f"<li class={'ok' if c['ok'] else 'bad'}><i>{'✓' if c['ok'] else '×'}</i><div><b>{escape(c['check'])}</b>"
                     f"<span>{escape(c['why'])}</span></div></li>" for c in r["checks"])
    body = (f"<div class=sum><div><small>{escape(o['buyer'])} · {escape(o['id'])}</small><h2>{status}</h2></div><div class=eur>€{o['total']:,.2f}</div></div>"
            f"<div class=lines>{lines}</div>"
            f"<div class='verdict {'ok' if r['all_ok'] else 'bad'}'>{'Every claim checks out' if r['all_ok'] else 'Something does not check out'}"
            f"<small>re-checked just now, not read from a stored status</small></div><ul class=checks>{checks}</ul>"
            f"<details><summary>Check it yourself</summary><p>Valio's public key: <a href='/.well-known/jwks.json'>/.well-known/jwks.json</a> "
            f"(key id {escape(r['kid'])}). The signed offer (compact JWS, EdDSA):</p><pre>{escape(r['jws'] or '')}</pre></details>")
    return _receipt_html("Receipt", body)


def _receipt_html(title, body):
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{escape(title)} · Vahti</title><link rel=icon href="data:,"><link rel=stylesheet href="/static/style.css">
<style>body{{background:#f2f5fb}} main{{max-width:560px;margin:0 auto;padding:22px 16px 40px}}
.card{{background:#fff;border-radius:22px;padding:22px 22px 18px;box-shadow:0 6px 24px rgba(10,31,77,.07)}}
.sum{{display:flex;justify-content:space-between;align-items:flex-end;gap:12px}} .sum small{{color:var(--mute)}} .sum h2{{margin:2px 0 0;font-size:26px;font-weight:800;letter-spacing:-.02em}}
.eur{{font-size:30px;font-weight:900;letter-spacing:-.03em}} .lines{{margin:14px 0;border-top:1px dashed var(--line);padding-top:10px}}
.line{{display:flex;justify-content:space-between;gap:12px;padding:4px 0;font-size:14.5px}}
.verdict{{border-radius:14px;padding:12px 16px;font-weight:800;font-size:17px}} .verdict.ok{{background:var(--ok-soft);color:var(--ok)}} .verdict.bad{{background:var(--bad-soft);color:var(--bad)}}
.verdict small{{display:block;font-weight:500;font-size:12.5px;color:var(--mute)}}
.checks{{list-style:none;padding:0;margin:14px 0 6px}} .checks li{{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--line)}}
.checks i{{font-style:normal;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:800;flex-shrink:0;color:#fff}}
.checks .ok i{{background:var(--ok)}} .checks .bad i{{background:var(--bad)}} .checks b{{display:block}} .checks span{{color:var(--mute);font-size:13.5px;overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;word-break:break-all;font-size:11px;background:#f5f8fc;border-radius:10px;padding:10px}}</style></head><body>
<header class=topbar><div class=inner><a class=brand href="#" aria-label="Vahti"><svg class=mark viewBox="0 0 64 12" aria-hidden=true><path d="M5 6H59" stroke=currentColor stroke-width=2 opacity=.55 />{''.join(f'<circle cx="{5 + i * 10.8:.1f}" cy=6 r="{4.2 if i == 5 else 3.4}" fill="{"#ffb02e" if i == 5 else "currentColor"}"/>' for i in range(6))}</svg><span class=word>vahti</span><span class=for>verified receipt</span></a></div></header>
<main><div class=card>{body}</div><p class="small muted" style="text-align:center;margin-top:14px">Vahti for Valio Aimo · demo data</p></main></body></html>"""


_SIM = {"proc": None, "path": None}


@app.get("/live", response_class=HTMLResponse, include_in_schema=False)
def live_page():
    return (TEMPLATES / "live.html").read_text()


@app.post("/api/sim/start", tags=["demo"])
def sim_start(request: Request):
    """Start a Monday-morning simulation in its own process and fresh state (people only)."""
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "people only")
    import os, subprocess, sys, tempfile, time
    if _SIM["proc"] and _SIM["proc"].poll() is None:
        return {"running": True, "id": _SIM["id"]}
    from .config import ROOT, STATE_DIR
    run_dir = tempfile.mkdtemp(prefix="sim-", dir=str(STATE_DIR))
    path = os.path.join(run_dir, "events.jsonl")
    env = dict(os.environ, TG_STATE_DIR=run_dir, GATEWAY_DB=os.path.join(run_dir, "gateway.db"))
    _SIM.update(proc=subprocess.Popen([sys.executable, "-m", "gateway.sim", path], cwd=str(ROOT), env=env,
                                      stdout=subprocess.DEVNULL, stderr=open(os.path.join(run_dir, "log.txt"), "w")),
                path=path, id=str(int(time.time())))
    return {"running": True, "id": _SIM["id"]}


@app.get("/api/sim/events", tags=["demo"])
def sim_events(since: int = 0):
    import json, os
    path = _SIM.get("path")
    if not path or not os.path.exists(path):
        return {"id": _SIM.get("id"), "events": [], "running": bool(_SIM["proc"] and _SIM["proc"].poll() is None)}
    lines = open(path, encoding="utf-8").read().splitlines()
    return {"id": _SIM["id"], "events": [json.loads(x) for x in lines[since:] if x.strip()], "next": len(lines),
            "running": _SIM["proc"].poll() is None}


@app.get("/api/trust", tags=["demo"])
def trust_report():
    """Earned trust per buyer and agent channel: level, clean orders, how far it may order alone."""
    return trust.everyone(policy.mandates().values())


@app.get("/rules", response_class=HTMLResponse, include_in_schema=False)
def rules_page():
    return (TEMPLATES / "rules.html").read_text()


@app.get("/api/rules/sample", tags=["demo"])
def rules_sample():
    return {"text": onboard.SAMPLE}


@app.post("/api/rules/draft", tags=["demo"])
async def rules_draft(request: Request, policy_file: UploadFile | None = File(None), text: str = Form("")):
    """Claude drafts the rules from a policy document, quoting the source of each (people only)."""
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "people only")
    pdf = await policy_file.read() if policy_file else None
    if pdf and not pdf.startswith(b"%PDF"):
        text, pdf = pdf.decode("utf-8", "replace"), None
    import asyncio
    try:
        return await asyncio.to_thread(onboard.draft, text or None, pdf)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/rules/sign", tags=["demo"])
def rules_sign(request: Request, body: dict = Body(...)):
    """A person signs the reviewed rules: the buyer exists from now on (people only)."""
    if getattr(request.state, "principal", None) != "human":
        raise HTTPException(403, "plans and rules are signed by people, not agents")
    mandate, problems = onboard.sign(body.get("fields") or {}, body.get("signer"))
    if problems:
        raise HTTPException(422, "; ".join(problems))
    store.log(mandate["mandate_id"], f"human:{mandate['signed_by']}", "mandate.signed_from_policy", {"buyer": mandate["buyer"]})
    return {"mandate": mandate, "token": mandate_token(mandate["mandate_id"])}


@app.get("/api/info", tags=["demo"])
def info():
    return {"ai_parser": ai.enabled(), "model": ai.MODEL if ai.enabled() else None, "demo_clock": DEMO_CLOCK,
            "photo_reader": vision.reader_name(), "reader": ai.reader_name(),
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


@app.get("/concepts/themes", response_class=HTMLResponse, include_in_schema=False)
def theme_options():
    """Visual theme candidates (docs/visuals/themes), for the team."""
    from .config import ROOT
    return (ROOT / "docs" / "visuals" / "themes" / "index.html").read_text()


@app.get("/concepts/sleek", response_class=HTMLResponse, include_in_schema=False)
def sleek_concepts():
    """Layout concepts with less text (docs/visuals/sleek), for the team."""
    from .config import ROOT
    return (ROOT / "docs" / "visuals" / "themes" / "sleek.html").read_text()


@app.get("/concepts/themes/{name}", include_in_schema=False)
def theme_image(name: str):
    from fastapi.responses import FileResponse
    from .config import ROOT
    f = ROOT / "docs" / "visuals" / "themes" / name
    if not (name.endswith(".png") and "/" not in name and f.is_file()):
        raise HTTPException(404, "not found")
    return FileResponse(f, media_type="image/png")


@app.get("/concepts/{name}", include_in_schema=False)
def concept_image(name: str):
    from fastapi.responses import FileResponse
    from .config import ROOT
    if name not in {f"concept_{k}.png" for k in "ABCDEFGHIJKLM"}:
        raise HTTPException(404, "not found")
    return FileResponse(ROOT / "docs" / "visuals" / name, media_type="image/png")


@app.get(PRM_PATH, include_in_schema=False)
def protected_resource_metadata(request: Request):
    """RFC 9728 metadata for the MCP and REST endpoints: what the resource is, how to send a token, which scopes
    exist. Tokens come from a signed-in person on /connect: a purchase token has all three scopes, an inquiry token
    has no orders:place and gets 403 insufficient_scope if it tries."""
    base = str(request.base_url).rstrip("/")
    return {"resource": f"{base}/mcp", "bearer_methods_supported": ["header"],
            "scopes_supported": SCOPES["purchase"].split(), "resource_name": "Vahti trade gateway for Valio Aimo",
            "resource_documentation": f"{base}/connect", "authorization_servers": [],
            "vahti_token_issuance": "per buyer on /connect (purchase or inquiry); no OAuth server in this prototype"}


@app.get("/connect", response_class=HTMLResponse, include_in_schema=False)
def connect_page(request: Request):
    page = (TEMPLATES / "connect.html").read_text()
    if getattr(request.state, "principal", None) == "human" and AGENT_TOKEN:  # people hand these to their agent
        import json as _json
        tokens = {mid: {"buyer": m["buyer"].replace(" (demo)", ""), "token": mandate_token(mid),
                        "inquiry_token": mandate_token(mid, "inquiry")} for mid, m in policy.mandates().items()}
        page = page.replace("<!--MANDATE_TOKENS-->", f"<script>window.TOKENS = {_json.dumps(tokens)};</script>")
    return page


def _own(request: Request, o):
    """Hide another buyer's order from an agent whose token is bound to a different mandate."""
    bound = getattr(request.state, "mandate", None)
    if o and bound and o["mandate_id"] != bound:
        store.log(o["id"], f"agent:{bound}", "security.mandate_mismatch", {"token_for": bound, "tried": o["mandate_id"]})
        return None
    return o


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
                    "approver": (o["policy"].get("approver") or {}).get("name"),
                    "gate": (o["policy"].get("ladder") or {}).get("decided_by"),
                    "why": next((r["detail"] for r in o["policy"]["rules"] if not r["ok"]), "")} for o in orders[:25]],
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
def quote(req: schemas.QuoteRequest):
    """Price and availability for specific lines, returned as a signed fixed-price offer (valid 15 min)."""
    body = req.model_dump(exclude_none=True)
    body["lines"] = [l.model_dump(exclude_none=True) for l in req.lines]
    if body.get("mandate_id") not in policy.mandates():
        raise HTTPException(404, "unknown mandate")
    if err := agent.caller_refusal(body["mandate_id"]):
        raise HTTPException(403, err)
    offer, jws, err = agent.quote(body["mandate_id"], body["deliver_by"], body.get("lines") or [],
                                  requested_quantity=body.get("requested_quantity"), requested_unit=body.get("requested_unit"),
                                  limits=body.get("request_limits"))
    if err:
        raise HTTPException(409, err)
    return {"offer": offer, "jws": jws}


@app.post("/v1/orders", tags=["v1: order"])
def order(request: Request, req: schemas.OrderRequest, idempotency_key: str = Header(...)):
    """Commit a signed offer, by `offer_id` or the full `jws`. The gateway verifies the signature, re-checks
    prices and applies the mandate."""
    body = req.model_dump(exclude_none=True)
    jws = body.get("jws") or (store.get_offer(body.get("offer_id") or "") or {}).get("jws")
    if not jws:
        raise HTTPException(422, "give offer_id or jws")
    o, err = agent.order_from_jws(jws, idempotency_key, body.get("rationale", ""), body.get("requested_units"))
    if not o:
        if "inquiries only" in err:  # an inquiry token: RFC 6750 / MCP spec "insufficient_scope"
            raise HTTPException(403, err, headers={"WWW-Authenticate": _www_authenticate(
                request, "insufficient_scope", "orders:place")})
        raise HTTPException(403 if err.startswith("this agent token acts for") else 409, err)
    return o


@app.get("/v1/orders/{order_id}", tags=["v1: order"])
def get_order(request: Request, order_id: str):
    o = _own(request, store.get_order(order_id))
    if not o:
        raise HTTPException(404, "unknown order")
    return o


@app.post("/v1/approvals/{order_id}", tags=["v1: approval"])
def approve(request: Request, order_id: str, req: schemas.ApprovalRequest):
    """Human sign-off. Only a signed-in person can call this (agents' Bearer tokens get 403), and the
    approver must be named in the buyer's mandate with authority for the order total."""
    body = req.model_dump()
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
        elif r["rule"] == "Earned trust":
            out.append(str(r["detail"]).split(";")[0])  # "New built-in agent (0 clean orders): may order alone up to …"
        else:
            d = str(r["detail"])
            out.append(f'{r["rule"].lower()}: {d if len(d) <= 100 else d[:100].rsplit(" ", 1)[0] + "…"}')
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
def decision(request: Request, order_id: str):
    """Human-readable decision record plus the audit events for this order."""
    o = _own(request, store.get_order(order_id))
    if not o:
        raise HTTPException(404, "unknown order")
    return {"order": o, "events": store.events(o["id"]) + store.events(o["offer_id"]), "chain": store.verify_chain()}


@app.post("/v1/offers/verify", tags=["v1: trust"])
def verify_offer(req: schemas.VerifyOfferRequest):
    ok, payload = signing.verify(req.jws)
    if not ok:
        store.log("security", "verifier", "security.invalid_signature", {"error": payload})
    return {"valid": ok, "payload": payload if ok else None, "error": None if ok else payload}


@app.get("/.well-known/jwks.json", tags=["v1: trust"])
def well_known_jwks():
    """Public keys: Ed25519 for offer signatures, ES256 for AP2-style checkout JWTs."""
    return {"keys": signing.jwks()["keys"] + [ap2.jwk()]}


@app.get("/.well-known/agent-card.json", include_in_schema=False)
def agent_card(request: Request):
    """A2A Agent Card: how other agents discover Vahti (public)."""
    return a2a.card(_public_base(request))


@app.post("/a2a", tags=["v1: A2A"])
def a2a_rpc(request: Request, body: dict = Body(...)):
    """A2A JSON-RPC 2.0 (SendMessage, GetTask; v0.3 names too). Text is answered as an inquiry and never orders;
    quoting and ordering take data parts and run the same checks as REST and MCP. Bearer agent token."""
    if getattr(request.state, "principal", None) != "agent":
        raise HTTPException(403, "A2A is for agents: use a per-buyer agent token from /connect")
    return a2a.handle(body, getattr(request.state, "mandate", None))


@app.get("/v1/offers/{offer_id}/ap2", tags=["v1: standards"])
def offer_ap2(request: Request, offer_id: str):
    """The offer as an AP2-style merchant-signed checkout JWT (ES256) with its checkout_hash."""
    stored = store.get_offer(offer_id)
    bound = getattr(request.state, "mandate", None)
    if not stored or (bound and stored["mandate_id"] != bound):
        raise HTTPException(404, "unknown offer")
    return ap2.checkout_jwt(stored["payload"])


@app.get("/v1/orders/{order_id}/peppol.xml", tags=["v1: standards"])
def peppol_xml(request: Request, order_id: str):
    """The placed order as a Peppol BIS Ordering 3 document (UBL 2.1 Order), ready for an ERP or access point."""
    o = _own(request, store.get_order(order_id))
    if not o:
        raise HTTPException(404, "unknown order")
    if o["status"] != "placed":
        raise HTTPException(409, f"order is {o['status']}: a Peppol order is only issued once it is placed")
    xml = standards.peppol_order(o, policy.mandates()[o["mandate_id"]], _public_base(request))
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'inline; filename="{order_id}.xml"'})


@app.get("/v1/mandates/{mandate_id}/credential", tags=["v1: standards"])
def mandate_credential(request: Request, mandate_id: str):
    """The buyer-signed mandate as a W3C Verifiable Credential (vc+jwt), plus its decoded form."""
    m = policy.mandates().get(mandate_id)
    bound = getattr(request.state, "mandate", None)
    if not m or (getattr(request.state, "principal", None) == "agent" and bound and bound != mandate_id):
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

