from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import integrations
from backend.auth import dev_header_provider
from backend.db import connect, init_db
from backend.main import create_app

# 2026-12-01 07:00 giờ Việt Nam.
START = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, value: datetime) -> None:
        self.now = value

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.sqlite3")
    conn = connect(path)
    init_db(conn)
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def reset_hooks():
    integrations.reset()
    yield
    integrations.reset()


@pytest.fixture
def files_dir(tmp_path):
    return str(tmp_path / "files")


@pytest.fixture
def client(db_path, clock, files_dir):
    app = create_app(db_path, files_dir=files_dir, principal_provider=dev_header_provider, clock=clock)
    return TestClient(app)
