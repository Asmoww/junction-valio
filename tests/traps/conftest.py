import os
from pathlib import Path

# Keep trap orders out of the demo database.
os.environ.setdefault("GATEWAY_DB", str(Path(__file__).resolve().parents[2] / "state" / "traps.db"))
