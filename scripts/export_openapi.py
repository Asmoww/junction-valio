"""Write the gateway's real OpenAPI spec to contracts/openapi.generated.json.

contracts/openapi.yaml is the hand-written contract; this dumps what FastAPI actually serves, so the two can be diffed.
Run after API changes:  uv run python scripts/export_openapi.py
"""
import json
from pathlib import Path

from gateway.main import app

out = Path(__file__).resolve().parent.parent / "contracts" / "openapi.generated.json"
out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
print(f"wrote {out}")
