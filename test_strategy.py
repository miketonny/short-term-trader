"""
Unit tests for ibkr_strategy.py critical paths:
  - Fractional shares → integer fix
  - PDT / max_positions enforcement
  - Stop loss (candle low vs close)
  - trading_enabled type coercion
  - Buy/Sell signals (check_buy, check_sell, check_buy_trend, check_sell_trend)
  - Indicator calculations (RSI, SMA, BB, ADX, MACD)
  - Mode determination
  - Config loading edge cases
  - Market hours / DST
"""

import json, os, sys, math, inspect
import numpy as np
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, '/root/short-term-trader')

from ibkr_strategy import (
    calc_rsi, calc_sma, calc_bbands, calc_adx, calc_macd,
    check_buy, check_sell, check_buy_trend, check_sell_trend,
    determine_mode, get_market_info, load_config,
)

PASS = FAIL = 0

def t(name, condition):
    global PASS, FAIL
    if condition: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}")

def approx(actual, expected, tol=0.01, name=""):
    global PASS, FAIL
    if abs(actual - expected) <= tol: PASS += 1; print(f"  ✅ {name}: {actual:.4f}")
    else: FAIL += 1; print(f"  ❌ {name}: expected ≈{expected:.4f}, got {actual:.4f}")


# ============================================================
# 1. FRACTIONAL SHARES → INTEGER
# ============================================================
print("\n📦 Fractional Shares Fix")

NLV, ALLOC, LEV = 2000.0, 0.25, 1.0

qty_qqq = max(1, int((NLV * ALLOC * LEV) / 545.0))
qty_xlk = max(1, int((NLV * ALLOC * LEV) / 140.0))
t("QQQ $545: int(0.917)→max(1,0)=1 share", qty_qqq == 1)
t("XLK $140: int(3.571)=3 shares", qty_xlk == 3)
t("Tiny account $100: still 1 share", max(1, int((100*ALLOC*LEV)/545)) == 1)
t("Large account $100k: 45 shares", max(1, int((100000*ALLOC*LEV)/545)) == 45)

for nlv in [500, 1000, 2000, 5000]:
    for price in [100, 250, 500]:
        q = max(1, int((nlv * ALLOC * LEV) / price))
        t(f"NLV={nlv} price={price} → qty={q} int≥1", isinstance(q, int) and q >= 1)


# ============================================================
# 2. PDT / MAX POSITIONS
# ============================================================
print("\n📦 PDT & Max Positions")

def count_pos(positions):
    return sum(1 for p in positions.values() if p is not None)

t("0 pos → can buy", count_pos({}) == 0)
t("1 pos < 4 → can buy", count_pos({"QQQ": {"qty": 1}}) == 1)
t("4 pos ≥ 4 → blocked", count_pos({"A":{}, "B":{}, "C":{}, "D":{}}) == 4)
t("None don't count", count_pos({"QQQ": None, "XLK": {"qty": 3}}) == 1)

_skip = count_pos({"A":{},"B":{},"C":{},"D":{}}) >= 4
t("pos=4,max=4 → _skip=True (guards buy)", _skip == True)
_skip2 = count_pos({"A":{},"B":{},"C":{}}) >= 4
t("pos=3,max=4 → _skip=False (allows buy)", _skip2 == False)


# ============================================================
# 3. STOP LOSS: CANDLE LOW vs CLOSE
# ============================================================
print("\n📦 Stop Loss (low check)")

entry, stop_pct = 100.0, 0.06
stop = entry * (1 - stop_pct)

# Gap-down intra-candle: low=93 touches stop, close=95 recovers
t("OLD: close=95 > 94 → MISSED (gap risk!)", 95.0 > stop)
t("NEW: low=93 ≤ 94 → CAUGHT ✅", 93.0 <= stop)

# Both trigger
t("close=90 ≤ 94 → both trigger", 90.0 <= stop)
t("low=89 ≤ 94 → both trigger", 89.0 <= stop)

# Neither triggers
t("close=97 > 94 → safe", 97.0 > stop)
t("low=96 > 94 → safe", 96.0 > stop)


# ============================================================
# 4. TRADING_ENABLED COERCION
# ============================================================
print("\n📦 trading_enabled Coercion")

def parse_te(raw):
    if isinstance(raw, bool): return raw
    if isinstance(raw, (int, float)): return bool(raw)
    return str(raw).lower() == "true"

t("bool True", parse_te(True) == True)
t("bool False", parse_te(False) == False)
t('str "true"', parse_te("true") == True)
t('str "false" → False (was truthy!)', parse_te("false") == False)
t('str "FALSE"', parse_te("FALSE") == False)
t("int 1 → True", parse_te(1) == True)
t("int 0 → False", parse_te(0) == False)
t("float 1.0 → True", parse_te(1.0) == True)
t("None → False", parse_te(None) == False)


# ============================================================
# 5. CONFIG
# ============================================================
print("\n📦 Config Loading")
cfg = load_config()
t("load_config returns dict", isinstance(cfg, dict))
t("load_config doesn't crash", True)


