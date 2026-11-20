# Trading bot

A local workstation for monitoring AAPL market observations, reviewing a chart and replaying recorded data. A Yahoo Finance WebSocket receiver stores observations in SQLite; Python and FastAPI deliver derived candlesticks and volume to a Vue dashboard. Paper trading is simulated and does not send brokerage orders.

## Technology and prerequisites

- macOS on Apple Silicon was used for setup; Python 3.14.7 and pip 26.2.1 are available in the existing Python installation.
- Node.js 24.18.1 and npm 11.16.0 are available.
- The backend uses FastAPI 0.162.3 and Uvicorn 0.54.0; the frontend uses Vue 3.5.45, Vite 8.3.7, and TradingView Lightweight Charts 5.2.1. See the dependency files for exact direct versions.
- Reuse the existing Python environment. Follow `AGENTS.md` before installing software or changing dependencies.

## Setup and running

From the project root, install the approved dependencies when needed:

```sh
python3 -m pip install -r backend/requirements.txt
cd frontend && npm ci
```

### OpenAI SDKs

The backend uses the Python interpreter that launches `scripts.run_backend`. On this Mac it is the shared Python 3.14.7 framework installation. The approved SDK installation used:

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install --no-cache-dir 'openai-agents==0.24.0' 'openai==3.52.0' 'websockets==16.1.1'
```

The Agents SDK requires `websockets>=15,<17`. Version 16.1.1 worked with the project's Yahoo receiver. To verify the installed SDKs with the backend interpreter, run these checks from the project root:

```sh
python3 - <<'PY'
from importlib.metadata import version
from openai import AsyncOpenAI
from agents import Agent, Runner, function_tool

for package, expected in (("openai", "3.52.0"), ("openai-agents", "0.24.0")):
    installed = version(package)
    print(package, installed)
    assert installed == expected
print("Verified imports:", AsyncOpenAI.__name__, Agent.__name__, Runner.__name__, function_tool.__name__)
PY
python3 -m pip --no-cache-dir check
```

Both version assertions and all four imports passed on this Mac. Pip reported `No broken requirements found.` before and after installation. Restart any FastAPI and Yahoo worker processes started before the install so they load the updated WebSocket library.

### Start the workstation

In separate terminals, start the backend from the project root and the frontend from `frontend/`:

```sh
python3 -m scripts.run_backend
```

```sh
npm run dev -- --host 127.0.0.1
```

In a third terminal, start the AAPL receiver from the project root:

```sh
SSL_CERT_FILE="$(python3 -m certifi)" python3 -m backend.live_feed --symbol AAPL
```

Open `http://127.0.0.1:5173/`. Choose a timeframe and select a candle to inspect its UTC prices, volume, coverage, and forming status. The dashboard distinguishes the Yahoo feed status from the FastAPI WebSocket connection and shows the latest source age and observation. Stop each service with **Ctrl+C** in its terminal. The worker uses the default observation database described below. The chart combines completed regular-session Yahoo one-minute history with recorded live observations; an outside-session quote is labeled separately and does not create a regular-session candle. The chart can be empty until a regular-session observation is received.

For an interactive live smoke test from the project root, run `sh scripts/smoke-test.sh`. It starts the three services, opens a browser, asks for a chart and stored-observation comparison, and checks upstream disconnect status. It needs an available live AAPL update, and its revised end-to-end run remains unverified; manual live checks are recorded in `docs/verification.md`.

## Folder map

