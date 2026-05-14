#!/usr/bin/env python3
import asyncio; asyncio.set_event_loop(asyncio.new_event_loop())
"""
V1: Forex 日内短线策略 — 复用 ETF 信号框架
MACD+RSI+Bollinger+ADX 信号 → ib_insync 下单 → 经济日历防御
数据源: IBKR reqHistoricalData (零额外成本)
执行: IBKR Paper Trading (4002) → 5天后切 Live (4001)
"""
import asyncio, json, os, sys, time
import numpy as np
from datetime import datetime, timedelta

# ════════════════ 配置 ════════════════
IB_HOST = "127.0.0.1"
IB_PORT = 4002
CLIENT_ID = 20

# 货币对 (显示名 → IBKR 合约名)
PAIRS = {
    "EUR.USD": "EURUSD",
    "GBP.USD": "GBPUSD",
    "USD.JPY": "USDJPY",
}

# 日内信号参数
CANDLES = 100
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_TREND_ENTRY = 50
RSI_TREND_OVERBOUGHT = 75
ADX_TRENDING = 20
MACD_HIST_THRESHOLD = 0.00005

# 风控
RISK_PCT = 0.01
STOP_PIPS = 30
TRAILING_PIPS = 20      # 移动止损：涨了锁利润
COOLDOWN_MINUTES = 15
REENTRY_COOLDOWN = 15

# IBKR
ORDER_TIMEOUT = 15
MAX_RETRIES = 3
INTERVAL = "15min"

TWELVE_DATA_KEY = "a3377a4097ee4b2fba8a646a6dd898ab"

# 输出
DASHBOARD_DIR = os.path.expanduser("~/forex_dashboard")
FAIL_COUNT_FILE = os.path.join(DASHBOARD_DIR, "fail_count.json")
os.makedirs(DASHBOARD_DIR, exist_ok=True)

# Pip 值 (per 100k units in USD)
PIP_VALUES = {"EUR.USD": 10.0, "GBP.USD": 10.0, "USD.JPY": 9.0}
# 1 pip 的价格单位
PIP_SIZES = {"EUR.USD": 0.0001, "GBP.USD": 0.0001, "USD.JPY": 0.01}

# ════════════════ 经济日历 (硬编码本周重大事件) ════════════════
# 格式: (月, 日, 时, 分, 币种, "事件名")
# 手动维护，每周更新
HIGH_IMPACT_EVENTS = [
    # 示例 - 本周实际事件需手动维护
    # (5, 14, 8, 30, "USD", "CPI m/m"),
    # (5, 14, 10, 0, "USD", "Crude Oil Inventories"),
]

def should_pause_for_news():
    """检查当前是否在重大事件前后15分钟"""
    now = datetime.now()
    for (month, day, hour, minute, currency, name) in HIGH_IMPACT_EVENTS:
        event_dt = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        diff = abs((now - event_dt).total_seconds())
        if diff < 900:  # 15分钟
            # 检查是否影响我们的货币对
            affected = {"EUR": ["EUR.USD"], "GBP": ["GBP.USD"], "JPY": ["USD.JPY"], "USD": list(PAIRS.keys())}
            return True, f"{hour:02d}:{minute:02d} {currency} {name} (前后15min暂停)"
    return False, ""

# ════════════════ 仓位计算 ════════════════
def calc_position_size(nlv, pair):
    risk_amount = nlv * RISK_PCT
    pip_value = PIP_VALUES.get(pair, 10.0)
    lots = risk_amount / (STOP_PIPS * pip_value)
    # Convert to integer units (1 lot = 100,000 units)
    units = int(round(lots * 100000, -3))  # round to nearest 1000
    units = max(20000, min(units, 500000))  # 0.2~5 lots, cap for safety
    return units

# ════════════════ 失败计数 ════════════════
def get_fail_count():
    try:
        with open(FAIL_COUNT_FILE) as f:
            return json.load(f).get("count", 0)
    except:
        return 0

def set_fail_count(n):
    with open(FAIL_COUNT_FILE, "w") as f:
        json.dump({"count": n, "time": datetime.now().strftime("%H:%M:%S")}, f)

