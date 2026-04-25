# Screener UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finviz-스타일 5탭 필터 + 3개 View 프리셋 + 60일 스파크라인 + Top Picks 식별 뱃지로 Screener UI 풀 리뉴얼하고, "Stock Picks" → "AI Top Picks" 라벨 변경 + 양방향 cross-link 추가.

**Architecture:**
- Backend(`api/routes/screener.py`)에서 `stock_universe_ohlcv` CTE 확장(r1m/r3m/r6m/ytd/ma20/60/200/drawdown_60d/sparkline_60d) + `top_picks_recent` JOIN으로 `is_top_pick` 플래그 추가. 신규 `/sectors` 엔드포인트로 sector 분포 제공.
- Frontend(`api/templates/screener.html`)는 5탭 필터 UI + View 프리셋(Overview/Performance/Technical/Custom) + 컬럼 토글 + URL 쿼리스트링 prefill. 새 CSS 모듈 `14_screener.css` 추가.
- 라벨 변경은 표기만 — URL/`active_page` 키는 보존하여 라우트 안정성 유지.

**Tech Stack:** FastAPI · psycopg2 · Jinja2 · Vanilla JS (모듈 IIFE) · CSS 모듈(`tools/build_css.py`로 빌드) · pytest

**Spec:** [docs/superpowers/specs/2026-04-25-screener-redesign-design.md](../specs/2026-04-25-screener-redesign-design.md)

---

## File Structure

**Modified:**
- `api/routes/screener.py` — `/run` CTE/WHERE/ORDER BY 확장, `/sectors` 신설, sector 라벨 const, `top_picks_recent` CTE
- `api/templates/screener.html` — 풀 리뉴얼 (탭·View·스파크라인·IIFE)
- `api/templates/base.html` — 사이드바 라벨 1자리
- `api/templates/watchlist.html` — CTA/empty state 라벨 3곳
- `api/templates/proposals.html` — 페이지 헤더 라벨 + "비슷한 종목" CTA
- `api/templates/dashboard.html` (있으면) — 라벨 텍스트 점검
- `api/templates/partials/dashboard/_top_picks.html` (있으면) — 라벨 텍스트 점검
- `api/templates/_macros/proposal.html` — "비슷한 종목" CTA 매크로 (선택)

**Created:**
- `api/static/css/src/14_screener.css` — 탭/뷰/sticky 테이블/스파크라인/뱃지/모바일
- `tests/test_screener_run.py` — `/run` 신규 spec 필드 SQL 생성 단위 테스트
- `tests/test_screener_sectors.py` — `/sectors` 응답 형태 + 캐시 헤더 테스트

**Out of scope:**
- DB 스키마 변경 없음
- `analyzer/screener.py` 변경 없음 (Stage 1-B 스크리너 — 별개)

---

## Test Conventions

- `tests/conftest.py` 가 `psycopg2` / `feedparser` / `claude_agent_sdk` 를 mock — DB 연결 없이 단위 테스트 가능.
- 패턴: `_FakeCursor` / `_FakeConn` 로컬 클래스로 `cursor.execute(sql, params)` 호출 인자를 캡처해 SQL 문자열·파라미터에 대한 assertion 수행.
- FastAPI 라우트는 `app.dependency_overrides[get_db_conn] = lambda: fake_conn` 으로 우회.
- 인증이 필요한 엔드포인트는 `app.dependency_overrides[get_current_user] = lambda: fake_user` 추가.

각 테스트의 첫 fixture는 `_FakeConn` + dependency override 로 시작한다.

---

### Task 1: `/api/screener/sectors` 엔드포인트 + sector 라벨 사전

**Files:**
- Modify: `api/routes/screener.py`
- Test: `tests/test_screener_sectors.py` (신규)

- [ ] **Step 1.1: 실패 테스트 작성**

```python
# tests/test_screener_sectors.py
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
        # sector 라벨 매핑: 알려진 키는 한글 라벨, 미등록 키는 키 그대로 fallback
        labels = {s["key"]: s["label"] for s in data["sectors"]}
        assert labels["semiconductors"] == "반도체"
        assert labels["energy"] == "에너지"
        assert labels["unknown_sector_x"] == "unknown_sector_x"
        # 캐시 헤더
        assert "max-age=1800" in r.headers.get("cache-control", "")
```

- [ ] **Step 1.2: 테스트 실패 확인**

Run: `pytest tests/test_screener_sectors.py -v`
Expected: FAIL — `/api/screener/sectors` 라우트 없음 (404).

- [ ] **Step 1.3: sector 라벨 사전 추가**

`api/routes/screener.py` 상단 import 직후에 추가:

```python
# sector_norm → 한국어 라벨 매핑. 누락 키는 fallback = key 그대로
# (analyzer/screener.py 의 sector_norm 28버킷과 일관 유지)
SECTOR_LABELS: dict[str, str] = {
    "semiconductors": "반도체",
    "energy": "에너지",
    "financials": "금융",
    "healthcare": "헬스케어",
    "biotech": "바이오",
    "internet": "인터넷",
    "software": "소프트웨어",
    "hardware": "하드웨어",
    "ai": "AI",
    "cloud": "클라우드",
    "ev": "전기차",
    "battery": "배터리",
    "auto": "자동차",
    "consumer": "소비재",
    "retail": "유통",
    "media": "미디어",
    "telecom": "통신",
    "utilities": "유틸리티",
    "real_estate": "부동산",
    "materials": "소재",
    "chemicals": "화학",
    "steel": "철강",
    "shipbuilding": "조선",
    "aerospace": "항공우주",
    "defense": "방산",
    "construction": "건설",
    "logistics": "물류",
    "robotics": "로봇",
}
```

(누락 sector_norm 은 시간 지나면서 채움. 모르는 키는 자동으로 key 그대로 fallback.)

- [ ] **Step 1.4: `/sectors` 엔드포인트 구현**

`api/routes/screener.py` — 라우터 정의 직후, 기존 `/run` 위에 추가:

```python
from fastapi import Response


@router.get("/sectors")
def list_sectors(response: Response, conn = Depends(get_db_conn)):
    """sector_norm 분포 (드롭다운 옵션). 30분 캐시."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT sector_norm, COUNT(*) AS count
            FROM stock_universe
            WHERE listed = TRUE AND has_preferred = FALSE
              AND sector_norm IS NOT NULL AND sector_norm <> ''
            GROUP BY sector_norm
            ORDER BY count DESC
            """
        )
        rows = cur.fetchall()
    sectors = [
        {
            "key": r["sector_norm"],
            "label": SECTOR_LABELS.get(r["sector_norm"], r["sector_norm"]),
            "count": int(r["count"]),
        }
        for r in rows
    ]
    response.headers["Cache-Control"] = "public, max-age=1800"
    return {"count": len(sectors), "sectors": sectors}
```

- [ ] **Step 1.5: 테스트 통과 확인**

Run: `pytest tests/test_screener_sectors.py -v`
Expected: PASS.

- [ ] **Step 1.6: 커밋**

```bash
git add api/routes/screener.py tests/test_screener_sectors.py
git commit -m "feat(screener): /api/screener/sectors 엔드포인트 + 라벨 사전"
```

---

### Task 2: `/run` SQL CTE 확장 — 신규 OHLCV 메트릭

기존 `ohlcv_metrics` CTE 가 `close_latest / high_252d / avg_daily_value / vol60_pct / volume_ratio / r1y` 만 산출. 여기에 `r1m / r3m / r6m / ytd / ma20 / ma60 / ma200 / high_60d / low_60d / low_252d / drawdown_60d_pct / ma200_proximity / sparkline_60d` 추가.

**Files:**
- Modify: `api/routes/screener.py` — `/run` 의 CTE 부분
- Test: `tests/test_screener_run.py` (신규)

- [ ] **Step 2.1: 테스트 파일 base 작성 + 메트릭 컬럼 존재 검증 테스트**

```python
# tests/test_screener_run.py
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
        # 신규 메트릭 컬럼이 SELECT 절에 있어야 함
        for col in ("r1m", "r3m", "r6m", "ytd",
                    "ma20", "ma60", "ma200",
                    "drawdown_60d_pct", "ma200_proximity",
                    "sparkline_60d"):
            assert col in sql, f"SELECT 절에 {col} 누락"
```

- [ ] **Step 2.2: 테스트 실패 확인**

Run: `pytest tests/test_screener_run.py::test_run_returns_new_metric_columns_when_ohlcv_filter_used -v`
Expected: FAIL — 신규 컬럼 미포함.

- [ ] **Step 2.3: CTE 확장 구현**

`api/routes/screener.py` 의 `if join_ohlcv:` 블록 안 `cte` 와 `sql` 을 다음으로 교체:

