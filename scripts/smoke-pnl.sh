#!/bin/sh
# Interactive, disposable synthetic P&L check; run with project ports free.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-pnl.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -c 'from pathlib import Path; import sys; from tests.test_pnl import fixture,run_sequence; p=Path(sys.argv[1]); fixture(p); run_sequence(p)' "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_LABEL='Synthetic P&L fixture' python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Inspect the labeled P&L table, switch to Replay, and compare totals 0, 40, 40, -5, 25 with SQLite. Do they match? Enter y for pass: '
read -r answer
[ "$answer" = y ] || { echo 'FAIL or unverified: P&L browser check'; exit 1; }
python3 -c 'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); rows=db.execute("select total from paper_pnl_history order by id").fetchall(); assert rows==[("0",),("40",),("40",),("-5",),("25",)],rows; print("PASS: synthetic SQLite P&L rows", rows)' "$fixture_dir/test.sqlite3"
