import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request

# ======================
# ENV
# ======================
DB_PATH = os.environ.get("DB_PATH", "sentences.db")

# ======================
# Database
# ======================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ======================
# FastAPI
# ======================
app = FastAPI(title="Language Reminder Server")

# ======================
# Routes
# ======================
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/ingest")
async def ingest(request: Request):
    """
    يتوقع JSON بسيط جدًا:
    {
      "text": "any sentence"
    }
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO sentences (text, created_at) VALUES (?, ?)",
        (text, now)
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()

    return {
        "ok": True,
        "saved": True,
        "id": sid,
        "text": text
    }

@app.get("/sentences")
def sentences():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sentences ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return {"count": len(rows), "sentences": [dict(r) for r in rows]}
