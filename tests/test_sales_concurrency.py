"""NEG-05 and NEG-17: BE1 confirmation and collection races."""

import threading

from backend import sales
from backend.db import connect, write_tx
from backend.errors import DomainError
from tests.test_sales import create_order, issued_quote


def run_race(db_path, actions):
    start = threading.Barrier(len(actions))
    results = [None] * len(actions)

    def run(index, action):
        connection = connect(db_path)
        try:
            start.wait()
            with write_tx(connection):
                results[index] = action(connection)
        except DomainError as error:
            results[index] = error
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, args=(index, action))
        for index, action in enumerate(actions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    return results


def test_only_one_concurrent_payment_can_consume_remaining_balance(
    db_path, conn, clock
):
    _, _, order = create_order(conn, clock())

    def record_payment(index):
        return lambda connection: sales.record_payment(
            connection,
            order["code"],
            amount_vnd=600_000,
            occurred_at=clock(),
            method="CASH",
            reference=None,
            note=f"Thu bởi nhân viên {index}",
            actor=f"fin{index}",
            now=clock(),
        )

    results = run_race(db_path, [record_payment(1), record_payment(2)])
    successful = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, DomainError)]

    assert len(successful) == 1, results
    assert len(rejected) == 1, results
    assert rejected[0].code == "PAYMENT_EXCEEDS_BALANCE"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM payments WHERE order_code=? AND reversal_of IS NULL",
            (order["code"],),
        ).fetchone()[0]
        == 1
    )
    assert sales.order_balance(conn, order["code"])["balance_vnd"] == 0


def test_only_one_concurrent_quote_acceptance_creates_an_order(db_path, conn, clock):
    _, quote = issued_quote(conn, clock())

    def accept(actor):
        return lambda connection: sales.respond_quote_and_create_order(
            connection,
            quote["code"],
            accepted=True,
            channel="PHONE",
            content="Khách xác nhận báo giá.",
            evidence=[],
            payment_due_date="2026-12-10",
            actor=actor,
            now=clock(),
        )

    results = run_race(db_path, [accept("sal1"), accept("sal2")])
    successful = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, DomainError)]

    assert len(successful) == 1, results
    assert len(rejected) == 1, results
    assert rejected[0].code == "INVALID_STATE"
    assert conn.execute("SELECT COUNT(*) FROM service_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM transport_trips").fetchone()[0] == 1


def test_only_one_concurrent_reversal_is_created(db_path, conn, clock):
    _, _, order = create_order(conn, clock())
    with write_tx(conn):
        payment = sales.record_payment(
            conn,
            order["code"],
            amount_vnd=25_000,
            occurred_at=clock(),
            method="CASH",
            reference=None,
            note="Khoản thu cần đảo.",
            actor="fin1",
            now=clock(),
        )["payment"]

    def reverse(actor):
        return lambda connection: sales.reverse_payment(
            connection,
            payment["code"],
            reason="Đính chính khoản thu.",
            actor=actor,
            now=clock(),
        )

    results = run_race(db_path, [reverse("fin1"), reverse("fin2")])
    successful = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, DomainError)]

    assert len(successful) == 1, results
    assert len(rejected) == 1, results
    assert conn.execute(
        "SELECT COUNT(*) FROM payments WHERE reversal_of=?", (payment["code"],)
    ).fetchone()[0] == 1
    assert sales.order_balance(conn, order["code"])["balance_vnd"] == 600_000


def test_concurrent_order_note_edits_reject_stale_version(db_path, conn, clock):
    _, _, order = create_order(conn, clock())

    def edit(actor, note):
        return lambda connection: sales.update_order_contact_note(
            connection,
            order["code"],
            contact_note=note,
            version=order["version"],
            actor=actor,
            now=clock(),
        )

    results = run_race(
        db_path,
        [edit("sal1", "Gọi trước 9 giờ."), edit("sal2", "Gọi sau 9 giờ.")],
    )
    successful = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, DomainError)]

    assert len(successful) == 1, results
    assert len(rejected) == 1, results
    assert rejected[0].code == "VERSION_CONFLICT"
