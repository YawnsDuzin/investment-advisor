"""screener /run 엔드포인트 SQL 생성 단위 테스트.

psycopg2 mock 환경 — cursor.execute(sql, params) 호출 인자에 대한 assertion 위주.
"""
from contextlib import contextmanager


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeConn:
    def __init__(self, rows=None):
        self._cur = _FakeCursor(rows)

    @property
    def executed(self):
        return self._cur.executed

    def cursor(self, **kw):
        return self._cur


@contextmanager
def _override_db(rows=None):
    from api.main import app
    from api.deps import get_db_conn
    fake = _FakeConn(rows)
    app.dependency_overrides[get_db_conn] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_db_conn, None)


def _last_sql(fake):
    sql, params = fake.executed[-1]
    return sql, list(params)


def test_run_returns_new_metric_columns_when_ohlcv_filter_used():
    """volume_ratio_min 같은 OHLCV 필터 사용 시 SELECT 절에 신규 메트릭이 모두 포함."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={"volume_ratio_min": 1.0})
        assert r.status_code == 200
        sql, _ = _last_sql(fake)
        for col in ("r1m", "r3m", "r6m", "ytd",
                    "ma20", "ma60", "ma200",
                    "drawdown_60d_pct", "ma200_proximity",
                    "sparkline_60d"):
            assert col in sql, f"SELECT 절에 {col} 누락"


def test_run_drops_sparkline_when_include_sparkline_false():
    """include_sparkline=False 면 응답 row 에서 sparkline_60d 제거."""
    from fastapi.testclient import TestClient
    from api.main import app
    rows = [
        {
            "ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple",
            "sparkline_60d": [180.0, 181.5, 182.0],
            "r1m": 5.2,
        }
    ]
    with _override_db(rows=rows):
        client = TestClient(app)
        r = client.post("/api/screener/run",
                        json={"volume_ratio_min": 1.0, "include_sparkline": False})
        assert r.status_code == 200
        data = r.json()
        assert len(data["rows"]) == 1
        assert "sparkline_60d" not in data["rows"][0]


def test_run_keeps_sparkline_when_include_sparkline_true():
    from fastapi.testclient import TestClient
    from api.main import app
    rows = [
        {
            "ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple",
            "sparkline_60d": [180.0, 181.5, 182.0],
            "r1m": 5.2,
        }
    ]
    with _override_db(rows=rows):
        client = TestClient(app)
        r = client.post("/api/screener/run",
                        json={"volume_ratio_min": 1.0, "include_sparkline": True})
        assert r.status_code == 200
        data = r.json()
        assert "sparkline_60d" in data["rows"][0]
