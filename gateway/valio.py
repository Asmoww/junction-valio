"""Client for the Valio Aimo mock pricing and availability APIs. Counts every call.

Rate limits (Valio, 2026-11-14): a few hundred calls a minute is fine, bursts are not; going over returns 429
and blocks the key for 30 seconds. So calls are throttled per process, and a 429 pauses every call until the
block ends, then retries.
"""
import threading
import time
from collections import deque

import httpx

from .config import VALIO_API_BASE_URL, VALIO_API_KEY


WINDOW_S, MAX_CALLS = 10, 30   # at most 30 calls in any 10 s (180 a minute)
BLOCK_S = 30                     # how long Valio blocks the key after a 429
_recent = deque()
_blocked_until = 0.0
_gate = threading.Lock()


def _wait_turn():
    """Block until a call fits the rate budget and no 429 block is active."""
    while True:
        with _gate:
            now = time.monotonic()
            while _recent and now - _recent[0] > WINDOW_S:
                _recent.popleft()
            wait = max(_blocked_until - now, (_recent[0] + WINDOW_S - now) if len(_recent) >= MAX_CALLS else 0)
            if wait <= 0:
                _recent.append(now)
                return
        time.sleep(min(wait, 1.0))


def _block(seconds):
    global _blocked_until
    with _gate:
        _blocked_until = max(_blocked_until, time.monotonic() + seconds)


class ValioClient:
    def __init__(self):
        self.calls = []  # (endpoint, n_products, ms) for the current agent run

    def _post(self, endpoint, customer, date, product_ids):
        t = time.monotonic()
        for attempt in range(3):
            _wait_turn()
            r = httpx.post(
                f"{VALIO_API_BASE_URL}/{endpoint}",
                headers={"X-API-Key": VALIO_API_KEY},
                json={"customer": customer, "requestedDate": date, "products": list(product_ids)},
                timeout=30,
            )
            if r.status_code == 429:  # blocked: wait out the block (Retry-After if given), then try again
                ra = r.headers.get("retry-after", "")
                _block(float(ra) if ra.replace(".", "", 1).isdigit() else BLOCK_S)
                continue
            if r.status_code < 500:
                break
            time.sleep(0.5 * 2 ** attempt)
        self.calls.append({"endpoint": f"POST /api/{endpoint}", "products": len(product_ids),
                           "date": date, "ms": round((time.monotonic() - t) * 1000), "status": r.status_code})
        r.raise_for_status()
        return r.json()

    def prices(self, customer, date, product_ids):
        return {p["productId"]: p for p in self._post("price", customer, date, product_ids)["products"]}

    def availability(self, customer, date, product_ids):
        data = self._post("availability", customer, date, product_ids)
        return data["timeNow"], {p["productId"]: p for p in data["products"]}
