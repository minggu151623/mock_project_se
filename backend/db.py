"""Kết nối SQLite và transaction ghi."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str) -> sqlite3.Connection:
    """Mở một kết nối. Mỗi request/luồng dùng kết nối riêng."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def write_tx(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE lấy khóa ghi ngay từ đầu.

    SQLite chỉ cho một người ghi tại một thời điểm, nên bước "kiểm tra trùng rồi ghi"
    bên trong transaction này là nguyên tử: người đến sau phải chờ và sẽ thấy dữ liệu
    người đến trước đã ghi.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def require_tx(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("Hàm này phải chạy bên trong write_tx()")


def next_code(conn: sqlite3.Connection, prefix: str, width: int = 6) -> str:
    require_tx(conn)
    row = conn.execute(
        "INSERT INTO code_sequences(prefix, last_value) VALUES (?, 1) "
        "ON CONFLICT(prefix) DO UPDATE SET last_value = last_value + 1 "
        "RETURNING last_value",
        (prefix,),
    ).fetchone()
    return f"{prefix}-{row[0]:0{width}d}"
