"""CRUD for general store items (rates only — no stock or sales linkage)."""
from datetime import datetime


def list_products(conn, search: str = ''):
    cur = conn.cursor()
    q = (search or '').strip()
    if q:
        like = f'%{q}%'
        cur.execute(
            """
            SELECT id, name, COALESCE(rate, 0), COALESCE(mrp, 0)
            FROM general_products
            WHERE name LIKE ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE
            """,
            (like,),
        )
    else:
        cur.execute(
            """
            SELECT id, name, COALESCE(rate, 0), COALESCE(mrp, 0)
            FROM general_products
            ORDER BY name COLLATE NOCASE
            """
        )
    return [
        {'id': r[0], 'name': r[1] or '', 'rate': float(r[2] or 0), 'mrp': float(r[3] or 0)}
        for r in cur.fetchall()
    ]


def save_product(conn, name: str, rate: float, mrp: float, product_id=None):
    name = (name or '').strip()
    if not name:
        raise ValueError('Product name is required.')
    rate = round(float(rate or 0), 2)
    mrp = round(float(mrp or 0), 2)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.cursor()

    if product_id:
        cur.execute(
            """
            UPDATE general_products
            SET name=?, rate=?, mrp=?, updated_at=?
            WHERE id=?
            """,
            (name, rate, mrp, now, int(product_id)),
        )
        if cur.rowcount == 0:
            raise ValueError('Product not found.')
        conn.commit()
        return int(product_id)

    cur.execute("SELECT id FROM general_products WHERE name=? COLLATE NOCASE", (name,))
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE general_products
            SET rate=?, mrp=?, updated_at=?
            WHERE id=?
            """,
            (rate, mrp, now, row[0]),
        )
        conn.commit()
        return int(row[0])

    cur.execute(
        """
        INSERT INTO general_products (name, rate, mrp, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, rate, mrp, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def delete_product(conn, product_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM general_products WHERE id=?", (int(product_id),))
    if cur.rowcount == 0:
        raise ValueError('Product not found.')
    conn.commit()