```python
if join_ohlcv:
    cte = """
    WITH ranked AS (
        SELECT ticker, UPPER(market) AS market, trade_date,
               close::float AS close, volume,
               change_pct::float AS change_pct,
               ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                  ORDER BY trade_date DESC) AS rn
        FROM stock_universe_ohlcv
        WHERE trade_date >= CURRENT_DATE - 400
    ),
    metrics AS (
        SELECT ticker, market,
            MAX(CASE WHEN rn=1   THEN close END) AS close_latest,
            MAX(CASE WHEN rn=21  THEN close END) AS close_1m,
            MAX(CASE WHEN rn=63  THEN close END) AS close_3m,
            MAX(CASE WHEN rn=126 THEN close END) AS close_6m,
            MAX(CASE WHEN rn=252 THEN close END) AS close_1y,
            MAX(close) FILTER (WHERE rn<=252) AS high_252d,
            MIN(close) FILTER (WHERE rn<=252) AS low_252d,
            MAX(close) FILTER (WHERE rn<=60)  AS high_60d,
            MIN(close) FILTER (WHERE rn<=60)  AS low_60d,
            AVG(close)  FILTER (WHERE rn<=200) AS ma200,
            AVG(close)  FILTER (WHERE rn<=60)  AS ma60,
            AVG(close)  FILTER (WHERE rn<=20)  AS ma20,
            AVG(close*volume) FILTER (WHERE rn<=60) AS avg_daily_value,
            STDDEV(LEAST(GREATEST(change_pct,-50),50)) FILTER (WHERE rn<=60) AS vol60_pct,
            AVG(volume) FILTER (WHERE rn<=20) AS v20,
            AVG(volume) FILTER (WHERE rn<=60) AS v60,
            ARRAY_AGG(close ORDER BY trade_date DESC) FILTER (WHERE rn<=60) AS sparkline_60d
        FROM ranked GROUP BY ticker, market
    ),
    ytd_anchor AS (
        SELECT DISTINCT ON (ticker, mkt)
               ticker, mkt AS market, close::float AS close_ytd
        FROM (
            SELECT ticker, UPPER(market) AS mkt, trade_date, close
            FROM stock_universe_ohlcv
            WHERE trade_date <  DATE_TRUNC('year', CURRENT_DATE)
              AND trade_date >= DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '30 days'
        ) t
        ORDER BY ticker, mkt, trade_date DESC
    ),
    ohlcv_metrics AS (
        SELECT m.ticker, m.market, m.close_latest, m.high_252d, m.low_252d,
               m.high_60d, m.low_60d, m.ma20, m.ma60, m.ma200,
               m.avg_daily_value, m.vol60_pct, m.sparkline_60d,
               y.close_ytd,
               (m.close_latest - m.close_1m) / NULLIF(m.close_1m,0) * 100 AS r1m,
               (m.close_latest - m.close_3m) / NULLIF(m.close_3m,0) * 100 AS r3m,
               (m.close_latest - m.close_6m) / NULLIF(m.close_6m,0) * 100 AS r6m,
               (m.close_latest - m.close_1y) / NULLIF(m.close_1y,0) * 100 AS r1y,
               (m.close_latest - y.close_ytd) / NULLIF(y.close_ytd,0) * 100 AS ytd,
               -- 60d 고점 대비 현재 낙폭 (peak-to-current). 음수가 정상.
               (m.close_latest - m.high_60d) / NULLIF(m.high_60d,0) * 100 AS drawdown_60d_pct,
               m.close_latest / NULLIF(m.ma200,0) AS ma200_proximity,
               m.close_latest / NULLIF(m.high_252d,0) AS high_52w_proximity,
               CASE WHEN m.v60>0 THEN m.v20/m.v60 END AS volume_ratio
        FROM metrics m
        LEFT JOIN ytd_anchor y ON y.ticker=m.ticker AND y.market=m.market
    )
    """
    sql = f"""
    {cte}
    SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
           u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
           m.close_latest, m.high_252d, m.low_252d,
           m.high_60d, m.low_60d, m.ma20, m.ma60, m.ma200,
           m.avg_daily_value, m.vol60_pct, m.volume_ratio,
           m.high_52w_proximity, m.ma200_proximity, m.drawdown_60d_pct,
           m.r1m, m.r3m, m.r6m, m.r1y, m.ytd,
           m.sparkline_60d
    FROM stock_universe u
    LEFT JOIN ohlcv_metrics m
      ON UPPER(u.ticker) = UPPER(m.ticker) AND UPPER(u.market) = UPPER(m.market)
    WHERE {where_sql}
    ORDER BY {order_by}
    LIMIT %s
    """
```

- [ ] **Step 2.4: 테스트 통과 확인**

Run: `pytest tests/test_screener_run.py::test_run_returns_new_metric_columns_when_ohlcv_filter_used -v`
Expected: PASS.

- [ ] **Step 2.5: include_sparkline=False 시 sparkline_60d 응답에서 drop 테스트**

`tests/test_screener_run.py` 에 추가:

```python
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
```

- [ ] **Step 2.6: 테스트 실패 확인**

Run: `pytest tests/test_screener_run.py::test_run_drops_sparkline_when_include_sparkline_false -v`
Expected: FAIL — 응답에 sparkline_60d 가 남아 있음.

- [ ] **Step 2.7: 응답 후처리에서 sparkline drop 구현**

`api/routes/screener.py` `/run` 의 마지막 `return` 직전:

```python
include_sparkline = bool(spec.get("include_sparkline", False))
result_rows = [_serialize_row(r) for r in rows]
if not include_sparkline:
    for row in result_rows:
        row.pop("sparkline_60d", None)

return {
    "count": len(result_rows),
    "tier": tier,
    "limit_applied": limit,
    "rows": result_rows,
}
```

(기존 `return` 안의 `[_serialize_row(r) for r in rows]` 인라인 컴프리헨션을 위처럼 분리)

- [ ] **Step 2.8: 테스트 통과 확인**

Run: `pytest tests/test_screener_run.py -v -k sparkline`
Expected: 두 테스트 모두 PASS.

- [ ] **Step 2.9: 커밋**

```bash
git add api/routes/screener.py tests/test_screener_run.py
git commit -m "feat(screener): /run CTE 확장 — r1m/3m/6m/ytd/ma/drawdown/sparkline 메트릭"
```

---

### Task 3: `/run` 신규 WHERE 필터 — q · buckets · return_ranges · drawdown · ma200

기존 spec 필드는 유지하면서 추가.

**Files:**
- Modify: `api/routes/screener.py` — `/run` 의 WHERE 동적 부분
- Test: `tests/test_screener_run.py` (Task 2 파일 확장)

- [ ] **Step 3.1: 검색어 `q` 테스트 작성**

`tests/test_screener_run.py` 에 추가:

```python
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
        # %키워드% 가 3번 들어감
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
```

- [ ] **Step 3.2: 테스트 실패 확인**

Run: `pytest tests/test_screener_run.py -v -k "q_filter"`
Expected: FAIL.

- [ ] **Step 3.3: `q` WHERE 절 구현**

`api/routes/screener.py` `/run` 의 동적 WHERE 시작부 (markets 필터 아래) 에 추가:

```python
# 검색어 q — 티커/이름/영문이름 LIKE (2자 이상만)
q = (spec.get("q") or "").strip()
if len(q) >= 2:
    where.append(
        "(u.ticker ILIKE %s OR u.asset_name ILIKE %s OR u.asset_name_en ILIKE %s)"
    )
    pat = f"%{q}%"
    params.extend([pat, pat, pat])
```

- [ ] **Step 3.4: 테스트 통과 확인**

Run: `pytest tests/test_screener_run.py -v -k "q_filter"`
Expected: PASS.

- [ ] **Step 3.5: `market_cap_buckets` 테스트 + 구현**

`tests/test_screener_run.py` 에 추가:

```python
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
        # ANY(%s) + 리스트 파라미터
        assert ["large", "mid"] in params
```

`api/routes/screener.py` WHERE 동적 부분에 추가 (시총 범위 아래):

```python
buckets = spec.get("market_cap_buckets")
if buckets:
    where.append("u.market_cap_bucket = ANY(%s)")
    params.append([str(b) for b in buckets])
```

- [ ] **Step 3.6: `return_ranges` 테스트 + 구현**

```python
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
        # min/max 값들이 params 에 들어감
        for v in (-10, 50, 0, 30, -20, 100, 5):
            assert float(v) in [float(p) if isinstance(p, (int, float)) else None for p in params]
```

`api/routes/screener.py` WHERE 동적 부분에 추가 (기존 `return_1y_range` 처리 자리 또는 그 아래):

