import os
import sqlite3
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# =================================
# Database (SQLite)
# =================================
DB_PATH = "sentences.db"

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
        level TEXT NOT NULL,
        source TEXT NOT NULL,
        review_state TEXT DEFAULT 'new',
        next_review_at TEXT,
        created_at TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()

init_db()

# =================================
# FastAPI
# =================================
app = FastAPI(title="Language Reminder Server")

# =================================
# Models
# =================================
class SentenceIn(BaseModel):
    text: str
    level: str
    source: str

class ReviewUpdate(BaseModel):
    review_state: str  # again | hard | good | easy

# =================================
# Helpers
# =================================
def calc_next_review(state: str) -> str:
    now = datetime.utcnow()
    mapping = {
        "again": now + timedelta(minutes=10),
        "hard":  now + timedelta(hours=12),
        "good":  now + timedelta(days=1),
        "easy":  now + timedelta(days=3),
    }
    return mapping.get(state, now + timedelta(days=1)).isoformat()

# =================================
# Routes
# =================================
@app.get("/health")
def health():
    return {"ok": True, "service": "language-reminder-server"}

@app.post("/ingest")
def ingest(sentence: SentenceIn):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO sentences (text, level, source, created_at)
        VALUES (?, ?, ?, ?)
    """, (sentence.text, sentence.level, sentence.source, now))
    conn.commit()
    conn.close()
    return {"ok": True, "saved": True}

@app.get("/sentences")
def get_sentences(limit: int = 50):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM sentences
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "sentences": [dict(r) for r in rows]}

@app.get("/next")
def next_sentence():
    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute("""
        SELECT * FROM sentences
        WHERE next_review_at IS NULL
           OR next_review_at <= ?
        ORDER BY next_review_at ASC
        LIMIT 1
    """, (now,)).fetchone()
    conn.close()
    if not row:
        return {"ok": True, "sentence": None}
    return {"ok": True, "sentence": dict(row)}

@app.post("/review/{sentence_id}")
def review(sentence_id: int, body: ReviewUpdate):
    next_time = calc_next_review(body.review_state)
    conn = get_db()
    cur = conn.execute("""
        UPDATE sentences
        SET review_state = ?, next_review_at = ?
        WHERE id = ?
    """, (body.review_state, next_time, sentence_id))
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sentence not found")

    return {
        "ok": True,
        "sentence_id": sentence_id,
        "review_state": body.review_state,
        "next_review_at": next_time
    }
