# CLAUDE.md — Short-Term Trader

> Read `CONTEXT.md` for domain glossary (Session, Signal, Mode, Cooldown, etc.) and architecture.

## Quick Connect

```bash
# SSH to live server
ssh -i ~/.ssh/trader_key root@112.213.39.172

# Use trader venv (NOT system python — numpy only here)
/opt/trader-venv/bin/python3 ibkr_strategy.py

# Server timezone: Australia/Sydney (AEST, UTC+10)
# US market: 23:30–06:00 AEST (EDT) / 00:30–07:00 AEST (EST)
```

## Key Files (on server: /root/short-term-trader/)

| File | Role |
|------|------|
| `ibkr_strategy.py` | **Main ETF strategy** (~820 lines, this is what you'll edit most) |
| `ibkr_forex_strategy.py` | Forex strategy (similar pattern, shared modules) |
| `strategy_core.py` | Shared indicator + signal functions |
| `circuit_breaker.py` | State-machine circuit breaker (CLOSED→OPEN→HALF_OPEN) |
| `data_cache.py` | TTL+LRU Twelve Data cache |
| `rate_limiter.py` | API rate limiter with jitter + UA rotation |
| `notifier.py` | Webhook/Telegram notifications |
| `test_strategy.py` | **Unit tests** (68 cases, run before pushing) |
| `run_live.sh` | Cron entry point (flock-guarded, every 5 min) |
| `strategy_config.json` | Editable parameters (symbols, RSI thresholds, allocation, etc.) |

## Logs (on server)

| Log | Path |
|-----|------|
| Strategy output | `/root/live_ibkr_dashboard/strategy.log` |
| Watchdog | `/root/ibkr_dashboard/watchdog.log` |
| Live runner | `/tmp/live_restart.log` |
| IBC/Gateway | `/ibgateway/ibc/logs/ibc-*.txt` |

## Run Tests

```bash
ssh -i ~/.ssh/trader_key root@112.213.39.172 \
  "/opt/trader-venv/bin/python3 /root/short-term-trader/test_strategy.py"
```

## Common Pitfalls & Known Fixes

### Fractional Shares → Error 10243
IBKR API rejects `totalQuantity=0.367`. Always use `max(1, int(qty))`.
Commit `fc3cdfd` fixed this. Test covers 16 fractional share cases.

### PDT Restriction (U24171197 until 2026-06-10)
Account is PDT-restricted. `trading_enabled` flag controls this.
`_skip` guard prevents buying when `pos_count >= MAX_POSITIONS`.

### DST Hardcoded to EDT
Old code: `timedelta(hours=4)` year-round. EST winter = 1-hour error.
Fixed: `utc_now.astimezone(ZoneInfo("America/New_York"))` — auto DST.

### Stop Loss Checked Close, Not Low
Old: `if price <= stop_price` (close only). Missed intra-candle gap-downs.
Fixed: `if l[-1] <= stop_price` (candle low). Test covers both cases.

### Bare `except:` Silently Loses State
4 data.json reads had bare excepts. Corrupted file → silent state reset.
Fixed: typed `except (FileNotFoundError, json.JSONDecodeError)` with warnings.

### `trading_enabled: "false"` (string) Was Truthy
JSON `"false"` string is truthy in Python. Config toggle silently failed.
Fixed: `bool(raw)` for numbers, `str(raw).lower() == "true"` for strings.

### Advisor Fallback Crashed on `await`
Sync lambda `call_advisor = lambda: None` crashed with `TypeError: can't await`.
Fixed: `async def call_advisor(*a, **kw): return None`.

### Swing GTC Orders Were Dead Code
`"after-close" in status_text` never matched (Chinese strings only).
Fixed: `status_code == "closed"`.

## Edit Workflow

1. Edit `ibkr_strategy.py` on server
2. Run tests: `/opt/trader-venv/bin/python3 test_strategy.py`
3. If tests pass, commit + push
4. Strategy auto-picks up changes next cron cycle (within 5 min)

## Code Conventions

- 4-space indent, snake_case, Chinese comments/logs
- Config via `strategy_config.json` with `_cfg.get("key", default)` pattern
- `_skip` flag pattern for guards that must NOT skip dashboard updates
- Use `notify_error(source, msg)` for errors that need TG/webhook alert
- Use `tg(msg)` for direct Telegram alerts
- Indicator functions are stateless (called fresh each cycle with 50-bar history)

## What NOT To Do

- Don't use `continue` inside the `for sym in SYMBOLS:` loop — it skips dashboard updates
- Don't use bare `except:` — always catch specific exceptions and log
- Don't hardcode paths — derive from `DASHBOARD_DIR` or config
- Don't use `round(,3)` for share quantities — IBKR needs integers
- Don't use `timedelta(hours=4)` for ET — use `ZoneInfo("America/New_York")`
- Don't check only `price` (close) for stop loss — use `l[-1]` (candle low)
- Don't edit on local and SCP — edit directly on server via SSH
