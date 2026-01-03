import os
import sqlite3
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# ======================
# Environment
# ======================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

DB_PATH = os.environ.get("DB_PATH", "sentences.db")

# ======================
# Database (SQLite)
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
        chat_id INTEGER NOT NULL,
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

# ======================
# FastAPI
# ======================
app = FastAPI(title="Language Reminder Server")

# ======================
# Models
# ======================
class SentenceIn(BaseModel):
    text: str
    level: str
    source: str

class ReviewUpdate(BaseModel):
    review_state: str  # again | hard | good | easy

# ======================
# Helpers
# ======================
def calc_next_review(state: str) -> str:
    now = datetime.utcnow()
    mapping = {
        "again": now + timedelta(minutes=10),
        "hard":  now + timedelta(hours=12),
        "good":  now + timedelta(days=1),
        "easy":  now + timedelta(days=3),
    }
    return mapping.get(state, now + timedelta(days=1)).isoformat()

def send_message(chat_id: int, text: str):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

# ======================
# Routes - Health
# ======================
@app.get("/health")
def health():
    return {"ok": True, "service": "language-reminder-server"}

# ======================
# Routes - iPhone / Pipedream
# ======================
@app.post("/ingest")
def ingest(sentence: SentenceIn):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO sentences (chat_id, text, level, source, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (0, sentence.text, sentence.level, sentence.source, now))
    conn.commit()
    conn.close()
    return {"ok": True, "saved": True}

# ======================
# Routes - Telegram Webhook
# ======================
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("TELEGRAM UPDATE:", update)

    message = update.get("message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":
        send_message(
            chat_id,
            "👋 مرحبًا بك في Language Reminder\n\n"
            "أرسل لي أي جملة الآن، وسأبدأ تذكيرك بها تلقائيًا."
        )
        return {"ok": True}

    # Save sentence from Telegram
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO sentences (chat_id, text, level, source, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (chat_id, text, "telegram", "telegram", now))
    conn.commit()
    conn.close()

    send_message(chat_id, "✅ تم حفظ الجملة. سأذكّرك بها لاحقًا.")

    return {"ok": True}

# ======================
# Review Logic
# ======================
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
