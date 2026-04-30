"""screener /run 엔드포인트 SQL 생성 단위 테스트.

psycopg2 mock 환경 — cursor.execute(sql, params) 호출 인자에 대한 assertion 위주.
"""
from contextlib import contextmanager


class _FakeCursor:
    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeConn:
    def __init__(self, rows=None, one=None):
        self._cur = _FakeCursor(rows, one)

    @property
    def executed(self):
        return self._cur.executed

    def cursor(self, **kw):
        return self._cur


@contextmanager
def _override_db(rows=None, one=None):
    from api.main import app
    from api.deps import get_db_conn
    fake = _FakeConn(rows, one)
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


# ──────────────────────────────────────────────
# Task 4: is_top_pick 뱃지 + top_picks_recent CTE
# ──────────────────────────────────────────────

def test_run_includes_top_picks_recent_cte_and_is_top_pick_flag():
    """결과에 is_top_pick 플래그 + CTE에 top_picks_recent + LEFT JOIN."""
    from fastapi.testclient import TestClient
    from api.main import app
    rows = [
        {"ticker": "005930", "market": "KOSPI", "asset_name": "삼성전자",
         "is_top_pick": True, "r1m": 4.0},
        {"ticker": "000660", "market": "KOSPI", "asset_name": "SK하이닉스",
         "is_top_pick": False, "r1m": 2.0},
    ]
    with _override_db(rows=rows) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={"volume_ratio_min": 1.0})
        assert r.status_code == 200
        sql, _ = _last_sql(fake)
        assert "top_picks_recent" in sql
        assert "is_top_pick" in sql
        assert "daily_top_picks" in sql
        assert "LEFT JOIN top_picks_recent" in sql
        data = r.json()
        assert data["rows"][0]["is_top_pick"] is True
        assert data["rows"][1]["is_top_pick"] is False


def test_run_includes_is_top_pick_even_without_ohlcv_join():
    """OHLCV 필터 없을 때도 is_top_pick 은 항상 반환."""
    from fastapi.testclient import TestClient
    from api.main import app
    rows = [{"ticker": "AAPL", "market": "NASDAQ", "asset_name": "Apple",
             "is_top_pick": False}]
    with _override_db(rows=rows) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/run", json={})
        assert r.status_code == 200
        sql, _ = _last_sql(fake)
        assert "top_picks_recent" in sql
        assert "is_top_pick" in sql


# ──────────────────────────────────────────────
# A3: /api/screener/count — 경량 카운트 엔드포인트
# ──────────────────────────────────────────────

def test_count_returns_n_for_simple_spec():
    """spec 매칭 종목 수만 반환 (rows 없이 경량 SQL)."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(one={"n": 1234}) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/count", json={})
        assert r.status_code == 200
        body = r.json()
        assert body == {"count": 1234}
        sql, _ = _last_sql(fake)
        assert "COUNT(*)" in sql
        # CTE 내부 DISTINCT ON 정렬은 허용. 메인 쿼리에 ORDER BY/LIMIT 없어야 (count = 전 매칭).
        # WHERE 이후 부분에서 ORDER BY/LIMIT 가 없는지 확인
        after_where = sql.upper().rsplit("WHERE", 1)[1]
        assert "ORDER BY" not in after_where
        assert "LIMIT" not in after_where


def test_count_handles_zero_match():
    """매칭 0건이면 count=0."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(one={"n": 0}):
        client = TestClient(app)
        r = client.post("/api/screener/count", json={"max_per": 0.5})
        assert r.status_code == 200
        assert r.json()["count"] == 0


def test_count_with_ohlcv_filter_includes_metrics_cte():
    """OHLCV 필터가 있으면 ohlcv_metrics CTE 가 포함된다."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(one={"n": 50}) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/count", json={"max_vol60_pct": 3.0})
        assert r.status_code == 200
        sql, _ = _last_sql(fake)
        # ohlcv_metrics CTE 키 + sparkline_60d 누락 (count 는 불필요)
        assert "ohlcv_metrics" in sql
        assert "sparkline_60d" not in sql
        assert "COUNT(*)" in sql


def test_count_with_foreign_filter_joins_foreign_flow_metrics():
    """외국인 필터 사용 시 foreign_flow_metrics CTE + JOIN."""
    from fastapi.testclient import TestClient
    from api.main import app
    with _override_db(one={"n": 12}) as fake:
        client = TestClient(app)
        r = client.post("/api/screener/count",
                        json={"min_foreign_ownership_pct": 30.0})
        assert r.status_code == 200
        sql, _ = _last_sql(fake)
        assert "foreign_flow_metrics" in sql
        assert "ff.own_latest" in sql


# ──────────────────────────────────────────────
# B4: /api/screener/distribution — 분포 통계 hint
# ──────────────────────────────────────────────

class _MultiCallCursor:
    """metric 별 SQL 호출당 다른 fetchone 응답을 줘야 하는 경우용."""
    def __init__(self, ones):
        self._ones = list(ones)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._ones.pop(0) if self._ones else None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _MultiCallConn:
    def __init__(self, ones):
        self._cur = _MultiCallCursor(ones)

    def cursor(self, **kw):
        return self._cur


def test_distribution_returns_avg_median_per_metric():
    """metrics=per,pbr → metric 별 avg/median/n 반환."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.deps import get_db_conn
    fake = _MultiCallConn([
        {"avg": 14.3, "median": 12.1, "n": 2451},   # per
        {"avg": 1.6, "median": 1.2, "n": 2300},     # pbr
    ])
    app.dependency_overrides[get_db_conn] = lambda: fake
    try:
        client = TestClient(app)
        r = client.get("/api/screener/distribution?metrics=per,pbr")
        assert r.status_code == 200
        body = r.json()
        assert body["metrics"]["per"]["avg"] == 14.3
        assert body["metrics"]["per"]["median"] == 12.1
        assert body["metrics"]["pbr"]["avg"] == 1.6
    finally:
        app.dependency_overrides.pop(get_db_conn, None)


def test_distribution_filters_invalid_metrics():
    """미지정 metric (예: foo) 은 응답에 포함 안 됨."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.deps import get_db_conn
    fake = _MultiCallConn([{"avg": 14.3, "median": 12.1, "n": 100}])
    app.dependency_overrides[get_db_conn] = lambda: fake
    try:
        client = TestClient(app)
        r = client.get("/api/screener/distribution?metrics=per,foo,bar")
        assert r.status_code == 200
        # 1개 SQL 호출만 (per) — foo/bar 는 거부
        assert len(fake._cur.executed) == 1
        assert "per" in fake._cur.executed[0][0]
    finally:
        app.dependency_overrides.pop(get_db_conn, None)


def test_distribution_with_markets_filter_passes_param():
    """markets 파라미터가 SQL params 에 ANY() 로 전달."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.deps import get_db_conn
    fake = _MultiCallConn([{"avg": 18.5, "median": 15.0, "n": 800}])
    app.dependency_overrides[get_db_conn] = lambda: fake
    try:
        client = TestClient(app)
        r = client.get("/api/screener/distribution?metrics=per&markets=KOSPI,KOSDAQ")
        assert r.status_code == 200
        sql, params = fake._cur.executed[0]
        assert "UPPER(market) = ANY(%s)" in sql
        assert ["KOSPI", "KOSDAQ"] in params
    finally:
        app.dependency_overrides.pop(get_db_conn, None)
