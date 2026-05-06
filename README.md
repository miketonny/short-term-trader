# Short-Term Trader

Automated multi-ETF short-term trading on Interactive Brokers (paper trading).

## Strategy

7 ETFs (SPY, QQQ, IWM, XLF, XLE, XLK, XLV), each allocated 10% of NLV.

**Dual mode:**
- 🔻 **Oversold**: RSI < 30 → 6 conditions → buy. Sell at RSI > 70 / BB upper / MACD death.
- 📈 **Trend**: RSI > 50 + price > MA20 → 4 conditions → buy. Sell at RSI > 75 / MACD death / below MA20.

**Protections**: -2% hard stop loss, 15-min cooldown, 3-failure circuit breaker.

## Quick Start

1. Start IB Gateway: `cd ~/ibgateway/ibc && bash gatewaystart.sh -inline`
2. Run once: `python3 ibkr_strategy.py`
3. Dashboard: `python3 -m http.server 8765` → http://localhost:8765

## Files

| File | Purpose |
|------|---------|
| `ibkr_strategy.py` | Main strategy script |
| `dashboard.html` | Real-time monitoring dashboard |
| `data.json` | Snapshot output (read by dashboard) |
| `CONTEXT.md` | Domain glossary and architecture |

## Prerequisites

- IBKR paper trading account + IB Gateway with IBC
- Twelve Data API key (free tier)
- Python 3.10+ with ib_insync, numpy
