#!/usr/bin/env python3
"""Dashboard server — serves HTML + data.json + config save endpoint."""
import json, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8765
DASHBOARD_DIR = Path(os.path.expanduser("~/ibkr_dashboard")).expanduser()
os.chdir(DASHBOARD_DIR)


class Handler(SimpleHTTPRequestHandler):
    def _path(self):
        return self.path.split("?")[0]

    def do_POST(self):
        if self._path() == "/save_config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                cfg = json.loads(body)
                config_path = DASHBOARD_DIR / "strategy_config.json"
                config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())
                print(f"  ⚙️ Config saved")
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self._path() == "/run_backtest":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "running", "message": "Backtest started — check /backtest_result"}).encode())
            Handler._bt_running = True
            # Run backtest in background
            import subprocess, threading
            def run():
                result_path = DASHBOARD_DIR / "backtest_result.json"
                # Auto-detect venv python (server vs local)
                import sys as _sys
                _python = _sys.executable  # use same python that runs server
                _bt_script = Path(os.path.expanduser("~/short-term-trader")) / "backtest.py"
                try:
                    subprocess.run([_python, str(_bt_script)],
                                   capture_output=True, text=True, timeout=120,
                                   cwd=str(_bt_script.parent))
                except:
                    pass
                Handler._bt_running = False
            threading.Thread(target=run, daemon=True).start()
        else:
            super().do_POST()

    _bt_running = False  # cache backtest running state

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self._path() == "/backtest_result":
            result_path = DASHBOARD_DIR / "backtest_result.json"
            prev_path = DASHBOARD_DIR / "strategy_config_prev.json"
            resp = {"status": "idle"}
            if result_path.exists():
                try:
                    resp = json.loads(result_path.read_text())
                    resp["status"] = "done"
                except:
                    resp = {"status": "error", "message": "Failed to parse results"}
            elif Handler._bt_running:
                resp["status"] = "running"
                Handler._bt_running = False  # reset — if it's done, file will exist next poll
            # Attach previous config if exists
            if prev_path.exists():
                try:
                    resp["prev_config"] = json.loads(prev_path.read_text())
                except:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
            return
        super().do_GET()

    def log_message(self, format, *args):
        if any(skip in (format % args) for skip in ["data.json", "strategy_config"]):
            return
        super().log_message(format, *args)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"📊 Dashboard: http://localhost:{PORT}")
    print(f"   Config save: POST /save_config")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutdown")
        server.shutdown()
