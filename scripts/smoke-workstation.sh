#!/bin/sh
# Offline end-to-end smoke test. No source files or real accounts are changed.
set -eu
fixture_dir=$(mktemp -d /tmp/trading-workstation.XXXXXX)
backend_pid= frontend_pid=
cleanup() {
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$frontend_pid" ] || kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$fixture_dir"
}
trap cleanup EXIT HUP INT TERM
TRADING_BOT_DB_PATH="$fixture_dir/test.sqlite3" python3 -m uvicorn scripts.workstation_fixture:app --host 127.0.0.1 --port 18761 >"$fixture_dir/backend.log" 2>&1 & backend_pid=$!
(cd frontend && WORKSTATION_BACKEND_URL=http://127.0.0.1:18761 exec ./node_modules/.bin/vite --host 127.0.0.1 --port 18762 --strictPort) >"$fixture_dir/frontend.log" 2>&1 & frontend_pid=$!
attempt=0
until curl -fsS http://127.0.0.1:18762/api/health >/dev/null 2>&1; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 30 ] || ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then
    echo 'FAIL: test services did not become ready'; cat "$fixture_dir/backend.log" "$fixture_dir/frontend.log"; exit 1
  fi
  sleep 1
done
if ! node frontend/tests/workstation_smoke.mjs; then
  echo 'FAIL: workstation browser check'; cat "$fixture_dir/backend.log" "$fixture_dir/frontend.log"; exit 1
fi
