import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel


# ======================
# ENV
# ======================
DB_PATH = os.environ.get("DB_PATH", "sentences.db")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# ضع CHAT_ID كمتغير بيئة في Railway (مهم جدًا لإرسال الرسائل)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# (اختياري) سرّ بسيط لحماية ingest من أي طلبات عشوائية:
INGEST_SECRET = os.environ.get("INGEST_SECRET", "").strip()


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
        text TEXT NOT NULL,
        level TEXT NOT NULL,
        source TEXT NOT NULL,
        review_state TEXT DEFAULT 'new',
        next_review_at TEXT,
        created_at TEXT NOT NULL
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()

def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute("""
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()
    conn.close()

def get_setting(key: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None

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
    level: str = "good"
    source: str = "pipedream"
    secret: Optional[str] = None  # اختياري


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

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()

def effective_chat_id() -> str:
    # أولوية: متغير بيئة -> آخر chat_id وصلنا من /start
    if TELEGRAM_CHAT_ID:
        return TELEGRAM_CHAT_ID
    saved = get_setting("telegram_chat_id")
    return saved or ""

async def tg_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()

async def send_sentence_to_telegram(sentence_row: Dict[str, Any]) -> None:
    chat_id = effective_chat_id()
    if not chat_id:
        # لا يمكن الإرسال بدون chat_id
        return

    sid = sentence_row["id"]
    text = sentence_row["text"]
    level = sentence_row["level"]
    source = sentence_row["source"]

    msg = (
        f"🧠 *New sentence*\n\n"
        f"*ID:* `{sid}`\n"
        f"*Level:* `{level}`\n"
        f"*Source:* `{source}`\n\n"
        f"✍️ {text}\n\n"
        f"اختر مستوى التذكر لتحديث الجدولة:"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "again", "callback_data": f"review:{sid}:again"},
            {"text": "hard",  "callback_data": f"review:{sid}:hard"},
            {"text": "good",  "callback_data": f"review:{sid}:good"},
            {"text": "easy",  "callback_data": f"review:{sid}:easy"},
        ]]
    }

    await tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "Markdown",
        "reply_markup": keyboard
    })


# ======================
# Routes (Basic)
# ======================
@app.get("/health")
def health():
    return {"ok": True, "service": "language-reminder-server"}

@app.post("/ingest")
async def ingest(sentence: SentenceIn):
    # حماية اختيارية
    if INGEST_SECRET:
        if not sentence.secret or sentence.secret != INGEST_SECRET:
            raise HTTPException(status_code=401, detail="Invalid secret")

    text = (sentence.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    conn = get_db()
    now = utc_now_iso()
    cur = conn.execute("""
        INSERT INTO sentences (text, level, source, created_at)
        VALUES (?, ?, ?, ?)
    """, (text, sentence.level, sentence.source, now))
    conn.commit()

    sid = cur.lastrowid
    row = conn.execute("SELECT * FROM sentences WHERE id = ?", (sid,)).fetchone()
    conn.close()

    # إرسال فوري إلى تيليغرام
    try:
        await send_sentence_to_telegram(dict(row))
    except Exception:
        # لا نُفشل ingest بسبب خطأ تيليغرام
        pass

    return {"ok": True, "saved": True, "id": sid}

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
    now = utc_now_iso()
    row = conn.execute("""
        SELECT * FROM sentences
        WHERE next_review_at IS NULL
           OR next_review_at <= ?
        ORDER BY COALESCE(next_review_at, created_at) ASC
        LIMIT 1
    """, (now,)).fetchone()
    conn.close()

    if not row:
        return {"ok": True, "sentence": None}
    return {"ok": True, "sentence": dict(row)}

@app.post("/review/{sentence_id}")
def review(sentence_id: int, body: ReviewUpdate):
    state = (body.review_state or "").strip().lower()
    if state not in {"again", "hard", "good", "easy"}:
        raise HTTPException(status_code=400, detail="Invalid review_state")

    next_time = calc_next_review(state)

    conn = get_db()
    cur = conn.execute("""
        UPDATE sentences
        SET review_state = ?, next_review_at = ?
        WHERE id = ?
    """, (state, next_time, sentence_id))
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sentence not found")

    return {"ok": True, "sentence_id": sentence_id, "review_state": state, "next_review_at": next_time}


# ======================
# Telegram Webhook
# ======================
@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    update = await req.json()

    # 1) إذا جاءت رسالة /start: احفظ chat_id كي نرسل له لاحقًا
    message = update.get("message") or update.get("edited_message")
    if message:
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if chat_id:
            set_setting("telegram_chat_id", str(chat_id))

        # رد بسيط عند /start فقط
        if text == "/start":
            try:
                await tg_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": "✅ Bot is connected. Now send sentences from iPhone → Pipedream → /ingest, and I will notify you here."
                })
            except Exception:
                pass

    # 2) إذا جاء callback من الأزرار: حدث الجدولة
    cb = update.get("callback_query")
    if cb:
        data = (cb.get("data") or "").strip()
        cb_id = cb.get("id")
        from_msg = cb.get("message") or {}
        chat_id = (from_msg.get("chat") or {}).get("id")

        if data.startswith("review:"):
            # review:{sid}:{state}
            parts = data.split(":")
            if len(parts) == 3:
                sid = int(parts[1])
                state = parts[2].lower().strip()

                try:
                    # تحديث DB
                    next_time = calc_next_review(state)
                    conn = get_db()
                    cur = conn.execute("""
                        UPDATE sentences
                        SET review_state = ?, next_review_at = ?
                        WHERE id = ?
                    """, (state, next_time, sid))
                    conn.commit()
                    conn.close()

                    if cur.rowcount == 0:
                        raise ValueError("Sentence not found")

                    # إشعار صغير
                    try:
                        await tg_api("answerCallbackQuery", {
                            "callback_query_id": cb_id,
                            "text": f"Saved: {state} ✅"
                        })
                    except Exception:
                        pass

                    # رسالة تأكيد
                    try:
                        await tg_api("sendMessage", {
                            "chat_id": chat_id,
                            "text": f"✅ Updated sentence #{sid}\nState: {state}\nNext: {next_time}"
                        })
                    except Exception:
                        pass

                except Exception:
                    try:
                        await tg_api("answerCallbackQuery", {
                            "callback_query_id": cb_id,
                            "text": "Error updating review"
                        })
                    except Exception:
                        pass

    return {"ok": True}
