import os
import logging
from datetime import datetime, timezone
from typing import Literal, Optional
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import psycopg2
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("language-reminder-server")


# =========================
# DB helpers
# =========================
def get_database_url() -> str:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set in Railway Variables.")

    # Ensure sslmode=require exists (helps on many hosted PGs)
    try:
        u = urlparse(db_url)
        q = parse_qs(u.query)
        if "sslmode" not in q:
            q["sslmode"] = ["require"]
            new_query = urlencode(q, doseq=True)
            db_url = urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception:
        # if parsing fails, just use the original
        pass

    return db_url


def db_connect():
    return psycopg2.connect(get_database_url(), cursor_factory=RealDictCursor)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    """
    IMPORTANT:
    - Does NOT delete data.
    - Creates tables if they don't exist.
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS {table_name} (
        id BIGSERIAL PRIMARY KEY,
        phrase TEXT NOT NULL,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        last_reviewed_at TIMESTAMPTZ NULL,
        next_review_at TIMESTAMPTZ NULL,

        repetitions INT NOT NULL DEFAULT 0,
        lapses INT NOT NULL DEFAULT 0,

        stability DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        difficulty DOUBLE PRECISION NOT NULL DEFAULT 0.0
    );
    """

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
                table = f"public.phrases_{lang}"
                idx = f"uq_phrases_{lang}_phrase"
                cur.execute(create_table_sql.format(table_name=table))
                cur.execute(create_unique_sql.format(index_name=idx, table_name=table))
        conn.commit()


def db_status():
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db, current_schema() AS schema;")
            meta = cur.fetchone()

            cur.execute("""
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename;
            """)
            tables = [r["tablename"] for r in cur.fetchall()]

            return {
                "db": meta["db"],
                "schema": meta["schema"],
                "tables": tables
            }


# =========================
# FastAPI lifespan (more reliable than startup decorator)
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Starting up: running init_db() ...")
        init_db()
        logger.info("init_db() done.")
    except Exception as e:
        logger.exception("init_db() failed: %s", str(e))
        # do NOT crash the service; we want to see status endpoint
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Language Reminder Server",
    version="1.0.0",
    lifespan=lifespan
)


# =========================
# API models
# =========================
Lang = Literal["en", "es"]


class IngestPayload(BaseModel):
    lang: Lang = Field(..., description="en or es")
    phrase: str = Field(..., min_length=1, description="Foreign sentence only (no translation).")


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
    return {"ok": True, "service": "language-reminder-server", "time": utcnow().isoformat()}


@app.get("/db/status")
def db_status_route():
    try:
        return {"ok": True, **db_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB status failed: {str(e)}")


@app.post("/db/init")
def db_init_route():
    try:
        init_db()
        return {"ok": True, "message": "init_db executed", **db_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB init failed: {str(e)}")


@app.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestPayload):
    table = f"public.phrases_{payload.lang}"

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
            return IngestResponse(ok=True, inserted=True, table=table, id=int(row["id"]), message="Inserted.")

        return IngestResponse(ok=True, inserted=False, table=table, id=None, message="Already exists (duplicate).")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/telegram/webhook")
def telegram_webhook():
    return {"ok": True}


@app.get("/")
def root():
    return {"service": "language-reminder-server", "endpoints": ["/health", "/db/status", "/db/init", "/ingest", "/telegram/webhook"]}
