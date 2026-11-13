"""Optional Claude-powered request reading. Falls back to the rule-based parser without a key.

Claude only turns the buyer's free text into a structured intent. It never sees supplier product text
and never decides anything the policy engine checks.
"""
import json
import os
from datetime import date

MODEL = "claude-opus-5-5"
SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "description": "Plain product noun(s) in English, e.g. 'milk', 'oat drink', 'muesli'."},
        "quantity": {"type": ["number", "null"]},
        "unit": {"type": "string", "enum": ["L", "KG", "PCS"]},
        "attributes": {"type": "array", "items": {"type": "string", "enum": [
            "FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN", "VEGAN", "ORGANIC", "FINNISH"]}},
        "deliver_by": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "fat_pct": {"type": ["number", "null"], "description": "Fat percentage only if the buyer states it, else null."},
        "shelf_life": {"anyOf": [{"type": "string", "enum": ["fresh", "long_life"]}, {"type": "null"}],
                       "description": "Only if the buyer states fresh or long-life/UHT, else null."},
    },
    "required": ["product", "quantity", "unit", "attributes", "deliver_by", "fat_pct", "shelf_life"],
    "additionalProperties": False,
}
SYSTEM = ("You convert a professional kitchen's purchasing request into a structured intent for a food "
          "wholesaler's catalog search. Product: the generic product only, in English, without brand, size or "
          "requirements (those go in attributes). Unit: L for liquids by volume, KG for weight, PCS otherwise. "
          "Resolve weekday names to the next such date after today. If no date is given, use today + 3 days. "
          "Never guess fat level or shelf life: leave them null unless stated.")


def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def parse(text, today=None):
    """Return an intent dict, or None if Claude is not configured or the call fails."""
    if not enabled():
        return None
    import anthropic

    today = today or date.today()
    try:
        client = anthropic.Anthropic()
        r = client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": f"Today is {today.isoformat()} ({today:%A}).\n\nRequest:\n{text}"}],
        )
    except anthropic.APIStatusError as e:
        return {"_error": f"Claude API error {e.status_code}: {str(e.message)[:160]}"}
    except anthropic.APIConnectionError:
        return {"_error": "Claude API unreachable"}
    if r.stop_reason == "refusal":
        return {"_error": "Claude declined the request"}
    out = next((b.text for b in r.content if b.type == "text"), None)
    if not out:
        return {"_error": "empty answer"}
    intent = json.loads(out)
    intent["raw"] = text
    return intent
