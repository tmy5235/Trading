# Assignment 4.2 — Indicators, simulated trading, and backtesting (draft)

**Student:** Trevor Yuhaniak  
**Repository:** [Trading](https://github.com/tmy5235/Trading)  
**Submitted commit:** To be added after review and commit.  
**Demonstration video:** To be recorded and linked (30–60 seconds; check instructor access).

## Work completed

The workstation displays two configurable simple moving averages and confirmed crossovers on the AAPL chart. Forming and incompletely covered candles are excluded from the SMA calculations. The simulated account records orders, fills, cash, positions, and realized and unrealized profit/loss. A separate backtest evaluates the same long-only crossover rule using recorded AAPL receipts in arrival order: buy the configured size on an upward crossover while flat and close the position on a downward crossover. It displays decisions, simulated fills and account results without modifying the paper account.

The backtest reads only receipts available at each step and fills an order using a later eligible quote. It starts with configurable cash and no position and assumes no fees, slippage or market impact. It excludes Yahoo history downloaded after the relevant decision, marks open shares at the last eligible quote, and leaves an order unfilled if no later quote is available. Historical observations, data gaps and one chosen ticker limit the conclusions; past results do not establish future performance. [README](../README.md) describes the controls and assumptions.

## Verification

The Python suite passed 52 tests, including comparison of the moving averages with pandas rolling means and hand-calculated crossover values, incremental replay and unfinished-candle checks, and accounting examples. The offline backtest browser test showed a buy at $1, a close at $2, $1 profit and no change to the paper ledger. The workstation smoke test also passed its separate buy/sell/close and P&L fixture. A 200-receipt recorded AAPL range produced seven decisions and no trades. Details, including the revised failed test, are in [verification](verification.md).

GitHub Actions is configured to run the Python tests and browser smoke tests on push. A remote run for the submitted version and a clean-checkout verification still need to be checked. The video and submitted commit must be added before this becomes `report.md` for the 4.2 Canvas submission.
