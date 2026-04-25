"""screener /sectors 엔드포인트 단위 테스트."""
from contextlib import contextmanager
from unittest.mock import MagicMock


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeConn:
    def __init__(self, rows):
        self._cur = _FakeCursor(rows)

    def cursor(self, **kw):
        return self._cur


@contextmanager
def _override_db(rows):
    from api.main import app
    from api.deps import get_db_conn
    fake = _FakeConn(rows)
    app.dependency_overrides[get_db_conn] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_db_conn, None)


def test_sectors_returns_distribution_with_labels():
    from fastapi.testclient import TestClient
    from api.main import app

    rows = [
        {"sector_norm": "semiconductors", "count": 47},
        {"sector_norm": "energy", "count": 23},
        {"sector_norm": "unknown_sector_x", "count": 5},
    ]
    with _override_db(rows):
        client = TestClient(app)
        r = client.get("/api/screener/sectors")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 3
        labels = {s["key"]: s["label"] for s in data["sectors"]}
        assert labels["semiconductors"] == "반도체"
        assert labels["energy"] == "에너지"
        assert labels["unknown_sector_x"] == "unknown_sector_x"
        assert "max-age=1800" in r.headers.get("cache-control", "")
