#!/usr/bin/env bash
# Reset the live demo to a clean start: no orders, empty leaderboard, every agent "New" again.
# Keeps all keys (offer signing, buyers' credentials, AP2, approval links), the scoreboard and trap results.
# Usage: ssh psavela@pikkuinen 'bash ~/hackathon-1/scripts/reset_demo.sh'
set -euo pipefail
cd "$(dirname "$0")/.."
stamp=$(date +%Y%m%d-%H%M)
systemctl --user stop valio-gateway
[ -f state/gateway.db ] && cp state/gateway.db "state/gateway.db.bak-$stamp"
rm -f state/gateway.db state/gateway.db-wal state/gateway.db-shm
rm -rf state/sim-*
rm -rf state/mandates            # buyers signed from a policy during testing
systemctl --user start valio-gateway
sleep 5
systemctl --user is-active valio-gateway
# Warm up the running server (catalog, Claude, Valio API) with one inquiry, which never orders
pw=$(grep '^DEMO_PASSWORD=' .env | cut -d= -f2-)
jar=$(mktemp)
curl -s -c "$jar" -o /dev/null -X POST --data-urlencode "password=$pw" -d next=/ http://127.0.0.1:5201/login
sed -i 's/^#HttpOnly_//; s/\tTRUE\t\/\tTRUE\t/\tTRUE\t\/\tFALSE\t/' "$jar"   # let curl send the secure cookie over local http
curl -s -b "$jar" -o /dev/null -w "warm-up request: %{http_code}\n" -H 'Content-Type: application/json' \
  -d '{"request": "What does lactose-free milk cost for Monday?", "mandate_id": "mnd_hotel_helsinki", "purpose": "inquiry"}' \
  http://127.0.0.1:5201/api/run
rm -f "$jar"
curl -s -o /dev/null -w "site: %{http_code}\n" http://127.0.0.1:5201/healthz
echo "Backup: state/gateway.db.bak-$stamp"
