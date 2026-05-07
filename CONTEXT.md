# Short-Term Trader — CONTEXT

Automated multi-ETF short-term trading on Interactive Brokers with a dual-mode
strategy. Runs on a cron schedule during US market hours, displays a real-time
dashboard with backtesting and parameter controls.

## Glossary

### Session
A US equity market trading session: Monday–Friday, 09:30–16:00 ET. The strategy
only pulls market data and evaluates trades during an open Session. Outside a
Session, the system preserves the last Snapshot.

### Snapshot
The most recent set of Signal scores, prices, and indicators captured during an
open Session. Snapshots persist across non-trading hours so the Dashboard shows
meaningful data when the user wakes up.

### Signal
A single boolean condition derived from a technical indicator. Multiple Signals
are evaluated per ETF; all must pass for a buy order. Signals are grouped by
Mode.

### Mode
The trading logic applied to an ETF. Two modes exist:

- **Oversold** — Triggered when RSI < 30. Requires 6 Signals (RSI, Bollinger
  lower band, trend direction, ADX, MACD histogram, volume). Exit when RSI > 70,
  price touches the upper Bollinger band, or MACD death cross.
- **Trend** — Triggered when RSI > 50 and price is above MA20. Requires 4
  Signals (RSI > 50, trend direction, MACD golden cross, volume). Exit when RSI
  > 75, MACD death cross, or price falls below MA20.

An ETF with RSI between 30 and 50 (or RSI > 50 but below MA20) produces no
Mode and waits.

### Position
A currently held quantity of an ETF. Tracks: symbol, quantity (fractional
shares), average entry cost, entry time, and entry Mode. Positions are rebuilt
from IBKR at the start of each run, with Mode and entry time inherited from the
previous Snapshot.

### Cooldown
A 15-minute window after entering a Position during which technical sell Signals
are ignored. Hard Stop Loss is NOT subject to Cooldown.

### Re-entry Cooldown
A 15-minute window after selling a Position during which the same symbol cannot
be bought again. Prevents immediate re-entry on whipsaw signals.

### Stop Loss
A hard -3% exit threshold measured from entry cost. Always active, never subject
to Cooldown. If the current price drops to or below entry × 0.97, the Position
is immediately sold.

### Circuit Breaker
After 3 consecutive IBKR connection failures, the strategy pauses all automated
trading. Manual intervention (deleting the failure counter file) is required to
resume.

### Allocation
Each ETF receives 10% of the account's Net Liquidation Value (USD). Fractional
shares are used. With 7 ETFs, maximum deployed capital is 70%.

### Backtest
A 30-day historical simulation that replays the strategy against past OHLCV data.
Uses no look-ahead bias (signal from candle N, execution at candle N+1 open)
with 0.1% slippage. Results show trade count, win rate, total P&L, and max
drawdown. Triggered from the Dashboard and compared against the previous
parameter set.

### Session Stats
Per-day trading statistics tracked in real-time: total trades, wins, losses,
P&L, and symbols traded. Reset at the start of each new trading day.
Displayed in the Dashboard's "今日战报" panel.

### MACD Histogram Threshold
The minimum absolute value of the MACD histogram for a valid buy Signal.
Set to 0.10 for 15-minute candles. Filters out weak crossovers where the
histogram barely crosses zero.

## Architecture

```
strategy_config.json ←─ Dashboard (parameter panel + save)
        │
        ▼
Twelve Data API ──→ 15-min OHLCV ──→ numpy indicators
                                         │
                                         ▼
                                   Signal evaluation
                                   (oversold / trend)
                                         │
                                         ▼
                                   ib_insync ──→ IB Gateway ──→ IBKR (paper)
                                         ▲
                                   Hermes cron (every 5 min)
                                         │
                                         ▼
                                   data.json ──→ Dashboard HTML (server.py)
                                         │
                                   backtest.py (30-day simulation)
```

## Key Files

| File | Purpose |
|------|---------|
| `ibkr_strategy.py` | Live trading strategy (reads config, trades via IBKR) |
| `backtest.py` | 30-day historical backtest engine |
| `strategy_core.py` | Shared indicator calculation + signal functions |
| `server.py` | HTTP server (serves dashboard, config save, backtest trigger) |
| `dashboard.html` | Real-time monitoring dashboard with config/backtest panels |
| `strategy_config.json` | Editable strategy parameters |
| `data.json` | Live snapshot output (market data, signals, session stats) |
| `CONTEXT.md` | This file — domain glossary and architecture |

## Decisions

### Why 15-minute candles?
5-minute MACD signals had 19-21% win rate and consistent losses (-$5K to -$13K
in backtests). 15-minute candles increased win rate to 43% with first positive
P&L (+$204). Longer timeframe filters MACD noise naturally.

### Why two Modes instead of one?
A pure oversold-bounce strategy only trades during dips. In sustained bull
markets it may sit idle for days. Adding Trend mode lets the system participate
in rallies.

### Why 7 ETFs at 10% each?
Three broad-market ETFs (SPY, QQQ, IWM) plus four sector ETFs (XLF, XLE, XLK,
XLV) provide exposure to different market segments. At 10% per ETF, total
deployment maxes at 70%, leaving 30% buffer.

### Why 5-minute cron with 15-minute candles?
The cron runs every 5 minutes to pick up new candle closures quickly. The
15-minute candle interval means most runs see no new data (the candle hasn't
closed yet), but the system catches the signal within 5 minutes of candle close.

### Why MACD histogram threshold 0.10?
Without a threshold, MACD golden crosses with histogram values of 0.001 produce
false signals. Threshold 0.10 on 15-min candles filters noise while preserving
meaningful crossovers. Backtest confirmed: 0.10 → +$204, 0.50 → -$293.

### Why HTML dashboard instead of React/TypeScript?
The single-file HTML dashboard (< 500 lines) covers all current needs: config
editing, backtest triggering, signal display, session stats, and news. Adding a
framework would introduce build complexity without enabling new capabilities.
Chart libraries can be added via CDN `<script>` tags when needed.
