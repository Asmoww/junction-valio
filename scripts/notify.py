"""Post a short progress update to the team Discord.

Usage: python scripts/notify.py "Gateway deployed at valio.psavela.com"
Reads DISCORD_WEBHOOK_URL from .env. Keep messages to one line; post only on milestones.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

env = Path(__file__).resolve().parent.parent / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

url = os.environ.get("DISCORD_WEBHOOK_URL")
msg = " ".join(sys.argv[1:]).strip()
if not url or not msg:
    sys.exit("usage: notify.py <message> (needs DISCORD_WEBHOOK_URL in .env)")
req = urllib.request.Request(url, data=json.dumps({"content": msg[:1900]}).encode(),
                             headers={"Content-Type": "application/json", "User-Agent": "hack1-notify"})
urllib.request.urlopen(req, timeout=15)
