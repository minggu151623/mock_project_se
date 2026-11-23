"""Kết nối SQLite và transaction ghi."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str) -> sqlite3.Connection:
    """Mở một kết nối. Mỗi request/luồng dùng kết nối riêng."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path, isolation_level=None, timeout=10, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply the idempotent baseline schema and later versioned SQL migrations.

    Existing development databases are upgraded safely: the baseline uses
    ``CREATE ... IF NOT EXISTS`` statements, so it fills missing objects without
    deleting existing data. Future changes belong in migrations/0002_*.sql and
    later files; each migration is recorded only after its SQL succeeds.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    if 1 not in applied:
        baseline = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + baseline
            + "\nINSERT INTO schema_migrations(version,name,applied_at) "
            "VALUES(1,'initial_schema',strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
            "COMMIT;"
        )

    migrations_dir = Path(__file__).with_name("migrations")
    for migration_path in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(migration_path.name.split("_", maxsplit=1)[0])
        if version in applied:
            continue
        migration_sql = migration_path.read_text(encoding="utf-8")
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + migration_sql
            + f"\nINSERT INTO schema_migrations(version,name,applied_at) "
            f"VALUES({version},'{migration_path.stem}',"
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'));\nCOMMIT;"
        )
        applied.add(version)


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
