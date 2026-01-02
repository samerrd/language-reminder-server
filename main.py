import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

# تخزين مؤقت في الذاكرة
SENTENCES = []

class Handler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/health":
            self._send_json({
                "ok": True,
                "service": "language-reminder-server"
            })

        elif self.path == "/sentences":
            self._send_json({
                "ok": True,
                "count": len(SENTENCES),
                "sentences": SENTENCES
            })

        else:
            self._send_json({
                "ok": False,
                "error": "Not found"
            }, status=404)

    def do_POST(self):
        if self.path != "/ingest":
            self._send_json({
                "ok": False,
                "error": "Not found"
            }, status=404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({
                "ok": False,
                "error": "Invalid JSON"
            }, status=400)
            return

        text = data.get("text")
        if not text:
            self._send_json({
                "ok": False,
                "error": "Missing 'text'"
            }, status=400)
            return

        sentence = {
            "text": text,
            "level": data.get("level", "unknown"),
            "source": data.get("source", "unknown")
        }

        SENTENCES.append(sentence)

        self._send_json({
            "ok": True,
            "saved": True,
            **sentence
        })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
