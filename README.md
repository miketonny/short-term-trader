# Short-Term Trader

Automated multi-ETF and forex short-term trading on Interactive Brokers (paper trading).

## Strategy

**ETF (7 symbols): SPY, QQQ, IWM, XLF, XLE, XLK, XLV** — each 10% NLV.

**Forex (3 pairs): EUR/USD, GBP/USD, USD/JPY** — 1% NLV risk per trade.

**Dual mode:**
- 🔻 **Oversold**: RSI < 30 → 5-6 conditions → buy. Sell at RSI > 70 / BB upper / MACD death.
- 📈 **Trend**: RSI > 50 + price > MA20 → 3-4 conditions → buy. Sell at RSI > 75 / MACD death / below MA20.

**Protections**: -3% hard stop loss, 15-min cooldown, circuit breaker (3-failure → 10-min pause).

## Quick Start

1. Start IB Gateway: `cd ~/ibgateway/ibc && bash gatewaystart.sh -inline`
2. ETF: `python3 ibkr_strategy.py`
3. Forex: `python3 ibkr_forex_strategy.py`
4. Dashboard: `python3 server.py` → http://localhost:8765

## Files

| File | Purpose |
|------|---------|
| `ibkr_strategy.py` | Live ETF trading strategy |
| `ibkr_forex_strategy.py` | Live forex trading strategy |
| `backtest.py` | 30-day historical backtest with equity curve |
| `strategy_core.py` | Shared indicator + signal functions |
| `circuit_breaker.py` | State-machine circuit breaker |
| `data_cache.py` | TTL+LRU cache for API responses |
| `rate_limiter.py` | Request rate limiter + UA rotation |
| `notifier.py` | Webhook notifications (trades/stops/errors) |
| `server.py` | HTTP server (dashboard + config save + backtest) |
| `dashboard.html` | Real-time monitoring dashboard |
| `forex_dashboard.html` | Forex monitoring dashboard |
| `data.json` | Snapshot output (read by dashboard) |
| `CONTEXT.md` | Domain glossary and architecture |

## Notifications (optional)

Set `NOTIFY_WEBHOOK_URL` to receive JSON events on trades, stop losses, and errors:
```bash
export NOTIFY_WEBHOOK_URL="https://hooks.example.com/trader"
```

## Prerequisites

- IBKR paper trading account + IB Gateway with IBC
- Twelve Data API key (free tier)
- Python 3.10+ with ib_insync, numpy
