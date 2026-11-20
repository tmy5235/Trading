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

The optional step-by-step replay reads the recorded database in SQLite read-only mode, selects receipts by receipt-time range and processes them in receipt-ID order into an in-memory database. It uses the existing minute and session aggregation before returning each replay step to the same Vue chart. It never writes replayed events into the recorded session. The UI displays both the event's source and receipt times.

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

## Image-only classroom chart signal

```text
Visible chart -> Vue captures PNG + timing in one synchronous operation
     -> POST /api/chart-agent (question + snapshot)
        -> Agents SDK model sees the question and tool definitions
           -> lookup_workstation_rules -> classroom question or paper strategy
           -> assess_visible_chart -> Decisions API receives the bound PNG + metadata
                -> classroom_chart_decisions in SQLite -> Vue result and saved preview
```

This path is independent of the position-aware decision and paper automation paths. The input contains the PNG, a small allowlisted context object and a limitations text; it does not contain candle arrays, individual OHLCV records or paper-account state. A `classroom_chart_decisions` row stores the PNG and its hash, capture context, question, validated choice/probabilities or failure status. The model receives the source label, mode, symbol, interval, capture time, last completed/forming candle timestamps, feed state and stale flag. The UI shows the retained capture for each saved result. Returned labels are not trading instructions and no order is created. The chart PNG may itself show prices, volumes and indicator values; structural PNG checks do not establish that an arbitrary client's pixels match the submitted metadata.

FastAPI binds the image and metadata to one SDK run. The assessment tool takes zero model-supplied arguments and reuses the decision function behind `POST /api/classroom-decisions`. Duplicate assessment calls within a run share its result. The SDK stops after tool execution. FastAPI returns the actual tool outputs or the agent failure. The standalone classroom endpoint remains available to existing callers. [OpenAI documentation on local agent context](https://developers.openai.com/api/docs/guides/agents/define-agents)

Chart chat sends the latest question, up to six earlier text exchanges and the chart captured at Send. Prior chart images remain attached to their own decisions. The agent may ask for clarification without calling a tool. The backend builds assessment replies from the returned decision and includes tool arguments and raw results for inspection. Vue compares the returned image digest, capture metadata and current view with the sent snapshot before updating Chart signal. The view comparison includes the selected interval, replay point, visible candle and indicator values, viewport, and chart selection changes. A late reply remains in the transcript with a link to its saved capture.

Each chat has a local UUID. SQLite links a successful chat assessment to it in `classroom_chat_assessments`, in the same transaction that saves the decision. On the next turn, the backend retrieves the latest matching saved assessment by conversation, ticker and mode. The agent receives its recorded answer and chart context before choosing a tool. The fresh Decisions call still receives only the current chart and timing. Its tool result references the earlier record, and the chat formats both from the stored values. **New chat** rotates the UUID and clears the text history. A standalone Chart signal request or a legacy decision without a conversation link cannot supply a chat baseline.

## Recent history and live chart

The Yahoo worker periodically downloads completed regular-session one-minute price-history bars. `yahoo_minute_bars` stores distinct revisions with their actual retrieval times; streaming observations and receipts remain separate. The live chart selects the latest historical revision available at the current time for each completed minute, falling back to observation-derived minutes; the current forming minute uses live observations. Higher timeframes use those same combined minute bars and identify whether their source is history, stream or both. Yahoo history volume and snapshot-derived stream volume have different sources and may be delayed, revised or unknown. Replay, paper fills and account-aware decision snapshots continue to use the recorded observations available at the relevant time; the historical backfill is not retroactively inserted into those paths. Consequently an older backfilled candle may not be eligible for the separate position-aware frozen decision workflow. The image-only chart exercise captures what is visible.


## Saved chart snapshot

The default Snapshot view calls `GET /api/chart-snapshot` with a symbol, UTC range and timeframe. A SQLite read transaction combines stored observations and historical bar revisions available at the as-of cutoff, aggregates the chosen interval, and returns chart records without advancing receipts or paper orders. The first minute intersecting the start is included; future observations and later revisions are excluded. The chart remains mounted in a keyed container when the user switches modes, and the view initially focuses on the latest 75 candles. The optional step-by-step replay remains a distinct observation-only process and can process simulated replay orders. Current indicator settings are applied to the saved chart; historical indicator configurations are not reconstructed.
