#!/usr/bin/env python3
"""ETF 看板服务 —— serve ~/live_ibkr_dashboard，只绑 127.0.0.1。

外网访问走 Cloudflare Tunnel（cloudflared 在本机连 127.0.0.1:8767），
所以这里不需要也不应该绑 0.0.0.0：/save_config 能直接改策略参数。
"""
import asyncio, json, os, subprocess, threading, sys
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("DASHBOARD_PORT", 8767))
DASHBOARD_DIR = Path(os.path.expanduser("~/live_ibkr_dashboard"))
TRADER_DIR = Path(os.path.expanduser("~/short-term-trader"))
ADVISOR_LOG = str(DASHBOARD_DIR / "advisor_log.json")
sys.path.insert(0, str(TRADER_DIR))
os.chdir(DASHBOARD_DIR)

from advisor_client import (call_advisor, log_advisor_call,          # noqa: E402
                            build_entry_context, build_position_context)


class Handler(SimpleHTTPRequestHandler):
    _bt_running = False

    def _path(self):
        return self.path.split("?")[0]

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self._path()
        if path == "/save_config":
            length = int(self.headers.get("Content-Length", 0))
            try:
                cfg = json.loads(self.rfile.read(length))
                if not isinstance(cfg, dict):
                    raise ValueError("config 必须是对象")
                target = DASHBOARD_DIR / "strategy_config.json"   # 软链接 → 真源
                # 只允许改现有键：漏传/传空 = 保持原值，绝不删键（一个空 POST 就能清空配置）
                base = json.loads(target.read_text())
                merged = {k: (cfg[k] if k in cfg else v) for k, v in base.items()}
                missing = {"symbols", "max_positions", "trading_enabled"} - set(merged)
                if missing:
                    raise ValueError(f"配置缺少必需字段: {sorted(missing)}")
                target.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
                changed = [k for k in merged if merged[k] != base.get(k)]
                print(f"  ⚙️ 参数已保存，改动 {len(changed)} 项: {changed}")
                self._json(200, {"ok": True, "changed": changed})
            except Exception as e:
                print(f"  ❌ save_config 失败: {e}")
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/ask_advisor":
            try:
                self._json(200, self.ask_advisor())
            except Exception as e:
                print(f"  ❌ 问顾问失败: {e}")
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/run_backtest":
            if Handler._bt_running:
                self._json(200, {"status": "running"})
                return
            Handler._bt_running = True

            def run():
                try:
                    # 用当前真实 NLV 跑，回测才算的是这个账户的摩擦（1 股 vs 多股差别巨大）
                    cmd = [sys.executable, str(TRADER_DIR / "backtest.py")]
                    try:
                        nlv = (json.loads((DASHBOARD_DIR / "data.json").read_text())
                               .get("account") or {}).get("nlv")
                        if nlv:
                            cmd += ["--nlv", str(round(float(nlv)))]
                    except Exception:
                        pass
                    subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                   cwd=str(TRADER_DIR))
                except Exception as e:
                    print(f"  ❌ 回测失败: {e}")
                finally:
                    Handler._bt_running = False

            threading.Thread(target=run, daemon=True).start()
            print("  📊 回测已启动")
            self._json(200, {"status": "running"})
            return

        self._json(404, {"ok": False, "error": "no such endpoint"})

    def ask_advisor(self):
        """用 data.json 的快照（策略自己算的指标）问一次顾问，结果写进 advisor_log.json。

        持仓中的标的走 /evaluate_position，其余走 /evaluate_entry —— 与策略自己的调用时机一致。
        记的 kind 是 manual_*，将来分析顾问准确性时能和真实信号区分开。
        """
        data = json.loads((DASHBOARD_DIR / "data.json").read_text())
        cfg = json.loads((DASHBOARD_DIR / "strategy_config.json").read_text())
        snap = data.get("symbols") or {}
        pos = data.get("positions") or {}
        nlv = (data.get("account") or {}).get("nlv") or 0
        ts = "%sT%s" % (data.get("date"), data.get("time"))
        alloc, lev = cfg.get("position_alloc", 0.08), cfg.get("leverage", 1.0)
        stop_pct = cfg.get("stop_loss_pct", 0.06)

        jobs = []
        for sym in cfg.get("symbols", []):
            d = snap.get(sym) or {}
            price = d.get("price")
            if not price:
                continue
            approx = "macd_line" not in d      # 盘中快照才有 line/signal，缺失就退回 hist
            p = pos.get(sym)
            if p and p.get("qty"):
                ctx = build_position_context(
                    sym, ts, price, p.get("avg_cost") or 0, p.get("qty") or 0,
                    p.get("entry_time"), p.get("mode") or "trend",
                    round((p.get("avg_cost") or 0) * (1 - stop_pct), 2),
                    round((price - (p.get("avg_cost") or 0)) * (p.get("qty") or 0), 2),
                    d.get("rsi"), d.get("sma"), d.get("adx"), nlv, [])
                kind, endpoint = "manual_position", "/evaluate_position"
            else:
                qty = max(1, int((nlv * alloc * lev) / price))
                ctx = build_entry_context(
                    sym, ts, price, d.get("rsi"), d.get("sma"),
                    d.get("bb_upper"), d.get("bb_lower"), d.get("adx"),
                    d.get("macd_signal", 0), d.get("macd_line", d.get("macd_hist", 0)),
                    nlv, qty, d.get("mode") or "trend", d.get("checks") or {}, [])
                kind, endpoint = "manual_entry", "/evaluate_entry"
            jobs.append((sym, kind, endpoint, ctx, approx))

        if not jobs:
            raise ValueError("data.json 里没有可问的标的快照")

        async def run_all():
            async def one(job):
                sym, kind, endpoint, ctx, approx = job
                r = await call_advisor(endpoint, ctx, timeout=45.0)
                log_advisor_call(kind, sym, "ASK", ctx, r, log_path=ADVISOR_LOG,
                                 shadow={"source": "dashboard", "approx_macd": approx})
                return {"symbol": sym, "kind": kind, "approx": approx,
                        "run_ts": datetime.now(timezone.utc).isoformat(), "decision": r}
            return await asyncio.gather(*[one(j) for j in jobs])

        results = asyncio.run(run_all())
        ok = sum(1 for r in results if r["decision"])
        print(f"  🧠 问顾问 {len(results)} 个标的，{ok} 个有返回")
        return {"ok": True, "snapshot": ts, "results": results}

    def do_GET(self):
        if self._path() == "/backtest_result":
            result = DASHBOARD_DIR / "backtest_result.json"
            if Handler._bt_running:
                resp = {"status": "running"}     # 先判运行中，否则重跑会一直报上次的 done
            elif result.exists():
                try:
                    resp = json.loads(result.read_text())
                    resp["status"] = "done"
                except json.JSONDecodeError:
                    resp = {"status": "error", "message": "回测结果不是合法 JSON"}
            else:
                resp = {"status": "idle"}
            self._json(200, resp)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 看板每 30 秒拉一次 data.json，别刷日志
        if any(s in (fmt % args) for s in ("data.json", "strategy_config", "backtest_result")):
            return
        super().log_message(fmt, *args)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"📊 ETF 看板 http://127.0.0.1:{PORT}  目录 {DASHBOARD_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 关闭")
