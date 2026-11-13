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
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")
DATA_DIR = ROOT / "data"
STATE_DIR = Path(os.environ.get("TG_STATE_DIR") or ROOT / "state")  # tests point this at a temp folder
STATE_DIR.mkdir(parents=True, exist_ok=True)
TIMEZONE = "Europe/Helsinki"
OFFER_TTL_MINUTES = 15
# Units sold by weight may be ordered in fractions; everything else (pieces, boxes, packs) in whole units.
WEIGHT_SALES_UNITS = {"KG", "KGM", "G", "GRM"}
MAX_OFFER_LINES = 20
