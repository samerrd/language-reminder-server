import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import psycopg2

# ======================
# App
# ======================
app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 8080))


# ======================
# DB INIT
# ======================
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # جدول الجمل
    cur.execute("""
    CREATE TABLE IF NOT EXISTS phrases (
        id SERIAL PRIMARY KEY,
        text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # جدول المراجعات (SRS)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id SERIAL PRIMARY KEY,
        phrase_id INTEGER REFERENCES phrases(id) ON DELETE CASCADE,
        review_state TEXT,
        review_date TIMESTAMP,
        next_review TIMESTAMP,
        interval_days INTEGER DEFAULT 0,
        ease REAL DEFAULT 2.5
    );
    """)

    conn.commit()
    cur.close()
    conn.close()


# ======================
# ROUTES
# ======================
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


@app.route("/ingest", methods=["POST"])
def ingest():
    data = request.get_json()

    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    # إدخال الجملة
    cur.execute(
        "INSERT INTO phrases (text) VALUES (%s) RETURNING id;",
        (text,)
    )
    phrase_id = cur.fetchone()[0]

    # إدخال سجل مراجعة ابتدائي
    now = datetime.utcnow()
    next_review = now + timedelta(days=1)

    cur.execute("""
        INSERT INTO reviews
        (phrase_id, review_state, review_date, next_review)
        VALUES (%s, %s, %s, %s);
    """, (phrase_id, "new", now, next_review))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "status": "saved",
        "phrase_id": phrase_id
    }), 200


@app.route("/phrases", methods=["GET"])
def list_phrases():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT p.id, p.text, p.created_at, r.next_review
        FROM phrases p
        JOIN reviews r ON r.phrase_id = p.id
        ORDER BY p.id DESC;
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(rows), 200


# ======================
# STARTUP
# ======================
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT)
