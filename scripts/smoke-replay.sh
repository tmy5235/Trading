#!/bin/sh
# From the project root. Interactive browser verification; Yahoo stays off.
set -eu
if [ ! -f backend/data/observations.sqlite3 ]; then echo 'FAIL: no recorded database'; exit 1; fi
counts() { python3 -c 'import sqlite3; db=sqlite3.connect("file:backend/data/observations.sqlite3?mode=ro",uri=True); print(db.execute("select count(*) from observations").fetchone()[0], db.execute("select count(*) from receipts").fetchone()[0]); db.close()'; }
before=$(counts)
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
cleanup() { kill "$backend_pid" "$frontend_pid" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT HUP INT TERM
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Select Replay, choose a saved range, start and play it twice. Compare the final candle against SQLite and confirm the two runs agree. Enter y for pass: '
read -r answer
[ "$answer" = y ] || { echo 'FAIL or unverified: replay'; exit 1; }
after=$(counts)
[ "$before" = "$after" ] || { echo "FAIL: counts changed ($before -> $after)"; exit 1; }
echo "PASS: replay and unchanged observation/receipt counts ($after)"