```python
# 기간별 수익률 범위 — return_ranges = {"1m": {"min", "max"}, ...}
return_ranges = spec.get("return_ranges") or {}
PERIOD_TO_COL = {"1m": "r1m", "3m": "r3m", "6m": "r6m", "1y": "r1y", "ytd": "ytd"}
for period, col in PERIOD_TO_COL.items():
    rg = return_ranges.get(period) or {}
    if rg.get("min") is not None:
        join_ohlcv = True
        where.append(f"m.{col} IS NOT NULL AND m.{col} >= %s")
        params.append(float(rg["min"]))
    if rg.get("max") is not None:
        join_ohlcv = True
        where.append(f"m.{col} IS NOT NULL AND m.{col} <= %s")
        params.append(float(rg["max"]))
```

(기존 `return_1y_range` 처리는 그대로 두어 backward compat — 둘 다 입력되면 둘 다 적용됨, 정상.)

- [ ] **Step 3.7: `max_drawdown_60d_pct` 테스트 + 구현**

```python
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
        # -15 가 파라미터에 들어감 (양수 입력 → 음수 비교)
        assert -15.0 in [float(p) if isinstance(p, (int, float)) else None for p in params]
```

`api/routes/screener.py` WHERE 동적 부분에 추가:

```python
# 60일 max drawdown 상한 — 입력은 절대값(양수), SQL 에선 부호 반전
mdd = spec.get("max_drawdown_60d_pct")
if mdd is not None:
    join_ohlcv = True
    where.append("m.drawdown_60d_pct IS NOT NULL AND m.drawdown_60d_pct >= %s")
    params.append(-float(mdd))
```

- [ ] **Step 3.8: `ma200_proximity_min` 테스트 + 구현**

```python
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
```

`api/routes/screener.py` WHERE 동적 부분에 추가:

```python
ma200_prox = spec.get("ma200_proximity_min")
if ma200_prox is not None:
    join_ohlcv = True
    where.append("m.ma200_proximity IS NOT NULL AND m.ma200_proximity >= %s")
    params.append(float(ma200_prox))
```

- [ ] **Step 3.9: 신규 정렬 옵션 추가**

`api/routes/screener.py` 의 `sort_map` 을 다음으로 교체:

```python
sort_map = {
    "market_cap_desc":   "u.market_cap_krw DESC NULLS LAST",
    "market_cap_asc":    "u.market_cap_krw ASC NULLS LAST",
    "r1m_desc":          "m.r1m DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "r3m_desc":          "m.r3m DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "r6m_desc":          "m.r6m DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "r1y_desc":          "m.r1y DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "ytd_desc":          "m.ytd DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "volume_surge_desc": "m.volume_ratio DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "liquidity_desc":    "m.avg_daily_value DESC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "drawdown_asc":      "m.drawdown_60d_pct ASC NULLS LAST" if join_ohlcv else "u.market_cap_krw DESC NULLS LAST",
    "name_asc":          "u.asset_name ASC",
}
```

- [ ] **Step 3.10: 모든 신규 필터 테스트 통과 확인**

Run: `pytest tests/test_screener_run.py -v`
Expected: 새로 추가된 모든 테스트 PASS, 기존 두 테스트(Task 2)도 그대로 PASS.

- [ ] **Step 3.11: 커밋**

```bash
git add api/routes/screener.py tests/test_screener_run.py
git commit -m "feat(screener): 신규 WHERE 필터 — q/buckets/return_ranges/drawdown/ma200 + 정렬 확장"
```

---

### Task 4: `/run` Top Picks 식별 뱃지 — `top_picks_recent` CTE + `is_top_pick`

**Files:**
- Modify: `api/routes/screener.py` — `/run` CTE/SELECT/JOIN
- Test: `tests/test_screener_run.py`

- [ ] **Step 4.1: 테스트 작성**

```python
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
        # LEFT JOIN top_picks_recent
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
```

- [ ] **Step 4.2: 테스트 실패 확인**

Run: `pytest tests/test_screener_run.py -v -k "top_pick"`
Expected: FAIL — `top_picks_recent` 미존재.

- [ ] **Step 4.3: CTE 구현 — `join_ohlcv=True` 분기**

`api/routes/screener.py` 의 `if join_ohlcv:` 블록에 있는 `cte` 문자열 끝부분(`ohlcv_metrics AS ( ... )` 닫는 `)` 직후)에 콤마 + 새 CTE 추가. 즉 Task 2.3 에서 작성한 `cte` 의 `ohlcv_metrics AS (...)` 바로 뒤에 다음을 붙인다:

```sql
,
top_picks_recent AS (
    -- 가장 최근 analysis_date (최근 7일 이내) 의 Top Picks 종목 식별
    SELECT DISTINCT UPPER(p.ticker) AS ticker, UPPER(p.market) AS market
    FROM investment_proposals p
    JOIN daily_top_picks d ON d.proposal_id = p.id
    WHERE d.analysis_date = (
        SELECT MAX(analysis_date)
        FROM daily_top_picks
        WHERE analysis_date >= CURRENT_DATE - INTERVAL '7 days'
    )
)
```

결과적으로 `cte` 변수는 `WITH ranked AS (...), metrics AS (...), ytd_anchor AS (...), ohlcv_metrics AS (...), top_picks_recent AS (...)` 형태가 된다 (Task 2 결과에 새 CTE 1개 추가).

그리고 메인 SELECT 절에 컬럼 + LEFT JOIN 추가:

```python
sql = f"""
{cte}
SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
       u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
       m.close_latest, m.high_252d, m.low_252d,
       m.high_60d, m.low_60d, m.ma20, m.ma60, m.ma200,
       m.avg_daily_value, m.vol60_pct, m.volume_ratio,
       m.high_52w_proximity, m.ma200_proximity, m.drawdown_60d_pct,
       m.r1m, m.r3m, m.r6m, m.r1y, m.ytd,
       m.sparkline_60d,
       (tp.ticker IS NOT NULL) AS is_top_pick
FROM stock_universe u
LEFT JOIN ohlcv_metrics m
  ON UPPER(u.ticker) = UPPER(m.ticker) AND UPPER(u.market) = UPPER(m.market)
LEFT JOIN top_picks_recent tp
  ON tp.ticker = UPPER(u.ticker) AND tp.market = UPPER(u.market)
WHERE {where_sql}
ORDER BY {order_by}
LIMIT %s
"""
```

- [ ] **Step 4.4: CTE 구현 — `join_ohlcv=False` 분기 (OHLCV 필터 없을 때도 is_top_pick 반환)**

`api/routes/screener.py` 의 `else:` (즉 OHLCV 필터 없는 단순 SELECT 분기) 를 다음으로 교체:

```python
else:
    sql = f"""
    WITH top_picks_recent AS (
        SELECT DISTINCT UPPER(p.ticker) AS ticker, UPPER(p.market) AS market
        FROM investment_proposals p
        JOIN daily_top_picks d ON d.proposal_id = p.id
        WHERE d.analysis_date = (
            SELECT MAX(analysis_date)
            FROM daily_top_picks
            WHERE analysis_date >= CURRENT_DATE - INTERVAL '7 days'
        )
    )
    SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
           u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
           (tp.ticker IS NOT NULL) AS is_top_pick
    FROM stock_universe u
    LEFT JOIN top_picks_recent tp
      ON tp.ticker = UPPER(u.ticker) AND tp.market = UPPER(u.market)
    WHERE {where_sql}
    ORDER BY {order_by}
    LIMIT %s
    """
```

- [ ] **Step 4.5: 테스트 통과 확인**

Run: `pytest tests/test_screener_run.py -v -k "top_pick"`
Expected: PASS.

- [ ] **Step 4.6: 전체 회귀 테스트**

Run: `pytest tests/test_screener_run.py tests/test_screener_sectors.py -v`
Expected: ALL PASS.

- [ ] **Step 4.7: 커밋**

```bash
git add api/routes/screener.py tests/test_screener_run.py
git commit -m "feat(screener): is_top_pick 플래그 + top_picks_recent CTE (최근 7일 폴백)"
```

---

### Task 5: 새 CSS 모듈 `14_screener.css`

**Files:**
- Create: `api/static/css/src/14_screener.css`

CSS 모듈 빌드 시스템: `tools/build_css.py` 가 `src/` 의 모듈을 알파벳 순으로 합쳐 `static/css/style.css` 를 만든다 ([api/static/css/README.md](../../../api/static/css/README.md) 참고). 14_ 접두는 13_ 다음 자리.

- [ ] **Step 5.1: 파일 생성**

`api/static/css/src/14_screener.css` 작성:

```css
/* ───────────────────────────────────────────────
 * Screener — Finviz 스타일 5탭 + View 프리셋 + sticky table
 * ─────────────────────────────────────────────── */

/* 탭 바 */
.screener-tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
}
.screener-tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-muted);
    padding: 10px 16px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    transition: color .15s, border-color .15s;
}
.screener-tab:hover { color: var(--text); }
.screener-tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
}
.screener-tab[disabled] { opacity: .4; cursor: not-allowed; }

/* 탭 본문 */
.screener-tab-body { display: none; padding: 14px 16px; }
.screener-tab-body.active { display: block; }
.screener-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px 18px;
    font-size: 13px;
}

/* View 프리셋 토글 */
.view-toggle {
    display: inline-flex;
    gap: 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
}
.view-btn {
    background: transparent;
    border: none;
    color: var(--text-muted);
    padding: 5px 14px;
    font-size: 12px;
    cursor: pointer;
}
.view-btn.active { background: var(--accent); color: var(--bg); }
.view-btn:not(.active):hover { background: rgba(255,255,255,0.04); color: var(--text); }

/* 결과 테이블 */
.result-table-wrap { overflow-x: auto; }
.result-table {
    width: 100%;
    font-size: 13px;
    border-collapse: collapse;
    min-width: 720px;
}
.result-table thead th {
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding: 6px 8px;
    text-align: left;
    font-weight: normal;
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
}
.result-table thead th[aria-sort="ascending"]::after { content: " ▲"; font-size: 10px; }
.result-table thead th[aria-sort="descending"]::after { content: " ▼"; font-size: 10px; }
.result-table tbody td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
}
.result-table tbody tr:hover { background: rgba(255,255,255,0.03); cursor: pointer; }
.result-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.result-table .ticker-cell { font-family: ui-monospace, monospace; }

/* sticky 첫 두 컬럼 */
.result-table th:nth-child(1), .result-table td:nth-child(1),
.result-table th:nth-child(2), .result-table td:nth-child(2) {
    position: sticky;
    background: var(--bg);
    z-index: 1;
}
.result-table th:nth-child(1), .result-table td:nth-child(1) { left: 0; }
.result-table th:nth-child(2), .result-table td:nth-child(2) { left: 110px; }

/* AI Pick 뱃지 */
.ai-pick-badge {
    display: inline-block;
    background: linear-gradient(90deg, #ffb300, #ff6b6b);
    color: #fff;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 6px;
    vertical-align: middle;
    cursor: help;
}

/* 스파크라인 */
.sparkline-svg { display: inline-block; width: 60px; height: 16px; vertical-align: middle; }
.sparkline-svg polyline { fill: none; stroke: var(--accent); stroke-width: 1.2; }

/* 한도 도달 배너 */
.limit-banner {
    background: rgba(255,179,0,0.12);
    border: 1px solid rgba(255,179,0,0.3);
    color: #ffb300;
    padding: 6px 12px;
    border-radius: 4px;
    font-size: 12px;
    margin: 8px 16px;
}

/* 모바일 — View 토글을 select 로 대체할 때 */
@media (max-width: 768px) {
    .view-toggle.mobile-as-select { display: none; }
    .view-mobile-select { display: inline-block !important; }
    .screener-tab { padding: 8px 12px; font-size: 12px; }
}
.view-mobile-select { display: none; }
```

- [ ] **Step 5.2: 빌드 실행**

Run: `python -m tools.build_css`
Expected: `api/static/css/style.css` 가 갱신됨, 콘솔에 `14_screener.css` 가 합쳐졌다는 메시지.

- [ ] **Step 5.3: 커밋**

```bash
git add api/static/css/src/14_screener.css api/static/css/style.css
git commit -m "feat(screener/css): 14_screener.css — 탭/View/sticky/뱃지/스파크라인/모바일"
```

---

### Task 6: `screener.html` 풀 리뉴얼 — 5탭 + 3 View + 스파크라인 + IIFE

**Files:**
- Modify: `api/templates/screener.html`

기존 파일을 통째로 교체. 분량 큼.

- [ ] **Step 6.1: 새 템플릿 작성**

`api/templates/screener.html` 의 전체 내용을 다음으로 교체:

```html
{% extends "base.html" %}
{% block title %}스크리너 — AlphaSignal{% endblock %}
{% block page_title %}프리미엄 스크리너{% endblock %}
{% block header_actions %}
<button class="btn" id="saveBtn" style="font-size:12px;padding:4px 12px;" onclick="Screener.savePreset()">프리셋 저장</button>
<button class="btn" id="loadBtn" style="font-size:12px;padding:4px 12px;margin-left:4px;" onclick="Screener.togglePresetPanel()">내 프리셋</button>
{% endblock %}

{% block content %}
<div class="card" style="margin-bottom:16px;">
    <div class="screener-tabs" role="tablist">
        <button class="screener-tab active" role="tab" data-tab="search" aria-selected="true">🔍 Search</button>
        <button class="screener-tab" role="tab" data-tab="descriptive" aria-selected="false">📊 Descriptive</button>
        <button class="screener-tab" role="tab" data-tab="performance" aria-selected="false">🚀 Performance</button>
        <button class="screener-tab" role="tab" data-tab="technical" aria-selected="false">📈 Technical</button>
        <button class="screener-tab" role="tab" data-tab="fundamental" disabled aria-disabled="true" title="데이터 준비 중 (B-2 단계)">💰 Fundamental</button>
    </div>

    <!-- Search -->
    <div class="screener-tab-body active" data-body="search">
        <label style="display:block;font-size:13px;">
            <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">티커 / 종목명 (한글·영문 모두) — 2자 이상</div>
            <input id="f-q" type="text" placeholder="예: 삼성, AAPL, 반도체 ..." style="width:100%;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:14px;">
        </label>
    </div>

    <!-- Descriptive -->
    <div class="screener-tab-body" data-body="descriptive">
        <div class="screener-grid">
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">시장</div>
                <div id="f-markets-checks" style="display:flex;gap:10px;flex-wrap:wrap;">
                    <label><input type="checkbox" value="KOSPI"> KOSPI</label>
                    <label><input type="checkbox" value="KOSDAQ"> KOSDAQ</label>
                    <label><input type="checkbox" value="NASDAQ"> NASDAQ</label>
                    <label><input type="checkbox" value="NYSE"> NYSE</label>
                </div>
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">섹터</div>
                <select id="f-sectors" multiple style="width:100%;min-height:80px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);padding:6px;">
                    <option value="">로딩 중…</option>
                </select>
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">시총 범위 (억원)</div>
                <div style="display:flex;gap:6px;">
                    <input id="f-mcap-min" type="number" placeholder="최소" style="flex:1;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
                    <input id="f-mcap-max" type="number" placeholder="최대" style="flex:1;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
                </div>
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">시총 버킷</div>
                <div style="display:flex;gap:10px;">
                    <label><input type="checkbox" name="f-bucket" value="large"> Large</label>
                    <label><input type="checkbox" name="f-bucket" value="mid"> Mid</label>
                    <label><input type="checkbox" name="f-bucket" value="small"> Small</label>
                </div>
            </label>
        </div>
    </div>

    <!-- Performance -->
    <div class="screener-tab-body" data-body="performance">
        <div class="screener-grid">
            {% for p, lbl in [('1m','1개월'),('3m','3개월'),('6m','6개월'),('1y','1년'),('ytd','YTD')] %}
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">{{ lbl }} 수익률 (%)</div>
                <div style="display:flex;gap:6px;">
                    <input id="f-r{{p}}-min" type="number" placeholder="min" style="flex:1;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
                    <input id="f-r{{p}}-max" type="number" placeholder="max" style="flex:1;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
                </div>
            </label>
            {% endfor %}
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">정렬</div>
                <select id="f-sort" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
                    <option value="market_cap_desc">시총 ↓</option>
                    <option value="r1m_desc">1m 수익률 ↓</option>
                    <option value="r3m_desc">3m 수익률 ↓</option>
                    <option value="r6m_desc">6m 수익률 ↓</option>
                    <option value="r1y_desc">1y 수익률 ↓</option>
                    <option value="ytd_desc">YTD ↓</option>
                    <option value="volume_surge_desc">거래량 비율 ↓</option>
                    <option value="liquidity_desc">거래대금 ↓</option>
                    <option value="drawdown_asc">낙폭 적은 순</option>
                    <option value="name_asc">이름 A→Z</option>
                </select>
            </label>
        </div>
    </div>

    <!-- Technical -->
    <div class="screener-tab-body" data-body="technical">
        <div class="screener-grid">
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">60일 변동성 상한 (%)</div>
                <input id="f-vol60" type="number" step="0.1" placeholder="예: 3.0" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">거래량 비율(20d/60d) 하한</div>
                <input id="f-volr" type="number" step="0.1" placeholder="예: 1.2" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">52주 고점 근접도 (0~1)</div>
                <input id="f-prox" type="number" step="0.01" min="0" max="1" placeholder="예: 0.85" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">60일 max drawdown 상한 (%, 절대값)</div>
                <input id="f-mdd" type="number" step="0.1" min="0" placeholder="예: 15" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">MA200 근접도 하한 (close/MA200)</div>
                <input id="f-ma200" type="number" step="0.01" min="0" placeholder="예: 0.95" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">일평균 거래대금 하한 (KRX, 억원)</div>
                <input id="f-liq-krw" type="number" placeholder="예: 10" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
            <label>
                <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px;">일평균 거래대금 하한 (US, 천달러)</div>
                <input id="f-liq-usd" type="number" placeholder="예: 500" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);">
            </label>
        </div>
    </div>

    <!-- Fundamental (disabled) -->
    <div class="screener-tab-body" data-body="fundamental">
        <div style="padding:30px 16px;text-align:center;color:var(--text-muted);font-size:13px;">
            펀더멘털 데이터(PER/PBR/배당/ROE 등)는 데이터 파이프라인 준비 중입니다 (B-2 단계 예정).
        </div>
    </div>

    <div style="padding:10px 16px;border-top:1px solid var(--border);display:flex;gap:10px;align-items:center;">
        <button class="btn btn-primary" onclick="Screener.run()" style="padding:6px 18px;">실행</button>
        <span id="status" style="color:var(--text-muted);font-size:12px;"></span>
    </div>
</div>

<!-- 프리셋 패널 -->
<div class="card" id="loadPanel" style="margin-bottom:16px;display:none;">
    <div class="card-header"><strong>내 프리셋 · 공개 프리셋</strong></div>
    <div id="presetsBody" style="padding:10px 16px;">
        <div class="tr-loading" style="color:var(--text-muted);font-size:13px;">불러오는 중…</div>
    </div>
</div>

<!-- 결과 -->
<div class="card">
    <div class="card-header" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
        <strong>결과</strong>
        <div style="display:flex;gap:10px;align-items:center;">
            <span id="resultSummary" style="color:var(--text-muted);font-size:12px;"></span>
            <label style="font-size:11px;color:var(--text-muted);">
                <input type="checkbox" id="f-sparkline" checked> 스파크라인
            </label>
            <div class="view-toggle">
                <button class="view-btn active" data-view="overview">Overview</button>
                <button class="view-btn" data-view="performance">Performance</button>
                <button class="view-btn" data-view="technical">Technical</button>
                <button class="view-btn" data-view="custom">Custom</button>
            </div>
        </div>
    </div>
    <div id="limitBanner" style="display:none;"></div>
    <div class="result-table-wrap" id="resultsBody" style="padding:10px 0;">
        <div style="padding:20px;color:var(--text-muted);font-size:13px;">위에서 조건을 설정하고 "실행"을 누르세요.</div>
    </div>
</div>

<script>
(function() {
    'use strict';

    // ── 상태 ──
    var currentRows = [];
    var currentView = 'overview';
    var sortState = { col: null, dir: 'desc' };

    // ── DOM 헬퍼 ──
    function $(id) { return document.getElementById(id); }
    function num(id) {
        var el = $(id);
        if (!el || el.value === '') return null;
        var v = parseFloat(el.value);
        return isNaN(v) ? null : v;
    }
    function checkedValues(name) {
        return Array.from(document.querySelectorAll('input[type=checkbox][name="'+name+'"]:checked'))
                    .map(function(el){ return el.value; });
    }
    function checkedValuesById(parentId) {
        return Array.from($(parentId).querySelectorAll('input[type=checkbox]:checked'))
                    .map(function(el){ return el.value; });
    }

    // ── Spec ↔ DOM ──
    var SpecBuilder = {
        fromDOM: function() {
            var spec = {};
            var q = $('f-q').value.trim();
            if (q.length >= 2) spec.q = q;

            var markets = checkedValuesById('f-markets-checks');
            if (markets.length) spec.markets = markets;

            var sectors = Array.from($('f-sectors').selectedOptions).map(function(o){ return o.value; });
            if (sectors.length) spec.sectors = sectors;

            var buckets = checkedValues('f-bucket');
            if (buckets.length) spec.market_cap_buckets = buckets;

            var mcMin = num('f-mcap-min'), mcMax = num('f-mcap-max');
            if (mcMin !== null || mcMax !== null) {
                spec.market_cap_krw = {};
                if (mcMin !== null) spec.market_cap_krw.min = mcMin * 1e8;
                if (mcMax !== null) spec.market_cap_krw.max = mcMax * 1e8;
            }

            var liqKrw = num('f-liq-krw');
            if (liqKrw !== null) spec.min_daily_value_krw = liqKrw * 1e8;
            var liqUsd = num('f-liq-usd');
            if (liqUsd !== null) spec.min_daily_value_usd = liqUsd * 1000;

            var ranges = {};
            ['1m','3m','6m','1y','ytd'].forEach(function(p) {
                var mn = num('f-r'+p+'-min'), mx = num('f-r'+p+'-max');
                if (mn !== null || mx !== null) {
                    ranges[p] = {};
                    if (mn !== null) ranges[p].min = mn;
                    if (mx !== null) ranges[p].max = mx;
                }
            });
            if (Object.keys(ranges).length) spec.return_ranges = ranges;

            var vol60 = num('f-vol60'); if (vol60 !== null) spec.max_vol60_pct = vol60;
            var volr = num('f-volr');   if (volr !== null) spec.volume_ratio_min = volr;
            var prox = num('f-prox');   if (prox !== null) spec.high_52w_proximity_min = prox;
            var mdd = num('f-mdd');     if (mdd !== null) spec.max_drawdown_60d_pct = mdd;
            var ma2 = num('f-ma200');   if (ma2 !== null) spec.ma200_proximity_min = ma2;

            spec.sort = $('f-sort').value;
            spec.include_sparkline = $('f-sparkline').checked;
            return spec;
        },

        toDOM: function(spec) {
            $('f-q').value = spec.q || '';

            var markets = spec.markets || [];
            $('f-markets-checks').querySelectorAll('input[type=checkbox]').forEach(function(el){
                el.checked = markets.indexOf(el.value) >= 0;
            });

            // 섹터는 sectors 옵션이 로드된 후에야 적용 — 보류 시 별도 처리
            Array.from($('f-sectors').options).forEach(function(o){
                o.selected = (spec.sectors||[]).indexOf(o.value) >= 0;
            });

            document.querySelectorAll('input[name="f-bucket"]').forEach(function(el){
                el.checked = (spec.market_cap_buckets||[]).indexOf(el.value) >= 0;
            });

            var mc = spec.market_cap_krw || {};
            $('f-mcap-min').value = mc.min != null ? mc.min/1e8 : '';
            $('f-mcap-max').value = mc.max != null ? mc.max/1e8 : '';

            $('f-liq-krw').value = spec.min_daily_value_krw != null ? spec.min_daily_value_krw/1e8 : '';
            $('f-liq-usd').value = spec.min_daily_value_usd != null ? spec.min_daily_value_usd/1000 : '';

            var rng = spec.return_ranges || {};
            ['1m','3m','6m','1y','ytd'].forEach(function(p) {
                $('f-r'+p+'-min').value = (rng[p] && rng[p].min != null) ? rng[p].min : '';
                $('f-r'+p+'-max').value = (rng[p] && rng[p].max != null) ? rng[p].max : '';
            });

            $('f-vol60').value = spec.max_vol60_pct != null ? spec.max_vol60_pct : '';
            $('f-volr').value  = spec.volume_ratio_min != null ? spec.volume_ratio_min : '';
            $('f-prox').value  = spec.high_52w_proximity_min != null ? spec.high_52w_proximity_min : '';
            $('f-mdd').value   = spec.max_drawdown_60d_pct != null ? spec.max_drawdown_60d_pct : '';
            $('f-ma200').value = spec.ma200_proximity_min != null ? spec.ma200_proximity_min : '';
            if (spec.sort) $('f-sort').value = spec.sort;
            if (spec.include_sparkline === false) $('f-sparkline').checked = false;
        }
    };

    // ── 탭 전환 ──
    function activateTab(name) {
        document.querySelectorAll('.screener-tab').forEach(function(t){
            var on = t.dataset.tab === name;
            t.classList.toggle('active', on);
            t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('.screener-tab-body').forEach(function(b){
            b.classList.toggle('active', b.dataset.body === name);
        });
    }
    document.querySelectorAll('.screener-tab').forEach(function(t){
        t.addEventListener('click', function(){
            if (t.disabled) return;
            activateTab(t.dataset.tab);
        });
    });

    // ── View 전환 ──
    function activateView(name) {
        currentView = name;
        document.querySelectorAll('.view-btn').forEach(function(b){
            b.classList.toggle('active', b.dataset.view === name);
        });
        renderResults();
    }
    document.querySelectorAll('.view-btn').forEach(function(b){
        b.addEventListener('click', function(){ activateView(b.dataset.view); });
    });

    // ── 컬럼 정의 ──
    var ColumnDefs = {
        ticker:         { label: '티커', cls: 'ticker-cell', html: function(r){
            return '<a href="/pages/proposals/history/'+encodeURIComponent(r.ticker)+'" style="color:var(--accent);">'+r.ticker+'</a>'+
                   ' <span style="color:var(--text-muted);font-size:11px;">'+(r.market||'')+'</span>'+
                   (r.is_top_pick ? ' <span class="ai-pick-badge" title="오늘의 AI Top Picks 포함">🏆 AI Pick</span>' : '');
        }},
        name:           { label: '종목명', html: function(r){ return r.asset_name || '-'; }},
        sector:         { label: '섹터', html: function(r){ return '<span style="color:var(--text-muted);">'+(r.sector_norm||'-')+'</span>'; }},
        market_cap:     { label: '시총', cls: 'num', html: function(r){ return fmtMcap(r.market_cap_krw); }},
        last_price:     { label: '현재가', cls: 'num', html: function(r){
            return r.close_latest != null ? r.close_latest.toLocaleString() + ' ' + (r.last_price_ccy||'') : '-';
        }},
        r1m: { label: '1m',  cls: 'num', html: function(r){ return fmtPctClass(r.r1m); }, sortKey:'r1m' },
        r3m: { label: '3m',  cls: 'num', html: function(r){ return fmtPctClass(r.r3m); }, sortKey:'r3m' },
        r6m: { label: '6m',  cls: 'num', html: function(r){ return fmtPctClass(r.r6m); }, sortKey:'r6m' },
        r1y: { label: '1y',  cls: 'num', html: function(r){ return fmtPctClass(r.r1y); }, sortKey:'r1y' },
        ytd: { label: 'YTD', cls: 'num', html: function(r){ return fmtPctClass(r.ytd); }, sortKey:'ytd' },
        liquidity: { label: '거래대금', cls: 'num', html: function(r){ return fmtMcap(r.avg_daily_value); }, sortKey:'avg_daily_value' },
        vol60: { label: 'Vol60', cls: 'num', html: function(r){ return r.vol60_pct != null ? r.vol60_pct.toFixed(2)+'%' : '-'; }, sortKey:'vol60_pct' },
        vol_ratio: { label: 'VolRatio', cls: 'num', html: function(r){ return r.volume_ratio != null ? '×'+r.volume_ratio.toFixed(2) : '-'; }, sortKey:'volume_ratio' },
        ma20:  { label: 'MA20',  cls: 'num', html: function(r){ return r.ma20  != null ? r.ma20.toFixed(0)  : '-'; }, sortKey:'ma20' },
        ma60:  { label: 'MA60',  cls: 'num', html: function(r){ return r.ma60  != null ? r.ma60.toFixed(0)  : '-'; }, sortKey:'ma60' },
        ma200: { label: 'MA200', cls: 'num', html: function(r){ return r.ma200 != null ? r.ma200.toFixed(0) : '-'; }, sortKey:'ma200' },
        prox52: { label: '52w근접', cls: 'num', html: function(r){ return r.high_52w_proximity != null ? (r.high_52w_proximity*100).toFixed(1)+'%' : '-'; }, sortKey:'high_52w_proximity' },
        high_252d: { label: '52w-high', cls: 'num', html: function(r){ return r.high_252d != null ? r.high_252d.toFixed(0) : '-'; }, sortKey:'high_252d' },
        low_252d:  { label: '52w-low',  cls: 'num', html: function(r){ return r.low_252d  != null ? r.low_252d.toFixed(0)  : '-'; }, sortKey:'low_252d' },
        drawdown:  { label: 'DD60', cls: 'num', html: function(r){ return r.drawdown_60d_pct != null ? r.drawdown_60d_pct.toFixed(2)+'%' : '-'; }, sortKey:'drawdown_60d_pct' },
        sparkline: { label: '60d', html: function(r){ return r.sparkline_60d ? sparklineSVG(r.sparkline_60d) : '-'; }},
    };

    var ViewColumns = {
        overview:    ['ticker','name','sector','market_cap','last_price','r1m','r1y','liquidity','sparkline'],
        performance: ['ticker','name','r1m','r3m','r6m','r1y','ytd','high_252d','low_252d','drawdown'],
        technical:   ['ticker','name','vol60','vol_ratio','ma20','ma60','ma200','prox52','last_price'],
        custom:      ['ticker','name','sector','market_cap','last_price','r1m','r3m','r1y','vol60','vol_ratio'],
    };

    // ── 포맷터 ──
    function fmtPct(v) {
        if (v == null) return '-';
        return (v > 0 ? '+' : '') + v.toFixed(2) + '%';
    }
    function fmtPctClass(v) {
        if (v == null) return '-';
        var cls = v >= 0 ? 'positive' : 'negative';
        return '<span class="'+cls+'">'+fmtPct(v)+'</span>';
    }
    function fmtMcap(v) {
        if (v == null) return '-';
        if (v >= 1e12) return (v/1e12).toFixed(2)+'조';
        if (v >= 1e8)  return (v/1e8).toFixed(0)+'억';
        return v.toLocaleString();
    }
    function sparklineSVG(arr) {
        if (!arr || !arr.length) return '-';
        // 배열이 시간역순(최신 먼저) — SVG는 좌→우 시간순으로
        var pts = arr.slice().reverse();
        var min = Math.min.apply(null, pts);
        var max = Math.max.apply(null, pts);
        var rng = max - min || 1;
        var W = 60, H = 16;
        var step = pts.length > 1 ? W / (pts.length - 1) : 0;
        var coords = pts.map(function(v, i){
            var x = (i * step).toFixed(2);
            var y = (H - ((v - min) / rng) * H).toFixed(2);
            return x + ',' + y;
        }).join(' ');
        return '<svg class="sparkline-svg" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none"><polyline points="'+coords+'"/></svg>';
    }

    // ── 정렬 ──
    function applySort(rows) {
        if (!sortState.col) return rows;
        var col = sortState.col, dir = sortState.dir === 'asc' ? 1 : -1;
        return rows.slice().sort(function(a, b) {
            var av = a[col], bv = b[col];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            if (av < bv) return -1 * dir;
            if (av > bv) return  1 * dir;
            return 0;
        });
    }

    // ── 렌더 ──
    function renderResults() {
        var body = $('resultsBody');
        var sumEl = $('resultSummary');
        if (!currentRows.length) {
            body.innerHTML = '<div style="padding:20px;color:var(--text-muted);font-size:13px;">조건을 만족하는 종목이 없습니다.</div>';
            sumEl.textContent = '';
            return;
        }
        sumEl.textContent = '총 '+currentRows.length+'건';
        var cols = ViewColumns[currentView] || ViewColumns.overview;
        var sorted = applySort(currentRows);

        var html = '<table class="result-table"><thead><tr>';
        cols.forEach(function(c) {
            var def = ColumnDefs[c];
            var sortKey = def.sortKey;
            var sortAttr = '';
            if (sortKey && sortState.col === sortKey) {
                sortAttr = ' aria-sort="'+(sortState.dir === 'asc' ? 'ascending' : 'descending')+'"';
            }
            html += '<th'+sortAttr+' data-sortkey="'+(sortKey||'')+'" class="'+(def.cls||'')+'">'+def.label+'</th>';
        });
        html += '</tr></thead><tbody>';
        sorted.forEach(function(r) {
            html += '<tr>';
            cols.forEach(function(c) {
                var def = ColumnDefs[c];
                html += '<td class="'+(def.cls||'')+'">'+def.html(r)+'</td>';
            });
            html += '</tr>';
        });
        html += '</tbody></table>';
        body.innerHTML = html;

        // 정렬 핸들러 — 헤더 클릭
        body.querySelectorAll('th[data-sortkey]').forEach(function(th) {
            var key = th.dataset.sortkey;
            if (!key) return;
            th.addEventListener('click', function() {
                if (sortState.col === key) {
                    sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    sortState.col = key;
                    sortState.dir = 'desc';
                }
                renderResults();
            });
        });
    }

    function showLimitBanner(count, limit) {
        var b = $('limitBanner');
        if (count >= limit) {
            b.className = 'limit-banner';
            b.style.display = 'block';
            b.textContent = '한도 ' + limit + '건에 도달했습니다. 더 좁은 필터를 사용하세요.';
        } else {
            b.style.display = 'none';
            b.textContent = '';
        }
    }

    // ── API 호출 ──
    function run() {
        var spec = SpecBuilder.fromDOM();
        $('status').textContent = '실행 중...';
        fetch('/api/screener/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(spec),
        }).then(function(r) {
            if (!r.ok) throw new Error('HTTP '+r.status);
            return r.json();
        }).then(function(data) {
            currentRows = data.rows || [];
            sortState = { col: null, dir: 'desc' };
            renderResults();
            showLimitBanner(data.count, data.limit_applied);
            $('status').textContent = '완료 ('+data.count+'건, tier='+data.tier+')';
        }).catch(function(err) {
            $('status').textContent = '실패: '+err.message;
        });
    }

    // ── 섹터 옵션 로드 ──
    function loadSectors(selectedKeys) {
        fetch('/api/screener/sectors').then(function(r){return r.json();}).then(function(data) {
            var sel = $('f-sectors');
            sel.innerHTML = '';
            (data.sectors||[]).forEach(function(s) {
                var opt = document.createElement('option');
                opt.value = s.key;
                opt.textContent = s.label + ' ('+s.count+')';
                if (selectedKeys && selectedKeys.indexOf(s.key) >= 0) opt.selected = true;
                sel.appendChild(opt);
            });
        }).catch(function(){
            $('f-sectors').innerHTML = '<option value="">로드 실패</option>';
        });
    }

    // ── URL 쿼리스트링 prefill ──
    function applyUrlParams() {
        var p = new URLSearchParams(window.location.search);
        var spec = {};
        if (p.has('q')) spec.q = p.get('q');
        if (p.has('sectors')) spec.sectors = p.get('sectors').split(',').filter(Boolean);
        if (p.has('markets')) spec.markets = p.get('markets').split(',').filter(Boolean);
        if (p.has('market_cap_buckets')) spec.market_cap_buckets = p.get('market_cap_buckets').split(',').filter(Boolean);
        if (Object.keys(spec).length === 0) return false;
        SpecBuilder.toDOM(spec);
        return true;
    }

    // ── 프리셋 CRUD UI ──
    function savePreset() {
        var name = prompt('프리셋 이름');
        if (!name) return;
        var isPublic = confirm('이 프리셋을 공개할까요? (확인=공개, 취소=비공개)');
        var spec = SpecBuilder.fromDOM();
        fetch('/api/screener/presets', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify({name: name, spec: spec, is_public: isPublic}),
        }).then(function(r) {
            if (r.status === 403) { r.json().then(function(d){ alert('저장 불가: '+d.detail); }); return null; }
            if (r.status === 409) { r.json().then(function(d){ alert(d.detail); }); return null; }
            if (!r.ok) throw new Error('HTTP '+r.status);
            return r.json();
        }).then(function(data) { if (data) alert('프리셋 "'+name+'" 저장 완료'); })
          .catch(function(err){ alert('실패: '+err.message); });
    }
    function togglePresetPanel() {
        var panel = $('loadPanel');
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
        if (panel.style.display === 'block') loadPresets();
    }
    function loadPresets() {
        var body = $('presetsBody');
        body.innerHTML = '<div class="tr-loading">불러오는 중…</div>';
        fetch('/api/screener/presets').then(function(r){
            if (r.status === 401) { body.innerHTML = '<div style="color:var(--text-muted);">로그인이 필요합니다.</div>'; return null; }
            if (!r.ok) throw new Error('HTTP '+r.status);
            return r.json();
        }).then(function(data){
            if (!data) return;
            if (!data.presets || data.presets.length === 0) {
                body.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">저장된 프리셋이 없습니다.</div>';
                return;
            }
            var html = '<div style="display:flex;flex-direction:column;gap:6px;">';
            data.presets.forEach(function(p) {
                var pub  = p.is_public ? ' <span style="color:var(--accent);font-size:10px;">PUBLIC</span>' : '';
                var mine = p.owned ? '' : ' <span style="color:var(--text-muted);font-size:10px;">by '+(p.owner_email||'?')+'</span>';
                html += '<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;">';
                html += '<strong style="flex:1;">'+p.name+'</strong>'+pub+mine;
                html += '<button class="btn" style="font-size:11px;padding:3px 10px;" onclick="Screener.loadPreset('+p.id+', '+JSON.stringify(p.spec).replace(/"/g,'&quot;')+')">불러오기</button>';
                if (p.owned) {
                    html += '<button class="btn" style="font-size:11px;padding:3px 10px;background:transparent;color:var(--danger);border-color:var(--danger);" onclick="Screener.deletePreset('+p.id+')">삭제</button>';
                }
                html += '</div>';
            });
            html += '</div>';
            body.innerHTML = html;
        }).catch(function(err){ body.innerHTML = '<div>로드 실패: '+err.message+'</div>'; });
    }
    function loadPreset(id, spec) {
        SpecBuilder.toDOM(spec);
        $('status').textContent = '프리셋 로드 완료';
        togglePresetPanel();
    }
    function deletePreset(id) {
        if (!confirm('이 프리셋을 삭제할까요?')) return;
        fetch('/api/screener/presets/'+id, {method:'DELETE', headers:{'X-Requested-With':'XMLHttpRequest'}})
            .then(function(r){ if (!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
            .then(function(){ loadPresets(); })
            .catch(function(err){ alert('삭제 실패: '+err.message); });
    }

    // ── 외부 노출 ──
    window.Screener = {
        run: run,
        savePreset: savePreset,
        togglePresetPanel: togglePresetPanel,
        loadPreset: loadPreset,
        deletePreset: deletePreset,
    };

    // ── 초기화 ──
    var prefilled = applyUrlParams();
    var pendingSectors = (prefilled && new URLSearchParams(window.location.search).get('sectors'))
        ? new URLSearchParams(window.location.search).get('sectors').split(',')
        : null;
    loadSectors(pendingSectors);
    if (prefilled) {
        // 섹터 로드 후 자동 실행 (1초 지연)
        setTimeout(run, 800);
    }
})();
</script>
{% endblock %}
```

