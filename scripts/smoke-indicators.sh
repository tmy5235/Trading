#!/bin/sh
# Interactive synthetic fixture check; run from the project root with ports free.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-indicators.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -c 'from pathlib import Path; import sys; from tests.test_indicator_registry import fixture; fixture(Path(sys.argv[1]))' "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_LABEL='Synthetic range fixture' python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'In Replay, add Range 2 and Range 3, play the fixture and inspect 14:32 UTC. Do they show 5 and 6? Refresh, check both return, and verify window 0 is rejected. Enter y for pass: '
read -r answer
[ "$answer" = y ] || { echo 'FAIL or unverified: indicator browser check'; exit 1; }
python3 -c 'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); rows=db.execute("select name,type,settings_json from indicator_instances order by id").fetchall(); assert len(rows)==2 and [r[0] for r in rows]==["Range 2","Range 3"], rows; print("PASS: two valid configurations persisted", rows)' "$fixture_dir/test.sqlite3"