# ════════════════ 导入策略核心 ════════════════
sys.path.insert(0, os.path.expanduser("~/short-term-trader"))
from strategy_core import (calc_rsi, calc_sma, calc_bbands, calc_adx, calc_macd,
                           check_buy_oversold, check_buy_trend,
                           check_sell_oversold, check_sell_trend, determine_mode)

from ib_insync import IB, Forex, MarketOrder

# ════════════════ 数据获取 ════════════════
async def fetch_forex_data(td_pair):
    """从 Twelve Data 拉 OHLCV"""
    import urllib.request

    url = f"https://api.twelvedata.com/time_series?symbol={td_pair}&interval={INTERVAL}&outputsize={CANDLES}&apikey={TWELVE_DATA_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"API error: {e}")
        return None

    if data.get("status") != "ok" or "values" not in data:
        return None

    values = data["values"]
    if len(values) < 30:
        return None

    # Twelve Data returns newest first, reverse to chronological
    values.reverse()
    closes = np.array([float(v["close"]) for v in values])
    highs = np.array([float(v["high"]) for v in values])
    lows = np.array([float(v["low"]) for v in values])

    rsi = calc_rsi(closes)
    sma = calc_sma(closes)
    upper, middle, lower = calc_bbands(closes)
    adx = calc_adx(highs, lows, closes)
    ml, sl, hist, ph = calc_macd(closes)

    price = closes[-1]

    return {
        "price": price, "rsi": rsi, "sma": sma,
        "bb_upper": upper, "bb_middle": middle, "bb_lower": lower,
        "adx": adx, "macd_ml": ml, "macd_sl": sl,
        "macd_hist": hist, "macd_prev_hist": ph
    }

# ════════════════ 市场时段 ════════════════
def is_forex_market_open():
    """Forex 24/5: 周日 5pm ET → 周五 5pm ET. 0=Mon, 6=Sun"""
    from datetime import timezone
    et = timezone(timedelta(hours=-4))  # EDT
    now_et = datetime.now(et)
    weekday = now_et.weekday()
    hour = now_et.hour + now_et.minute / 60

    if weekday == 4 and hour >= 17:  # Friday after 5pm ET
        return False
    if weekday == 5:  # Saturday
        return False
    if weekday == 6 and hour < 17:  # Sunday before 5pm ET
        return False
    return True
async def place_and_confirm(ib, ibkr_pair, action, quantity):
    contract = Forex(ibkr_pair)
    order = MarketOrder(action, quantity)
    trade = ib.placeOrder(contract, order)

    deadline = time.time() + ORDER_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(1)
        status = trade.orderStatus.status
        if status == "Filled":
            avg_price = trade.orderStatus.avgFillPrice
            print(f"  ✅ {action} {ibkr_pair} ×{quantity} @ {avg_price:.5f}")
            return True, avg_price
        if status in ("Cancelled", "Inactive", "Rejected"):
            print(f"  ❌ {ibkr_pair} 订单失败: {status}")
            return False, None

    print(f"  ⏰ {ibkr_pair} 订单超时 ({ORDER_TIMEOUT}s)")
    ib.cancelOrder(order)
    return False, None

