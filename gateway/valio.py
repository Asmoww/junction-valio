"""Client for the Valio Aimo mock pricing and availability APIs. Counts every call."""
import time

import httpx

from .config import VALIO_API_BASE_URL, VALIO_API_KEY


class ValioClient:
    def __init__(self):
        self.calls = []  # (endpoint, n_products, ms) for the current agent run

    def _post(self, endpoint, customer, date, product_ids):
        t = time.monotonic()
        for attempt in range(4):  # the mock API has undisclosed rate limits: back off on 429/5xx
            r = httpx.post(
                f"{VALIO_API_BASE_URL}/{endpoint}",
                headers={"X-API-Key": VALIO_API_KEY},
                json={"customer": customer, "requestedDate": date, "products": list(product_ids)},
                timeout=30,
            )
            if r.status_code != 429 and r.status_code < 500:
                break
            time.sleep(float(r.headers.get("retry-after", 0) or 0) or 0.5 * 2 ** attempt)
        self.calls.append({"endpoint": f"POST /api/{endpoint}", "products": len(product_ids),
                           "date": date, "ms": round((time.monotonic() - t) * 1000), "status": r.status_code})
        r.raise_for_status()
        return r.json()

    def prices(self, customer, date, product_ids):
        return {p["productId"]: p for p in self._post("price", customer, date, product_ids)["products"]}

    def availability(self, customer, date, product_ids):
        data = self._post("availability", customer, date, product_ids)
        return data["timeNow"], {p["productId"]: p for p in data["products"]}
