import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

def _read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="ignore")
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {"_raw": raw}

class Handler(BaseHTTPRequestHandler):
    def _send(self, status=200, data=None, content_type="application/json; charset=utf-8"):
        payload = data if data is not None else {}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Health + Root
        if self.path in ["/", "/health"]:
            self._send(200, {"ok": True, "service": "language-reminder-server"})
            return

        # Unknown
        self._send(404, {"ok": False, "error": "Not Found", "path": self.path})

    def do_POST(self):
        if self.path == "/ingest":
            data = _read_json(self)

            # المتوقع من iPhone/Pipedream:
            # {"text":"...", "level":"good", "tags":["..."], "source":"ios"}
            text = (data.get("text") or "").strip()

            if not text:
                self._send(400, {"ok": False, "error": "Missing 'text'"})
                return

            # حالياً فقط نؤكد الاستلام (الخطوة التالية: نحفظ في DB)
            self._send(200, {"ok": True, "received": True, "text_len": len(text)})
            return

        self._send(404, {"ok": False, "error": "Not Found", "path": self.path})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
