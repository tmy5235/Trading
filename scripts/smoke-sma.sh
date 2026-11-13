#!/bin/sh
# Interactive synthetic replay check. Run from the project root with ports free.
set -eu
fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/trading-bot-sma.XXXXXX")
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
python3 -c 'from pathlib import Path; import sys; from tests.test_sma import fixture; fixture(Path(sys.argv[1]))' "$fixture_dir/test.sqlite3"
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" TRADING_BOT_DATA_LABEL='Synthetic test data' python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 & backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then echo 'FAIL: services did not become ready'; exit 1; fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Choose Replay, Start replay, then Play. Are the synthetic markers up at 14:34 (16/13) and down at 14:36 (4/9)? Enter y for pass: '
read -r answer
if [ "$answer" = y ]; then echo 'PASS: synthetic SMA replay'; else echo 'FAIL or unverified: synthetic SMA replay'; exit 1; fi
