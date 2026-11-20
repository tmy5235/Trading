# Assignment 4, Part 4.1 — Market Data and the Dashboard

Trevor Michael Yuhaniak · IST 402 · November 12, 2026

**Repository:** [tmy5235/Trading](https://github.com/tmy5235/Trading)

**Submitted application and evidence commit:** [`1f22bbd90decea134533987433480d96527ee88d`](https://github.com/tmy5235/Trading/commit/1f22bbd90decea134533987433480d96527ee88d)

**Demonstration video:** The video link will be provided in the Canvas submission comment.

The linked commit identifies the submitted application and supporting evidence. This report is the standalone Canvas deliverable. The demonstration video must be accessible to the instructor and run 30 seconds to one minute.

## Completed solution

Market Studio is a local Vue and FastAPI dashboard for monitoring AAPL. A Python `yfinance` WebSocket worker receives Yahoo Finance observations and saves the decoded messages and receipt times to SQLite. FastAPI derives one-minute and larger regular-session candlesticks and snapshot-based volume, then sends chart updates and feed status to Vue over a WebSocket. The dashboard shows the data source, latest observation, source age, upstream feed status and the separate browser-to-backend connection. It marks a candle as forming or ended and shows coverage and unknown volume.

The dashboard can replay stored observations without connecting to Yahoo. Replay reads a selected receipt-time range in arrival order, retains source timestamps and uses the same candle calculation. An outside-session quote is labeled separately; the chart itself uses the XNAS regular session. The CSV sample is a separate practice feature, not the live-streaming evidence.

[Startup and database initialization](https://github.com/tmy5235/Trading/blob/1f22bbd90decea134533987433480d96527ee88d/README.md) · [Data flow and SQLite schema](https://github.com/tmy5235/Trading/blob/1f22bbd90decea134533987433480d96527ee88d/docs/design-pipeline.md) · [Agent instructions](https://github.com/tmy5235/Trading/blob/1f22bbd90decea134533987433480d96527ee88d/AGENTS.md)

## Design decisions

The SQLite `observations` table stores the canonical decoded message, ticker and raw source time. The `receipts` table records every arrival, its UTC receipt time and an observation ID. An exact repeated message shares the original observation ID but creates another receipt; calculations count the observation once. A changed message gets a new observation, including when it has the same source time. Both values are preserved because the feed does not supply a verified correction identifier.

Candle prices are ordered by source time, with observation ID as a deterministic tie-breaker. Replay follows receipt order, so a late observation changes its source minute only when it reaches that replay step. Candle completion and coverage are calculated alongside OHLCV rather than saved as immutable candle rows; an ended candle can still change when late data arrives. Larger intervals are derived from the minute data using the exchange calendar.

## Verification and what it establishes

| Check | Expected and observed result |
| --- | --- |
| **Live source to chart and SQLite** | On November 10, real Yahoo observation 6 contained AAPL price **$348.06**, source time **19:15:19 UTC** and receipt time **19:15:21.429214 UTC**. The browser showed the forming 19:15 candle with a **$348.06 close**. Volume was unknown because a suitable baseline was absent. |
| **Unavailable feed** | Stopping the Yahoo worker changed the browser's upstream feed status to **disconnected** while its FastAPI connection remained **connected**; the last candle remained visible. |
| **Recorded replay** | A real 25-receipt capture was replayed twice. Both runs produced a **$348.01 close** and an estimated **615-share volume** for 19:16; source observation and receipt counts remained unchanged. |
| **Duplicates and changed messages** | A constructed disposable-database fixture sent the same message twice, followed by a different price at the same source time. The expected and observed result was **two observations and three receipts**, including after reopening the database. |
| **Late arrival and completion** | A constructed late 15:00:20 observation arrived after 15:00:50. It changed the candle high from **101 to 103** at its replay position; the expected final close was **101** and estimated volume **11**. The test also checked the forming and ended states. |
| **After-hours connection** | On November 12 Eastern, a real AAPL observation at **$356.26** was stored and displayed as outside the regular session. It did not produce a regular-session candle. |
| **Automated checks** | `python3 -m unittest discover -s tests -q` passed **36 backend tests** and the frontend Vite production build passed. The comprehensive workstation SmokeTest has not passed end to end. |

The [verification record](https://github.com/tmy5235/Trading/blob/1f22bbd90decea134533987433480d96527ee88d/docs/verification.md) contains commands, timestamps, manual browser observations and failure evidence. Real Yahoo observations and constructed test data are identified separately.

## Remaining limitations

The live regular-session chart check was performed during the November 10 session. The later after-hours quote demonstrates a live update and storage, but does not update the regular-session chart. Yahoo source timestamp units and cumulative-volume semantics were inferred from observed fields rather than verified against a provider contract. Volume calculated from snapshot differences may be unknown or incomplete and is not necessarily the exact number of shares traded during a minute. The feed does not provide a verified correction identity, so distinct same-time messages are not automatically treated as replacements.

The saved live SQLite database is intentionally local and excluded from Git; a new checkout must capture or supply observations before it can replay them. The full workstation SmokeTest remains unverified end to end, and the exchange mapping currently assumes XNAS. The [evidence log](https://github.com/tmy5235/Trading/blob/1f22bbd90decea134533987433480d96527ee88d/docs/evidence-log.md) connects selected instructions, code, observations and a failed check to the resulting decisions.

## AI use

OpenAI Codex assisted with the implementation, documentation and verification. The application uses Yahoo as its market-data source; the optional model-decision feature is separate from Part 4.1 market-data checks. See the [AI-use disclosure](https://github.com/tmy5235/Trading/blob/1f22bbd90decea134533987433480d96527ee88d/AI-USE.md) and saved [project prompts](https://github.com/tmy5235/Trading/tree/1f22bbd90decea134533987433480d96527ee88d/prompts).
