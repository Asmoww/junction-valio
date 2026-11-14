"""Settings from the environment (.env in the repo root)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

VALIO_API_BASE_URL = os.environ.get("VALIO_API_BASE_URL") or "https://junction26.valioaimo.fi/api"
VALIO_API_KEY = os.environ.get("VALIO_API_KEY", "")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")  # people sign in with this (browser)
# Agents use a different secret (Authorization: Bearer). Default: derived from the password, so it never equals it and
# can't be used to sign in as a person. Shown to signed-in people on /connect.
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
# Which mandate the single AGENT_TOKEN may act under. Each mandate also has its own derived token (shown on
# /connect to a signed-in person). "all" restores the old unbound behaviour; not recommended.
AGENT_TOKEN_SCOPE = os.environ.get("AGENT_TOKEN_SCOPE", "mnd_hotel_helsinki")
DATA_DIR = ROOT / "data"
STATE_DIR = Path(os.environ.get("TG_STATE_DIR") or ROOT / "state")  # tests point this at a temp folder
STATE_DIR.mkdir(parents=True, exist_ok=True)
# Separate database for test runs, so trap tests never show up as demo orders.
DB_PATH = Path(os.environ.get("GATEWAY_DB") or STATE_DIR / "gateway.db")
TIMEZONE = "Europe/Helsinki"
OFFER_TTL_MINUTES = 15
# The mock's availability data is a snapshot whose order cut-offs (e.g. Thu 12 Nov 15:00) have already
# passed. DEMO_CLOCK replays the data as if "now" were this moment (ISO time, e.g.
# 2026-11-12T12:00:00+02:00): every cut-off is shifted by the same amount, so fresh products are orderable
# again and the price lock still ends at the (shifted) cut-off. Empty = Valio's real clock. Labelled in the UI.
DEMO_CLOCK = os.environ.get("DEMO_CLOCK", "").strip()
# Units sold by weight may be ordered in fractions; everything else (pieces, boxes, packs) in whole units.
WEIGHT_SALES_UNITS = {"KG", "KGM", "G", "GRM"}
MAX_OFFER_LINES = 20
