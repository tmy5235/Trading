# Assignment 4, Part 4.1 — Market Data and the Dashboard

**Repository:** [Trading](https://github.com/tmy5235/Trading)  
**Submitted source commit:** [`1f22bbd90decea134533987433480d96527ee88d`](https://github.com/tmy5235/Trading/commit/1f22bbd90decea134533987433480d96527ee88d).  
**30–60 second demonstration video:** TO BE ADDED by the student after recording and uploading; verify that the instructor can open the link.

## Completed work

The local Vue dashboard receives AAPL data from a Python/FastAPI backend. A yfinance WebSocket worker receives Yahoo Finance observations and stores decoded messages and receipt times in SQLite. FastAPI derives regular-session candlesticks and estimated volume, then sends chart data and status to the browser over a WebSocket. The dashboard displays the source, latest price and time, source age, and separate upstream and dashboard connection statuses. It labels outside-session quotes separately from regular-session chart candles. Saved observations can be replayed by receipt time without changing the market record.

## Design and verification

Exact duplicate messages share one canonical observation and retain separate receipts. Different messages at the same source time remain separate because the feed does not establish which is a correction. Replay follows receipt order; candle prices follow source time, so late arrivals can revise a derived candle. Completion and coverage accompany the candle and are recomputed, not stored as immutable candle rows. Volume is estimated from cumulative snapshots and may be unknown or incomplete.

A live AAPL update on November 10 was followed from SQLite observation 6 at $348.06 to the forming 19:15 UTC candle. A saved interval replayed twice to the same $348.01 close and estimated 615-share volume, without changing the recorded counts. On November 13 at 03:20 UTC, a fresh Yahoo AAPL observation 27 at $356.26 appeared in SQLite and in the dashboard as an outside-session quote. Stopping the Yahoo worker changed the feed to disconnected while the dashboard connection remained connected. Constructed duplicate and changed messages produced two observations and three receipts, including after reopening the database. Targeted candle, replay, live-chart and timeframe tests and the frontend build passed. Details, commands, failure evidence and timestamps are in [verification](https://github.com/tmy5235/Trading/blob/main/docs/verification.md).

## Limitations

The latest after-hours quote is live but does not update the regular-session chart; the observed live chart update was captured during an earlier regular session. Source timestamp units and volume semantics are inferred from observed fields rather than confirmed against a provider contract. Snapshot-derived volume is not necessarily the true minute total, and some values are unavailable. The feed does not provide a verified correction identity. The comprehensive offline workstation smoke test has not passed end to end. The SQLite market database is intentionally local and ignored by Git, so replay on a different machine requires capturing or supplying data.

## Supporting materials

- [README and startup instructions](https://github.com/tmy5235/Trading/blob/main/README.md)
- [Data flow and SQLite schema](https://github.com/tmy5235/Trading/blob/main/docs/design-pipeline.md)
- [Agent instructions](https://github.com/tmy5235/Trading/blob/main/AGENTS.md)
- [Verification record](https://github.com/tmy5235/Trading/blob/main/docs/verification.md)
- [Evidence log](https://github.com/tmy5235/Trading/blob/main/docs/evidence-log.md) and [AI-use disclosure](https://github.com/tmy5235/Trading/blob/main/AI-USE.md)
