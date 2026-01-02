import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# تخزين الجمل في الذاكرة (مشترك بين كل الطلبات)
SENTENCES = []

class Handler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json({
                "ok": True,
                "service": "language-reminder-server"
            })

        elif path == "/sentences":
            self._send_json({
                "ok": True,
                "count": len(SENTENCES),
                "sentences": SENTENCES
            })

        else:
            self._send_json({
                "ok": False,
                "error": "Not found"
            }, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path != "/ingest":
            self._send_json({
                "ok": False,
                "error": "Not found"
            }, 404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except Exception:
            self._send_json({
                "ok": False,
                "error": "Invalid JSON"
            }, 400)
            return

        text = data.get("text")
        if not text:
            self._send_json({
                "ok": False,
                "error": "Missing 'text'"
            }, 400)
            return

        record = {
            "text": text,
            "level": data.get("level", "good"),
            "source": data.get("source", "manual")
        }

        SENTENCES.append(record)

        self._send_json({
            "ok": True,
            "saved": True,
            "record": record
        })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