- [ ] **Step 6.2: API 서버 기동 + 수동 페이지 검증**

Run (별도 터미널): `python -m api.main`
브라우저: `http://localhost:8000/pages/screener`

체크 항목:
- [ ] 5탭 표시 (Search / Descriptive / Performance / Technical / Fundamental)
- [ ] Fundamental 탭은 dimmed + 클릭해도 활성화 안 됨
- [ ] 섹터 드롭다운에 sector_norm 옵션이 카운트와 함께 로드됨
- [ ] Search 탭에서 "삼성" 입력 → 실행 → 결과 테이블에 삼성 관련 종목
- [ ] View 토글 (Overview/Performance/Technical/Custom) 클릭하면 컬럼이 즉시 바뀜
- [ ] 결과 행 hover 시 highlight, 클릭하면 종목 페이지로 이동
- [ ] 컬럼 헤더 클릭 → 정렬 화살표 표시 + 행 정렬됨
- [ ] Overview View 에서 스파크라인 SVG 표시, 끄면 즉시 사라짐 (체크박스 토글 후 실행 다시)
- [ ] 한도 도달 시 노란 배너 표시
- [ ] 가로 스크롤 + sticky 첫 두 컬럼

- [ ] **Step 6.3: 커밋**

```bash
git add api/templates/screener.html
git commit -m "feat(screener/ui): Finviz 스타일 풀 리뉴얼 — 5탭/View프리셋/IIFE/스파크라인"
```

