#!/bin/sh
# Interactive, disposable simulated-account check; run from the project root.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-paper.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -c 'from pathlib import Path; import sys; from tests.test_paper import fixture; fixture(Path(sys.argv[1]))' "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_LABEL='Synthetic paper fixture' python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Choose Replay and Start replay. Buy 4, retry it, then advance once; sell 1 and advance; close and advance; try selling 1. Does the dashboard show $1025, 0 shares, three fills and one rejection? Enter y for pass: '
read -r answer
[ "$answer" = y ] || { echo 'FAIL or unverified: paper browser check'; exit 1; }
python3 -c 'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); assert db.execute("select cash from paper_account").fetchone()[0]=="1025"; assert db.execute("select shares from paper_positions where symbol=\"AAPL\"").fetchone()[0]==0; assert db.execute("select count(*) from paper_fills").fetchone()[0]==3; print("PASS: persisted paper account and three fills")' "$fixture_dir/test.sqlite3"
