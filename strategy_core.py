#!/usr/bin/env python3
"""
Shared strategy core — indicator calculation + signal evaluation.
Used by both ibkr_strategy.py (live) and backtest.py (historical).
"""
import numpy as np


# ============ 指标计算 ============
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    diffs = np.diff(closes[-(period + 1):])
    gains = np.where(diffs > 0, diffs, 0)
    losses = np.where(diffs < 0, -diffs, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    return 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

def calc_sma(closes, period=20):
    return float(np.mean(closes[-period:])) if len(closes) >= period else None

def calc_bbands(closes, period=20):
    if len(closes) < period:
        return None, None, None
    mid = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return float(mid + 2 * std), float(mid), float(mid - 2 * std)

def calc_adx(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    n = len(closes)
    tr = np.array([max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])) for i in range(1, n)])
    up = np.array([max(highs[i] - highs[i - 1], 0)
                   if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0 for i in range(1, n)])
    down = np.array([max(lows[i - 1] - lows[i], 0)
                     if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0 for i in range(1, n)])
    atr = np.zeros_like(tr)
    atr[0] = np.mean(tr[:period])
    for i in range(1, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    plus_di = np.zeros_like(atr)
    minus_di = np.zeros_like(atr)
    plus_di[0] = 100 * np.mean(up[:period]) / atr[0] if atr[0] != 0 else 0
    minus_di[0] = 100 * np.mean(down[:period]) / atr[0] if atr[0] != 0 else 0
    for i in range(1, len(tr)):
        plus_di[i] = (plus_di[i - 1] * (period - 1) + 100 * up[i] / atr[i]) / period if atr[i] != 0 else plus_di[i - 1]
        minus_di[i] = (minus_di[i - 1] * (period - 1) + 100 * down[i] / atr[i]) / period if atr[i] != 0 else minus_di[i - 1]
    dx = np.where(plus_di + minus_di > 0, 100 * abs(plus_di - minus_di) / (plus_di + minus_di), 0)
    return float(np.mean(dx[-period:]))

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None, None
    def ema(data, p):
        a = 2 / (p + 1)
        r = np.zeros_like(data)
        r[0] = data[0]
        for i in range(1, len(data)):
            r[i] = a * data[i] + (1 - a) * r[i - 1]
        return r
    m = ema(closes, fast) - ema(closes, slow)
    s = ema(m, signal)
    h = m - s
    return float(m[-1]), float(s[-1]), float(h[-1]), float(h[-2])


# ============ 信号判断 ============
def check_buy_oversold(rsi, price, sma, upper, middle, lower, adx, ml, sl, hist, ph, avg_vol, cur_vol,
                       rsi_oversold, adx_trending):
    checks = {
        "RSI超卖":   (rsi < rsi_oversold, f"RSI={rsi:.1f}"),
        "触及下轨":   (price <= lower * 1.02, f"${price:.2f}"),
        "趋势向上":   (price > sma, f"MA{sma:.2f}"),
        "趋势明确":   (adx > adx_trending, f"ADX={adx:.1f}"),
        "MACD转正":   (hist > 0 and ph < hist, f"{hist:.4f}"),
        "量能确认": (cur_vol > avg_vol, f"Vol={cur_vol:.0f}" if cur_vol and avg_vol else "N/A")
    }
    return all(v[0] for v in checks.values())

def check_sell_oversold(rsi, price, upper, ml, sl, hist, rsi_overbought):
    if rsi > rsi_overbought: return True
    if price >= upper * 0.98: return True
    if sl > ml and hist < 0: return True
    return False

def check_buy_trend(rsi, price, sma, ml, sl, hist, avg_vol, cur_vol, rsi_trend_entry):
    macd_golden = ml > sl and hist > 0
    checks = [
        rsi > rsi_trend_entry,
        price > sma,
        macd_golden,
        cur_vol > avg_vol
    ]
    return all(checks)

def check_sell_trend(rsi, price, sma, ml, sl, hist, rsi_trend_overbought):
    if rsi > rsi_trend_overbought: return True
    if sl > ml and hist < 0: return True
    if price < sma: return True
    return False

def in_reentry_cooldown(last_sell_bar, current_bar, cooldown_minutes):
    """Check if we're still in re-entry cooldown after a sell. Bars are 5-min."""
    if last_sell_bar is None:
        return False
    cooldown_bars = cooldown_minutes // 5
    return (current_bar - last_sell_bar) < cooldown_bars

def determine_mode(rsi, price, sma, rsi_oversold, rsi_trend_entry):
    if rsi < rsi_oversold:
        return "oversold"
    if rsi > rsi_trend_entry and price > sma:
        return "trend"
    return None
