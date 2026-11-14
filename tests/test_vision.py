"""Photographed order list: the reader only transcribes; each line becomes a plain request."""
import json

import pytest

from gateway import vision


def fake(answer):
    return lambda b64, media_type, prompt: json.dumps(answer)


def test_lines_become_plain_requests(monkeypatch):
    monkeypatch.setattr(vision, "_ask_claude", fake({"lines": [
        {"written": "40 l laktoositon maito", "quantity": 40, "unit": "L", "product": "lactose-free milk", "needed_by": None, "legible": True},
        {"written": "200 munaa", "quantity": 200, "unit": "PCS", "product": "eggs", "needed_by": "2026-11-17", "legible": True},
        {"written": "voi ??", "quantity": None, "unit": None, "product": "butter", "needed_by": None, "legible": False},
    ], "note": ""}))
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    out = vision.read_list(b"x", "image/jpeg", default_date="2026-11-16")
    reqs = [l["request"] for l in out["lines"]]
    assert reqs == ["Order 40 litres of lactose-free milk by 2026-11-16.", "Order 200 eggs by 2026-11-17.", "Order butter by 2026-11-16."]
    assert [l["legible"] for l in out["lines"]] == [True, True, False]  # no amount: a person fills it in
    assert out["reader"] == "Claude"


def test_nemotron_reads_when_its_key_is_set(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test")
    monkeypatch.setattr(vision, "_ask_nemotron", fake({"lines": [], "note": "empty list"}))
    assert vision.read_list(b"x", "image/png")["reader"] == "Nemotron 3.5 Super VL"


@pytest.mark.parametrize("media_type, size", [("application/pdf", 10), ("image/jpeg", 9 * 1024 * 1024)])
def test_refuses_what_is_not_a_photo(media_type, size):
    assert "error" in vision.read_list(b"x" * size, media_type)


def test_a_failed_read_never_guesses(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(vision, "_ask_claude", lambda *a: "sorry, I can't")
    assert "error" in vision.read_list(b"x", "image/jpeg")


def test_agents_cannot_use_the_photo_endpoint():
    from fastapi.testclient import TestClient
    from gateway import main
    c = TestClient(main.app, base_url="https://testserver")
    r = c.post("/api/photo", files={"photo": ("a.jpg", b"x", "image/jpeg")}, headers={"Authorization": f"Bearer {main.AGENT_TOKEN}"})
    assert r.status_code == 403
