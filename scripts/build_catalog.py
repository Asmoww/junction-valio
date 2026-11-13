"""Build a slim product index from the challenge data files.

Usage: python scripts/build_catalog.py [data_dir]
Reads the product JSON, delivery reliability CSV and carbon footprint CSV from data_dir (default
data/) and writes data/catalog.json, a small index the gateway loads at startup.
"""
import csv
import json
import sys
from pathlib import Path

data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")


def text(values, lang):
    return next((v["value"] for v in values or [] if v.get("language") == lang), "")


def nutrient(synkka, code):
    """Value per 100 g/ml of a nutrient (e.g. LACS lactose, FAT), or None if not declared."""
    return next((n["value"] for n in synkka.get("nutritionalContent") or [] if n.get("id") == code), None)


def read_csv(name, col):
    out = {}
    with open(data / name, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["productId"]] = float(row[col])
            except (ValueError, KeyError):
                pass
    return out


reliability = read_csv("valio_aimo_junction26_delivery_reliability.csv", "deliveryReliability")
carbon = read_csv("valio_aimo_junction26_carbon_footprint.csv", "AVG kg CO2-eq/kg")
products = json.load(open(data / "valio_aimo_junction26_product_data.json", encoding="utf-8"))

catalog = []
for p in products:
    s = p.get("synkkaData") or {}
    groups = {c["name"]: c.get("values") or [] for c in s.get("classifications", [])}
    net = next((u.get("netWeight") for u in s.get("unitConversions") or [] if u.get("netWeight")), None)
    catalog.append({
        "id": p["id"],
        "name": text(s.get("names"), "en") or text(s.get("names"), "fi"),
        "name_fi": text(s.get("names"), "fi"),
        "brand": s.get("brand"),
        "vendor": p.get("vendorName"),
        "category": p.get("category"),
        "salesUnit": p.get("salesUnit"),
        "baseUnit": p.get("baseUnit"),
        "lot": p.get("allowedLotSize") or 1,
        "units": {u["unitId"]: u.get("sizeInBaseUnits") for u in p.get("units") or []},
        "origin": p.get("countryOfOrigin"),
        "contains": sorted({v["id"] for v in groups.get("allergen", []) if v.get("unit") == "CONTAINS"}),
        "mayContain": sorted({v["id"] for v in groups.get("allergen", []) if v.get("unit") == "MAY_CONTAIN"}),
        "undeclared": sorted({v["id"] for v in groups.get("allergen", []) if v.get("unit") == "UNDECLARED"}),
        # No allergen or free-from values at all means "unknown", never "allergen-free".
        "allergenData": bool(groups.get("allergen") or groups.get("nonAllergen")),
        "lactoseG": nutrient(s, "LACS"),
        "fatPct": nutrient(s, "FAT"),
        "freeFrom": sorted({v["id"] for v in groups.get("nonAllergen", []) if v.get("unit") == "FREE_FROM"}),
        "claims": sorted({v["id"] for v in groups.get("nutritionalClaim", [])}),
        "labels": sorted({v["id"] for v in groups.get("packagingMarkedLabel", []) if v["id"].isupper()}),
        "netWeightKg": (net["value"] / 1000 if net["unit"] == "GRM" else net["value"]) if net else None,
        "reliability": reliability.get(p["id"]),
        "carbonKgPerKg": carbon.get(p["id"]),
        "substitutes": [x["substituteProduct"] for x in p.get("substitutions") or []],
    })

out = data / "catalog.json"
json.dump(catalog, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"{len(catalog)} products -> {out} ({out.stat().st_size // 1024} KB)")
