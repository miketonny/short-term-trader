#!/usr/bin/env python3
"""
Backtest Engine — 用与实盘同一套规则、同一个 K 线周期跑历史回放。

数据周期取自 strategy_config.json 的 interval / candles（实盘当前是日线）。
无未来函数：第 N 根出信号，第 N+1 根开盘成交（含 0.1% 滑点）。

已对齐实盘的规则：止损、买入冷却、重入冷却、24h 最短持有、SMA 趋势过滤、
                  最大持仓数、MACD 阈值、两套入场/离场信号。
实盘也没有的规则（配置里有键但策略从不读，这里同样不实现）：
                  take_profit_pct、max_hold_days、use_advisor。

Usage: python3 backtest.py [--config strategy_config.json] [--bars 500]
Output: JSON to stdout，同时写 <config 所在目录>/backtest_result.json
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from strategy_core import (calc_adx, calc_bbands, calc_macd, calc_rsi, calc_sma,
                           check_buy_oversold, check_buy_trend, check_sell_oversold,
                           check_sell_trend, determine_mode)

# ─── Config ─────────────────────────────────────────────────
TWELVE_DATA_KEY = "a3377a4097ee4b2fba8a646a6dd898ab"
SLIPPAGE = 0.001            # 0.1% per side
DEFAULT_NLV = 100_000       # 起始净值，--nlv 可改（跑真实账户规模用 --nlv 1010）
DEFAULT_BARS = 500          # 回测窗口（根）
COMMISSION_PER_SHARE = 0.005   # IBKR Fixed
COMMISSION_MIN = 1.00          # 每笔最低佣金
LEVERAGE_DEFAULT = 1.0

# 各周期一天有几根 K 线，用来把"分钟"形式的冷却换算成"根"
BARS_PER_DAY = {"1min": 390, "5min": 78, "15min": 26, "30min": 13, "45min": 9,
                "1h": 7, "2h": 3, "4h": 2, "1day": 1, "1week": 0.2}


def bars_per_day(interval):
    return BARS_PER_DAY.get(interval, 1)


def mins_to_bars(minutes, interval):
    """N 分钟冷却 = 多少根 K 线（按交易日折算，日线时 1440 分钟 = 1 根）"""
    return max(1, int(round(minutes / 1440.0 * bars_per_day(interval))))


def commission(shares):
    """IBKR Fixed：$0.005/股，每笔最低 $1"""
    return max(COMMISSION_MIN, COMMISSION_PER_SHARE * abs(shares))


def position_qty(nlv, alloc, leverage, price):
    """与实盘同一行：整数股，最少 1 股（IBKR API 不收小数股）"""
    return max(1, int((nlv * alloc * leverage) / price))


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    cfg.setdefault("interval", "1day")
    cfg.setdefault("candles", 200)
    cfg.setdefault("max_positions", 3)
    cfg.setdefault("trend_filter_sma_period", 0)
    cfg.setdefault("reentry_cooldown_minutes", 15)
    cfg.setdefault("macd_hist_threshold", 0.0)
    return cfg


# ─── Data Fetch ──────────────────────────────────────────────
def fetch_candles(symbol, interval, outputsize):
    """按配置的周期取 K 线。返回 dict of arrays 或 None。"""
    try:
        resp = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": symbol, "interval": interval,
            "outputsize": min(outputsize, 5000), "apikey": TWELVE_DATA_KEY,
        }, timeout=30)
        data = resp.json()
    except Exception as e:
        print(f"  ⚠ {symbol}: 请求失败 {e}", file=sys.stderr)
        return None
    if "values" not in data:
        print(f"  ⚠ {symbol}: {data.get('message', 'no data')}", file=sys.stderr)
        return None
    values = list(reversed(data["values"]))
    return {
        "open": np.array([float(v["open"]) for v in values]),
        "high": np.array([float(v["high"]) for v in values]),
        "low": np.array([float(v["low"]) for v in values]),
        "close": np.array([float(v["close"]) for v in values]),
        "volume": np.array([float(v["volume"]) for v in values]),
    }


# ─── Simulation ──────────────────────────────────────────────
def run_backtest(cfg, bars=DEFAULT_BARS, starting_nlv=DEFAULT_NLV):
    symbols = cfg["symbols"]
    interval = cfg["interval"]
    rsi_oversold = cfg["rsi_oversold"]
    rsi_overbought = cfg["rsi_overbought"]
    rsi_trend_overbought = cfg["rsi_trend_overbought"]
    rsi_trend_entry = cfg["rsi_trend_entry"]
    adx_trending = cfg["adx_trending"]
    stop_loss_pct = cfg["stop_loss_pct"]
    position_alloc = cfg["position_alloc"]
    leverage = cfg.get("leverage", LEVERAGE_DEFAULT)
    max_positions = cfg["max_positions"]
    trend_sma = cfg["trend_filter_sma_period"]
    macd_threshold = cfg["macd_hist_threshold"]

    # 冷却按"根"算：实盘拿真实时钟比，回测没有时钟，只能按交易日折算
    cooldown_bars = mins_to_bars(cfg["cooldown_minutes"], interval)          # 买入后禁再买/禁卖
    reentry_bars = mins_to_bars(cfg["reentry_cooldown_minutes"], interval)   # 卖出后禁买
    warmup = max(52, (trend_sma + 2) if trend_sma else 0)

    print(f"📥 取 {len(symbols)} 个标的 · 周期 {interval} · 窗口 {bars} 根", file=sys.stderr)
    all_data = {}
    for sym in symbols:
        candles = fetch_candles(sym, interval, bars)
        if candles and len(candles["close"]) > warmup:
            all_data[sym] = candles
        time.sleep(0.3)
    print(f"   成功 {len(all_data)} 个", file=sys.stderr)
    if not all_data:
        return {"error": "没有取到数据"}

    min_bars = min(len(d["close"]) for d in all_data.values())
    span_days = min_bars / bars_per_day(interval)
    print(f"   公共根数 {min_bars} (~{span_days:.0f} 个交易日)；"
          f"冷却 {cooldown_bars}/{reentry_bars} 根", file=sys.stderr)

    trades, equity_curve = [], []
    positions = {}     # {symbol: {...}}
    last_sells = {}    # {symbol: bar}
    last_buys = {}     # {symbol: bar}
    nlv = starting_nlv
    cumulative_pnl = peak_equity = max_drawdown_pct = 0.0
    total_commission = 0.0

    for bar in range(warmup, min_bars - 1):   # 末根不跑：成交要用 bar+1 的开盘
        for sym, candles in all_data.items():
            c = candles["close"][:bar + 1]
            h, l, v = candles["high"][:bar + 1], candles["low"][:bar + 1], candles["volume"][:bar + 1]
            price = float(c[-1])
            next_open = float(candles["open"][bar + 1])
            buy_price = next_open * (1 + SLIPPAGE)
            sell_price = next_open * (1 - SLIPPAGE)

            avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else 0
            cur_vol = float(v[-1])
            rsi, sma = calc_rsi(c), calc_sma(c)
            upper, mid, lower = calc_bbands(c)
            adx = calc_adx(h, l, c)
            macd_result = calc_macd(c)
            if None in (rsi, sma, upper, adx) or macd_result is None:
                continue
            ml, sl, hist, ph = macd_result
            sma_trend = calc_sma(c, trend_sma) if trend_sma else None

            pos = positions.get(sym)

            if pos is None:
                if sym in last_sells and (bar - last_sells[sym]) < reentry_bars:
                    continue
                if sym in last_buys and (bar - last_buys[sym]) < cooldown_bars:
                    continue
                # SMA 趋势过滤：价格在均线下方不做多（实盘对两种模式同等对待）
                if sma_trend is not None and price < sma_trend:
                    continue
                if sum(1 for p in positions.values() if p) >= max_positions:
                    continue

                mode = determine_mode(rsi, price, sma, rsi_oversold, rsi_trend_entry)
                ok = False
                if mode == "oversold":
                    ok = bool(check_buy_oversold(rsi, price, sma, upper, mid, lower, adx, ml, sl,
                                                 hist, ph, avg_vol, cur_vol, rsi_oversold,
                                                 adx_trending, macd_threshold))
                elif mode == "trend":
                    ok = bool(check_buy_trend(rsi, price, sma, ml, sl, hist, avg_vol, cur_vol,
                                              rsi_trend_entry, macd_threshold))
                if ok:
                    qty = position_qty(nlv, position_alloc, leverage, buy_price)
                    comm = commission(qty)
                    total_commission += comm
                    positions[sym] = {"entry_price": buy_price, "entry_bar": bar,
                                      "mode": mode, "qty": qty, "comm": comm}
                    last_buys[sym] = bar
                    trades.append({"sym": sym, "action": "BUY", "bar": bar,
                                   "price": buy_price, "mode": mode, "qty": qty,
                                   "notional": round(buy_price * qty, 2), "comm": round(comm, 2)})
            else:
                entry_price, entry_bar, mode = pos["entry_price"], pos["entry_bar"], pos["mode"]
                stop_price = entry_price * (1 - stop_loss_pct)
                hold_bars = bar - entry_bar

                # 硬止损：用本根最低价判断（与实盘一致，不看收盘价）
                sell_triggered = float(l[-1]) <= stop_price
                sell_reason = "stop_loss"

                # 24h 最短持有期内不做技术卖出
                if not sell_triggered and hold_bars >= cooldown_bars:
                    if mode == "trend":
                        if check_sell_trend(rsi, price, sma, ml, sl, hist, rsi_trend_overbought):
                            sell_triggered, sell_reason = True, "trend_sell"
                    else:
                        if check_sell_oversold(rsi, price, upper, ml, sl, hist, rsi_overbought):
                            sell_triggered, sell_reason = True, "oversold_sell"

                if sell_triggered:
                    comm = commission(pos["qty"])
                    total_commission += comm
                    pnl = (sell_price - entry_price) * pos["qty"] - pos["comm"] - comm
                    cumulative_pnl += pnl
                    trades.append({"sym": sym, "action": "SELL", "bar": bar, "price": sell_price,
                                   "reason": sell_reason, "pnl": pnl, "qty": pos["qty"],
                                   "notional": round(sell_price * pos["qty"], 2),
                                   "comm": round(comm, 2)})
                    last_sells[sym] = bar
                    del positions[sym]

        equity_curve.append(cumulative_pnl)
        peak_equity = max(peak_equity, cumulative_pnl)
        if peak_equity > 0:
            max_drawdown_pct = max(max_drawdown_pct,
                                   (peak_equity - cumulative_pnl) / (nlv + peak_equity))

    # 收尾：仍持有的按最后一根开盘平掉
    for sym, pos in list(positions.items()):
        last_price = float(all_data[sym]["open"][-1]) * (1 - SLIPPAGE)
        comm = commission(pos["qty"])
        total_commission += comm
        pnl = (last_price - pos["entry_price"]) * pos["qty"] - pos["comm"] - comm
        cumulative_pnl += pnl
        trades.append({"sym": sym, "action": "SELL", "bar": min_bars - 1, "price": last_price,
                       "reason": "end_of_period", "pnl": pnl, "qty": pos["qty"], "comm": round(comm, 2)})

    sell_trades = [t for t in trades if t["action"] == "SELL"]
    wins = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losses = [t for t in sell_trades if t.get("pnl", 0) <= 0]

    return {
        "config": cfg,
        "interval": interval,
        "bars_fetched": min_bars,
        "bars_simulated": min_bars - warmup,
        "period_days": round(min_bars / bars_per_day(interval)),
        "symbols_used": list(all_data.keys()),
        "trades": {
            "buy_count": len([t for t in trades if t["action"] == "BUY"]),
            "sell_count": len(sell_trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(sell_trades) * 100, 1) if sell_trades else 0,
        },
        "pnl": {
            "total": round(cumulative_pnl, 2),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        },
        "max_drawdown_pct": round(max_drawdown_pct * 100, 2),
        "starting_nlv": nlv,
        "commission_total": round(total_commission, 2),
        "commission_per_roundtrip": round(total_commission / len(sell_trades), 2) if sell_trades else 0,
        "equity_curve": equity_curve[::max(1, len(equity_curve) // 200)],
        "trade_list": trades,
        "detail": trade_summary(trades),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def trade_summary(trades):
    by_sym = {}
    for t in trades:
        d = by_sym.setdefault(t["sym"], {"buys": 0, "sells": 0, "pnl": 0.0})
        if t["action"] == "BUY":
            d["buys"] += 1
        else:
            d["sells"] += 1
            d["pnl"] += t.get("pnl", 0)
    return {s: {"buys": d["buys"], "sells": d["sells"], "pnl": round(d["pnl"], 2)}
            for s, d in by_sym.items()}


def main():
    ap = argparse.ArgumentParser(description="ETF 策略历史回放")
    default_cfg = os.path.expanduser("~/live_ibkr_dashboard/strategy_config.json")
    ap.add_argument("--config", default=default_cfg)
    ap.add_argument("--bars", type=int, default=DEFAULT_BARS, help="回测窗口根数")
    ap.add_argument("--nlv", type=float, default=DEFAULT_NLV,
                    help="起始净值，跑真实账户规模用 --nlv 1010")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)
    result = run_backtest(cfg, args.bars, args.nlv)

    # 结果写到 config 所在目录，看板才读得到（--config 不再是摆设）
    out = cfg_path.parent / "backtest_result.json"
    if "error" not in result:
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\n📁 {out}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
