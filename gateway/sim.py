"""Monday morning: kitchens' agents order at once, through the real gateway, on a map.

Runs as its own process with a fresh state folder (TG_STATE_DIR), so the demo database stays clean and every run
starts with full allowances. Same gateway code and Valio's real API: every decision on the map is a real one.
Each simulated agent orders the way an outside agent does (signed offer -> order), without the language model.

  python -m gateway.sim <events.jsonl>        (started by POST /api/sim/start)
"""
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

H, S, W = "mnd_hotel_helsinki", "mnd_school_espoo", "mnd_hospital_ward"
HUB = {"name": "Valio Aimo", "lat": 60.2934, "lon": 24.9687}
SITES = {
    "Aurora Kamppi": (H, 60.1690, 24.9330), "Aurora Katajanokka": (H, 60.1676, 24.9660), "Aurora Pasila": (H, 60.1990, 24.9330),
    "Aurora Otaniemi": (H, 60.1860, 24.8280), "Aurora Airport": (H, 60.3170, 24.9600),
    "Tapiola school": (S, 60.1757, 24.8050), "Leppävaara school": (S, 60.2190, 24.8130), "Matinkylä school": (S, 60.1600, 24.7370),
    "Espoonlahti school": (S, 60.1480, 24.6600), "Olari school": (S, 60.1720, 24.7380), "Kilo school": (S, 60.2180, 24.7810),
    "Soukka school": (S, 60.1390, 24.6720), "Kalajärvi school": (S, 60.2560, 24.7290),
    "Jorvi ward 4": (W, 60.2186, 24.6862), "Meilahti ward 7": (W, 60.1890, 24.9020), "Malmi ward 2": (W, 60.2510, 25.0130),
    "Peijas ward 3": (W, 60.3300, 25.0640),
}
# Products Valio stocks itself and that have allergen data, so a routine order can go through alone
MILK, QUARK, YOG, BUTTER, OAT, JUICE = "469363", "477086", "470136", "476740", "472833", "464385"
MUESLI, PORRIDGE, RICE, BREAD, CHEESE, PEANUT = "467135", "472413", "469296", "464637", "475719", "475383"
# (site, product, units, kind): kind says what the scenario expects, the gateway decides
SCENARIO = [
    ("Aurora Kamppi", MILK, 40, "routine"), ("Tapiola school", OAT, 30, "routine"), ("Jorvi ward 4", OAT, 24, "routine"),
    ("Aurora Katajanokka", QUARK, 20, "routine"), ("Leppävaara school", PORRIDGE, 6, "routine"), ("Meilahti ward 7", RICE, 1, "routine"),
    ("Aurora Pasila", YOG, 4, "routine"), ("Matinkylä school", PEANUT, 4, "attack: peanuts for a nut-free school"),
    ("Malmi ward 2", BREAD, 10, "routine"), ("Aurora Otaniemi", BUTTER, 20, "routine"), ("Espoonlahti school", MUESLI, 2, "routine"),
    ("Peijas ward 3", MILK, 30, "attack: milk for a milk-ban ward"), ("Aurora Airport", CHEESE, 40, "big: needs the head chef"),
    ("Olari school", JUICE, 3, "routine"), ("Jorvi ward 4", PORRIDGE, 4, "routine"), ("Aurora Kamppi", JUICE, 4, "routine"),
    ("Kilo school", MILK, 30, "routine"), ("Meilahti ward 7", "462421", 10, "attack: lactose-free butter for a milk-ban ward"),
    ("Aurora Katajanokka", RICE, 2, "routine"), ("Soukka school", BREAD, 12, "routine"), ("Malmi ward 2", OAT, 20, "routine"),
    ("Aurora Pasila", YOG, 420, "attack: over the €5,000 ceiling"), ("Kalajärvi school", MUESLI, 9, "big: over the school's €300"),
    ("Peijas ward 3", RICE, 2, "routine"), ("Aurora Otaniemi", MILK, 30, "routine"), ("Tapiola school", JUICE, 2, "routine"),
    ("Jorvi ward 4", BREAD, 8, "routine"), ("Aurora Airport", QUARK, 16, "routine"), ("Leppävaara school", OAT, 20, "routine"),
    ("Meilahti ward 7", PORRIDGE, 3, "routine"),
]


def main(path):
    from . import agent, catalog  # imported here: the state folder comes from the environment of this process
    lock, out = threading.Lock(), open(path, "a", buffering=1)
    deliver = (date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)).isoformat()  # next Monday
    t0 = time.monotonic()

    def emit(e):
        with lock:
            out.write(json.dumps(e, ensure_ascii=False) + "\n")

    emit({"type": "start", "hub": HUB, "sites": [{"name": n, "buyer": b, "lat": la, "lon": lo} for n, (b, la, lo) in SITES.items()],
          "orders": len(SCENARIO), "deliver_by": deliver})

    def one(i):
        site, pid, units, kind = SCENARIO[i]
        buyer, lat, lon = SITES[site]
        time.sleep(max(0, t0 + i * 1.5 + random.random() * 0.8 - time.monotonic()))  # a morning in about a minute
        start = time.monotonic()
        p = catalog.get(pid) or {}
        e = {"type": "order", "i": i, "site": site, "buyer": buyer, "lat": lat, "lon": lon, "kind": kind,
             "product": (p.get("name") or pid)[:60], "units": units, "t": round(time.monotonic() - t0, 1)}
        offer, jws, err = agent.quote(buyer, deliver, [{"productId": pid, "units": units}])
        if err:
            e.update(outcome="refused", why=err, total=None)
        else:
            order, oerr = agent.order_from_jws(jws, f"sim-{i}")
            if not order:
                e.update(outcome="refused", why=oerr, total=offer["total_eur"])
            else:
                failed = [r for r in order["policy"]["rules"] if not r["ok"]]
                e.update(outcome={"placed": "placed", "pending_approval": "person", "blocked": "stopped"}.get(order["status"], order["status"]),
                         total=order["total"], why=(failed[0]["rule"] + ": " + failed[0]["detail"]) if failed else "every check passed",
                         gate=(order["policy"].get("ladder") or {}).get("decided_by"))
        e["ms"] = round((time.monotonic() - start) * 1000)
        emit(e)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(one, range(len(SCENARIO))))
    emit({"type": "done", "t": round(time.monotonic() - t0, 1)})


if __name__ == "__main__":
    main(sys.argv[1])
