#!/usr/bin/env python3
"""
V1: 多品种并行 ETF 短线策略 + 看板数据输出
每个 ETF 独立追踪持仓、独立买卖、独立确认成交。
Twelve Data OHLCV → numpy 本地计算 6 指标 → ib_insync 下单 → 订单确认。
每轮输出 ~/ibkr_dashboard/data.json
"""
import requests, asyncio, json, os, time
import numpy as np
from datetime import datetime, timezone, timedelta
from ib_insync import IB, Stock, MarketOrder

# ============ 配置 ============
TWELVE_DATA_KEY = "a3377a4097ee4b2fba8a646a6dd898ab"
IB_HOST = "127.0.0.1"
IB_PORT = 4002
CLIENT_ID = 10

SYMBOLS = ["SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "XLV"]
INTERVAL = "5min"
CANDLES = 50
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_TREND_OVERBOUGHT = 75
ADX_TRENDING = 20
POSITION_ALLOC = 0.10
ORDER_TIMEOUT = 10
STOP_LOSS_PCT = 0.02
COOLDOWN_MINUTES = 15

DASHBOARD_DIR = os.path.expanduser("~/ibkr_dashboard")
FAIL_COUNT_FILE = os.path.expanduser("~/ibkr_dashboard/fail_count.json")
MAX_RETRIES = 3
os.makedirs(DASHBOARD_DIR, exist_ok=True)

# ── 读取策略配置（Dashboard 可编辑）──
CONFIG_FILE = os.path.expanduser("~/ibkr_dashboard/strategy_config.json")
def load_config():
    """从 strategy_config.json 加载参数，覆盖默认值"""
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return cfg
    except:
        return {}

_cfg = load_config()
SYMBOLS = _cfg.get("symbols", SYMBOLS)
RSI_OVERSOLD = _cfg.get("rsi_oversold", RSI_OVERSOLD)
RSI_OVERBOUGHT = _cfg.get("rsi_overbought", RSI_OVERBOUGHT)
RSI_TREND_OVERBOUGHT = _cfg.get("rsi_trend_overbought", RSI_TREND_OVERBOUGHT)
RSI_TREND_ENTRY = _cfg.get("rsi_trend_entry", 50)
ADX_TRENDING = _cfg.get("adx_trending", ADX_TRENDING)
POSITION_ALLOC = _cfg.get("position_alloc", POSITION_ALLOC)
ORDER_TIMEOUT = _cfg.get("order_timeout", ORDER_TIMEOUT)
STOP_LOSS_PCT = _cfg.get("stop_loss_pct", STOP_LOSS_PCT)
COOLDOWN_MINUTES = _cfg.get("cooldown_minutes", COOLDOWN_MINUTES)
MAX_RETRIES = _cfg.get("max_retries", MAX_RETRIES)

# ============ 市场时间 ============
def get_market_info():
    """返回 (status_code, status_text, et_time_str, should_trade)
    should_trade=True 时表示应该在盘中进行策略计算和交易"""
    utc_now = datetime.now(timezone.utc)
    et_now = utc_now - timedelta(hours=4)  # EDT
    is_weekday = et_now.weekday() < 5
    et_time = et_now.strftime("%H:%M ET")

    if not is_weekday:
        return "weekend", "⚫ 周末休市", et_time, False
    market_open = (et_now.hour > 9 or (et_now.hour == 9 and et_now.minute >= 30))
    market_closed = et_now.hour >= 16
    premarket = is_weekday and not market_open and not market_closed

    if market_closed:
        return "closed", "🔴 已收盘", et_time, False
    elif premarket:
        return "premarket", "🟡 盘前", et_time, False   # 盘前不交易，只显示信息
    elif market_open:
        return "open", "🟢 盘中", et_time, True

    return "unknown", "未知", et_time, False

# ============ 新闻 ============
def fetch_news():
    try:
        import xml.etree.ElementTree as ET
        resp = requests.get(
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY,QQQ&region=US&lang=en-US",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        root = ET.fromstring(resp.text)
        items = []
        for item in root.findall(".//item")[:6]:
            items.append({
                "title": item.find("title").text if item.find("title") is not None else "",
                "source": "Yahoo Finance",
                "url": item.find("link").text if item.find("link") is not None else "",
                "time": item.find("pubDate").text[:22] if item.find("pubDate") is not None else ""
            })
        return items
    except Exception as e:
        print(f"  新闻获取失败: {e}")
    return []

# ============ Twelve Data API ============
def fetch_candles(symbol):
    resp = requests.get("https://api.twelvedata.com/time_series", params={
        "symbol": symbol, "interval": INTERVAL, "outputsize": CANDLES,
        "apikey": TWELVE_DATA_KEY
    }, timeout=10)
    data = resp.json()
    if "values" not in data:
        return None, None
    closes = np.array([float(v["close"]) for v in reversed(data["values"])])
    highs  = np.array([float(v["high"]) for v in reversed(data["values"])])
    lows   = np.array([float(v["low"]) for v in reversed(data["values"])])
    volumes = np.array([float(v["volume"]) for v in reversed(data["values"])])
    return {"close": closes, "high": highs, "low": lows, "volume": volumes}, closes[-1]

def get_price(symbol):
    resp = requests.get("https://api.twelvedata.com/price", params={
        "symbol": symbol, "apikey": TWELVE_DATA_KEY
    }, timeout=10)
    data = resp.json()
    return float(data["price"]) if "price" in data else None

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
def check_buy(rsi, price, sma, upper, middle, lower, adx, ml, sl, hist, ph, avg_vol, cur_vol):
    checks = {
        "RSI超卖":   (rsi < RSI_OVERSOLD, f"RSI={rsi:.1f}"),
        "触及下轨":   (price <= lower * 1.02, f"${price:.2f}"),
        "趋势向上":   (price > sma, f"MA{sma:.2f}"),
        "趋势明确":   (adx > ADX_TRENDING, f"ADX={adx:.1f}"),
        "MACD转正":   (hist > 0 and ph < hist, f"{hist:.4f}"),
        "量能确认": (cur_vol > avg_vol, f"Vol={cur_vol:.0f}" if cur_vol and avg_vol else "N/A")
    }
    return {k: bool(v[0]) for k, v in checks.items()}, all(v[0] for v in checks.values())

def check_sell(rsi, price, upper, ml, sl, hist):
    """超卖模式卖出触发条件"""
    triggers = []
    if rsi > RSI_OVERBOUGHT:
        triggers.append("RSI超买")
    if price >= upper * 0.98:
        triggers.append("触上轨")
    if sl > ml and hist < 0:
        triggers.append("MACD死叉")
    return triggers

def check_buy_trend(rsi, price, sma, adx, ml, sl, hist, ph, avg_vol, cur_vol):
    """顺势追涨模式买入条件（4条，全部满足才买入）"""
    macd_golden = ml > sl and hist > 0  # MACD 金叉
    checks = {
        "RSI>50":       (rsi > RSI_TREND_ENTRY, f"{rsi:.1f}"),
        "趋势向上":       (price > sma, f"MA{sma:.2f}"),
        "MACD金叉":      (macd_golden, f"ML={ml:.4f}"),
        "量能确认":      (cur_vol > avg_vol, f"Vol={cur_vol:.0f}" if cur_vol and avg_vol else "N/A")
    }
    return {k: bool(v[0]) for k, v in checks.items()}, all(v[0] for v in checks.values())

def check_sell_trend(rsi, price, sma, ml, sl, hist):
    """顺势模式卖出触发条件"""
    triggers = []
    if rsi > RSI_TREND_OVERBOUGHT:
        triggers.append(f"RSI>{RSI_TREND_OVERBOUGHT}")
    if sl > ml and hist < 0:
        triggers.append("MACD死叉")
    if price < sma:
        triggers.append("跌破MA20")
    return triggers

def determine_mode(rsi, price, sma):
    """判断当前应该用什么模式：'oversold' / 'trend' / None"""
    if rsi < RSI_OVERSOLD:
        return "oversold"
    if rsi > RSI_TREND_ENTRY and price > sma:
        return "trend"
    return None

# ============ 失败计数 ============
def get_fail_count():
    try:
        with open(FAIL_COUNT_FILE) as f:
            return json.load(f).get("count", 0)
    except:
        return 0

def set_fail_count(n):
    with open(FAIL_COUNT_FILE, "w") as f:
        json.dump({"count": n, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f)

# ============ 交易执行 ============
async def place_and_confirm(ib, sym, action, quantity):
    """
    下单并等待成交。返回 (filled: bool, fill_price: float|None)
    """
    contract = Stock(sym, "SMART", "USD")
    await ib.qualifyContractsAsync(contract)
    order = MarketOrder(action, quantity)
    trade = await ib.placeOrderAsync(contract, order)

    # 等待成交
    deadline = time.time() + ORDER_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(1)
        status = trade.orderStatus.status
        if status == "Filled":
            avg_price = trade.orderStatus.avgFillPrice
            print(f"  ✅ {action} {sym} ×{quantity:.3f} @ ${avg_price:.2f}")
            return True, avg_price
        if status in ("Cancelled", "Inactive", "Rejected"):
            print(f"  ❌ {sym} 订单失败: {status}")
            return False, None

    print(f"  ⏰ {sym} 订单超时 ({ORDER_TIMEOUT}s), 当前状态: {trade.orderStatus.status}")
    await ib.cancelOrderAsync(order)
    return False, None

# ============ 主逻辑 ============
async def run():
    now = datetime.now()
    fails = get_fail_count()

    # ── 失败熔断 ──
    if fails >= MAX_RETRIES:
        print(f"⛔ 已连续失败 {fails}/{MAX_RETRIES} 次，停止自动重试。")
        dashboard = {
            "time": now.strftime("%H:%M:%S"), "date": now.strftime("%Y-%m-%d"),
            "market_status": "blocked", "market_text": "⛔ 已暂停",
            "market_et": f"连续{fails}次失败", "news": [], "symbols": {},
            "positions": {}, "account": None
        }
        with open(f"{DASHBOARD_DIR}/data.json", "w") as f:
            json.dump(dashboard, f)
        return

    # ── 连接 IB ──
    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=10)
        if fails > 0:
            set_fail_count(0)
            print(f"✅ 连接恢复 (之前失败 {fails} 次，已重置)")
    except Exception as e:
        new_fails = fails + 1
        set_fail_count(new_fails)
        print(f"❌ IB 连接失败 ({new_fails}/{MAX_RETRIES}): {e}")
        if new_fails >= MAX_RETRIES:
            print(f"⛔ 达到最大重试次数，暂停自动交易。")
        return

    # ── 获取账户净值和当前持仓 ──
    await asyncio.sleep(2)  # 等 IB 推送账户数据
    nlv = None
    buying_power = None
    for v in ib.accountValues():
        if v.tag == "NetLiquidationByCurrency" and v.currency == "USD":
            nlv = float(v.value)
        if v.tag == "BuyingPower" and v.currency == "USD":
            buying_power = float(v.value)

    print(f"  账户 NLV=${nlv:,.2f}" if nlv else "  无法获取 NLV")

    # 从 IB 重建每个 ETF 的持仓状态，并从上轮 data.json 继承 entry_time + entry_mode
    prev_entry_times = {}
    prev_entry_modes = {}
    try:
        with open(f"{DASHBOARD_DIR}/data.json") as f:
            prev = json.load(f)
            for sym, p in (prev.get("positions") or {}).items():
                if p and p.get("entry_time"):
                    prev_entry_times[sym] = p["entry_time"]
                if p and p.get("mode"):
                    prev_entry_modes[sym] = p["mode"]
    except:
        pass

    positions = {sym: None for sym in SYMBOLS}
    for p in ib.positions():
        if p.contract.symbol in SYMBOLS and p.position > 0:
            entry = {
                "qty": float(p.position),
                "avg_cost": float(p.avgCost) if p.avgCost else 0
            }
            if p.contract.symbol in prev_entry_times:
                entry["entry_time"] = prev_entry_times[p.contract.symbol]
            if p.contract.symbol in prev_entry_modes:
                entry["mode"] = prev_entry_modes[p.contract.symbol]
            positions[p.contract.symbol] = entry

    # ── 市场状态 ──
    status_code, status_text, et_time, should_trade = get_market_info()
    news = fetch_news()

    dashboard = {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "market_status": status_code,
        "market_text": status_text,
        "market_et": et_time,
        "account": {"nlv": nlv, "buying_power": buying_power},
        "positions": {sym: pos for sym, pos in positions.items()},
        "news": news,
        "symbols": {},
        "symbols_time": None
    }

    # ── 非交易时段：只更新时间和市场状态，保留盘中最后一轮信号数据 ──
    if not should_trade:
        # 读取现有 data.json，保留 symbols + positions + news
        try:
            with open(f"{DASHBOARD_DIR}/data.json") as f:
                existing = json.load(f)
            dashboard["symbols"] = existing.get("symbols", {})
            dashboard["news"] = existing.get("news", news)  # 优先用旧新闻
            dashboard["symbols_time"] = existing.get("symbols_time")  # 保留盘中快照时间
            # 保留盘中持仓（不从 IB 覆盖）
            if existing.get("market_status") in ("open",):
                dashboard["positions"] = existing.get("positions", dashboard["positions"])
        except:
            pass
        with open(f"{DASHBOARD_DIR}/data.json", "w") as f:
            json.dump(dashboard, f)
        ib.disconnect()
        print(f"  非交易时段 ({status_text}) — 保留盘中数据")
        return

    # ── 每个 ETF 独立分析 + 交易 ──
    try:
        for sym in SYMBOLS:
            print(f"  ── {sym} ── ", end="")
            candles, _ = fetch_candles(sym)
            if candles is None:
                print("数据获取失败")
                continue

            # 用 K线收盘价替代 get_price 调用（省 API 额度）
            price = float(candles["close"][-1])

            c, h, l = candles["close"], candles["high"], candles["low"]
            avg_vol = float(np.mean(candles["volume"][-20:]))
            cur_vol = float(candles["volume"][-1])

            rsi = calc_rsi(c)
            sma = calc_sma(c)
            upper, mid, lower = calc_bbands(c)
            adx = calc_adx(h, l, c)
            ml, sl, hist, ph = calc_macd(c)

            if None in (rsi, sma, upper, adx, ml):
                print("指标计算不足")
                continue

            pos = positions.get(sym)
            entry_mode = pos.get("mode") if pos else None
            score = 0
            checks = {}
            all_ok = False
            sell_triggers = []

            # ── 无持仓：判断应该用哪种模式 ──
            if pos is None:
                mode = determine_mode(rsi, price, sma)

                if mode == "oversold":
                    checks, all_ok = check_buy(rsi, price, sma, upper, mid, lower, adx, ml, sl, hist, ph, avg_vol, cur_vol)
                    score = sum(1 for v in checks.values() if v)
                    print(f"${price:.2f} RSI={rsi:.1f} 🔻超卖 {score}/6", end="")
                    if all_ok:
                        if nlv and nlv > 0:
                            qty = (nlv * POSITION_ALLOC) / price
                            print(f"\n  🟢 BUY [{mode}] {sym} = {qty:.3f}股")
                            filled, fill_price = await place_and_confirm(ib, sym, "BUY", qty)
                            if filled:
                                positions[sym] = {"qty": qty, "avg_cost": fill_price, "entry_time": now.isoformat(), "mode": mode}
                        else:
                            print("  (NLV不可用)")
                    else:
                        print("")

                elif mode == "trend":
                    checks, all_ok = check_buy_trend(rsi, price, sma, adx, ml, sl, hist, ph, avg_vol, cur_vol)
                    score = sum(1 for v in checks.values() if v)
                    print(f"${price:.2f} RSI={rsi:.1f} 📈顺势 {score}/4", end="")
                    if all_ok:
                        if nlv and nlv > 0:
                            qty = (nlv * POSITION_ALLOC) / price
                            print(f"\n  🟢 BUY [{mode}] {sym} = {qty:.3f}股")
                            filled, fill_price = await place_and_confirm(ib, sym, "BUY", qty)
                            if filled:
                                positions[sym] = {"qty": qty, "avg_cost": fill_price, "entry_time": now.isoformat(), "mode": mode}
                        else:
                            print("  (NLV不可用)")
                    else:
                        print("")
                else:
                    print(f"${price:.2f} RSI={rsi:.1f} — 等待信号")

            # ── 已持仓：检查卖出信号（按入场模式）──
            else:
                entry_price = pos["avg_cost"]
                entry_time_str = pos.get("entry_time")
                stop_price = entry_price * (1 - STOP_LOSS_PCT)

                # 根据入场模式选择卖出检查函数
                if entry_mode == "trend":
                    sell_triggers = check_sell_trend(rsi, price, sma, ml, sl, hist)
                else:
                    sell_triggers = check_sell(rsi, price, upper, ml, sl, hist)

                print(f"${price:.2f} RSI={rsi:.1f} [{entry_mode or '?'}]" + (" ⚡卖出!" if sell_triggers else ""))

                # ── 硬止损（无条件）──
                if price <= stop_price:
                    print(f"  🛑 STOP LOSS {sym}: ${price:.2f} ≤ ${stop_price:.2f} (-{STOP_LOSS_PCT*100:.0f}%)")
                    filled, _ = await place_and_confirm(ib, sym, "SELL", pos["qty"])
                    if filled:
                        positions[sym] = None
                        sell_triggers = ["硬止损"]
                else:
                    # ── 技术卖出信号（受保护期限制）──
                    in_cooldown = False
                    if entry_time_str:
                        try:
                            entry_dt = datetime.fromisoformat(entry_time_str)
                            elapsed = (now - entry_dt).total_seconds()
                            in_cooldown = elapsed < COOLDOWN_MINUTES * 60
                        except:
                            pass

                    if sell_triggers:
                        if in_cooldown:
                            remain_sec = COOLDOWN_MINUTES * 60 - elapsed
                            print(f"  ⏳ {sym} 保护期内 (还需 {remain_sec:.0f}s)，跳过: {sell_triggers}")
                            sell_triggers = []
                        else:
                            print(f"  🔔 SELL {sym}: {sell_triggers}")
                            filled, _ = await place_and_confirm(ib, sym, "SELL", pos["qty"])
                            if filled:
                                positions[sym] = None
                    elif not in_cooldown:
                        print(f"  持有中 (成本 ${entry_price:.2f}, 止损 ${stop_price:.2f})")
                    else:
                        remain_sec = COOLDOWN_MINUTES * 60 - elapsed
                        print(f"  持有中 (保护期还剩 {remain_sec:.0f}s, 止损 ${stop_price:.2f})")

            dashboard["symbols"][sym] = {
                "price": price, "rsi": round(rsi, 1), "sma": round(sma, 2),
                "bb_upper": round(upper, 2), "bb_lower": round(lower, 2),
                "adx": round(adx, 1), "macd_hist": round(hist, 4),
                "mode": entry_mode or determine_mode(rsi, price, sma),
                "checks": checks, "score": score, "all_ok": all_ok,
                "sell_triggers": sell_triggers
            }

    finally:
        ib.disconnect()
        # 写回最新的持仓状态
        dashboard["positions"] = {sym: pos for sym, pos in positions.items()}
        dashboard["symbols_time"] = now.strftime("%H:%M:%S")  # 记录盘中快照时间

    with open(f"{DASHBOARD_DIR}/data.json", "w") as f:
        json.dump(dashboard, f)
    pos_count = sum(1 for p in positions.values() if p is not None)
    print(f"  看板已更新 | 市场: {status_text} | 持仓: {pos_count}/{len(SYMBOLS)} | 新闻: {len(news)}条")

if __name__ == "__main__":
    print(f"=== Multi-ETF V1 | {datetime.now().strftime('%H:%M:%S')} ===")
    asyncio.run(run())
    print("=== Done ===\n")