---

### Task 7: URL prefill 작동 확인 (브라우저 수동)

Step 6 의 `applyUrlParams()` 로직이 동작하는지 단독 확인.

**Files:** 없음 (수동 검증)

- [ ] **Step 7.1: 수동 검증**

브라우저 주소창:
- `http://localhost:8000/pages/screener?sectors=semiconductors,energy`
  - [ ] Descriptive 탭의 섹터 드롭다운에 두 항목이 선택된 상태로 진입
  - [ ] 1초 후 자동 실행 → 해당 섹터 종목들 표시

- `http://localhost:8000/pages/screener?market_cap_buckets=large&q=Apple`
  - [ ] Search 탭에 "Apple" 입력됨
  - [ ] Descriptive 탭의 시총 버킷 = Large 체크
  - [ ] 자동 실행 → AAPL 등 매칭

문제 있으면 fix → 다시 검증.

- [ ] **Step 7.2: 커밋 (수정 있을 시만)**

수정 없으면 skip.

---

### Task 8: 라벨 변경 — `Stock Picks` → `AI Top Picks`

**Files:**
- Modify: `api/templates/base.html` (라인 ~72)
- Modify: `api/templates/watchlist.html` (3곳)
- Modify: `api/templates/proposals.html` (페이지 헤더 — 있을 시)
- Modify: `api/templates/dashboard.html`, `api/templates/partials/dashboard/_top_picks.html` (있을 시 라벨 텍스트 점검)