```text
trading-bot/
|-- AGENTS.md
|-- README.md
|-- backend/                 FastAPI route and Python requirements
|-- frontend/                Vue UI, Vite proxy and npm dependencies
|-- docs/                    Design and verification records
|-- prompts/                 Saved major prompts
|-- scripts/                 Repeatable checks
`-- handoffs/                Handoff procedure and current state
```

## Historical price sample

`GET /api/history` derives candles from `backend/data/sample-candles.csv`, a synthetic one-minute sample associated with AAPL for this prototype. It accepts `symbol=AAPL` and `timeframe=1m|5m|15m|1h|1d` (defaults: AAPL and 1m), plus an optional timezone-aware `as_of` timestamp, for example `/api/history?symbol=AAPL&timeframe=5m&as_of=2026-11-09T14:34:00Z`. Other symbols return 404 rather than mislabeling the sample. The response contains `symbol`, `timeframe`, a `candles` array with exactly the six OHLCV fields per candle, and a separate `status` array keyed by timestamp with coverage counts, coverage completeness, and whether the interval has ended. Unknown volume is `null`; zero is a known value. A partial group's volume is the sum of available known minutes, not a complete-interval total.

Sessions use the installed `exchange_calendars` XNAS regular-session schedule, including holidays, daylight saving and early closes. Groups align to session open; a daily group spans one session. The schedule is packaged data and may need updates when exchange rules change. A minute is included only after it is available and ended. For the static CSV, which has no receipt or revision history, availability is modeled as minute end: this is a synthetic replay assumption, not evidence of when a real data revision arrived. The CSV rejects duplicate timestamps because revision timing is unavailable. The underlying aggregation accepts timestamped revisions and selects the latest one available by `as_of`. Live chart history and replay use saved observations through separate endpoints, described below.

## Yahoo Finance stream check

To receive one update for AAPL (or replace `AAPL` with another ticker), run from the project root:

```sh
SSL_CERT_FILE="$(python3 -m certifi)" python3 -m backend.yahoo_stream_check AAPL --timeout 30
```

The check uses the existing Python environment, stops after one update or the timeout, and does not require a Yahoo login. The CA bundle is specified because the first check on this computer failed certificate verification with Python's default trust configuration. The source timestamp and daily volume meanings are inferred, not verified against provider documentation; see `docs/verification.md`. This one-update diagnostic is separate from the continuous worker used by the chart.

## Recording market observations

The Yahoo receiver records decoded messages in `backend/data/observations.sqlite3` by default. Initialize the database from the project root with:

```sh
python3 -m backend.observations
```

The receiver also initializes it automatically. Use `--database /path/to/file.sqlite3` with either command to select another database. The database and its SQLite sidecars are ignored by Git. The continuous worker records updates until stopped; the bounded check remains available as a diagnostic.

The `observations` table stores the complete decoded message as canonical JSON, along with ticker and raw source time. An identical decoded message (same JSON values regardless of object key order) shares one observation row; each arrival has its own `receipts` row, UTC receipt time, and increasing receipt ID. To avoid counting repeats, count observation IDs rather than receipt rows. Any changed message creates another observation, including changes at the same source time. Because the feed does not establish correction identity, neither observation is overwritten or labeled as a correction. Sort receipts by ID for arrival order and use the raw source time separately when investigating late updates. The decoded JSON preserves the fields returned by yfinance, not the original encoded network frame.

## One-minute candles from observations

`backend.candles.one_minute_candles(db, ticker, now=...)` recomputes candles from a SQLite connection and returns entries containing a six-field `candle` and a separate `ended` boolean. The live history and WebSocket derive their candles from this function; the CSV history endpoint remains separate. `now` is an aware datetime used only to determine whether each minute has ended; an ended candle can still change when late data arrives.

The function treats source `time` as Unix epoch milliseconds and `day_volume` as a running session share total, consistent with the earlier observations but not yet confirmed by Yahoo's documentation. It sorts by source time; for distinct messages with the exact same source time, first observation ID (first receipt order) breaks ties deterministically, without claiming actual market order or identifying corrections. Prices give the first, maximum, minimum, and last observed price in each UTC minute. Exact repeated decoded messages are counted only once because aggregation reads the `observations` table, not `receipts`.

Volume is the last cumulative total observed in the minute minus the last total observed before it. A missing baseline, missing or invalid total, detected decrease (reset), conflicting totals at the same source time, or a baseline from a different UTC date yields `null` (unknown). UTC date is only a conservative boundary, not a verified exchange session calendar. The delta covers the interval between snapshots, which may extend outside the minute or omit later trades; it is not guaranteed to equal complete minute volume. A reset that occurs and recovers between snapshots may be undetectable. Accurate production candles require verified feed semantics, session boundaries, and sufficient observations.

Recomputing incorporates late observations into their source minute and can also change a later minute's volume baseline. The current feed supplies no verified correction identity, so distinct same-time messages are retained; no automatic replacement occurs. If a trustworthy correction identifier becomes available, preserve the original, select the authoritative revision, and recompute affected minutes and subsequent baselines. Run the synthetic aggregation checks with `python3 -m unittest tests.test_candles -v`.

## Live chart data

The continuous worker refreshes Yahoo's recent one-minute history approximately once a minute, in addition to the WebSocket feed. Completed historical bars are saved separately in `yahoo_minute_bars` with their retrieval times and changed revisions; they are not fabricated observation receipts. After a completed historical bar becomes available it takes precedence over a partial observation-derived bar for that minute. The still-forming minute uses only received live observations. Historical volume is supplied by Yahoo and may be delayed or revised; observation-derived volume is an estimate and can be unknown. The display identifies each selected bar's source, and larger intervals combine the same minute data. If history is unavailable, the chart falls back to recorded streaming observations. The display retains red and green candlesticks, recent-minute volume, crosshair inspection, an optional crossover overlay and a Go to latest control.

History has its own availability time: it cannot be treated as if it was received at an earlier candle close. Historical bars are not included in replay, paper fills, or the account-aware decision snapshots, which continue to use received observations and their original availability. Thus a selected historical backfill candle may not be available to the older position-aware **Ask about chart** workflow; the image-only **Chart signal** can analyze the visible chart. All quotes and bars may be delayed or revised, and this is a paper-trading classroom application, not a brokerage feed.


`GET /api/live-history?symbol=AAPL&timeframe=1m` returns the same aggregated records sent by `/api/live` over WebSocket, with symbol, timeframe, source, upstream status, last source time, and observation ID. Supported intervals are `1m`, `5m`, `15m`, `1h`, and `1d`. The worker updates an upstream heartbeat; a stopped worker or stale heartbeat is reported as disconnected even if the browser remains connected. Source age assumes the Yahoo source time is epoch milliseconds; field meanings and snapshot-derived volumes remain unverified. Unknown volume is displayed as unknown, and a forming candle is not final. The worker's close detection relies on a private yfinance socket interface and should be rechecked on yfinance upgrades.

To inspect stored observations while the smoke test is running, use a separate terminal:

```sh
python3 -c 'import sqlite3; db=sqlite3.connect("backend/data/observations.sqlite3"); print(db.execute("SELECT id,source_time,message_json FROM observations ORDER BY id DESC LIMIT 5").fetchall())'
```

## Saved chart snapshots and replay

Choose **Snapshot** in the dashboard to immediately see the latest saved AAPL candlestick chart. The timeframe buttons redraw the snapshot using that interval. **Change time range** opens UTC start and end fields; edit them and select **Load snapshot** to view another period. The end is the as-of cutoff, and the first minute intersecting the start is included. No future observations or later historical revisions are used. The chart shows the most recent portion initially; scroll or zoom to inspect earlier candles. A snapshot is read-only and cannot place or fill paper orders. If the chosen range has no recorded candles, the dashboard says so.

Snapshot combines recorded streaming observations and Yahoo historical bars that were actually received by the cutoff. Their per-candle source, completion and coverage are shown. It is a reconstruction from data the app saved, not a guarantee of a complete exchange feed. Indicator settings are the current dashboard configuration, not historical settings. Existing account-aware decision snapshots are available only in step-by-step replay; the image-only **Chart signal** can analyze the visible saved chart.

For the original receipt-by-receipt mode, choose **Step through observations**. Select **Start replay**, then **Play** or **Next observation**; **Pause** stops playback. This mode uses recorded observations only, follows receipt-ID order in a temporary in-memory database, and preserves source timestamps. It retains the original simulated-account behavior, including processing eligible pending replay orders as receipts advance. A range is limited to 5,000 receipts; empty selections report no observations. Choose **Back to snapshot** to return to the read-only view.

Run `sh scripts/smoke-chart-snapshot.sh` for a repeatable browser check using a disposable synthetic database, or `python3 -m unittest tests.test_chart_snapshot -v` for cutoff, revision and paper-account checks. `sh scripts/smoke-replay.sh` is the earlier interactive replay check; its end-to-end run remains unverified.

## Simple moving averages

The chart calculates short and long SMAs in Python for the selected symbol and timeframe. Enter positive whole-number windows with the short window smaller than the long window, then choose **Apply averages**. Blue and orange lines show the short and long averages; arrows and the crossover list mark confirmed changes of side. Selecting a candle displays its averages and signal. Values are unavailable during warm-up or while that candle is forming. Only ended candles with complete coverage are included in the windows; gaps and partially covered larger intervals are skipped. Ties preserve the previous side, and calculations compare exact rational values derived from the stored numeric prices before the UI rounds them to two decimals. Late data recomputes the affected history as it becomes available in replay.

Run `python3 -m unittest tests.test_sma -v` for the hand-calculated reference and replay checks. For a repeatable interactive browser check, run `sh scripts/smoke-sma.sh` from the project root with ports 8000 and 5173 free. It creates a separate temporary synthetic database, starts the backend and frontend, opens the chart, and asks you to inspect the reference markers. The script is syntax-checked but has not yet been run end to end; equivalent browser checks were performed manually.

## Configurable chart indicators

Under **Chart indicators**, choose a type, name it, set its window, and select **Add indicator**. Each named configuration is saved in the `indicator_instances` table of the configured SQLite database and restored on refresh. Edit its window and select the matching Update button to change it. Names are unique within that database; configurations apply to the currently selected symbol and timeframe. The existing short/long crossover controls remain separate. The indicator list and selected-candle values appear beside the chart. Price-unit indicators use the price scale; price-difference indicators share a separate overlay scale. Available types and settings come from `GET /api/indicator-types`; `GET /api/indicators` lists saved instances and `POST /api/indicators` creates one.

The registry in `backend/indicators.py` provides the extension interface. To add a rolling type, register its label, units, UI settings metadata, validation function, and calculation function in `INDICATOR_TYPES`. The validator accepts the instance settings and returns its positive integer window; the calculation receives that many completed candle dictionaries and the validated settings. The engine returns one value or `null` warm-up gap at each input candle time, with gaps for forming or incompletely covered candles. Calculations run separately for each instance and recompute from the currently available live or replay candles. SMA returns the mean of closes in instrument price units; rolling price range returns the highest high minus the lowest low, in price-difference units. The existing crossover SMAs use the same calculation interface. Actual source coverage and timestamp limitations remain as described above.

Run `python3 -m unittest tests.test_indicator_registry -v` for the synthetic fixture and persistence check. With ports free, `sh scripts/smoke-indicators.sh` starts the app against a temporary, labeled fixture and asks you to create and inspect two ranges, refresh, and check invalid input. It also checks the persisted settings; the script is syntax-checked but has not been run end to end. The equivalent browser checks were performed manually.

## Simulated paper account

The dashboard's **Paper account** is a local long-only account in the configured SQLite database. A newly initialized account starts with $1,000 of simulated cash. Use whole-share **Buy** or **Sell**, or **Close** the displayed symbol's position. **Hold** requires a position; **Wait** requires no holdings. Hold and wait do not create orders. The selected replay symbol is used in Replay; Live currently uses AAPL. The account is shared between modes. No brokerage orders are sent.

An accepted order stays pending until a subsequent eligible receipt for its symbol arrives in its mode: in Live, a new receipt stored by the running worker; in Replay, the next receipt advanced in the same selected replay range after submission. A positive finite observed price fills the order at that price with zero fees. Eligibility follows receipt order, not source-time order, so a late source observation can be used when it arrives. Previously revealed receipts cannot fill a new order. Replay does not modify the recorded observations; it writes only paper account, order and fill records. Restart the same replay selection to continue a pending replay order; switching to a different range does not fill it. Pending orders have no cancellation control yet.

Sales exceeding the current holding and invalid quantities are rejected on submission. Cash and shares are rechecked at fill; a close is rejected if the position changed since submission rather than leaving a partial holding. Cash, shares, order status and the fill are committed in a single SQLite transaction. Fills link to the recorded observation and receipt and carry execution time; orders retain submission time, status and rejection reason. Money is stored as decimal text. Request IDs uniquely identify order submissions: retrying the same ID and payload returns the existing order; reusing an ID with a different payload is rejected. The dashboard's **Retry last request** reuses the last ID within that page session. The reusable backend entry points are `backend.paper.execute_action` and `backend.paper.apply_receipt`.

Run `python3 -m unittest tests.test_paper -v` for the disposable account checks. With ports free, `sh scripts/smoke-paper.sh` starts the app using a separate synthetic $1,000 account and asks you to repeat the three-price exercise. The script was syntax-checked but not run end to end; equivalent browser operations and a backend restart were verified manually in `docs/verification.md`.

## Paper profit and loss

The paper account displays a P&L history table with event time, price, price source and source time, cash, shares, cumulative realized P&L, unrealized P&L and total P&L. Values are per symbol; cash belongs to the shared account. Fills use weighted-average purchase cost. A partial sale realizes shares sold × (fill price − average cost) and leaves the remaining average unchanged. Unrealized P&L uses the current open quantity and the row's price; total is realized plus unrealized. Monetary calculations use decimal arithmetic without fees.

Every fill creates a linked P&L row atomically with the account updates. While shares are open, a newly completed regular-session one-minute candle or a later changed close creates a valuation row using its close. In replay, only receipts reached in the selected range are considered; in live mode, the worker also checks minute completion while connected. A late observation can revise a completed candle and add a new valuation row. Reprocessing the same event does not add another row, and previous rows are retained. Price source, source time, receipt and observation IDs remain in SQLite. When no usable valuation has been recorded for an open position, the dashboard labels it unavailable. A fill's event time is the actual execution time; a replay candle valuation's event time is its replayed receipt time.

For accounts created before this feature, cost basis and realized P&L are reconstructed from recorded fills when possible. Earlier candle valuation history cannot be recreated from fills and is not backfilled; an open position without a new valuation is shown as unavailable. Values depend on the completeness and meaning of the underlying observations and the XNAS regular-session calendar.

Run `python3 -m unittest tests.test_pnl -v` for the synthetic reference, weighted-cost, idempotency and live-completion checks. With ports free, `sh scripts/smoke-pnl.sh` builds a temporary labeled $1,000 paper fixture, opens the dashboard and asks for a table comparison with SQLite. That script is syntax-checked but has not been run end to end; the equivalent comparison and backend restart were verified manually.

## Completed-candle decisions

Create a frozen snapshot separately from the model request:

```sh
curl -sS -X POST http://127.0.0.1:8000/api/decision-snapshots \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"AAPL","timeframe":"1m","candle_timestamp":"2026-11-09T14:32:00Z","short":2,"long":3}'
```

A snapshot contains an ID, candle, SMAs and crossover, account cash and position, order size, source label, eligibility, and `chart_records` that can be rendered from the exact frozen evidence. Retrieve it with `GET /api/decision-snapshots/{id}`. Live snapshots use market receipts available by the interval close and require full coverage, warmed-up SMAs, a verifiable account creation time and a paper ledger that reconciles with current cash and positions. Accounts initialized before creation-time tracking are not automatically assumed to have existed at an earlier close. Synthetic snapshots must be labeled with `TRADING_BOT_DATA_IS_TEST=1` and `TRADING_BOT_DATA_LABEL`, and are never eligible for paper orders.

For replay, start the selected recording, advance to a point where the candle has ended, pause, select the candle in **Inspect candle**, and choose **Freeze replay snapshot**. Review the frozen chart, close, SMA values, account and replay point before selecting **Request model proposal**. The snapshot uses only the selected receipt-time range and receipt-ID prefix through that replay point, including any late observations already received there. Thus a replay decision is as of the displayed replay point, which may be later than the selected candle's close and may include later revisions; it is not a reconstruction of the original close-time view. Replay account checkpoints capture the shared paper account atomically after processing each replay receipt, including fills at that step. The snapshot retains that checkpoint even if the account later changes. An old replay step without a checkpoint must be replayed again. Replay proposals remain ineligible to authorize paper orders; the decision endpoint never executes trades.

For API callers, pass `mode: "replay"`, `replay_start`, `replay_end`, `replay_position`, `replay_cutoff`, and `replay_checkpoint_id` to `/api/decision-snapshots`. The checkpoint ID is returned by `/api/paper/replay-step`; its scope and receipt must match the chosen replay range and position. A selected candle that has not ended at that replay point is rejected. The snapshot includes the replay selection, event and account capture time.

Run `sh scripts/smoke-replay-decision.sh` with ports 8000 and 5173 free for an interactive browser and SQLite comparison using a separate synthetic database. Its syntax has been checked; the equivalent browser flow was verified manually, but the script itself has not yet been run end to end.

To evaluate a snapshot, supply `OPENAI_API_KEY` in the backend process environment or the owner-only, Git-ignored project `.env` file and send `POST /api/decisions` with `{"snapshot_id":"..."}`. `GET /api/decisions/{id}` retrieves the saved result. The backend sends a named `paper_action` choice to OpenAI's `/v1/decisions` with `gpt-6-luna`, checks the choice against frozen long-only account rules, and writes its own factual explanation from snapshot values. Status distinguishes `valid`, `invalid_proposal`, `missing_input`, `timeout`, `malformed_answer`, and `provider_error`. A valid result is a proposal only; neither endpoint submits or fills an order. The configured buy/sell size is the positive integer `PAPER_ORDER_SIZE` in the backend environment, defaulting to 1 share. The proposal is checked against the frozen close; actual execution would need a fresh cash and position check and is not connected.

For an isolated synthetic provider check, create a new fixture without overwriting an existing file:

```sh
python3 -m scripts.prepare_decision_fixture /private/tmp/new-decision-fixture.sqlite3
```

The command prints its snapshot ID. Start FastAPI with `TRADING_BOT_DB_PATH` pointing to that fixture, and with the key present only in the backend environment; submit that snapshot ID to `/api/decisions`. A live provider check with a synthetic snapshot returned a valid proposal; see `docs/verification.md`. `python3 -m unittest tests.test_decisions -v` runs fixed-snapshot and controlled-failure checks without a provider key.

The backend startup command `python3 -m scripts.run_backend` reads `OPENAI_API_KEY` from the project `.env` only if it is not already present in the process environment. The file is Git-ignored; keep its permissions restricted and do not paste credentials into chat.

## Ask about chart

Select a completed, visible candle using **Inspect candle**, then choose **Ask about chart**. In Replay, advance to a checkpoint first and pause playback. The app freezes the account and chart records, renders the visible portion through the selected candle in a separate chart, captures that chart only as a PNG, and displays the preview with the symbol, timeframe, range and cutoff. It sends the frozen image and snapshot to OpenAI in one request, and shows the three named questions, model answers, Python crossover, comparison and validation status. Live chart updates do not alter the preview. No order is created.

The backend loads the snapshot from SQLite, checks the capture metadata against it and validates PNG structure, checksums and decoded scanlines. Metadata matching cannot independently prove that arbitrary pixels supplied by another API client depict the claimed chart; use the dashboard's capture and inspect the preview. Unreadable, incomplete, conflicting or account-invalid answers are ineligible for orders. Model answers are claims, not verified facts. The model receives the configured SMA crossover rules and is instructed not to invent additional rules. The PNG, exact cutoff-limited model input, questions and answers are saved with the decision; retrieve the result using `GET /api/decisions/{id}` and its image at `GET /api/decisions/{id}/chart.png`. The replay checkpoint is the account at the selected replay step, which can be later than the selected candle's original close.

Run `sh scripts/smoke-chart-decision.sh` from the project root with ports 8000 and 5173 free. It starts a labeled synthetic crossover fixture and provider double, opens the dashboard, and checks the browser answers, saved PNG hash and paper-order count. It does not call OpenAI. The live image call is recorded separately in `docs/verification.md`.

## Analyst and reviewer

Both decision routes now make a separate analyst call and, if its named proposal is valid, a reviewer call. They use the same frozen snapshot; chart requests give both the same cutoff-limited snapshot and PNG. The reviewer sees the analyst's actual answers and backend facts, including cash, shares, crossover, closing price and configured buy cost. It chooses `accept`, `reject_insufficient_cash`, `reject_unsupported_signal` or `reject_other`. Each call has a timeout and runs at most once. The dashboard displays the proposal, reviewer verdict and final backend check; `GET /api/decisions/{id}` includes both model outputs and reviewer input.

Python's existing long-only account and chart evidence checks remain authoritative. A rejection, timeout, invalid reviewer response, invalid analyst response or failed account/evidence check cannot authorize an order. Older decisions without a reviewer and synthetic or replay snapshots are also ineligible. Decision requests never place orders. The configured strategy is checked independently of the reviewer; the analyst and reviewer are instructed not to invent additional rules. A proposed purchase is checked at the frozen close, while any future execution would still need a fresh price and balance check.

Run `python3 -m unittest tests.test_review -v` for the three fixed review cases and failure checks. `sh scripts/smoke-chart-decision.sh` runs the labeled synthetic dashboard workflow with separate analyst and reviewer doubles; it compares their image hashes with the saved capture and checks the order count.

## Paper automation

The Live dashboard includes Start and Stop paper automation. While its browser is open, newly completed candles are claimed in SQLite, frozen and rendered with the chart capture function, then submitted to the analyst and reviewer. The dashboard lists the candle, crossover, decision ID, proposal, review verdict, order, fill or skip, and resulting account values when available. Manual chart questions do not submit orders. A failed capture is recorded as a skip. The dashboard tab that starts automation must stay open; after a reload, use Stop and Start to resume. This version does not provide an unattended browser worker.

The paper strategy buys the configured order size on an upward SMA crossover when flat, closes all shares on a downward crossover while holding, and otherwise waits when flat or holds when invested. A decision expires 60 seconds after its candle closes. The analyst and reviewer both see these rules; Python rejects a proposal that conflicts with them and rechecks the current position. A decision also requires a valid reviewer acceptance and a valid chart analysis. Stop disables the current session, marks in-flight claims skipped, and rejects pending automatic orders. A backend restart also stops automation. Durable SQLite uniqueness deduplicates by account, strategy, symbol, timeframe and candle. Eligible orders use the next eligible observation, with cash, shares, decision age, status and session checked at fill; fill, balance, P&L and automation link commit together. Pending orders may fill while the dashboard is closed unless Stop is pressed or the backend restarts, subject to the 60-second limit.

`sh scripts/smoke-automation.sh` runs disposable-account backend tests and opens the dashboard to check Start and Stop. The browser smoke test uses a simulated candle completion and provider doubles. Isolated synthetic execution is allowed only with the explicit `TRADING_BOT_AUTOMATION_TEST_MODE=1` setting, a marked synthetic disposable account, and a SQLite database under a system temporary directory; synthetic snapshots are rejected in normal operation. `python3 -m unittest tests.test_automation tests.test_strategy -v` checks the strategy, isolated synthetic execution, duplicate handling, veto and expiry.

## Discuss a decision by voice

In the dashboard, use **Refresh decisions**, select a recorded decision and choose **Start voice**. Allow microphone access, wait for the connected status, and ask a question; follow-up questions use the same session. **End voice** closes the connection and stops the microphone. The status shows when the microphone is active. The browser sends audio to OpenAI; the backend sends the selected decision's read-only SQLite summary and negotiates WebRTC without exposing the backend API key. No trading tools are given to the voice session. The summary includes the analyst, reviewer, frozen account and any linked automation outcome; no linked outcome does not prove that nothing else happened to the account.

GPT-Live requires a supported browser with microphone permission, localhost or HTTPS, an OpenAI API key with GPT-Live access, and speakers or headphones. Voice use is billed by the provider. `sh scripts/smoke-voice.sh` starts the application on a separate synthetic decision fixture, opens a browser and asks for a live two-question comparison; it needs an actual microphone, speaker and working provider access. The live conversation is not yet verified on this computer because the in-app browser denied microphone access. `python3 -m unittest tests.test_voice -v` and `node --test tests/test_voice_frontend.mjs` run controlled backend and lifecycle checks without provider or microphone access.

## Offline workstation smoke test

Run `sh scripts/smoke-workstation.sh` from the project root. It uses installed Chrome and Playwright, starts isolated local services on ports 18761 and 18762, and creates a temporary synthetic account. Yahoo and both model calls are replaced with local doubles. The current implementation has not passed end to end: replay startup can race option loading, and the final isolated run timed out before the first observation. See `docs/verification.md` for the actual observed results and remaining checks. The same command is configured in `.github/workflows/workstation-smoke.yml` on pushes; it has not been run in GitHub Actions yet. Live Yahoo, image-model and voice checks remain separate.

## Image-only chart signal exercise

In **Chart signal**, choose a timeframe and make the desired candles visible, then select **Analyze visible chart**. The application sends a PNG of the visible chart and limited symbol, interval, capture/source times, completion timing, source label and staleness metadata to OpenAI. It does not send a separate OHLCV array, paper position or account values in this request. The picture itself can contain visible prices, volume and indicator lines. The dashboard previews the captured image and preserves recent choices with their images in the local SQLite database. Captures and responses may contain market data; the local database is ignored by Git. Only AAPL is currently supported by the live worker and this exercise.

The model receives a named `chart_signal` question with **long**, **short** and **wait** choices. It is instructed to inspect the last two completed visible candles, exclude forming candles, and choose wait for stale, ambiguous or insufficient evidence. For live data, stale means the source observation is missing, more than 120 seconds old, materially future-dated, or the upstream feed is disconnected. In replay, staleness compares source time with the replayed receipt time, not the present wall clock. The displayed probability is matched to the returned choice by value; unavailable is not replaced with a guess, and the provider's separate confidence is not substituted. A probability describes the model's choice among the supplied options, not a probability of profit. No chart-signal request places a paper order. The existing **Ask about chart** control remains a separate position-aware workflow that sends additional account and candle context and must not be confused with this image-only exercise.

### Read-only chart agent

**Analyze visible chart** sends one question and its frozen browser capture to `POST /api/chart-agent`. The backend uses the Agents SDK to select a tool. A chart assessment uses a second OpenAI call through the existing Decisions client. Configure `OPENAI_API_KEY` in the backend environment or the ignored project `.env`. Both calls can incur API usage.

| Registered tool | Model arguments | Result |
| --- | --- | --- |
| `lookup_workstation_rules` | `rule_set` must be `chart_signal` or `paper_strategy` | The chart question and its choices, or the paper strategy text and decision age limit. |
| `assess_visible_chart` | Empty object | The existing classroom decision result, including its status, answer, capture context, PNG hash and saved image link. A missing or invalid capture returns an error. |

For a rules question, send just the question. This command uses the configured provider:

```sh
curl --json '{"question":"Explain the long, short, and wait rules for the chart signal exercise."}' http://127.0.0.1:5173/api/chart-agent
```

For an assessment, the browser includes `snapshot.image_url` (a PNG data URL) and `snapshot.context` (the same timing and symbol fields as the existing classroom endpoint). Each item in the response's `tools` list has the SDK tool name and its returned `result`. An agent-level failure has a status, an error detail and an empty list. A completed tool call can itself report a Decisions provider error. Only the existing interpretation history is written. Paper cash, positions, orders and fills are outside the agent's tools.

### Chart chat

Open **Chart chat** from the workstation header. Type a question or use **Explain rules** or **Assess this chart**. Send captures the visible chart and its timing. The assistant can look up the rules even when the chart is empty. For “Should I wait?” it asks whether to explain the rules or assess the chart. Expand **Tool calls** under a reply to inspect each tool's name, arguments and returned result.

An assessment reports the ticker, interval, candle time, choice and returned probability. **Chart signal** displays the same decision and saved image when they still match the view. If the chart changes while a reply is pending, that reply and its capture stay accessible in the chat. The existing chart arrows show SMA crossovers. Chat has no trading tools. Messages stay in the current tab, and the latest six exchanges provide follow-up context. Saved chart decisions remain in the existing SQLite history after refresh.

For a comparison, assess a chart, display a later snapshot and ask for a new decision in the same chat. The reply shows the current and most recent successful earlier assessment for that ticker and mode (Live or Snapshot), including each interval, UTC chart time, choice and returned probability. A first assessment says there is no earlier match. **New chat** clears the transcript and starts a separate comparison history. Saved chart interpretations remain available under **Chart signal**. [Browser checks and screenshots](docs/verification.md#25--compare-chart-assessments)

Run `python3 -m unittest tests.test_chart_agent tests.test_classroom_decisions -v` for tool and input checks. Run `sh scripts/smoke-classroom.sh` for the Chrome test with a disposable SQLite account and controlled providers. [Tool verification](docs/verification.md#23--read-only-chart-agent) · [Chat verification](docs/verification.md#24--workstation-chart-chat)

### Read-only watchlist exercise

The **Watchlist review** panel runs two coordinated Agents SDK agents on a labeled, deterministic five-ticker synthetic replay. It selects ready rows whose second close rises strictly more than the chosen dollar threshold, ranks by rise then ticker, and assesses up to three frozen captures with the chart signal rules. The defaults are a five-second interval, three cycles and a 20-call session limit; agent model requests and Decision API calls count toward the limit. Run once performs one cycle, Start advances through the three synthetic cutoffs, and Stop prevents further work while labeling in-flight results late. The watchlist log and results are held in backend memory until restart. This exercise never places an order.

Start the normal backend and frontend as described above, then scroll to Watchlist review. Set the threshold to 0 to inspect the AAPL example, or 0.50 for an empty scan. Actual model outputs and probabilities may vary. To run the repeatable offline browser check: `sh scripts/smoke-watchlist.sh`. The activity does not yet load a recorded multi-ticker watchlist; the charts are synthetic test data.

### SMA backtest (Part 4.2)

Use **SMA backtest** to evaluate the existing long-only crossover rule against recorded AAPL stream receipts. The default UTC range covers the latest 200 saved receipts; edit it to choose a different range, up to 500 receipts. Configure short and long SMA windows, initial cash and entry shares. The simulator begins flat and buys on an upward confirmed crossover, closes the entire position on a downward crossover, and otherwise waits or holds. The result shows completed-bar decisions, simulated orders and fills, cash, shares, realized and unrealized profit/loss, and final equity. It runs in memory and does not change the paper account or its ledger.

The engine walks receipts in arrival order under a frozen maximum receipt ID, rebuilds the chart with only the receipts available at each step, and ignores forming or incompletely covered candles. It does not reissue a decision when a later revision changes a previously assessed candle. A signal produces a pending order; the next non-stale quote with both source and receipt time after the decision fills it. An order remaining at the end is unfilled; insufficient cash is rejected at the fill price. A signal first observed over a minute after its bar closed is skipped. No fees, spread, slippage or market impact are modeled. Quotes outside regular hours can be fills; candles used for signals follow the existing XNAS regular-session handling. Final open positions are marked at the latest eligible saved quote. Downloaded Yahoo historical bars are excluded because their retrieval time does not establish their availability at a historical decision time.

A short single-symbol sample, missing observations, selection/survivorship bias and parameter tuning limit what the results establish. The 500-receipt cap bounds a deliberately straightforward replay implementation. Historical performance cannot establish future returns. `sh scripts/smoke-backtest.sh` runs the offline browser test on a temporary recorded fixture; `python3 -m unittest tests.test_backtest tests.test_sma tests.test_pnl tests.test_paper tests.test_strategy` checks known accounting, timing, and indicator examples. The existing GitHub Actions workflow runs the full Python suite, this browser test, and the workstation smoke on pushes.

### A quieter workspace

The chart, data age, and paper-account balance remain visible. Expand the named rows to access SMA settings, candle selection, additional indicators, backtesting, watchlist review, chart analysis, chat, trading history, automation, and voice. Running automation is surfaced in its section so its Stop control is accessible. The header's **Chart chat** link opens the chat section.

Use **Hide SMAs / Show SMAs** above the chart to hide or show the two crossover SMA lines. The button changes only their display; SMA calculations, crossover signals, and strategy rules continue to use the same values. Additional separately configured indicators are unaffected. The visible-chart image sent to Chart signal or Chart chat reflects the chosen visibility; the separate account-aware frozen chart still renders its strategy indicators. This display choice resets when the page reloads. `sh scripts/smoke-live-chart.sh` checks the visible chart before and after the toggle with isolated synthetic data.
