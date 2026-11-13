# Design pipeline

```text
Yahoo Finance WebSocket -> yfinance continuous worker -> SQLite observations + receipts
                                       |                        |
                                       v                        v
                              upstream feed status       one-minute candles
                                       |                        |
                                       +-------> FastAPI <-------+
                                          session aggregation
                                          | HTTP history / WebSocket snapshots
                                          v
                                     Vite proxy -> Vue chart
```

The AAPL worker stores decoded updates and receipt times. The backend derives one-minute prices from distinct recorded observations, then aggregates them using the XNAS regular-session calendar for both live history and WebSocket snapshots. The Vue chart selects a timeframe and displays upstream status separately from its FastAPI connection. `/api/history` remains a separate synthetic CSV demonstration; it is not mixed into live history. Source-time units and cumulative-volume semantics are inferred, and snapshot-derived volume can be unknown or incomplete. The worker uses yfinance's underlying socket close notification to detect upstream disconnects; that private interface may change in future yfinance versions.

Replay reads the recorded database in SQLite read-only mode, selects receipts by receipt-time range and processes them in receipt-ID order into an in-memory database. It uses the existing minute and session aggregation before returning each replay step to the same Vue chart. It never writes replayed events into the recorded session. The UI displays both the event's source and receipt times.

## Observation schema and identity

`observations` holds `id` (primary key), `ticker`, `source_time` (the raw source value), and `message_json` (complete decoded message, canonicalized and unique). `receipts` holds `id` (monotonic arrival order), `observation_id` (foreign key), and `received_at_utc`. `feed_status` holds a symbol, worker state and heartbeat time. SQLite files are local and ignored by Git; run `python3 -m backend.observations` to initialize the default file.

Identical canonical messages reuse an observation ID but retain each arrival as a receipt. A changed message gets a new ID even when its source time matches an earlier message; both remain available because this feed does not establish revision identity. Replay follows receipt order, while candles order distinct prices by source time, breaking same-time ties by first observation ID. Late observations cause affected candles and volume baselines to be recomputed when they arrive. The `ended` and coverage fields are derived alongside the six OHLCV candle fields, rather than persisted as immutable candle rows; an ended candle can still change on a late receipt. Unknown volume remains distinct from zero. Outside-session updates remain recorded and appear as the latest observation, but do not become regular-session chart candles.

For live snapshots and replay steps, Python computes the selected short and long SMAs from ended, fully covered candles, and supplies per-candle values and confirmed crossover signals with the records. Vue renders the two lines and crossover arrows; its display rounds values only after the calculation.

The indicator registry exposes type, units and setting metadata through FastAPI. Named configurations are saved in SQLite. For live and replay records, Python evaluates each instance independently over ended, fully covered candles and attaches values or warm-up gaps by candle time. Vue draws configured lines and reports the selected candle's values. The short/long crossover calculation reuses the registered SMA calculation.

Paper orders pass from Vue through FastAPI to `backend.paper.execute_action`. A pending live order is processed against subsequent recorded Yahoo receipts; a replay step processes only the selected session's next receipt. `backend.paper.apply_receipt` atomically checks and writes the simulated cash, position, order and linked fill in SQLite. Paper state is separate from the preserved source observations.

Paper fills update weighted cost basis and realized P&L atomically with account and fill records. Completed regular-session one-minute close events revalue an open position and append idempotent P&L history rows; Vue displays their event and price sources and times. Replay advances valuations only as selected receipts are reached. P&L is per symbol, with account cash recorded in each snapshot.

Live decision snapshots freeze a completed candle, chart records, SMA/crossover, and reconciled paper account as of candle close. Replay steps atomically save a checkpoint of the shared paper account after applying fills; a replay snapshot pairs that checkpoint with the exact selected receipt-range prefix and chart records at the displayed point. Its candle may include revisions received after the original close but before the replay point. FastAPI separately sends frozen evidence to the OpenAI Decisions API and saves the named proposal, factual validation and status. Decisions never execute paper orders; replay snapshots are ineligible to authorize orders.

For chart analysis, Vue renders the selected range through the completed candle from a frozen SQLite snapshot in a separate Lightweight Charts instance, exports a PNG and displays it. FastAPI validates the capture metadata and PNG, then submits the PNG and cutoff-limited snapshot together with three named choice questions. It stores the exact model input, image, questions, answers and comparison alongside the decision. PNG structural validation does not establish the semantic content of pixels supplied by arbitrary API clients. No decision executes an order.

For each decision, the analyst and reviewer make separate bounded calls. Both receive the same frozen snapshot; chart review receives the same saved image and analyst answers. The reviewer additionally receives backend account and indicator facts. SQLite stores both outputs, the reviewer input and final check under the decision ID. Python's existing account and chart validations override an acceptance. No strategy beyond the documented account and signal checks is currently configured, and decisions do not execute orders.

In the current partial automation implementation, a live Vue dashboard detects newly completed candles and requests durable SQLite claims. It builds a fresh frozen chart capture and submits the existing analyst/reviewer decision, then asks the backend to apply it. A claimed event links the snapshot, decision, automatic order and eventual fill or skip. Stop and fill transactions serialize against one another; stop cancels pending automatic orders. The fixed paper strategy acts on SMA crossovers and rejects decisions older than 60 seconds after candle close. Synthetic execution requires an explicit test-mode flag and a marked temporary SQLite account; normal accounts cannot execute synthetic snapshots. The dashboard tab must remain open to discover candles; an unattended chart worker is not implemented.

For voice, Vue selects a stored decision and requests microphone access. FastAPI reads a decision, frozen snapshot, role findings and linked paper outcome using a read-only SQLite connection, then provides that limited summary in GPT-Live's instructions while exchanging the browser's SDP offer for an answer. Microphone and assistant audio use WebRTC media tracks. No trading tool or write access is supplied to the voice session. Vue closes the peer and releases tracks on End, disconnect or startup failure.
