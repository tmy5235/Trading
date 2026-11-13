#!/bin/sh
# Synthetic replay, provider double, browser and SQLite check; run from project root.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-chart-decision.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -c 'from pathlib import Path; import sys; from tests.test_sma import fixture; fixture(Path(sys.argv[1]))' "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_IS_TEST=1 TRADING_BOT_DATA_LABEL='Synthetic crossover chart test' python3 -m scripts.mock_chart_server & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Choose Replay, Start replay, advance six observations, select 14:34 UTC and Ask about chart. Does the preview end at 14:34 and show action wait, crossover up, evidence sufficient and comparison matches? Enter y for pass: '
read -r answer
if [ "$answer" != y ]; then echo 'FAIL or unverified: synthetic chart decision'; exit 1; fi
python3 - "$fixture_dir/test.sqlite3" <<'PY'
import hashlib,json,sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    row=db.execute('SELECT c.png,c.sha256,c.answers_json,c.comparison,d.snapshot_id FROM chart_decisions c JOIN decisions d ON d.id=c.decision_id ORDER BY d.rowid DESC LIMIT 1').fetchone()
    assert row, 'No saved chart decision'
    png, sha, answers, comparison, snapshot_id=row
    assert hashlib.sha256(png).hexdigest()==sha
    assert db.execute('SELECT snapshot_id,sha256 FROM synthetic_provider_calls ORDER BY rowid DESC LIMIT 1').fetchone()==(snapshot_id,sha)
    assert db.execute('SELECT snapshot_id,sha256 FROM synthetic_review_calls ORDER BY rowid DESC LIMIT 1').fetchone()==(snapshot_id,sha)
    assert json.loads(answers)=={'action':'wait','crossover':'up','evidence':'sufficient'} and comparison=='matches'
    review=db.execute('SELECT reviewer_status,verdict FROM decision_reviews ORDER BY rowid DESC LIMIT 1').fetchone()
    assert review==('valid','accept')
    assert db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]==0
print('PASS: provider PNG, saved PNG, answers and unchanged order count match')
PY
