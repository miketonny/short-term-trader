#!/usr/bin/env python3
"""
Webhook 通知 — 通用事件通知通道（开仓/平仓/止损/报错）。
以后接微信/Telegram/Discord 只需改 URL。

环境变量 NOTIFY_WEBHOOK_URL 设置 webhook 地址。
发送 JSON: {"event": "...", "data": {...}, "time": "..."}

用法:
    from notifier import notify
    notify("trade_open", {"symbol": "SPY", "action": "BUY", "price": 720.0})
"""
import json, os, urllib.request
from datetime import datetime


WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "")
ENABLED = bool(WEBHOOK_URL)


def notify(event, data=None, timeout=5):
    """发送 webhook 通知（同步，非阻塞）。失败静默忽略。"""
    if not ENABLED:
        return

    payload = {
        "event": event,
        "data": data or {},
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=timeout)
    except Exception:
        pass  # 通知失败不影响交易主流程


# ── 便捷函数 ──
def notify_trade(symbol, action, price, qty=None, reason=None):
    notify("trade", {
        "symbol": symbol,
        "action": action,
        "price": price,
        "qty": qty,
        "reason": reason
    })

def notify_error(source, message):
    notify("error", {"source": source, "message": str(message)[:500]})

def notify_stop_loss(symbol, price, stop_price, mode="hard"):
    notify("stop_loss", {
        "symbol": symbol,
        "price": price,
        "stop_price": stop_price,
        "mode": mode
    })
