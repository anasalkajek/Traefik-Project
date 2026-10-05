import os
import json
import requests
from flask import Flask, request, jsonify
import psycopg2
import psycopg2.extras
import redis

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://user-service:5000")
PRODUCT_SERVICE_URL = os.environ.get("PRODUCT_SERVICE_URL", "http://product-service:5001")

CACHE_TTL_SECONDS = 30

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "order-service"}), 200


@app.route("/", methods=["POST"])
def create_order():
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    product_id = data.get("product_id")
    quantity = int(data.get("quantity", 1))

    if not user_id or not product_id or quantity < 1:
        return jsonify({"error": "user_id, product_id and a positive quantity are required"}), 400

    # 1. Validate the user exists (call user-service)
    try:
        user_resp = requests.get(f"{USER_SERVICE_URL}/{user_id}", timeout=5)
    except requests.RequestException as e:
        return jsonify({"error": f"user-service unavailable: {e}"}), 502
    if user_resp.status_code != 200:
        return jsonify({"error": "user not found"}), 404

    # 2. Reserve stock on product-service (also validates product exists / has stock)
    try:
        reserve_resp = requests.post(
            f"{PRODUCT_SERVICE_URL}/{product_id}/reserve",
            json={"quantity": quantity},
            timeout=5,
        )
    except requests.RequestException as e:
        return jsonify({"error": f"product-service unavailable: {e}"}), 502
    if reserve_resp.status_code != 200:
        return jsonify(reserve_resp.json()), reserve_resp.status_code

    product = reserve_resp.json()
    total_price = float(product["price"]) * quantity

    # 3. Persist the order
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO orders (user_id, product_id, quantity, total_price, status)
                   VALUES (%s, %s, %s, %s, 'CONFIRMED')
                   RETURNING id, user_id, product_id, quantity, total_price, status, created_at;""",
                (user_id, product_id, quantity, total_price),
            )
            order = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    # 4. Cache the order in Redis so repeat reads are fast
    order_serializable = dict(order)
    order_serializable["created_at"] = str(order_serializable["created_at"])
    order_serializable["total_price"] = float(order_serializable["total_price"])
    r.setex(f"order:{order['id']}", CACHE_TTL_SECONDS, json.dumps(order_serializable))
    r.delete("orders:all")  # invalidate the list cache

    return jsonify(order_serializable), 201


@app.route("/", methods=["GET"])
def list_orders():
    cached = r.get("orders:all")
    if cached:
        return jsonify({"source": "cache", "orders": json.loads(cached)}), 200

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_id, product_id, quantity, total_price, status, created_at FROM orders ORDER BY id;"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    orders = []
    for row in rows:
        row = dict(row)
        row["created_at"] = str(row["created_at"])
        row["total_price"] = float(row["total_price"])
        orders.append(row)

    r.setex("orders:all", CACHE_TTL_SECONDS, json.dumps(orders))
    return jsonify({"source": "db", "orders": orders}), 200


@app.get("/<int:order_id>")
def get_order(order_id):
    cache_key = f"order:{order_id}"
    cached = r.get(cache_key)
    if cached:
        return jsonify({"source": "cache", "order": json.loads(cached)}), 200

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_id, product_id, quantity, total_price, status, created_at FROM orders WHERE id = %s;",
                (order_id,),
            )
            order = cur.fetchone()
    finally:
        conn.close()

    if not order:
        return jsonify({"error": "order not found"}), 404

    order = dict(order)
    order["created_at"] = str(order["created_at"])
    order["total_price"] = float(order["total_price"])
    r.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(order))
    return jsonify({"source": "db", "order": order}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port)
