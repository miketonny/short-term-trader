#!/usr/bin/env python3
"""ETF 看板服务 —— serve ~/live_ibkr_dashboard，只绑 127.0.0.1。

外网访问走 Cloudflare Tunnel（cloudflared 在本机连 127.0.0.1:8767），
所以这里不需要也不应该绑 0.0.0.0：/save_config 能直接改策略参数。
"""
import json, os, subprocess, threading, sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("DASHBOARD_PORT", 8767))
DASHBOARD_DIR = Path(os.path.expanduser("~/live_ibkr_dashboard"))
TRADER_DIR = Path(os.path.expanduser("~/short-term-trader"))
sys.path.insert(0, str(TRADER_DIR))
os.chdir(DASHBOARD_DIR)


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
                target.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
                print(f"  ⚙️ 参数已保存 {len(cfg)} 项")
                self._json(200, {"ok": True})
            except Exception as e:
                print(f"  ❌ save_config 失败: {e}")
                self._json(400, {"ok": False, "error": str(e)})
            return

        if path == "/run_backtest":
            if Handler._bt_running:
                self._json(200, {"status": "running"})
                return
            Handler._bt_running = True

            def run():
                try:
                    subprocess.run([sys.executable, str(TRADER_DIR / "backtest.py")],
                                   capture_output=True, text=True, timeout=180,
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

    def do_GET(self):
        if self._path() == "/backtest_result":
            result = DASHBOARD_DIR / "backtest_result.json"
            resp = {"status": "running" if Handler._bt_running else "idle"}
            if result.exists():
                try:
                    resp = json.loads(result.read_text())
                    resp["status"] = "done"
                except json.JSONDecodeError:
                    resp = {"status": "error", "message": "回测结果不是合法 JSON"}
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
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"📊 ETF 看板 http://127.0.0.1:{PORT}  目录 {DASHBOARD_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 关闭")
