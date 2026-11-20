# Assignment 4, Part 4.2 — Indicators, Simulated Trading, and Backtesting

Trevor Michael Yuhaniak · IST 402

**Repository:** [tmy5235/Trading](https://github.com/tmy5235/Trading)

**Submitted application commit:** [`0343122975924378c4a49939c58d5c039f2b4b60`](https://github.com/tmy5235/Trading/commit/0343122975924378c4a49939c58d5c039f2b4b60)

**Demonstration video:** Recorded; the accessible link will be provided in the Canvas submission comment.

## Completed solution

Market Studio is a local Vue and FastAPI workstation that displays recorded and live AAPL market data. For Part 4.2, the chart shows two configurable simple moving averages (SMAs), calculated from completed candles, and can show confirmed crossover markers. The **Show SMAs / Hide SMAs** control changes the chart display without changing the calculations. The interface also shows source age and whether a candle is forming or ended.

The paper account tracks simulated orders and fills, cash, share positions, and realized and unrealized profit or loss. The SMA strategy buys a configured number of shares on an upward crossover when flat, closes the position on a downward crossover while holding, and otherwise waits or holds. The workstation also provides a separate historical backtest of this rule; its results do not change the paper account.

## Design and assumptions

The short SMA is the arithmetic mean of the most recent completed closes in its window; the long SMA uses a larger window. Both remain unavailable while warming up. Forming candles and candles without complete coverage do not enter the windows. Crossovers compare the unrounded values, with an equal reading retaining the prior side until a change of side is confirmed.

The backtest walks recorded AAPL observations in receipt order under a frozen receipt cutoff. Each decision uses the evidence available at that replay step. A signal creates a pending simulated order that can fill only on a later eligible quote; an order without a later quote remains unfilled. The backtest starts flat with configurable cash and share size, rejects unaffordable buys, and marks any open position using the latest eligible quote. It excludes Yahoo history downloaded later because its retrieval time does not establish what was available at the historical decision time.

The simulations are long-only and assume no fees, spread, slippage, or market impact. The paper account uses weighted-average purchase cost to calculate realized and unrealized results. The backtest runs in memory and is limited to 500 receipts per run. Missing observations, a single selected instrument, and parameter choices limit what the results establish; historical results do not predict future performance.

## Verification

| Check | Result |
| --- | --- |
| SMA and crossover | The SMA tests compared the rolling averages with pandas and hand-calculated values, checked crossovers and ties, and compared replay prefixes with batch calculations. Forming candles were excluded. |
| Backtest and accounting | In a synthetic recorded fixture, an upward signal led to a later buy at $1 and a later close at $2, producing $1 realized profit and $10,001 final cash. A later revision outside the range did not change the run. |
| Browser and paper ledger | The isolated backtest browser smoke test showed those two fills and $1 profit while confirming the separate paper ledger was unchanged. The workstation smoke test also passed a separate synthetic buy/sell/close example with $25 realized and $0 unrealized profit. |
| Recorded data | A 200-receipt recorded AAPL sample produced seven completed-bar decisions and no trades, leaving $10,000 cash and $0 profit or loss. A later browser run also identified an upward signal that was skipped because its evidence was stale. |
| Automated checks | The Python suite passed 52 tests, the Vite build passed, and the backtest and workstation browser smoke tests passed locally. The [GitHub Actions run for the submitted application commit](https://github.com/tmy5235/Trading/actions/runs/44677472369) passed its Python suite, backtest browser smoke test, and workstation browser smoke test. |

The detailed commands, expected results, observed results, and distinction between synthetic and recorded data are in the project [verification log](https://github.com/tmy5235/Trading/blob/0343122975924378c4a49939c58d5c039f2b4b60/docs/verification.md). One initial test used a cutoff that accidentally included a fill receipt; the cutoff was corrected and the test passed. The project [README](https://github.com/tmy5235/Trading/blob/0343122975924378c4a49939c58d5c039f2b4b60/README.md) describes setup, the backtest, and its assumptions.

## Limitations and AI use

A clean local checkout of the application commit passed the Python tests, frontend build, and backtest browser smoke test using the existing Python environment and installed frontend dependencies. GitHub Actions also passed after installing the specified dependencies on its runner. The local market-recording database is not included in the repository, so a fresh checkout needs recorded observations to run a backtest on real data. The synthetic smoke tests provide a repeatable offline check.

OpenAI Codex assisted with code, documentation, and verification. The [AI-use disclosure](https://github.com/tmy5235/Trading/blob/0343122975924378c4a49939c58d5c039f2b4b60/AI-USE.md), [saved prompts](https://github.com/tmy5235/Trading/tree/0343122975924378c4a49939c58d5c039f2b4b60/prompts), and [evidence log](https://github.com/tmy5235/Trading/blob/0343122975924378c4a49939c58d5c039f2b4b60/docs/evidence-log.md) describe the recorded development process.
