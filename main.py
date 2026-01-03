import os
from datetime import datetime, timezone
from typing import Literal, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Language Reminder Server", version="1.0.0")


# =========================
# DB helpers
# =========================
def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set in Railway Variables.")
    return db_url


def db_connect():
    # Railway DATABASE_URL works directly with psycopg2
    return psycopg2.connect(get_database_url(), cursor_factory=RealDictCursor)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    """
    IMPORTANT:
    - This does NOT delete data.
    - It only creates tables if they don't exist.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS {table_name} (
        id BIGSERIAL PRIMARY KEY,
        phrase TEXT NOT NULL,

        -- timestamps
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        -- SRS fields (generic, can be adapted later to FSRS/Anki-like logic)
        last_reviewed_at TIMESTAMPTZ NULL,
        next_review_at TIMESTAMPTZ NULL,

        repetitions INT NOT NULL DEFAULT 0,
        lapses INT NOT NULL DEFAULT 0,

        -- "again/hard/good/easy" history is computed later; here we keep numeric state
        stability DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        difficulty DOUBLE PRECISION NOT NULL DEFAULT 0.0
    );
    """

    # Optional: avoid duplicates (same phrase inserted twice in same language table)
    # If you want to allow duplicates later, remove this.
    create_unique_sql = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = '{index_name}'
        ) THEN
            CREATE UNIQUE INDEX {index_name} ON {table_name} (phrase);
        END IF;
    END $$;
    """

    with db_connect() as conn:
        with conn.cursor() as cur:
            for lang in ("en", "es"):
                table = f"phrases_{lang}"
                idx = f"uq_{table}_phrase"
                cur.execute(create_table_sql.format(table_name=table))
                cur.execute(create_unique_sql.format(index_name=idx, table_name=table))
        conn.commit()


# Create tables automatically on startup (Option 1)
@app.on_event("startup")
def on_startup():
    init_db()


# =========================
# API models
# =========================
Lang = Literal["en", "es"]


class IngestPayload(BaseModel):
    lang: Lang = Field(..., description="en or es")
    phrase: str = Field(..., min_length=1, description="The foreign sentence only (no translation).")


class IngestResponse(BaseModel):
    ok: bool
    inserted: bool
    table: str
    id: Optional[int] = None
    message: str


# =========================
# Routes
# =========================
@app.get("/health")
def health():
    return {"ok": True, "time": utcnow().isoformat()}


@app.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestPayload):
    table = f"phrases_{payload.lang}"

    sql_insert = f"""
    INSERT INTO {table} (phrase)
    VALUES (%s)
    ON CONFLICT (phrase) DO NOTHING
    RETURNING id;
    """

    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_insert, (payload.phrase.strip(),))
                row = cur.fetchone()
            conn.commit()

        if row and row.get("id") is not None:
            return IngestResponse(
                ok=True,
                inserted=True,
                table=table,
                id=int(row["id"]),
                message="Inserted."
            )

        return IngestResponse(
            ok=True,
            inserted=False,
            table=table,
            id=None,
            message="Already exists (duplicate)."
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Telegram webhook placeholder (so you stop seeing 404)
@app.post("/telegram/webhook")
def telegram_webhook():
    # Later we will verify Telegram secret token + parse updates
    return {"ok": True}


# Root
@app.get("/")
def root():
    return {"service": "language-reminder-server", "endpoints": ["/health", "/ingest", "/telegram/webhook"]}