URL/`active_page='proposals'` 키는 **변경하지 않는다** — 라우트와 분리됨.

- [ ] **Step 8.1: base.html 사이드바 라벨 변경**

`api/templates/base.html` 의 해당 줄을 다음과 같이:

```html
<li><a href="/pages/proposals" class="{% if active_page == 'proposals' %}active{% endif %}" onclick="document.body.classList.remove('sidebar-open')">
    <i data-lucide="trending-up" aria-hidden="true"></i><span class="nav-label">AI Top Picks</span>
</a></li>
```

(`Stock Picks` → `AI Top Picks` 만 교체. `active_page == 'proposals'` 와 URL `/pages/proposals` 는 그대로)

- [ ] **Step 8.2: watchlist.html 3곳 변경**

`api/templates/watchlist.html` 에서 "Stock Picks" 텍스트를 "AI Top Picks" 로 모두 교체 (3곳).

- [ ] **Step 8.3: proposals.html 페이지 타이틀 변경**

`api/templates/proposals.html` 에서 `{% block title %}` 와 `{% block page_title %}` 안의 "Stock Picks" 또는 "Proposals" 표기를 "AI Top Picks" 로 교체. ("Stock Picks" 정확 매칭이 없으면 page_title 만 변경.)

확인:

```bash
grep -n "Stock Picks\|page_title\|block title" api/templates/proposals.html
```

해당 자리 라벨만 변경.

- [ ] **Step 8.4: dashboard 관련 파일 점검 (있을 시)**

```bash
grep -n "Stock Picks" api/templates/dashboard.html api/templates/partials/dashboard/_top_picks.html 2>/dev/null
```

매치되는 텍스트가 있으면 "AI Top Picks" 로 교체. 없으면 skip.

- [ ] **Step 8.5: 잔류 검사**

```bash
grep -rn "Stock Picks" api/templates/
```

Expected: 매치 0건. (앞서 보고 프롬프트 디렉토리는 무시 — 그 매치는 `_docs/_prompts/`)

- [ ] **Step 8.6: 수동 페이지 검증**

브라우저:
- 사이드바에 "AI Top Picks" 표시
- `/pages/watchlist` empty state 라벨 변경 확인
- `/pages/proposals` 페이지 타이틀 변경 확인
- 사이드바에서 "AI Top Picks" 클릭 → 정상 이동 (`active_page='proposals'` active 표시)

- [ ] **Step 8.7: 커밋**

먼저 변경된 파일만 확인:

```bash
git status -s api/templates/
```

수정된 파일만 추려서 add (위 출력에서 ` M ` 으로 표기된 파일만):

```bash
git add api/templates/base.html api/templates/watchlist.html
# 아래 3개는 실제 수정된 경우에만 add (없으면 생략):
# git add api/templates/proposals.html
# git add api/templates/dashboard.html
# git add api/templates/partials/dashboard/_top_picks.html
git commit -m "refactor(ui): Stock Picks → AI Top Picks 라벨 변경 (URL/active_page 보존)"
```

---

### Task 9: AI Top Picks 카드에 "🔍 비슷한 종목 더 찾기" CTA

**Files:**
- Modify: `api/templates/_macros/proposal.html` 또는 `api/templates/proposals.html`

기존 매크로 파일이 있다면 그 안에서, 없으면 `proposals.html` 의 종목 카드 렌더 위치에서.

- [ ] **Step 9.1: 매크로 위치 확인**

```bash
ls api/templates/_macros/
grep -n "proposal_card_full\|proposal_card_compact" api/templates/_macros/proposal.html 2>/dev/null | head -5
```

- [ ] **Step 9.2: CTA 추가**

`api/templates/_macros/proposal.html` 의 `proposal_card_full` 또는 `proposal_card_compact` 매크로 안, 종목 액션 영역(보통 매크로 끝부분 외부 링크 옆)에 다음 HTML 삽입:

```html
{% if p.sector_norm or p.market_cap_bucket %}
<a class="btn"
   href="/pages/screener?{% if p.sector_norm %}sectors={{ p.sector_norm }}{% endif %}{% if p.sector_norm and p.market_cap_bucket %}&{% endif %}{% if p.market_cap_bucket %}market_cap_buckets={{ p.market_cap_bucket }}{% endif %}"
   style="font-size:11px;padding:3px 10px;color:var(--text-muted);">
    🔍 비슷한 종목 더 찾기
</a>
{% endif %}
```

매크로 파일이 없거나 카드 구조가 다르면, `proposals.html` 의 종목 행 렌더 부분에 동일 패턴으로 직접 삽입.

- [ ] **Step 9.3: 수동 검증**

브라우저:
- `/pages/proposals` 진입 → 종목 카드 하단 "🔍 비슷한 종목 더 찾기" 버튼 노출
- 클릭 → `/pages/screener?sectors=...&market_cap_buckets=...` 이동 + 자동 실행
- 결과에 동일 섹터/시총 종목들 표시

- [ ] **Step 9.4: 커밋**

```bash
git add api/templates/_macros/proposal.html api/templates/proposals.html
git commit -m "feat(ai-top-picks): 카드에 Screener prefilled CTA 버튼 추가"
```

---

### Task 10: 통합 검증 + 회귀 테스트 + 빌드 산출물 갱신

**Files:** 없음 (검증만, 필요 시 fix)

- [ ] **Step 10.1: 전체 회귀 테스트**

Run: `pytest -v`
Expected: 기존 테스트 + 신규 `tests/test_screener_run.py` + `tests/test_screener_sectors.py` 모두 PASS.

- [ ] **Step 10.2: CSS 빌드 산출물 최종 갱신**

Run: `python -m tools.build_css`
Expected: `api/static/css/style.css` 최신화. 변경된 라인 수 확인.

- [ ] **Step 10.3: 통합 수동 검증 (실제 DB 환경에서 1회)**

API 기동 (`python -m api.main`) 상태에서:

| 검증 항목 | Expected |
|---|---|
| 사이드바 "AI Top Picks" | ✅ 표시 |
| `/pages/screener` 진입 | 5탭 + Fundamental disabled |
| Search 탭 "삼성" 검색 | 결과 행에 삼성 관련 종목 |
| Descriptive 섹터 드롭다운 | sector_norm + 한국어 라벨 + 카운트 |
| Performance 1m min=10 | 1m 수익률 ≥ 10% 종목만 |
| Technical 60d 변동성 ≤ 3% | 매칭 종목 |
| max_drawdown_60d_pct=15 | 낙폭 15% 이내 종목 |
| Top Picks 종목 행 | 🏆 AI Pick 뱃지 표시 |
| View 전환 | 컬럼 즉시 변경 |
| 컬럼 헤더 클릭 정렬 | 화살표 + 행 정렬 |
| Overview 스파크라인 | SVG 표시 |
| 스파크라인 OFF + 실행 | SVG 사라짐 |
| 한도 도달 시 | 노란 배너 |
| `/pages/proposals` 카드 CTA | "🔍 비슷한 종목 더 찾기" 버튼 |
| CTA 클릭 → Screener | 섹터/시총 prefilled + 자동 실행 |
| `/pages/screener?q=AAPL` | Search 탭에 AAPL 입력 + 자동 실행 |
| 모바일 (<768px) | 탭 가로 스크롤, 결과 sticky 컬럼 |
| 기존 프리셋 로드 | 빈 신규 필드 무시, 정상 동작 |

문제 있으면 해당 Task 로 돌아가서 fix → 재검증.

- [ ] **Step 10.4: 최종 커밋**

CSS 빌드 변경 외 변경 없으면 다음만:

```bash
git add api/static/css/style.css
git commit -m "chore(css): build 산출물 최종 갱신"
```

수동 검증에서 fix 가 발생했다면 해당 fix 별도 커밋.

---

## Verification Checklist

모든 task 완료 후 `superpowers:verification-before-completion` 스킬 흐름:

- [ ] `pytest -v` ALL PASS
- [ ] 위 통합 수동 검증 표 ALL ✅
- [ ] `grep -rn "Stock Picks" api/templates/` → 0건
- [ ] `git log --oneline` 에 커밋 9~10개 (Task 별 1~3개)
- [ ] DB 마이그레이션 변경 없음 (`shared/db/migrations/versions.py` diff 없음)

---

## Out of Scope (다음 Spec 으로)

- Fundamental 데이터 파이프라인 (PER/PBR/배당/ROE) — `stock_fundamentals` 테이블 + `analyzer/universe_sync.py --mode fundamentals`
- 다중 컬럼 정렬
- CSV/Excel export
- 자연어 Smart Filter (Claude SDK)
- 인덱스/ETF 비교 차트
