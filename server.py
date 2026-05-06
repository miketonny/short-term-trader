#!/usr/bin/env python3
"""Dashboard server — serves HTML + data.json + config save endpoint."""
import json, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8765
DASHBOARD_DIR = Path(os.path.expanduser("~/ibkr_dashboard")).expanduser()
os.chdir(DASHBOARD_DIR)


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/save_config":
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
        else:
            super().do_POST()

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
