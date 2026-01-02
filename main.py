import os
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = "sentences.db"


# ---------- Database ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            level TEXT,
            source TEXT
        )
    """)
    conn.commit()
    conn.close()


def insert_sentence(text, level, source):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sentences (text, level, source) VALUES (?, ?, ?)",
        (text, level, source)
    )
    conn.commit()
    conn.close()


def get_all_sentences():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT text, level, source FROM sentences ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return [
        {"text": r[0], "level": r[1], "source": r[2]}
        for r in rows
    ]


# ---------- HTTP Handler ----------
class Handler(BaseHTTPRequestHandler):

    def _json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "language-reminder-server"})
            return

        if self.path == "/sentences":
            sentences = get_all_sentences()
            self._json(200, {
                "ok": True,
                "count": len(sentences),
                "sentences": sentences
            })
            return

        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if self.path != "/ingest":
            self._json(404, {"ok": False, "error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            data = json.loads(body)
        except Exception:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return

        text = data.get("text")
        level = data.get("level", "unknown")
        source = data.get("source", "manual")

        if not text:
            self._json(400, {"ok": False, "error": "Missing 'text'"})
            return

        insert_sentence(text, level, source)

        self._json(200, {
            "ok": True,
            "saved": True,
            "record": {
                "text": text,
                "level": level,
                "source": source
            }
        })


# ---------- Main ----------
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
