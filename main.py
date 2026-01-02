import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

# تخزين مؤقت للجمل (في الذاكرة)
SENTENCES = []

class Handler(BaseHTTPRequestHandler):

    def _send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {
                "ok": True,
                "service": "language-reminder-server"
            })

        elif self.path == "/sentences":
            self._send_json(200, {
                "ok": True,
                "count": len(SENTENCES),
                "sentences": SENTENCES
            })

        else:
            self._send_json(404, {
                "ok": False,
                "error": "Not found"
            })

    def do_POST(self):
        if self.path == "/ingest":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {
                    "ok": False,
                    "error": "Invalid JSON"
                })
                return

            text = data.get("text")
            level = data.get("level", "unknown")
            source = data.get("source", "unknown")

            if not text:
                self._send_json(400, {
                    "ok": False,
                    "error": "Missing 'text'"
                })
                return

            record = {
                "text": text,
                "level": level,
                "source": source
            }

            SENTENCES.append(record)

            self._send_json(200, {
                "ok": True,
                "saved": True,
                "record": record
            })

        else:
            self._send_json(404, {
                "ok": False,
                "error": "Not found"
            })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
