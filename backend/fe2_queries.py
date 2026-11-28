"""Read models for FE2 screens; reuse BE1 balances and BE2 scheduling rules."""

from . import assignments, booking, sales


def commercial_summary(conn, kind, code):
    lookup, service_type = {
        "trips": (assignments.trip_row, "TRANSPORT"),
        "rentals": (assignments.rental_row, "RENTAL"),
    }[kind]
    operation = lookup(conn, code)
    order = conn.execute(
        "SELECT code FROM service_orders WHERE code=? AND service_type=? AND operational_code=?",
        (operation["order_code"], service_type, code),
    ).fetchone()
    # The original BE2 demo data predates the BE1 orders; do not invent a balance.
    if order is None:
        return {"available": False, "order_code": operation["order_code"], "balance": None,
                "charges": [], "pending_charges": None}
    charges = [dict(row) for row in conn.execute(
        "SELECT code, amount_vnd, reason, status, created_at, created_by, "
        "decision_note, decided_at, decided_by FROM charges WHERE order_code=? ORDER BY created_at,id",
        (order["code"],),
    )]
    return {"available": True, "order_code": order["code"],
            "balance": sales.order_balance(conn, order["code"]), "charges": charges,
            "pending_charges": sum(charge["status"] == "PENDING" for charge in charges)}


def schedule(conn, **filters):
    result = booking.list_schedule(conn, **filters)
    # At most two batched lookups, independent of page size. Never join charge/payment
    # rows here: that would multiply bookings and break pagination/counts.
    for service_type, table in (("TRANSPORT", "transport_trips"), ("RENTAL", "rental_records")):
        items = [item for item in result["items"] if item["source_type"] == service_type]
        if not items:
            continue
        codes = [item["source_code"] for item in items]
        placeholders = ",".join("?" for _ in codes)
        orders = {row["code"]: row["order_code"] for row in conn.execute(
            f"SELECT code,order_code FROM {table} WHERE code IN ({placeholders})", codes)}
        for item in items:
            item["order_code"] = orders.get(item["source_code"])
    return result
