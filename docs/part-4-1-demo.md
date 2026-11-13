# Part 4.1 recording guide

Record one 30–60 second video and upload it somewhere your instructor can access. Add the shareable link to `report.md`. Check the link in a private browser window or share it with the instructor explicitly.

1. Show the AAPL Live dashboard, Yahoo source, feed status, source age and latest observation. If a new quote arrives while recording, point out the changed price and time. Explain that after-hours quotes are recorded but the candlestick chart displays regular-session candles.
2. Show a regular-session candle and its volume, then switch to Replay and step through a saved AAPL range. Point out source and receipt time and the candle being reconstructed. These are recorded observations, not new live updates.
3. Stop the Yahoo worker with Ctrl+C in its terminal. Show **Feed: disconnected** while the dashboard connection stays connected. Restart it with the command in README if desired.
4. Briefly show the SQLite query in README or the verification notes to connect the observations to persistent records. Explain that exact duplicates share one observation and different same-time messages are retained.

Suggested narration: “Yahoo updates are stored with source and receipt times. FastAPI sends derived regular-session candles to Vue over a WebSocket. Replay follows the original receipt order. If Yahoo is unavailable, the feed status changes independently of the browser connection. Volume is an estimate from snapshots and can be unknown.”

The stored live observation recorded on November 10 demonstrated an actual live chart update. On November 12 after market hours a fresh Yahoo quote was received and shown separately; it did not update a regular-session candle. Do not present a recorded or synthetic candle as a live update. If the video must itself show a changing live candle, record during the next regular session or follow your instructor's guidance; this has not been verified after hours.
