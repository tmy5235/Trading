#!/bin/sh
# Interactive synthetic replay snapshot check; run from the project root with ports free.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-replay-decision.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -m scripts.prepare_replay_decision_fixture "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_IS_TEST=1 TRADING_BOT_DATA_LABEL='Synthetic replay decision test' python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Choose Replay, start, buy 4, advance four observations, select 14:32 UTC, freeze. Does it show close 7, SMAs 8.5/10, cash 948 and shares 4? Enter y for pass: '
read -r answer
if [ "$answer" != y ]; then echo 'FAIL or unverified: replay snapshot'; exit 1; fi
python3 - "$fixture_dir/test.sqlite3" <<'PY'
import json, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    row = db.execute('SELECT snapshot_json FROM decision_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
    assert row, 'No saved snapshot'
    s = json.loads(row[0])
    assert (s['candle']['close'], s['sma']['short'], s['sma']['long'], s['account']['cash'], s['account']['shares']) == (7, 8.5, 10, '948', 4)
    assert not s['eligible_for_paper_order']
print('PASS: saved synthetic replay snapshot matches')
PY
