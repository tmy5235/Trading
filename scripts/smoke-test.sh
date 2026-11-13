#!/bin/sh
# Run from the project root. This opens a browser and uses the real AAPL feed.
set -eu
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1) &
frontend_pid=$!
SSL_CERT_FILE="$(python3 -m certifi)" python3 -m backend.live_feed --symbol AAPL &
feed_pid=$!
cleanup() { kill "$backend_pid" "$frontend_pid" 2>/dev/null || true; if [ -n "$feed_pid" ]; then kill "$feed_pid" 2>/dev/null || true; fi; wait 2>/dev/null || true; }
trap cleanup EXIT HUP INT TERM
attempt=0
until curl -fsS http://127.0.0.1:5173/api/health >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ] || ! kill -0 "$backend_pid" "$frontend_pid" "$feed_pid" 2>/dev/null; then
    echo 'FAIL: services did not become ready' >&2; exit 1
  fi
  sleep 1
done
python3 -m webbrowser http://127.0.0.1:5173/
printf 'Check AAPL, 1m, connected dashboard and feed, and a candle. Switch to 5m and inspect a candle. Do they match the stored observations? Enter y for pass: '
read -r answer
if [ "$answer" != y ]; then echo 'FAIL or unverified: live chart'; exit 1; fi
kill -TERM "$feed_pid"
wait "$feed_pid" 2>/dev/null || true
feed_pid=
printf 'After up to 8 seconds, with the page still open, does Feed show disconnected while Dashboard remains connected? Enter y for pass: '
read -r answer
if [ "$answer" = y ]; then echo 'PASS: live chart and upstream disconnect'; else echo 'FAIL: upstream disconnect status'; exit 1; fi
