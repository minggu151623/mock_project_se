"""Seed BE1 customer, request, quote, order, and payment sample records."""

import sys
from datetime import timedelta

from . import sales
from .db import connect, init_db, write_tx
from .timeutil import SYSTEM_TZ, system_now

ACTOR = "seed-be1"


def seed(db_path: str) -> None:
    conn = connect(db_path)
    try:
        init_db(conn)
        if conn.execute("SELECT COUNT(*) FROM service_orders").fetchone()[0]:
            print("BE1 demo order already exists; seed was skipped.")
            return

        now = system_now()
        local_today = now.astimezone(SYSTEM_TZ).date()
        service_data = {
            "pickup_address": "Cảng Cát Lái",
            "delivery_address": "Khu công nghiệp Tân Bình",
            "cargo_description": "Linh kiện đóng kiện",
            "cargo_weight_kg": 1200,
            "planned_pickup_at": (now + timedelta(days=1))
            .astimezone(SYSTEM_TZ)
            .isoformat(),
            "committed_end": (now + timedelta(days=1, hours=4))
            .astimezone(SYSTEM_TZ)
            .isoformat(),
        }

        with write_tx(conn):
            customer = sales.create_customer(
                conn,
                customer_type="BUSINESS",
                name="Công ty mẫu An Phát",
                contact_name="Nguyễn Minh An",
                phone="0909000001",
                email="anphat@example.test",
                address="Quận 1, TP. Hồ Chí Minh",
                actor=ACTOR,
                now=now,
            )
            request = sales.create_request(
                conn,
                service_type="TRANSPORT",
                source="INTERNAL",
                contact={
                    "name": customer["name"],
                    "contact_name": customer["contact_name"],
                    "phone": customer["phone"],
                    "email": customer["email"],
                },
                service_data=service_data,
                actor=ACTOR,
                now=now,
            )
            request = sales.update_request(
                conn,
                request["code"],
                version=request["version"],
                customer_code=customer["code"],
                actor=ACTOR,
                now=now,
            )
            quote = sales.create_quote(
                conn,
                request_code=request["code"],
                valid_until=(local_today + timedelta(days=7)).isoformat(),
                service_data=service_data,
                terms="Thanh toán trong vòng 14 ngày.",
                lines=[
                    {
                        "description": "Cước vận chuyển",
                        "quantity": 1,
                        "unit_price_vnd": 1_250_000,
                    },
                    {
                        "description": "Phí bốc xếp",
                        "quantity": 1,
                        "unit_price_vnd": 250_000,
                    },
                ],
                actor=ACTOR,
                now=now,
            )
            sales.issue_quote(conn, quote["code"], actor=ACTOR, now=now)
            result = sales.respond_quote_and_create_order(
                conn,
                quote["code"],
                accepted=True,
                channel="IN_PERSON",
                content="Khách xác nhận báo giá mẫu.",
                evidence=[],
                payment_due_date=local_today + timedelta(days=14),
                actor=ACTOR,
                now=now,
            )
            sales.record_payment(
                conn,
                result["order"]["code"],
                amount_vnd=500_000,
                occurred_at=now,
                method="BANK_TRANSFER",
                reference="SEED-BE1-001",
                note="Khoản thanh toán mẫu một phần.",
                actor=ACTOR,
                now=now,
            )

        print(f"BE1 demo records loaded into {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    seed(sys.argv[1] if len(sys.argv) > 1 else "data/dev.sqlite3")
