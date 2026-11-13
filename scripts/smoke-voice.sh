#!/bin/sh
# Interactive live microphone test using a synthetic decision and real GPT-Live.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-voice.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -m unittest tests.test_voice -q
python3 -m scripts.prepare_voice_fixture "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_IS_TEST=1 TRADING_BOT_DATA_LABEL='Synthetic voice test data' python3 -m scripts.run_backend & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Start voice for AAPL, ask what the agents decided and what happened to the paper account, and listen. Expected: buy proposed, reviewer rejected, execution skipped. End voice and check microphone off. Enter y if all pass: '
read -r answer
if [ "$answer" != y ]; then echo 'FAIL or unverified: live voice'; exit 1; fi
python3 - "$fixture_dir/test.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
 assert db.execute('select count(*) from paper_orders').fetchone()[0]==0
 assert db.execute('select status,reason from automation_events where decision_id=?',('voice-decision',)).fetchone()==('skipped','Reviewer rejected the proposal')
print('PASS: synthetic account remained unchanged')
PY