# ════════════════ 主逻辑 ════════════════
async def run():
    now = datetime.now()
    fails = get_fail_count()

    if fails >= MAX_RETRIES:
        print(f"⛔ 已失败 {fails}/{MAX_RETRIES} 次，停止重试。")
        return

    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=10)
        set_fail_count(0)
    except Exception as e:
        fails += 1
        set_fail_count(fails)
        print(f"❌ IB 连接失败 ({fails}/{MAX_RETRIES}): {e}")
        return

    try:
        # ── NLV ──
        nlv = None
        for v in ib.accountValues():
            if v.tag == "NetLiquidationByCurrency" and v.currency == "USD":
                nlv = float(v.value)
        print(f"  账户 NLV=${nlv:,.2f}" if nlv else "  无法获取 NLV")

        # ── 市场时段 ──
        if not is_forex_market_open():
            print("  非交易时段 (周末休市)")
            dashboard = {
                "time": now.strftime("%H:%M:%S"),
                "date": now.strftime("%Y-%m-%d"),
                "status": "closed", "status_text": "🔴 休市",
                "nlv": nlv, "pairs": {}, "positions": {},
                "session_stats": {}, "trade_history": []
            }
            # preserve existing data if possible
            try:
                with open(f"{DASHBOARD_DIR}/data.json") as f:
                    existing = json.load(f)
                dashboard["trade_history"] = existing.get("trade_history", [])
                dashboard["session_stats"] = existing.get("session_stats", {})
                dashboard["pairs"] = existing.get("pairs", {})
                dashboard["positions"] = existing.get("positions", {})
            except:
                pass
            with open(f"{DASHBOARD_DIR}/data.json", "w") as f:
                json.dump(dashboard, f)
            return

        # ── 日历防御 ──
        paused, reason = should_pause_for_news()
        if paused:
            print(f"  ⏸️ {reason}")
            dashboard = {
                "time": now.strftime("%H:%M:%S"),
                "date": now.strftime("%Y-%m-%d"),
                "status": "paused", "status_text": f"⏸️ {reason}",
                "nlv": nlv, "pairs": {}, "positions": {},
                "session_stats": {}, "trade_history": []
            }
            with open(f"{DASHBOARD_DIR}/data.json", "w") as f:
                json.dump(dashboard, f)
            return

        # ── 读上轮数据 ──
        prev_entry_times = {}
        prev_entry_modes = {}
        prev_entry_trails = {}
        prev_last_sells = {}
        prev_last_buys = {}
        trade_history = []
        session_stats = {
            "date": now.strftime("%Y-%m-%d"), "trades": 0,
            "wins": 0, "losses": 0, "pnl": 0.0,
            "symbols_traded": [], "session_start": now.strftime("%H:%M:%S")
        }
        try:
            with open(f"{DASHBOARD_DIR}/data.json") as f:
                prev = json.load(f)
            for sym, p in (prev.get("positions") or {}).items():
                if p and p.get("entry_time"):
                    prev_entry_times[sym] = p["entry_time"]
                if p and p.get("mode"):
                    prev_entry_modes[sym] = p["mode"]
                if p and p.get("trailing_stop"):
                    prev_entry_trails[sym] = p["trailing_stop"]
            for sym, ts in (prev.get("last_sells") or {}).items():
                prev_last_sells[sym] = ts
            for sym, ts in (prev.get("last_buys") or {}).items():
                prev_last_buys[sym] = ts
            trade_history = prev.get("trade_history", [])
            ps = prev.get("session_stats", {})
            if ps.get("date") == now.strftime("%Y-%m-%d"):
                session_stats = ps
        except:
            pass

        # ── 从 IB 重建持仓 ──
        positions = {pair: None for pair in PAIRS}
        for p in ib.positions():
            sym = p.contract.localSymbol
            for display_pair, ibkr_pair in PAIRS.items():
                if ibkr_pair == sym and p.position != 0:
                    entry = {"qty": float(p.position), "avg_cost": float(p.avgCost) if p.avgCost else 0}
                    if display_pair in prev_entry_times:
                        entry["entry_time"] = prev_entry_times[display_pair]
                    if display_pair in prev_entry_modes:
                        entry["mode"] = prev_entry_modes[display_pair]
                    if display_pair in prev_entry_trails:
                        entry["trailing_stop"] = prev_entry_trails[display_pair]
                    positions[display_pair] = entry

        # ── 如果 IB 没显示仓位但上轮有记录，恢复上轮仓位 ──
        for pair in PAIRS:
            if positions[pair] is None and pair in prev_entry_times:
                # Restore from previous data.json
                prev_pos = (prev.get("positions") or {}).get(pair)
                if prev_pos:
                    positions[pair] = prev_pos

        session_trades = []
        pair_data = {}

        # ── 分析每个货币对 ──
        for display_pair, ibkr_pair in PAIRS.items():
            print(f"  ── {display_pair} ── ", end="")
            try:
                d = await fetch_forex_data(display_pair.replace(".", "/"))
                if d is None:
                    print("数据不足")
                    pair_data[display_pair] = {"price": None}
                    # Preserve position from prev data if exists
                    if positions[display_pair] is None and display_pair in prev_entry_times:
                        prev_p = (prev.get("positions") or {}).get(display_pair)
                        if prev_p:
                            positions[display_pair] = prev_p
                    continue
            except Exception as e:
                print(f"获取失败: {e}")
                pair_data[display_pair] = {"price": None}
                if positions[display_pair] is None and display_pair in prev_entry_times:
                    prev_p = (prev.get("positions") or {}).get(display_pair)
                    if prev_p:
                        positions[display_pair] = prev_p
                continue

            price = d["price"]
            rsi = d["rsi"]
            sma = d["sma"]
            upper, lower = d["bb_upper"], d["bb_lower"]
            adx = d["adx"]
            ml, sl, hist, ph = d["macd_ml"], d["macd_sl"], d["macd_hist"], d["macd_prev_hist"]

            pair_data[display_pair] = {
                "price": float(price) if price else None,
                "rsi": round(float(rsi), 1) if rsi else None,
                "sma": round(float(sma), 5) if sma else None,
                "bb_upper": round(float(upper), 5) if upper else None,
                "bb_lower": round(float(lower), 5) if lower else None,
                "adx": round(float(adx), 1) if adx else None,
                "macd_hist": round(float(hist), 6) if hist else None,
                "mode": None, "checks": {}, "score": 0,
                "all_ok": False, "sell_triggers": []
            }

            if None in (rsi, sma, adx, hist, price):
                print("指标不全")
                continue

            pos = positions[display_pair]
            sell_triggers = []

            # ── 无持仓 → 检查买入 ──
            if pos is None:
                mode = determine_mode(rsi, price, sma, RSI_OVERSOLD, RSI_TREND_ENTRY)
                pair_data[display_pair]["mode"] = mode

                if mode == "oversold":
                    # 5个条件: RSI超卖 + 触及下轨 + 趋势向上 + 趋势明确 + MACD转正
                    checks = {
                        "RSI超卖": bool(rsi < RSI_OVERSOLD),
                        "触及下轨": bool(price <= lower * 1.02),
                        "趋势向上": bool(price > sma),
                        "趋势明确": bool(adx > ADX_TRENDING),
                        "MACD转正": bool(hist > MACD_HIST_THRESHOLD and ph < hist),
                    }
                    score = sum(1 for v in checks.values() if v)
                    pair_data[display_pair]["checks"] = checks
                    pair_data[display_pair]["score"] = score
                    all_ok = all(checks.values())
                    pair_data[display_pair]["all_ok"] = all_ok

                    print(f"{price:.5f} RSI={rsi:.1f} 🔻超卖 {score}/5", end="")
                    if display_pair in prev_last_buys:
                        try:
                            last_buy = datetime.fromisoformat(prev_last_buys[display_pair])
                            if (now - last_buy).total_seconds() < COOLDOWN_MINUTES * 60:
                                print(f"\n  ⏳ 买入冷却 {int(COOLDOWN_MINUTES - (now-last_buy).total_seconds()/60)}min")
                                all_ok = False
                        except:
                            pass
                    if all_ok:
                        qty = calc_position_size(nlv, display_pair)
                        print(f"\n  🟢 BUY [{mode}] {display_pair} = {qty} units")
                        filled, fill_price = await place_and_confirm(ib, ibkr_pair, "BUY", qty)
                        if filled:
                            prev_last_buys[display_pair] = now.isoformat()
                            pip_size = PIP_SIZES.get(display_pair, 0.0001)
                            trail = fill_price - STOP_PIPS * pip_size
                            positions[display_pair] = {"qty": qty, "avg_cost": fill_price,
                                                       "entry_time": now.isoformat(), "mode": mode,
                                                       "trailing_stop": trail}
                            session_trades.append({
                                "sym": display_pair, "action": "BUY", "price": fill_price,
                                "qty": qty, "time": now.strftime("%H:%M:%S")
                            })
                    else:
                        print("")

                elif mode == "trend":
                    # 3个条件: RSI>50 + 趋势向上 + MACD金叉
                    checks = {
                        "RSI>50": bool(rsi > RSI_TREND_ENTRY),
                        "趋势向上": bool(price > sma),
                        "MACD金叉": bool(ml > sl and hist > MACD_HIST_THRESHOLD),
                    }
                    score = sum(1 for v in checks.values() if v)
                    all_ok = all(checks.values())
                    pair_data[display_pair]["checks"] = checks
                    pair_data[display_pair]["score"] = score
                    pair_data[display_pair]["all_ok"] = all_ok

                    print(f"{price:.5f} RSI={rsi:.1f} 📈顺势 {score}/3", end="")
                    if display_pair in prev_last_buys:
                        try:
                            last_buy = datetime.fromisoformat(prev_last_buys[display_pair])
                            if (now - last_buy).total_seconds() < COOLDOWN_MINUTES * 60:
                                print(f"\n  ⏳ 买入冷却 {int(COOLDOWN_MINUTES - (now-last_buy).total_seconds()/60)}min")
                                all_ok = False
                        except:
                            pass
                    if all_ok:
                        qty = calc_position_size(nlv, display_pair)
                        print(f"\n  🟢 BUY [{mode}] {display_pair} = {qty} units")
                        filled, fill_price = await place_and_confirm(ib, ibkr_pair, "BUY", qty)
                        if filled:
                            prev_last_buys[display_pair] = now.isoformat()
                            pip_size = PIP_SIZES.get(display_pair, 0.0001)
                            trail = fill_price - STOP_PIPS * pip_size
                            positions[display_pair] = {"qty": qty, "avg_cost": fill_price,
                                                       "entry_time": now.isoformat(), "mode": mode,
                                                       "trailing_stop": trail}
                            session_trades.append({
                                "sym": display_pair, "action": "BUY", "price": fill_price,
                                "qty": qty, "time": now.strftime("%H:%M:%S")
                            })
                    else:
                        print("")
                else:
                    print(f"{price:.5f} RSI={rsi:.1f} — 等待信号")

            # ── 已持仓 → 检查卖出 ──
            else:
                entry_price = pos["avg_cost"]
                entry_mode = pos.get("mode", "trend")
                entry_time_str = pos.get("entry_time")

                pip_size = PIP_SIZES.get(display_pair, 0.0001)
                original_stop = entry_price - STOP_PIPS * pip_size

                # ── 移动止损：涨了就上移 ──
                trailing_stop = pos.get("trailing_stop", original_stop)
                new_trail = price - TRAILING_PIPS * pip_size
                if new_trail > trailing_stop:
                    trailing_stop = new_trail
                    pos["trailing_stop"] = trailing_stop  # persist

                effective_stop = max(original_stop, trailing_stop)

                if entry_mode == "trend":
                    sell_triggered = check_sell_trend(rsi, price, sma, ml, sl, hist, RSI_TREND_OVERBOUGHT)
                else:
                    sell_triggered = check_sell_oversold(rsi, price, upper, ml, sl, hist, RSI_OVERBOUGHT)

                sell_names = []
                if sell_triggered:
                    if rsi > (RSI_TREND_OVERBOUGHT if entry_mode == "trend" else RSI_OVERBOUGHT):
                        sell_names.append("RSI超买")
                    if entry_mode == "trend" and price < sma:
                        sell_names.append("跌破MA20")
                    if sl > ml and hist < 0:
                        sell_names.append("MACD死叉")

                pair_data[display_pair]["sell_triggers"] = sell_names
                pair_data[display_pair]["mode"] = entry_mode

                print(f"{price:.5f} RSI={rsi:.1f} [{entry_mode}]" + (" ⚡卖出!" if sell_names else ""))

                # ── 硬止损 / 移动止损 ──
                if price <= effective_stop:
                    reason = "trailing_stop" if trailing_stop > original_stop else "stop_loss"
                    stop_label = "移动止损" if trailing_stop > original_stop else "硬止损"
                    print(f"  🛑 {stop_label}: {price:.5f} ≤ {effective_stop:.5f} (原始止损 {original_stop:.5f})")
                    filled, _ = await place_and_confirm(ib, ibkr_pair, "SELL", pos["qty"])
                    if filled:
                        prev_last_sells[display_pair] = now.isoformat()
                        pnl_pips = (price - pos["avg_cost"]) / pip_size
                        pnl_usd = pnl_pips * PIP_VALUES.get(display_pair, 10.0) * (pos["qty"] / 100000)
                        session_trades.append({
                            "sym": display_pair, "action": "SELL", "reason": reason,
                            "price": round(price, 5), "qty": pos["qty"],
                            "pnl": round(pnl_usd, 2), "time": now.strftime("%H:%M:%S")
                        })
                        positions[display_pair] = None
                elif sell_names:
                    in_cooldown = False
                    if entry_time_str:
                        try:
                            entry_dt = datetime.fromisoformat(entry_time_str)
                            if (now - entry_dt).total_seconds() < COOLDOWN_MINUTES * 60:
                                in_cooldown = True
                        except:
                            pass

                    if in_cooldown:
                        print(f"  ⏳ 保护期内，跳过: {sell_names}")
                    else:
                        print(f"  🔔 SELL: {sell_names}")
                        filled, _ = await place_and_confirm(ib, ibkr_pair, "SELL", pos["qty"])
                        if filled:
                            prev_last_sells[display_pair] = now.isoformat()
                            pnl_pips = (price - pos["avg_cost"]) / pip_size
                            pnl_usd = pnl_pips * PIP_VALUES.get(display_pair, 10.0) * (pos["qty"] / 100000)
                            session_trades.append({
                                "sym": display_pair, "action": "SELL", "reason": "technical",
                                "price": round(price, 5), "qty": pos["qty"],
                                "pnl": round(pnl_usd, 2), "time": now.strftime("%H:%M:%S")
                            })
                            positions[display_pair] = None
                else:
                    pnl_pips = (price - entry_price) / pip_size
                    pnl_usd = pnl_pips * PIP_VALUES.get(display_pair, 10.0) * (pos["qty"] / 100000)
                    sign = "+" if pnl_usd >= 0 else "-"
                    if trailing_stop > original_stop:
                        trail_pips = (price - trailing_stop) / pip_size
                        print(f"  持有中 ({sign}${abs(pnl_usd):.2f}, 移动止损 ${trailing_stop:.5f} / {trail_pips:.0f}pips)")
                    else:
                        print(f"  持有中 ({sign}${abs(pnl_usd):.2f}, 止损 {STOP_PIPS}pips)")

        # ── 更新 stats ──
        for t in session_trades:
            if t["action"] == "BUY":
                session_stats["trades"] += 1
                if t["sym"] not in session_stats["symbols_traded"]:
                    session_stats["symbols_traded"].append(t["sym"])
            elif t["action"] == "SELL" and "pnl" in t:
                session_stats["pnl"] += t["pnl"]
                if t["pnl"] > 0:
                    session_stats["wins"] += 1
                else:
                    session_stats["losses"] += 1

        # ── 写看板 ──
        last_sells_out = dict(prev_last_sells)
        for pair in PAIRS:
            if positions.get(pair) is None and prev_last_sells.get(pair):
                last_sells_out[pair] = prev_last_sells[pair]

        dashboard = {
            "time": now.strftime("%H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "status": "live",
            "status_text": "🟢 交易中",
            "nlv": nlv,
            "pairs": pair_data,
            "positions": {pair: pos for pair, pos in positions.items()},
            "session_stats": session_stats,
            "trade_history": trade_history + session_trades,
            "last_sells": last_sells_out,
            "last_buys": prev_last_buys
        }

        with open(f"{DASHBOARD_DIR}/data.json", "w") as f:
            json.dump(dashboard, f)

        pos_count = sum(1 for p in positions.values() if p is not None)
        print(f"  看板已更新 | 持仓: {pos_count}/{len(PAIRS)} | 交易: {len(session_trades)}笔")

    finally:
        ib.disconnect()

if __name__ == "__main__":
    print(f"=== Forex V1 | {datetime.now().strftime('%H:%M:%S')} ===")
    asyncio.run(run())
    print("=== Done ===\n")