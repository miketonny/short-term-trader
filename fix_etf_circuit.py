python3 << 'PYEOF'
import json

path = "/root/short-term-trader/ibkr_strategy.py"
with open(path) as f:
    content = f.read()

old_block = '''    # ── 熔断检查 ──
    if not _circuit.available():
        remaining = int(_circuit.remaining_cooldown)
        print(f"⛔ 熔断中（剩余 {remaining}s），跳过本轮。最后错误: {_circuit.last_error}")
        dashboard = {
            "time": now.strftime("%H:%M:%S"), "date": now.strftime("%Y-%m-%d"),
            "market_status": "blocked", "market_text": f"⛔ 已熔断 ({remaining}s剩余)",
            "market_et": _circuit.last_error or "", "news": [], "symbols": {},
            "positions": {}, "account": None
        }'''

new_block = '''    # ── 熔断检查 ──
    if not _circuit.available():
        remaining = int(_circuit.remaining_cooldown)
        print(f"⛔ 熔断中（剩余 {remaining}s），跳过本轮。最后错误: {_circuit.last_error}")
        # 保留已有持仓/交易记录，只更新状态（避免数据丢失）
        try:
            with open(f"{DASHBOARD_DIR}/data.json") as f:
                dashboard = json.load(f)
        except:
            dashboard = {}
        dashboard.update({
            "time": now.strftime("%H:%M:%S"), "date": now.strftime("%Y-%m-%d"),
            "market_status": "blocked", "market_text": f"⛔ 已熔断 ({remaining}s剩余)",
            "market_et": _circuit.last_error or "",
        })
        dashboard.setdefault("news", [])
        dashboard.setdefault("symbols", {})
        dashboard.setdefault("positions", {})
        dashboard.setdefault("account", None)'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open(path, "w") as f:
        f.write(content)
    print("✅ ETF策略熔断保护已修复 — 熔断时保留已有数据而非清空")
else:
    print("❌ 未匹配到旧代码块，可能已经被修改过")
PYEOF
