# Short-Term Trader — CONTEXT

Automated multi-ETF short-term trading on Interactive Brokers with a dual-mode
strategy. Runs on a cron schedule during US market hours, displays a real-time
dashboard.

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

A Position records the Mode under which it was entered. The same Mode determines
exit logic throughout the Position's lifetime.

### Position
A currently held quantity of an ETF. Tracks: symbol, quantity (fractional
shares), average entry cost, entry time, and entry Mode. Positions are rebuilt
from IBKR at the start of each run, with Mode and entry time inherited from the
previous Snapshot.

### Cooldown
A 15-minute window after entering a Position during which technical sell Signals
are ignored. Hard Stop Loss is NOT subject to Cooldown.

### Stop Loss
A hard -2% exit threshold measured from entry cost. Always active, never subject
to Cooldown. If the current price drops to or below entry × 0.98, the Position
is immediately sold.

### Circuit Breaker
After 3 consecutive IBKR connection failures, the strategy pauses all automated
trading. Manual intervention (deleting the failure counter file) is required to
resume.

### Allocation
Each ETF receives 10% of the account's Net Liquidation Value (USD). Fractional
shares are used. With 7 ETFs, maximum deployed capital is 70%.

## Architecture

```
Twelve Data API ──→ OHLCV candles ──→ numpy indicators
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
                                   Dashboard HTML (data.json)
```

## Decisions

### Why two Modes instead of one?
A pure oversold-bounce strategy only trades during dips. In sustained bull
markets it may sit idle for days. Adding Trend mode lets the system participate
in rallies.

### Why 7 ETFs at 10% each?
Three broad-market ETFs (SPY, QQQ, IWM) plus four sector ETFs (XLF, XLE, XLK,
XLV) provide exposure to different market segments. At 10% per ETF, total
deployment maxes at 70%, leaving 30% buffer. This also stays within the Twelve
Data free tier (7 API calls × 78 runs/day = 546, under 800 limit).

### Why fractional shares?
With a small account, 10% of NLV may not buy a whole share of SPY (~$720).
Fractional shares via IBKR SMART routing allow exact allocation regardless of
share price.

### Why 5-minute intervals instead of 3?
7 ETFs × 130 runs (3-min) = 910 API calls/day, exceeding the Twelve Data free
800 limit. 5-minute intervals = 78 runs/day = 546 calls. Each run uses fresh
non-overlapping 5-min candles, improving Signal quality over overlapping 3-min
polls.

### Why Yahoo Finance RSS for news?
Twelve Data has no news endpoint. Yahoo Finance RSS is free, requires only a
User-Agent header, and provides 6 headlines per poll. Used for Dashboard
display, not trading decisions.
