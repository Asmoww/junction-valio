"""Valio's rate limit: a 429 blocks the key for 30 s, so the client waits it out instead of failing."""
import httpx

from gateway import valio


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


def test_a_429_waits_out_the_block_then_succeeds(monkeypatch):
    answers = [Resp(429), Resp(200, {"products": [{"productId": "1"}]})]
    monkeypatch.setattr(valio.httpx, "post", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(valio, "BLOCK_S", 0.3)
    monkeypatch.setattr(valio, "_blocked_until", 0.0)
    t = valio.time.monotonic()
    c = valio.ValioClient()
    assert c.prices("123", "2026-11-16", ["1"]) == {"1": {"productId": "1"}}
    assert valio.time.monotonic() - t >= 0.3 and not answers


def test_calls_are_throttled_to_the_budget(monkeypatch):
    monkeypatch.setattr(valio.httpx, "post", lambda *a, **k: Resp(200, {"products": []}))
    monkeypatch.setattr(valio, "WINDOW_S", 0.5)
    monkeypatch.setattr(valio, "MAX_CALLS", 3)
    monkeypatch.setattr(valio, "_recent", valio.deque())
    monkeypatch.setattr(valio, "_blocked_until", 0.0)
    c = valio.ValioClient()
    t = valio.time.monotonic()
    for _ in range(4):
        c.prices("123", "2026-11-16", ["1"])
    assert valio.time.monotonic() - t >= 0.4  # the 4th call waited for the window
