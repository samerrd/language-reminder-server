import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

# تخزين الجمل في الذاكرة (مؤقتًا)
SENTENCES = []

class Handler(BaseHTTPRequestHandler):

    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            self._set_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "service": "language-reminder-server"
            }).encode())

        elif self.path == "/health":
            self._set_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "service": "language-reminder-server"
            }).encode())

        elif self.path == "/sentences":
            self._set_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "count": len(SENTENCES),
                "sentences": SENTENCES
            }).encode())

        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({
                "ok": False,
                "error": "Not found"
            }).encode())

    def do_POST(self):
        if self.path != "/ingest":
            self._set_headers(404)
            self.wfile.write(json.dumps({
                "ok": False,
                "error": "Not found"
            }).encode())
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({
                "ok": False,
                "error": "Invalid JSON"
            }).encode())
            return

        text = data.get("text")
        level = data.get("level", "unknown")
        source = data.get("source", "unknown")

        if not text:
            self._set_headers(400)
            self.wfile.write(json.dumps({
                "ok": False,
                "error": "Missing 'text'"
            }).encode())
            return

        entry = {
            "text": text,
            "level": level,
            "source": source
        }

        SENTENCES.append(entry)

        self._set_headers()
        self.wfile.write(json.dumps({
            "ok": True,
            "saved": True,
            **entry
        }).encode())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
