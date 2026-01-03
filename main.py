import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ======================
# Config
# ======================
DB_PATH = os.environ.get("DB_PATH", "sentences.db")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

# ======================
# Database (SQLite)
# ======================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_column(conn: sqlite3.Connection, table: str, column_def: str):
    """
    Tries to add a column; ignores if it already exists.
    column_def example: "telegram_chat_id INTEGER"
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError:
        # Most likely: duplicate column name
        pass

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

    # Add helpful columns if missing
    ensure_column(conn, "sentences", "telegram_chat_id INTEGER")

    # Subscribers: store chat_ids that should receive reminders (for /ingest pushes)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        telegram_chat_id INTEGER PRIMARY KEY,
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
# Helpers (SRS)
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

# ======================
# Helpers (Telegram)
# ======================
def telegram_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN) and TELEGRAM_API_BASE.startswith("https://api.telegram.org/bot")

def tg_post(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not telegram_enabled():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set on the server.")
    url = f"{TELEGRAM_API_BASE}/{method}"
    r = requests.post(url, json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data

def tg_send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_post("sendMessage", payload)

def tg_edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    tg_post("editMessageText", payload)

def tg_answer_callback(callback_query_id: str, text: str = "تم") -> None:
    tg_post("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

def srs_keyboard(sentence_id: int) -> Dict[str, Any]:
    # callback_data format: "review:<id>:<state>"
    return {
        "inline_keyboard": [
            [
                {"text": "Again", "callback_data": f"review:{sentence_id}:again"},
                {"text": "Hard",  "callback_data": f"review:{sentence_id}:hard"},
            ],
            [
                {"text": "Good",  "callback_data": f"review:{sentence_id}:good"},
                {"text": "Easy",  "callback_data": f"review:{sentence_id}:easy"},
            ],
        ]
    }

# ======================
# DB actions
# ======================
def add_subscriber(chat_id: int) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO subscribers (telegram_chat_id, created_at) VALUES (?, ?)",
        (chat_id, utc_now_iso()),
    )
    conn.commit()
    conn.close()

def list_subscribers() -> List[int]:
    conn = get_db()
    rows = conn.execute("SELECT telegram_chat_id FROM subscribers ORDER BY created_at ASC").fetchall()
    conn.close()
    return [int(r["telegram_chat_id"]) for r in rows]

def save_sentence(text: str, level: str, source: str, telegram_chat_id: Optional[int] = None) -> int:
    conn = get_db()
    now = utc_now_iso()
    cur = conn.execute("""
        INSERT INTO sentences (text, level, source, created_at, telegram_chat_id)
        VALUES (?, ?, ?, ?, ?)
    """, (text, level, source, now, telegram_chat_id))
    conn.commit()
    sentence_id = int(cur.lastrowid)
    conn.close()
    return sentence_id

def apply_review(sentence_id: int, review_state: str) -> Dict[str, Any]:
    next_time = calc_next_review(review_state)

    conn = get_db()
    cur = conn.execute("""
        UPDATE sentences
        SET review_state = ?, next_review_at = ?
        WHERE id = ?
    """, (review_state, next_time, sentence_id))
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sentence not found")

    return {"sentence_id": sentence_id, "review_state": review_state, "next_review_at": next_time}

# ======================
# Routes (Core)
# ======================
@app.get("/health")
def health():
    return {"ok": True, "service": "language-reminder-server"}

@app.post("/ingest")
def ingest(sentence: SentenceIn):
    """
    Used by Pipedream / Shortcut: saves the sentence
    and also pushes it to all Telegram subscribers (if any).
    """
    sentence_id = save_sentence(sentence.text, sentence.level, sentence.source, telegram_chat_id=None)

    # Push to Telegram (if bot configured and user already did /start)
    if telegram_enabled():
        subs = list_subscribers()
        if subs:
            msg = f"جملة جديدة:\n\n{sentence.text}\n\nاختر مستوى التذكّر:"
            kb = srs_keyboard(sentence_id)
            for chat_id in subs:
                try:
                    tg_send_message(chat_id, msg, reply_markup=kb)
                except Exception:
                    # Do not fail ingestion if Telegram fails
                    pass

    return {"ok": True, "saved": True, "sentence_id": sentence_id}

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
    data = apply_review(sentence_id, body.review_state)
    return {"ok": True, **data}

# ======================
# Telegram Webhook
# ======================
@app.post("/telegram/webhook")
def telegram_webhook(update: Dict[str, Any]):
    """
    Telegram sends updates here.
    Handles:
    - /start (register subscriber)
    - normal messages (save and show SRS buttons)
    - callback_query (user pressed Again/Hard/Good/Easy)
    """
    # 1) Callback query (button press)
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq.get("id", "")
        data = (cq.get("data") or "").strip()

        # message context (to edit message)
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        message_id = msg.get("message_id")

        try:
            if data.startswith("review:"):
                _, sid_str, state = data.split(":", 2)
                sentence_id = int(sid_str)
                result = apply_review(sentence_id, state)

                # Acknowledge tap
                try:
                    tg_answer_callback(cq_id, text="تم تسجيل اختيارك")
                except Exception:
                    pass

                # Edit original message to confirm and remove keyboard
                if isinstance(chat_id, int) and isinstance(message_id, int):
                    confirm_text = (
                        "تم تحديث المراجعة.\n\n"
                        f"الحالة: {result['review_state']}\n"
                        f"المراجعة التالية: {result['next_review_at']}"
                    )
                    try:
                        tg_edit_message(chat_id, message_id, confirm_text, reply_markup={"inline_keyboard": []})
                    except Exception:
                        pass

        except Exception:
            # Do not crash webhook
            try:
                if cq_id:
                    tg_answer_callback(cq_id, text="حدث خطأ")
            except Exception:
                pass

        return {"ok": True}

    # 2) Normal message
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()

        if not isinstance(chat_id, int):
            return {"ok": True}

        # /start: register subscriber and welcome
        if text.startswith("/start"):
            add_subscriber(chat_id)
            if telegram_enabled():
                tg_send_message(
                    chat_id,
                    "تم تفعيل البوت.\n"
                    "أرسل أي جملة الآن، وسأعرض لك أزرار التقييم (Again/Hard/Good/Easy)."
                )
            return {"ok": True}

        # Ignore empty messages
        if not text:
            return {"ok": True}

        # Save sentence and ask for rating
        try:
            sentence_id = save_sentence(text=text, level="good", source="telegram", telegram_chat_id=chat_id)
            if telegram_enabled():
                msg_text = f"تم حفظ الجملة:\n\n{text}\n\nاختر مستوى التذكّر:"
                kb = srs_keyboard(sentence_id)
                tg_send_message(chat_id, msg_text, reply_markup=kb)
        except Exception:
            # If something fails, try to notify user
            try:
                if telegram_enabled():
                    tg_send_message(chat_id, "حدث خطأ أثناء حفظ الجملة.")
            except Exception:
                pass

        return {"ok": True}

    return {"ok": True}
