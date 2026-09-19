"""General products CRUD for the Tauri desktop API."""
from __future__ import annotations

from typing import Any


def list_general_products(conn, search: str = "") -> dict[str, Any]:
    from core.general_product_service import list_products

    return {"ok": True, "products": list_products(conn, search)}


def save_general_product(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.general_product_service import save_product

    try:
        pid = save_product(
            conn,
            str(body.get("name") or ""),
            float(body.get("rate") or 0),
            float(body.get("mrp") or 0),
            product_id=body.get("id"),
        )
        return {"ok": True, "id": int(pid)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def delete_general_product(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.general_product_service import delete_product

    try:
        pid = int(body.get("id") or 0)
        delete_product(conn, pid)
        return {"ok": True, "id": pid}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
