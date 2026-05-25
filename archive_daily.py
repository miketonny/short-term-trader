#!/usr/bin/env python3
"""每日归档：保存昨天的 session_stats 到 daily_summary.json，防止 Gateway 崩溃后战报丢失"""
import json, os
from datetime import datetime

ARCHIVE_FILE = "/root/ibkr_dashboard/daily_summary.json"
FOREX_DATA = "/root/forex_dashboard/data.json"
ETF_DATA = "/root/ibkr_dashboard/data.json"
FOREX_ARCHIVE = "/root/forex_dashboard/daily_summary.json"

def archive_one(data_path, archive_path, label):
    if not os.path.exists(data_path):
        return
    try:
        with open(data_path) as f:
            data = json.load(f)
    except:
        return
    
    # 读取 session_stats（字段名因策略而异）
    # forex: data["session_stats"]   ETF: data.get("session_stats")
    stats = data.get("session_stats") if isinstance(data, dict) else None
    if not stats or not stats.get("date"):
        return
    
    stat_date = stats["date"]
    today = datetime.now().strftime("%Y-%m-%d")
    if stat_date == today:
        return  # 今天的数据不归档
    
    # 读现有归档
    archive = {}
    if os.path.exists(archive_path):
        try:
            with open(archive_path) as f:
                archive = json.load(f)
        except:
            archive = {}
    
    if stat_date in archive:
        return  # 已归档
    
    archive[stat_date] = {
        "label": label,
        "trades": stats.get("trades", 0),
        "wins": stats.get("wins", 0),
        "losses": stats.get("losses", 0),
        "pnl": stats.get("pnl", 0.0),
        "symbols_traded": stats.get("symbols_traded", []),
        "session_start": stats.get("session_start", "?"),
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    with open(archive_path, "w") as f:
        json.dump(archive, f, indent=2)
    print(f"[{label}] 归档 {stat_date}: {stats.get('trades',0)}笔 PnL=${stats.get('pnl',0):.2f}")

if __name__ == "__main__":
    archive_one(FOREX_DATA, FOREX_ARCHIVE, "Forex")
    archive_one(ETF_DATA, ARCHIVE_FILE, "ETF")
