import os
from flask import Flask, request, jsonify
import psycopg2
import psycopg2.extras

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "user-service"}), 200


@app.route("/", methods=["GET"])
def list_users():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, email, created_at FROM users ORDER BY id;")
            rows = cur.fetchall()
        return jsonify(rows), 200
    finally:
        conn.close()


@app.route("/", methods=["POST"])
def create_user():
    data = request.get_json(force=True)
    name, email = data.get("name"), data.get("email")
    if not name or not email:
        return jsonify({"error": "name and email are required"}), 400

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id, name, email, created_at;",
                (name, email),
            )
            user = cur.fetchone()
            conn.commit()
        return jsonify(user), 201
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "email already exists"}), 409
    finally:
        conn.close()


@app.get("/<int:user_id>")
def get_user(user_id):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, email, created_at FROM users WHERE id = %s;", (user_id,))
            user = cur.fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404
        return jsonify(user), 200
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
