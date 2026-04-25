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


# ──────────────────────────────────────────────
# Task 3 신규 필터 테스트
# ──────────────────────────────────────────────

def test_run_q_filter_generates_ilike_clause_with_three_columns():
    """q 가 있으면 ticker/asset_name/asset_name_en 3개 컬럼 ILIKE."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={"q": "삼성"})
        assert r.status_code == 200
        sql, params = _last_sql(fake)
        assert "ILIKE" in sql.upper()
        assert "u.ticker" in sql and "u.asset_name" in sql and "u.asset_name_en" in sql
        assert params.count("%삼성%") == 3


def test_run_q_filter_short_keyword_rejected():
    """q 가 2자 미만이면 SQL 에 안 들어감 (풀스캔 가드)."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={"q": "a"})
        assert r.status_code == 200
        sql, params = _last_sql(fake)
        assert "%a%" not in params


def test_run_market_cap_buckets_filter():
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run",
                        json={"market_cap_buckets": ["large", "mid"]})
        assert r.status_code == 200
        sql, params = _last_sql(fake)
        assert "u.market_cap_bucket" in sql
        assert ["large", "mid"] in params


def test_run_return_ranges_filter_all_periods():
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        spec = {
            "return_ranges": {
                "1m": {"min": -10, "max": 50},
                "3m": {"min": 0},
                "6m": {"max": 30},
                "1y": {"min": -20, "max": 100},
                "ytd": {"min": 5},
            }
        }
        r = client.post("/api/screener/run", json=spec)
        assert r.status_code == 200
        sql, params = _last_sql(fake)
        for col in ("m.r1m", "m.r3m", "m.r6m", "m.r1y", "m.ytd"):
            assert col in sql
        for v in (-10, 50, 0, 30, -20, 100, 5):
            assert float(v) in [float(p) if isinstance(p, (int, float)) else None for p in params]


def test_run_max_drawdown_60d_filter():
    """사용자 입력은 절대값 양수, SQL 에선 부호 뒤집어 비교."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={"max_drawdown_60d_pct": 15})
        assert r.status_code == 200
        sql, params = _last_sql(fake)
        assert "drawdown_60d_pct" in sql
        assert -15.0 in [float(p) if isinstance(p, (int, float)) else None for p in params]


def test_run_ma200_proximity_filter():
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(rows=[]) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={"ma200_proximity_min": 0.95})
        assert r.status_code == 200
        sql, params = _last_sql(fake)
        assert "m.ma200_proximity" in sql
        assert 0.95 in [float(p) if isinstance(p, (int, float)) else None for p in params]
