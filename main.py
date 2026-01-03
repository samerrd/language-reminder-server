import os
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional, Literal, Any, Dict, List

import anyio
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from psycopg_pool import ConnectionPool


# ----------------------------
# Config
# ----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_URL_RAW = os.getenv("DATABASE_URL", "").strip()  # Must be set on Railway


def normalize_db_url(url: str) -> str:
    """
    Railway sometimes provides postgres://... which some drivers expect as postgresql://...
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


DATABASE_URL = normalize_db_url(DATABASE_URL_RAW)

pool: Optional[ConnectionPool] = None


# ----------------------------
# Models
# ----------------------------
Lang = Literal["en", "es"]
Rating = Literal["again", "hard", "good", "easy"]


class IngestItem(BaseModel):
    # Keep compatibility with your Shortcut: it sends "text"
    text: str = Field(..., min_length=0)
    lang: Lang = "en"
    tags: Optional[List[str]] = None
    voice: Optional[str] = None  # optional hint for TTS voice later


class ReviewUpdate(BaseModel):
    lang: Lang = "en"
    rating: Rating


# ----------------------------
# DB helpers
# ----------------------------
def table_name(lang: Lang) -> str:
    return "sentences_en" if lang == "en" else "sentences_es"


CREATE_TABLE_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
  id BIGSERIAL PRIMARY KEY,
  text TEXT NOT NULL,
  tags TEXT[] DEFAULT ARRAY[]::TEXT[],
  voice TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- SRS / scheduling fields (FSRS-style placeholders)
  due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_review_at TIMESTAMPTZ NULL,

  stability REAL NOT NULL DEFAULT 0,
  difficulty REAL NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  state SMALLINT NOT NULL DEFAULT 0,  -- 0=new,1=learning,2=review,3=relearning
  last_rating TEXT NULL
);

CREATE INDEX IF NOT EXISTS {table}_due_idx ON {table} (due_at);
"""


def db_init_sync() -> None:
    global pool
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_TEMPLATE.format(table="sentences_en"))
            cur.execute(CREATE_TABLE_TEMPLATE.format(table="sentences_es"))
        conn.commit()


def db_health_sync() -> bool:
    global pool
    if pool is None:
        return False
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            _ = cur.fetchone()
    return True


def insert_sentence_sync(item: IngestItem) -> int:
    global pool
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    tname = table_name(item.lang)
    text = (item.text or "").strip()

    # Accept empty by mistake but do not store it
    if not text:
        return 0

    tags = item.tags or []
    voice = item.voice

    q = f"""
    INSERT INTO {tname} (text, tags, voice)
    VALUES (%s, %s::TEXT[], %s)
    RETURNING id;
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q, (text, tags, voice))
            new_id = cur.fetchone()[0]
        conn.commit()
    return int(new_id)


def list_sentences_sync(lang: Lang, limit: int, offset: int) -> List[Dict[str, Any]]:
    global pool
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    tname = table_name(lang)
    q = f"""
    SELECT id, text, tags, voice, created_at, due_at, reps, lapses, state
    FROM {tname}
    ORDER BY id DESC
    LIMIT %s OFFSET %s;
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q, (limit, offset))
            rows = cur.fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "text": r[1],
                "tags": r[2] or [],
                "voice": r[3],
                "created_at": r[4].isoformat(),
                "due_at": r[5].isoformat(),
                "reps": r[6],
                "lapses": r[7],
                "state": r[8],
            }
        )
    return out


