import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import Application


# ======================
# ENV
# ======================
DB_PATH = os.environ.get("DB_PATH", "sentences.db")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

if not TELEGRAM_BOT_TOKEN:
    # سيعمل السيرفر، لكن تيليغرام لن يعمل بدون التوكن
    print("WARNING: TELEGRAM_BOT_TOKEN is missing. Telegram webhook will fail.")


# ======================
# Database (SQLite)
# ======================
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            review_state TEXT DEFAULT 'new',
            next_review_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# ======================
# FastAPI
# ======================
app = FastAPI(title="Language Reminder Server")


# ======================
# Telegram (python-telegram-bot)
# ======================
telegram_app: Optional[Application] = None

if TELEGRAM_BOT_TOKEN:
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()


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
# SRS Helpers
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


# ======================
# DB Helpers
# ======================
def db_insert_sentence(text: str, level: str, source: str) -> int:
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        INSERT INTO sentences (text, level, source, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (text, level, source, now),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def db_get_sentences(limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM sentences
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_get_next_sentence() -> Optional[Dict[str, Any]]:
    conn = get_db()
    now = datetime.utcnow().isoformat()
    row = conn.execute(
        """
        SELECT * FROM sentences
        WHERE next_review_at IS NULL
           OR next_review_at <= ?
        ORDER BY COALESCE(next_review_at, created_at) ASC
        LIMIT 1
        """,
        (now,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def db_review(sentence_id: int, state: str) -> Dict[str, Any]:
    next_time = calc_next_review(state)

    conn = get_db()
    cur = conn.execute(
        """
        UPDATE sentences
        SET review_state = ?, next_review_at = ?
        WHERE id = ?
        """,
        (state, next_time, sentence_id),
    )
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sentence not found")

    return {"sentence_id": sentence_id, "review_state": state, "next_review_at": next_time}


# ======================
# Telegram Message Helpers
# ======================
def build_review_keyboard(sentence_id: int) -> InlineKeyboardMarkup:
    # callback_data قصيرة وواضحة
    # review:<id>:<state>
    keyboard = [
        [
            InlineKeyboardButton("Again", callback_data=f"review:{sentence_id}:again"),
            InlineKeyboardButton("Hard",  callback_data=f"review:{sentence_id}:hard"),
        ],
        [
            InlineKeyboardButton("Good",  callback_data=f"review:{sentence_id}:good"),
            InlineKeyboardButton("Easy",  callback_data=f"review:{sentence_id}:easy"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_sentence_msg(s: Dict[str, Any]) -> str:
    # نص بسيط وواضح
    text = (s.get("text") or "").strip()
    level = (s.get("level") or "").strip()
    source = (s.get("source") or "").strip()
    sid = s.get("id")
    return f"Sentence #{sid}\nLevel: {level}\nSource: {source}\n\n{text}"


async def tg_send_next(chat_id: int):
    if not telegram_app:
        raise HTTPException(status_code=500, detail="Telegram app not configured")

    s = db_get_next_sentence()
    if not s:
        await telegram_app.bot.send_message(chat_id=chat_id, text="لا توجد جمل مستحقة للمراجعة الآن.")
        return

    await telegram_app.bot.send_message(
        chat_id=chat_id,
        text=format_sentence_msg(s),
        reply_markup=build_review_keyboard(int(s["id"])),
    )


# ======================
# REST Routes (Existing)
# ======================
@app.get("/health")
def health():
    return {"ok": True, "service": "language-reminder-server"}


@app.post("/ingest")
def ingest(sentence: SentenceIn):
    new_id = db_insert_sentence(sentence.text, sentence.level, sentence.source)
    return {"ok": True, "saved": True, "id": new_id}


@app.get("/sentences")
def get_sentences(limit: int = 50):
    rows = db_get_sentences(limit=limit)
    return {"ok": True, "count": len(rows), "sentences": rows}


@app.get("/next")
def next_sentence():
    row = db_get_next_sentence()
    return {"ok": True, "sentence": row}


@app.post("/review/{sentence_id}")
def review(sentence_id: int, body: ReviewUpdate):
    result = db_review(sentence_id, body.review_state)
    return {"ok": True, **result}


# ======================
# Telegram Webhook Route
# ======================
@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    if not telegram_app:
        raise HTTPException(status_code=500, detail="Telegram is not configured. Set TELEGRAM_BOT_TOKEN.")

    data = await req.json()

    # تحويل JSON إلى Update
    update = Update.de_json(data, telegram_app.bot)

    # 1) /start أو أي رسالة
    if update.message and update.message.text:
        txt = update.message.text.strip()
        chat_id = update.message.chat_id

        if txt.startswith("/start"):
            await telegram_app.bot.send_message(
                chat_id=chat_id,
                text="تم التشغيل. سأرسل لك الآن الجملة القادمة للمراجعة.",
            )
            await tg_send_next(chat_id)
            return {"ok": True}

        # أي رسالة أخرى: نرسل الجملة القادمة أيضًا (اختياري)
        await telegram_app.bot.send_message(
            chat_id=chat_id,
            text="استلمت رسالتك. هذه هي الجملة القادمة للمراجعة:",
        )
        await tg_send_next(chat_id)
        return {"ok": True}

    # 2) ضغط أزرار التقييم
    if update.callback_query and update.callback_query.data:
        cq = update.callback_query
        chat_id = cq.message.chat_id if cq.message else None

        # مهم: تأكيد الاستلام حتى لا يبقى زر التحميل في تيليغرام
        await telegram_app.bot.answer_callback_query(callback_query_id=cq.id)

        try:
            parts = cq.data.split(":")
            # review:<id>:<state>
            if len(parts) != 3 or parts[0] != "review":
                raise ValueError("Bad callback_data")

            sentence_id = int(parts[1])
            state = parts[2].strip().lower()

            if state not in {"again", "hard", "good", "easy"}:
                raise ValueError("Bad state")

            r = db_review(sentence_id, state)

            if chat_id is not None:
                await telegram_app.bot.send_message(
                    chat_id=chat_id,
                    text=f"تم حفظ التقييم: {state}\nالمراجعة القادمة: {r['next_review_at']}",
                )
                # إرسال الجملة التالية مباشرة
                await tg_send_next(chat_id)

            return {"ok": True}

        except Exception:
            if chat_id is not None:
                await telegram_app.bot.send_message(chat_id=chat_id, text="حدث خطأ في معالجة التقييم.")
            return {"ok": False}

    # أي تحديث آخر نتجاهله بأمان
    return {"ok": True}
