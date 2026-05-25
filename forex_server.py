#!/usr/bin/env python3
"""Forex dashboard server — serves HTML + data.json on port 8766."""
import json, os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8766
DASHBOARD_DIR = Path(os.path.expanduser("~/forex_dashboard")).expanduser()
os.chdir(DASHBOARD_DIR)

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

print(f"Forex Dashboard: http://127.0.0.1:{PORT}")
HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
