"""A2A and AP2: discovery card, JSON-RPC; text never orders; quotes carry an AP2-style ES256 checkout JWT."""
import base64
import hashlib

from fastapi.testclient import TestClient

from gateway import ap2, main, store


def client(mandate="mnd_hotel_helsinki"):
    return TestClient(main.app, base_url="https://testserver"), {"Authorization": f"Bearer {main.mandate_token(mandate)}"}


def rpc(c, auth, method, params):
    return c.post("/a2a", json={"jsonrpc": "2.0", "id": "1", "method": method, "params": params}, headers=auth).json()


def test_agent_card_is_public_and_describes_the_endpoint():
    c, _ = client()
    card = c.get("/.well-known/agent-card.json").json()
    assert card["supportedInterfaces"][0]["url"].endswith("/a2a") and card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert {s["id"] for s in card["skills"]} == {"inquiry", "quote", "order"}
    assert c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}}).status_code == 401


def test_text_is_an_inquiry_and_never_orders():
    c, auth = client()
    before = len(store.list_orders(100000))
    r = rpc(c, auth, "SendMessage", {"message": {"messageId": "m1", "role": "ROLE_USER",
                                                   "parts": [{"text": "Order 900 litres of lactose-free milk by 2026-11-16."}]}})
    assert r["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(store.list_orders(100000)) == before


def test_quote_carries_a_verifiable_ap2_checkout_and_order_works():
    c, auth = client()
    q = rpc(c, auth, "SendMessage", {"message": {"messageId": "m2", "role": "ROLE_USER", "parts": [{"data": {
        "skill": "quote", "mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16", "lines": [{"productId": "464306", "units": 3}]}}]}})
    data = q["result"]["artifacts"][0]["parts"][1]["data"]
    jwt = data["ap2"]["checkout_jwt"]
    assert jwt.split(".")[0] and ap2.verify(jwt)[0]
    assert data["ap2"]["checkout_hash"] == base64.urlsafe_b64encode(hashlib.sha256(jwt.encode()).digest()).rstrip(b"=").decode()
    assert ap2.verify(jwt)[1]["checkout"]["total"]["amount"] == 303  # €3.03 in cents
    assert any(k.get("alg") == "ES256" for k in c.get("/.well-known/jwks.json").json()["keys"])
    o = rpc(c, auth, "message/send", {"message": {"messageId": "m3", "role": "user", "parts": [{"data": {
        "skill": "order", "offer_id": data["offer"]["offer_id"], "idempotency_key": "k-a2a-1"}}]}})
    assert o["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert rpc(c, auth, "GetTask", {"id": o["result"]["id"]})["result"]["id"] == o["result"]["id"]


def test_a_token_acts_for_its_own_buyer_only():
    c, auth = client("mnd_school_espoo")
    r = rpc(c, auth, "SendMessage", {"message": {"messageId": "m4", "role": "ROLE_USER", "parts": [{"data": {
        "skill": "quote", "mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16", "lines": [{"productId": "464306", "units": 3}]}}]}})
    assert r["result"]["status"]["state"] == "TASK_STATE_REJECTED"


def test_a_tampered_checkout_fails():
    from gateway import agent
    from .conftest import FakeValio
    offer, _, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 4}], FakeValio())
    h, p, s = ap2.checkout_jwt(offer)["checkout_jwt"].split(".")
    assert not ap2.verify(f"{h}.{p[:-4]}AAAA.{s}")[0]


def _msg(parts, ctx="ctx_canary"):
    return {"message": {"messageId": "m", "role": "ROLE_USER", "contextId": ctx, "parts": parts}}


def test_another_buyer_cannot_read_a_task_by_id():
    """Codex, 14 Nov: a school token read a hotel task (and its canary contextId) via GetTask. Every task state,
    every method name and both id forms: only the creating buyer gets it, anyone else gets 'not found'."""
    c, hotel = client()
    _, school = client("mnd_school_espoo")
    _, hospital = client("mnd_hospital_ward")
    quote = {"skill": "quote", "mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16",
             "lines": [{"productId": "464306", "units": 900}]}
    tasks = {"rejected": rpc(c, hotel, "SendMessage", _msg([]))["result"],
             "inquiry": rpc(c, hotel, "SendMessage", _msg([{"text": "What does lactose-free milk cost?"}]))["result"],
             "quote": rpc(c, hotel, "SendMessage", _msg([{"data": quote}]))["result"]}
    offer_id = tasks["quote"]["artifacts"][0]["parts"][1]["data"]["offer"]["offer_id"]
    tasks["input_required"] = rpc(c, hotel, "SendMessage", _msg([{"data": {"skill": "order", "offer_id": offer_id,
                                                                            "idempotency_key": "k-a2a-iso"}}]))["result"]
    assert tasks["input_required"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    for kind, t in tasks.items():
        for method, params in (("GetTask", {"id": t["id"]}), ("tasks/get", {"id": t["id"]}),
                               ("GetTask", {"name": f"tasks/{t['id']}"}), ("tasks/get", {"name": f"tasks/{t['id']}"})):
            own = rpc(c, hotel, method, params)
            assert own["result"]["id"] == t["id"], (kind, method, params)
            for other in (school, hospital):
                r = rpc(c, other, method, params)
                assert "result" not in r and r["error"]["code"] == -32001, (kind, method, params)
                assert "ctx_canary" not in str(r) and t["id"] not in str(r["error"])


def test_a_missing_or_malformed_task_id_is_not_found():
    c, hotel = client()
    for params in ({}, {"id": None}, {"name": 5}, {"id": "task_nope"}):
        assert rpc(c, hotel, "GetTask", params)["error"]["code"] == -32001
