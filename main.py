import os
import json
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = "sentences.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            level TEXT,
            source TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


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
            return

        if self.path == "/sentences":
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("""
                SELECT text, level, source, created_at
                FROM sentences
                ORDER BY id DESC
            """)
            rows = cur.fetchall()
            conn.close()

            sentences = [
                {
                    "text": r[0],
                    "level": r[1],
                    "source": r[2],
                    "created_at": r[3]
                } for r in rows
            ]

            self._send_json({
                "ok": True,
                "count": len(sentences),
                "sentences": sentences
            })
            return

        self._send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self):
        if self.path != "/ingest":
            self._send_json({"ok": False, "error": "Not found"}, 404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except Exception:
            self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
            return

        text = data.get("text")
        level = data.get("level", "unknown")
        source = data.get("source", "unknown")

        if not text:
            self._send_json({"ok": False, "error": "Missing text"}, 400)
            return

        created_at = datetime.utcnow().isoformat()

        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sentences (text, level, source, created_at)
            VALUES (?, ?, ?, ?)
        """, (text, level, source, created_at))
        conn.commit()
        conn.close()

        self._send_json({
            "ok": True,
            "saved": True,
            "record": {
                "text": text,
                "level": level,
                "source": source,
                "created_at": created_at
            }
        })


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running on port {port}")
    server.serve_forever()
