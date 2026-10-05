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
    return jsonify({"status": "ok", "service": "product-service"}), 200


@app.route("/", methods=["GET"])
def list_products():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, description, price, stock, created_at FROM products ORDER BY id;")
            rows = cur.fetchall()
        return jsonify(rows), 200
    finally:
        conn.close()


@app.route("/", methods=["POST"])
def create_product():
    data = request.get_json(force=True)
    name = data.get("name")
    price = data.get("price")
    description = data.get("description", "")
    stock = data.get("stock", 0)
    if not name or price is None:
        return jsonify({"error": "name and price are required"}), 400

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO products (name, description, price, stock)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id, name, description, price, stock, created_at;""",
                (name, description, price, stock),
            )
            product = cur.fetchone()
            conn.commit()
        return jsonify(product), 201
    finally:
        conn.close()


@app.get("/<int:product_id>")
def get_product(product_id):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, description, price, stock, created_at FROM products WHERE id = %s;",
                (product_id,),
            )
            product = cur.fetchone()
        if not product:
            return jsonify({"error": "product not found"}), 404
        return jsonify(product), 200
    finally:
        conn.close()


@app.post("/<int:product_id>/reserve")
def reserve_stock(product_id):
    """Decrement stock atomically; used internally by order-service."""
    data = request.get_json(force=True)
    quantity = int(data.get("quantity", 1))

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """UPDATE products SET stock = stock - %s
                   WHERE id = %s AND stock >= %s
                   RETURNING id, name, price, stock;""",
                (quantity, product_id, quantity),
            )
            product = cur.fetchone()
            conn.commit()
        if not product:
            return jsonify({"error": "insufficient stock or product not found"}), 409
        return jsonify(product), 200
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