def next_sentence_sync(lang: Lang) -> Optional[Dict[str, Any]]:
    global pool
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    tname = table_name(lang)
    q = f"""
    SELECT id, text, tags, voice, due_at, reps, lapses, state
    FROM {tname}
    WHERE due_at <= NOW()
    ORDER BY due_at ASC
    LIMIT 1;
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q)
            row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "text": row[1],
        "tags": row[2] or [],
        "voice": row[3],
        "due_at": row[4].isoformat(),
        "reps": row[5],
        "lapses": row[6],
        "state": row[7],
    }


def apply_simple_srs(rating: Rating) -> timedelta:
    """
    Placeholder scheduling until you implement full FSRS.
    """
    if rating == "again":
        return timedelta(minutes=10)
    if rating == "hard":
        return timedelta(days=1)
    if rating == "good":
        return timedelta(days=3)
    return timedelta(days=7)  # easy


def review_sentence_sync(sentence_id: int, upd: ReviewUpdate) -> Dict[str, Any]:
    global pool
    if pool is None:
        raise RuntimeError("DB pool is not initialized")

    tname = table_name(upd.lang)
    delta = apply_simple_srs(upd.rating)

    q_get = f"SELECT id, reps, lapses FROM {tname} WHERE id = %s;"
    q_upd = f"""
    UPDATE {tname}
    SET
      updated_at = NOW(),
      last_review_at = NOW(),
      due_at = NOW() + (%s || ' seconds')::interval,
      reps = reps + 1,
      lapses = lapses + CASE WHEN %s = 'again' THEN 1 ELSE 0 END,
      last_rating = %s
    WHERE id = %s
    RETURNING id, due_at, reps, lapses, last_rating;
    """

    seconds = int(delta.total_seconds())

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q_get, (sentence_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Sentence not found")

            cur.execute(q_upd, (seconds, upd.rating, upd.rating, sentence_id))
            updated = cur.fetchone()
        conn.commit()

    return {
        "id": updated[0],
        "due_at": updated[1].isoformat(),
        "reps": updated[2],
        "lapses": updated[3],
        "last_rating": updated[4],
    }


# ----------------------------
# FastAPI lifespan
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set it in Railway service variables.")

    pool = ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=5,
        timeout=10,
        open=True,
    )

    # Create tables on startup
    await anyio.to_thread.run_sync(db_init_sync)

    yield

    if pool is not None:
        pool.close()
        pool = None


app = FastAPI(title="Language Reminder Server", version="0.1.0", lifespan=lifespan)


# ----------------------------
# Routes
# ----------------------------
@app.get("/health")
async def health():
    ok = await anyio.to_thread.run_sync(db_health_sync)
    return {"ok": True, "db": ok}


@app.post("/ingest")
async def ingest(item: IngestItem):
    new_id = await anyio.to_thread.run_sync(insert_sentence_sync, item)
    if new_id == 0:
        # empty text ignored
        return {"ok": True, "stored": False, "reason": "empty_text"}
    return {"ok": True, "stored": True, "id": new_id, "lang": item.lang}


@app.get("/sentences")
async def get_sentences(
    lang: Lang = Query("en"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    rows = await anyio.to_thread.run_sync(list_sentences_sync, lang, limit, offset)
    return {"lang": lang, "count": len(rows), "items": rows}


@app.get("/next")
async def get_next(lang: Lang = Query("en")):
    row = await anyio.to_thread.run_sync(next_sentence_sync, lang)
    return {"lang": lang, "item": row}


@app.post("/review/{sentence_id}")
async def review(
    sentence_id: int = Path(..., ge=1),
    upd: ReviewUpdate = ...,
):
    result = await anyio.to_thread.run_sync(review_sentence_sync, sentence_id, upd)
    return {"ok": True, "lang": upd.lang, "result": result}


@app.post("/telegram/webhook")
async def telegram_webhook(update: Dict[str, Any]):
    """
    For now: accept webhook and optionally ingest plain text messages.
    This prevents Telegram from getting 404.
    """
    try:
        msg = (update.get("message") or {})
        text = (msg.get("text") or "").strip()

        # Optional simple commands:
        # "/en hello" -> English table
        # "/es hola"  -> Spanish table
        lang: Lang = "en"
        payload_text = text

        if text.startswith("/en "):
            lang = "en"
            payload_text = text[4:].strip()
        elif text.startswith("/es "):
            lang = "es"
            payload_text = text[4:].strip()

        if payload_text:
            item = IngestItem(text=payload_text, lang=lang)
            await anyio.to_thread.run_sync(insert_sentence_sync, item)

        return {"ok": True}
    except Exception:
        # Do not break webhook delivery
        return {"ok": True}
