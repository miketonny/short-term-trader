import http.server, os, sys, json, subprocess
os.chdir(os.path.expanduser('~/live_ibkr_dashboard'))
PORT = 8767
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/save_config':
            length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(length))
            with open(os.path.expanduser('~/live_ibkr_dashboard/strategy_config.json'), 'w') as f:
                json.dump(data, f, indent=2)
            self.send_response(200); self.end_headers()
            self.wfile.write(b'ok')
            return
        self.send_response(404); self.end_headers()
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()
http.server.HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