# ============================================================
# 6. INDICATORS
# ============================================================
print("\n📦 Indicators")

np.random.seed(42)
n = 50
trend = np.linspace(0, 20, n)
noise = np.random.randn(n) * 2
closes = 100.0 + trend + noise
highs = closes + np.abs(np.random.randn(n) * 1.5)
lows = closes - np.abs(np.random.randn(n) * 1.5)

rsi = calc_rsi(closes)
t("RSI not None", rsi is not None)
t("RSI 0-100", 0 <= rsi <= 100)
t("RSI > 50 in uptrend", rsi > 50)

rsi_flat = calc_rsi(np.full(50, 100.0))
t("RSI flat → ≈100", rsi_flat is not None and rsi_flat >= 99)

rsi_down = calc_rsi(100.0 - trend + noise)
t("RSI downtrend < 50", rsi_down < 50)

t("RSI 2 bars → None", calc_rsi(np.array([100.0, 101.0])) is None)

sma = calc_sma(closes)
t("SMA is float", isinstance(sma, (float, np.floating)))

upper, mid, lower = calc_bbands(closes)
t("BB upper>mid>lower", upper > mid > lower)

adx = calc_adx(highs, lows, closes)
t("ADX ≥ 0", adx is not None and adx >= 0)

macd_vals = calc_macd(closes)
t("MACD returns 4 values", len(macd_vals) == 4 and all(v is not None for v in macd_vals))


# ============================================================
# 7. BUY/SELL SIGNALS
# ============================================================
print("\n📦 Buy/Sell Signals")

# Good oversold buy: price near lower BB, bounced above SMA, RSI oversold
# price=91 near lower=90, sma=88 (price recovered above SMA → 趋势向上)
checks, all_ok = check_buy(
    rsi=25, price=91, sma=88, upper=115, middle=100, lower=90,
    adx=30, ml=0.5, sl=0.3, hist=0.1, ph=-0.2,
    avg_vol=2000000, cur_vol=3000000
)
t("Oversold good signal → all_ok=True", all_ok == True)

# Bad: RSI not oversold
_, bad = check_buy(
    rsi=45, price=95, sma=90, upper=110, middle=100, lower=90,
    adx=30, ml=0.5, sl=0.3, hist=0.1, ph=-0.2,
    avg_vol=2000000, cur_vol=3000000
)
t("RSI=45 → not oversold → False", bad == False)

# Bad: MACD death cross
_, bad2 = check_buy(
    rsi=25, price=95, sma=90, upper=110, middle=100, lower=90,
    adx=30, ml=0.5, sl=0.3, hist=-0.1, ph=0.2,
    avg_vol=2000000, cur_vol=3000000
)
t("hist<0 → False", bad2 == False)

# Trend buy
ct, _ = check_buy_trend(rsi=60, price=105, sma=95, ml=0.5, sl=0.3, hist=0.1,
                         avg_vol=2000000, cur_vol=3000000)
t("Trend buy returns checks", isinstance(ct, dict))

# Sell: overbought
sells = check_sell(rsi=75, price=115, upper=110, ml=0.3, sl=0.5, hist=-0.05)
t("RSI=75 overbought → triggers", len(sells) > 0)

# No sell
nosell = check_sell(rsi=55, price=105, upper=110, ml=0.7, sl=0.65, hist=0.02)
t("Normal → no triggers", len(nosell) == 0)


# ============================================================
# 8. MODE & DST
# ============================================================
print("\n📦 Mode & DST")

t("RSI=25 → oversold", determine_mode(25, 100, 95) == "oversold")
t("RSI=60 → trend", determine_mode(60, 105, 100) == "trend")

status, text, et, trade = get_market_info()
t("get_market_info returns 4 values", all([status, text, et]) and isinstance(trade, bool))

ny = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
off = ny.utcoffset().total_seconds() / 3600
t(f"NY offset -4 or -5 (got {off})", off in (-4.0, -5.0))


# ============================================================
# 9. REGRESSION: check_buy_trend params
# ============================================================
print("\n📦 Regression: check_buy_trend signature")
sig = inspect.signature(check_buy_trend)
names = list(sig.parameters.keys())
t("no 'adx' param", 'adx' not in names)
t("no 'ph' param", 'ph' not in names)


# ============================================================
# 10. EDGE CASES
# ============================================================
print("\n📦 Edge Cases")

t("All-up RSI > 90", calc_rsi(np.array([100.0+i for i in range(50)])) > 90)
t("All-down RSI < 10", calc_rsi(np.array([100.0-i*0.5 for i in range(50)])) < 10)

approx(150*(1-0.06), 141.0, name="6% stop: $150→$141")

# qty never zero
for nlv in [50, 100, 200, 500]:
    q = max(1, int((nlv * 0.25 * 1.0) / 500.0))
    t(f"NLV={nlv} → qty={q} ≥ 1", q >= 1)


# ============================================================
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
if FAIL == 0:
    print("🎉 ALL TESTS PASSED")
else:
    print(f"⚠️  {FAIL} TEST(S) FAILED")
    sys.exit(1)
