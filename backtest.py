#!/usr/bin/env python3
"""
Backtest Engine
================
30-day historical simulation using the same strategy logic as live trading.
No look-ahead bias: signal from candle N, execution at candle N+1 open.

Usage: python3 backtest.py [--config strategy_config.json] [--days 30]
Output: JSON to stdout
"""
import json, sys, argparse, requests, os, time
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from strategy_core import *

# ─── Config ─────────────────────────────────────────────────
TWELVE_DATA_KEY = "a3377a4097ee4b2fba8a646a6dd898ab"
SLIPPAGE = 0.001  # 0.1% slippage per trade
DASHBOARD_DIR = Path(os.path.expanduser("~/ibkr_dashboard"))
CONFIG_FILE = DASHBOARD_DIR / "strategy_config.json"
PREV_CONFIG_FILE = DASHBOARD_DIR / "strategy_config_prev.json"


def load_config():
    cfg = {
        "symbols": ["SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "XLV"],
        "rsi_oversold": 30, "rsi_overbought": 70, "rsi_trend_overbought": 75,
        "rsi_trend_entry": 50, "adx_trending": 20, "stop_loss_pct": 0.02,
        "cooldown_minutes": 15, "position_alloc": 0.10
    }
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    return cfg


# ─── Data Fetch ──────────────────────────────────────────────
def fetch_candles(symbol, days=30):
    """Fetch 5min OHLCV for the past N days. Returns dict of arrays or None."""
    resp = requests.get("https://api.twelvedata.com/time_series", params={
        "symbol": symbol, "interval": "15min", "outputsize": min(days * 26, 5000),  # max 5000 bars  # ~78 5min bars/day
        "apikey": TWELVE_DATA_KEY
    }, timeout=30)
    data = resp.json()
    if "values" not in data:
        print(f"  ⚠ {symbol}: no data", file=sys.stderr)
        return None
    values = list(reversed(data["values"]))
    return {
        "open":   np.array([float(v["open"]) for v in values]),
        "high":   np.array([float(v["high"]) for v in values]),
        "low":    np.array([float(v["low"]) for v in values]),
        "close":  np.array([float(v["close"]) for v in values]),
        "volume": np.array([float(v["volume"]) for v in values]),
    }


# ─── Simulation ──────────────────────────────────────────────
def run_backtest(cfg):
    symbols = cfg["symbols"]
    rsi_oversold = cfg["rsi_oversold"]
    rsi_overbought = cfg["rsi_overbought"]
    rsi_trend_overbought = cfg["rsi_trend_overbought"]
    rsi_trend_entry = cfg["rsi_trend_entry"]
    adx_trending = cfg["adx_trending"]
    stop_loss_pct = cfg["stop_loss_pct"]
    cooldown_minutes = cfg["cooldown_minutes"]
    position_alloc = cfg["position_alloc"]
    reentry_cooldown = cfg.get("reentry_cooldown_minutes", 15)
    macd_threshold = cfg.get("macd_hist_threshold", 0.05)

    cooldown_bars = cooldown_minutes // 5  # bars of 5min

    trades = []
    equity_curve = []  # cumulative P&L per bar
    positions = {}  # {symbol: {"entry_price": float, "entry_bar": int, "mode": str, "qty": float}}
    last_sells = {}  # {symbol: int} — bar index of last sell, for re-entry cooldown
    nlv = 100_000  # fixed $100K starting NLV for simulation

    print(f"📥 Fetching {len(symbols)} ETFs...", file=sys.stderr)
    all_data = {}
    for sym in symbols:
        candles = fetch_candles(sym)
        if candles and len(candles["close"]) > 51:
            all_data[sym] = candles
        time.sleep(0.3)
    print(f"   Got {len(all_data)} ETFs", file=sys.stderr)

    if not all_data:
        return {"error": "No data fetched"}

    # Find common bar range (shortest ETF)
    min_bars = min(len(d["close"]) for d in all_data.values())
    print(f"   Common bars: {min_bars} (~{min_bars/78:.0f} days)", file=sys.stderr)

    cumulative_pnl = 0.0
    peak_equity = 0.0
    max_drawdown_pct = 0.0

    for bar in range(50, min_bars - 1):  # start after warmup, stop before last bar (execution uses bar+1)
        # Process each symbol
        for sym, candles in all_data.items():
            c = candles["close"][:bar + 1]
            h = candles["high"][:bar + 1]
            l = candles["low"][:bar + 1]
            v = candles["volume"][:bar + 1]

            price = float(c[-1])
            exec_price = float(candles["open"][bar + 1]) * (1 + SLIPPAGE)  # buy at next open + slippage

            avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else 0
            cur_vol = float(v[-1])

            rsi = calc_rsi(c)
            sma = calc_sma(c)
            upper, mid, lower = calc_bbands(c)
            adx = calc_adx(h, l, c)
            macd_result = calc_macd(c)
            if None in (rsi, sma, upper, adx) or macd_result is None:
                continue
            ml, sl, hist, ph = macd_result

            pos = positions.get(sym)

            if pos is None:
                # Check re-entry cooldown
                last_sell_bar = last_sells.get(sym)
                if in_reentry_cooldown(last_sell_bar, bar, reentry_cooldown):
                    continue

                mode = determine_mode(rsi, price, sma, rsi_oversold, rsi_trend_entry)
                if mode == "oversold":
                    if check_buy_oversold(rsi, price, sma, upper, mid, lower, adx, ml, sl, hist, ph, avg_vol, cur_vol, rsi_oversold, adx_trending, macd_threshold):
                        qty = (nlv * position_alloc) / exec_price
                        positions[sym] = {"entry_price": exec_price, "entry_bar": bar, "mode": mode, "qty": qty}
                        trades.append({"sym": sym, "action": "BUY", "bar": bar, "price": exec_price, "mode": mode, "qty": qty})
                elif mode == "trend":
                    if check_buy_trend(rsi, price, sma, ml, sl, hist, avg_vol, cur_vol, rsi_trend_entry, macd_threshold):
                        qty = (nlv * position_alloc) / exec_price
                        positions[sym] = {"entry_price": exec_price, "entry_bar": bar, "mode": mode, "qty": qty}
                        trades.append({"sym": sym, "action": "BUY", "bar": bar, "price": exec_price, "mode": mode, "qty": qty})
            else:
                entry_price = pos["entry_price"]
                entry_bar = pos["entry_bar"]
                mode = pos["mode"]
                stop_price = entry_price * (1 - stop_loss_pct)
                exec_sell_price = float(candles["open"][bar + 1]) * (1 - SLIPPAGE)  # sell at next open - slippage
                in_cooldown = (bar - entry_bar) < cooldown_bars

                # Stop loss always active
                sell_triggered = price <= stop_price
                sell_reason = "stop_loss"

                # Technical sell (only after cooldown)
                if not sell_triggered and not in_cooldown:
                    if mode == "trend":
                        if check_sell_trend(rsi, price, sma, ml, sl, hist, rsi_trend_overbought):
                            sell_triggered = True
                            sell_reason = "trend_sell"
                    else:
                        if check_sell_oversold(rsi, price, upper, ml, sl, hist, rsi_overbought):
                            sell_triggered = True
                            sell_reason = "oversold_sell"

                if sell_triggered:
                    pnl = (exec_sell_price - entry_price) * pos["qty"]
                    cumulative_pnl += pnl
                    trades.append({"sym": sym, "action": "SELL", "bar": bar, "price": exec_sell_price,
                                   "reason": sell_reason, "pnl": pnl, "qty": pos["qty"]})
                    last_sells[sym] = bar
                    del positions[sym]

        # Track equity curve
        equity_curve.append(cumulative_pnl)
        if cumulative_pnl > peak_equity:
            peak_equity = cumulative_pnl
        if peak_equity > 0:
            dd = (peak_equity - cumulative_pnl) / (nlv + peak_equity)  # drawdown as % of peak
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

    # Close remaining positions at last price
    for sym, pos in list(positions.items()):
        last_price = float(all_data[sym]["open"][-1]) * (1 - SLIPPAGE)
        pnl = (last_price - pos["entry_price"]) * pos["qty"]
        cumulative_pnl += pnl
        trades.append({"sym": sym, "action": "SELL", "bar": min_bars - 1, "price": last_price,
                       "reason": "end_of_period", "pnl": pnl, "qty": pos["qty"]})
        del positions[sym]

    # Metrics
    sell_trades = [t for t in trades if t["action"] == "SELL"]
    buy_count = len([t for t in trades if t["action"] == "BUY"])
    sell_count = len(sell_trades)
    wins = [t for t in sell_trades if t.get("pnl", 0) > 0]

    return {
        "config": cfg,
        "symbols_used": list(all_data.keys()),
        "bars_simulated": min_bars - 50,
        "trades": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "win_count": len(wins),
            "loss_count": sell_count - len(wins),
            "win_rate": round(len(wins) / sell_count * 100, 1) if sell_count > 0 else 0,
        },
        "pnl": {
            "total": round(cumulative_pnl, 2),
            "avg_win": round(sum(t.get("pnl", 0) for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t.get("pnl", 0) for t in sell_trades if t.get("pnl", 0) <= 0) /
                              max(1, sell_count - len(wins)), 2),
        },
        "max_drawdown_pct": round(max_drawdown_pct * 100, 2),
        "starting_nlv": nlv,
        "equity_curve": equity_curve[::max(1, len(equity_curve) // 200)],  # downsample to ~200 points
        "trade_list": trades,
        "detail": trade_summary(trades),
    }


def trade_summary(trades):
    """Group trades by symbol"""
    by_sym = {}
    for t in trades:
        s = t["sym"]
        if s not in by_sym:
            by_sym[s] = {"buys": 0, "sells": 0, "pnl": 0}
        if t["action"] == "BUY":
            by_sym[s]["buys"] += 1
        else:
            by_sym[s]["sells"] += 1
            by_sym[s]["pnl"] += t.get("pnl", 0)
    return {s: {"buys": d["buys"], "sells": d["sells"], "pnl": round(d["pnl"], 2)}
            for s, d in by_sym.items()}


if __name__ == "__main__":
    cfg = load_config()
    result = run_backtest(cfg)

    # Save previous config for comparison
    if CONFIG_FILE.exists():
        import shutil
        shutil.copy(CONFIG_FILE, PREV_CONFIG_FILE)

    # Write result file for Dashboard
    result_path = DASHBOARD_DIR / "backtest_result.json"
    result_path.write_text(json.dumps(result, indent=2))
    print(f"\n📁 Saved: {result_path}", file=sys.stderr)

    print(json.dumps(result, indent=2))
