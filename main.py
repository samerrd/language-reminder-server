import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            return self._send_json(200, {"ok": True, "service": "language-reminder-server"})
        return self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if self.path != "/ingest":
            return self._send_json(404, {"ok": False, "error": "Not found"})

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else ""

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self._send_json(400, {"ok": False, "error": "Invalid JSON"})

        text = (payload.get("text") or "").strip()
        if not text:
            return self._send_json(400, {"ok": False, "error": "Missing 'text'"})

        level = (payload.get("level") or "good").strip()
        source = (payload.get("source") or "manual").strip()

        # حاليا: نعيد نجاح فقط (لاحقًا نضيف قاعدة البيانات)
        return self._send_json(200, {"ok": True, "saved": True, "text": text, "level": level, "source": source})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
