#!/bin/sh
# Synthetic local browser and SQLite check, with no live provider calls.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-automation.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -m unittest tests.test_automation -q
python3 -c 'from pathlib import Path; import sys; from tests.test_sma import fixture; fixture(Path(sys.argv[1]))' "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_IS_TEST=1 TRADING_BOT_DATA_LABEL='Synthetic automation smoke test' python3 -m scripts.mock_automation_server & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Click Start paper automation, wait for the synthetic 14:34 decision to show wait / accept / skipped, then click Stop. Did those results appear? Enter y for pass: '
read -r answer
if [ "$answer" != y ]; then echo 'FAIL or unverified: automation controls'; exit 1; fi
python3 - "$fixture_dir/test.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
 assert db.execute('SELECT enabled FROM automation_state WHERE account_id=1').fetchone()[0]==0
 assert db.execute('SELECT count(*) FROM paper_fills').fetchone()[0]==0
 row=db.execute('SELECT e.status,e.decision_id,d.proposal,r.verdict,c.sha256 FROM automation_events e JOIN decisions d ON d.id=e.decision_id JOIN decision_reviews r ON r.decision_id=d.id JOIN chart_decisions c ON c.decision_id=d.id').fetchone()
 assert row and row[0]=='skipped' and row[2:4]==('wait','accept')
 calls=db.execute('SELECT role,image_hash FROM synthetic_calls ORDER BY rowid').fetchall()
 assert calls==[('analyst',row[4]),('reviewer',row[4])]
 print('PASS: automatic synthetic capture, both roles, skip, stopped state and zero fills')
PY
