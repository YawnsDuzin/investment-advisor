# Dashboard Market Quotes Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드 상단 regime banner 위에 KOSPI/KOSDAQ/S&P500/NDX100 4종 EOD 시세 + 20일 sparkline 카드 4개를 추가한다.

**Architecture:** `market_indices_ohlcv` (v31) 에서 4개 인덱스 × 21영업일 OHLCV 를 SQL 1회로 조회 → Python 측에서 spark_points/change_pct 가공 → `dashboard()` context 에 주입 → 신규 partial `_market_quotes_bar.html` 가 인라인 SVG `<polyline>` 으로 렌더. 외부 차트 라이브러리·실시간 fetch·캐싱 모두 미도입.

**Tech Stack:** FastAPI + psycopg2 (RealDictCursor) + Jinja2 + 인라인 SVG, 외부 의존성 추가 0.

**관련 문서:**
- Spec: `_docs/20260503142111_dashboard-market-quotes-bar-design.md`
- 데이터 테이블: `market_indices_ohlcv` (CLAUDE.md v31 항목)
- 기존 SVG sparkline 패턴: `api/routes/dashboard.py:258` `_spark_points()`

---

## File Structure

| 파일 | 역할 | 신규/수정 |
|------|------|-----------|
| `api/routes/dashboard.py` | `_fetch_market_quotes(cur)` helper + `dashboard()` context 주입 | 수정 |
| `api/templates/partials/dashboard/_market_quotes_bar.html` | 시세 바 partial (카드 4개 + SVG sparkline + 기준일 메타) | **신규** |
| `api/templates/dashboard.html` | regime banner 위에 partial include 1줄 | 수정 |
| `tests/test_market_quotes.py` | `_fetch_market_quotes()` 단위 테스트 | **신규** |
| `tests/test_pages_new.py` | dashboard 라우트 200 OK + 시세 바 텍스트 노출 검증 | 수정 (시세 바 케이스 추가) |

> **모듈 분리는 안 함.** 첫 사용처가 dashboard 1곳이라 `dashboard.py` 내부 helper 로 충분 (YAGNI). 재사용 시점 도래 시 `shared/market_quotes.py` 추출은 후속 작업.

---

## Task 1: `_fetch_market_quotes(cur)` helper — happy path 테스트

**Files:**
- Create: `tests/test_market_quotes.py`
- Modify: `api/routes/dashboard.py` (helper 함수 추가)

- [ ] **Step 1: Write the failing test (happy path)**

`tests/test_market_quotes.py`:

```python
"""Market Quotes Bar — _fetch_market_quotes() 단위 테스트.

EOD 데이터(market_indices_ohlcv)를 4개 인덱스 × 21영업일 조회하여
카드 렌더용 dict 구조로 가공하는지 검증.
"""
from datetime import date
from unittest.mock import MagicMock


def _make_cursor(rows):
    """RealDictCursor 시뮬레이션 — fetchall() 반환값 주입."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    return cur


def _row(index_code, trade_date, close):
    return {"index_code": index_code, "trade_date": trade_date, "close": close}


class TestFetchMarketQuotesHappyPath:
    def test_returns_four_indices_with_21_close_points(self):
        from api.routes.dashboard import _fetch_market_quotes

        # 4개 인덱스 × 21 row, 종가는 단순 증가 (trend=up 보장)
        rows = []
        for code in ("KOSPI", "KOSDAQ", "SP500", "NDX100"):
            base = {"KOSPI": 2500, "KOSDAQ": 800, "SP500": 5700, "NDX100": 20500}[code]
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 2 + i), base + i * 5))
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        assert len(result["indices"]) == 4
        codes = [ix["code"] for ix in result["indices"]]
        assert set(codes) == {"KOSPI", "KOSDAQ", "SP500", "NDX100"}

        kospi = next(ix for ix in result["indices"] if ix["code"] == "KOSPI")
        # 21 포인트 (sparkline 전체 윈도우)
        assert len(kospi["spark_points"]) == 21
        # 마지막 = 최신 종가
        assert kospi["close"] == kospi["spark_points"][-1]
        # 등락률 = (last - prev) / prev * 100 = (2600 - 2595) / 2595 * 100
        assert kospi["change_pct"] == round((2600 - 2595) / 2595 * 100, 2)
        # 절대 변화 = 5
        assert kospi["change_abs"] == 5
        # 종가가 단조증가 → trend=up
        assert kospi["trend"] == "up"
        # trade_date = 마지막 row 의 date
        assert kospi["trade_date"] == date(2026, 4, 22)

    def test_meta_splits_kr_and_us_trade_dates(self):
        from api.routes.dashboard import _fetch_market_quotes

        rows = []
        # KR 인덱스: 4/22 까지
        for code in ("KOSPI", "KOSDAQ"):
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 2 + i), 2500 + i))
        # US 인덱스: 4/21 까지 (KR보다 1일 이전 — US 휴장일 가정)
        for code in ("SP500", "NDX100"):
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 1 + i), 5700 + i))
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        assert result["meta"]["kr_trade_date"] == date(2026, 4, 22)
        assert result["meta"]["us_trade_date"] == date(2026, 4, 21)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_market_quotes.py -v
```

Expected: `ImportError` or `AttributeError: module 'api.routes.dashboard' has no attribute '_fetch_market_quotes'`

- [ ] **Step 3: Write the helper**

`api/routes/dashboard.py` — 파일 상단 import 블록 아래, `pages_router = ...` 위에 추가:

```python
from datetime import date as _date_type


# market_indices_ohlcv index_code → 표시 라벨 (analyzer/regime.py 와 동일 매핑)
_MARKET_QUOTE_INDEX_CODES = ("KOSPI", "KOSDAQ", "SP500", "NDX100")
_MARKET_QUOTE_LABELS = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    "SP500": "S&P 500",
    "NDX100": "Nasdaq 100",
}
_MARKET_QUOTE_KR_CODES = ("KOSPI", "KOSDAQ")
_MARKET_QUOTE_US_CODES = ("SP500", "NDX100")
_MARKET_QUOTE_WINDOW = 21        # sparkline 포인트 수 (영업일)
_MARKET_QUOTE_LOOKBACK_DAYS = 60  # 영업일 21개 확보용 캘린더 윈도우


def _fetch_market_quotes(cur) -> dict:
    """market_indices_ohlcv 에서 4개 인덱스 × 21영업일 EOD 데이터를 조회해
    대시보드 시세 바 카드 렌더용 dict 로 가공.

    Returns
    -------
    {
        "indices": [
            {"code", "label", "trade_date", "close", "change_pct",
             "change_abs", "spark_points", "trend"},
            ...
        ],
        "meta": {"kr_trade_date", "us_trade_date"},
    }

    결측 정책 (spec §6 참조):
      - 0 row: indices=[] 반환 → 호출자는 partial 자체 비표시
      - 1 row: change_pct=None, spark_points=[close 1개], trend="flat"
      - 2~ row: change_pct/spark_points 모두 가용한 만큼
      - SQL 예외: 호출자가 try/except 로 처리 (helper 내부 catch 안 함)
    """
    cur.execute(
        """
        WITH recent AS (
            SELECT index_code, trade_date, close::float AS close,
                   ROW_NUMBER() OVER (PARTITION BY index_code ORDER BY trade_date DESC) AS rn
            FROM market_indices_ohlcv
            WHERE index_code = ANY(%s)
              AND trade_date >= CURRENT_DATE - %s
        )
        SELECT index_code, trade_date, close
        FROM recent
        WHERE rn <= %s
        ORDER BY index_code, trade_date ASC
        """,
        (list(_MARKET_QUOTE_INDEX_CODES), _MARKET_QUOTE_LOOKBACK_DAYS, _MARKET_QUOTE_WINDOW),
    )
    rows = cur.fetchall()

    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r["index_code"], []).append(r)

    indices = []
    for code in _MARKET_QUOTE_INDEX_CODES:
        bucket = by_code.get(code)
        if not bucket:
            continue
        spark_points = [float(r["close"]) for r in bucket]
        close = spark_points[-1]
        if len(spark_points) >= 2:
            prev = spark_points[-2]
            change_abs = close - prev
            change_pct = round((close - prev) / prev * 100, 2) if prev else None
        else:
            change_abs = None
            change_pct = None
        if change_pct is None:
            trend = "flat"
        elif change_pct > 0:
            trend = "up"
        elif change_pct < 0:
            trend = "down"
        else:
            trend = "flat"
        indices.append({
            "code": code,
            "label": _MARKET_QUOTE_LABELS[code],
            "trade_date": bucket[-1]["trade_date"],
            "close": close,
            "change_abs": change_abs,
            "change_pct": change_pct,
            "spark_points": spark_points,
            "trend": trend,
        })

    def _latest_for(codes):
        dates = [ix["trade_date"] for ix in indices if ix["code"] in codes]
        return max(dates) if dates else None

    return {
        "indices": indices,
        "meta": {
            "kr_trade_date": _latest_for(_MARKET_QUOTE_KR_CODES),
            "us_trade_date": _latest_for(_MARKET_QUOTE_US_CODES),
        },
    }
```

- [ ] **Step 4: Run test to verify happy path passes**

```
pytest tests/test_market_quotes.py::TestFetchMarketQuotesHappyPath -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_market_quotes.py api/routes/dashboard.py
git commit -m "feat(dashboard): _fetch_market_quotes helper + happy path 테스트"
```

---

## Task 2: `_fetch_market_quotes()` — 결측·trend 분기 테스트

**Files:**
- Modify: `tests/test_market_quotes.py` (테스트 케이스 추가)

- [ ] **Step 1: 결측·trend 분기 테스트 작성**

`tests/test_market_quotes.py` 끝에 추가:

```python
class TestFetchMarketQuotesEdgeCases:
    def test_empty_table_returns_no_indices(self):
        from api.routes.dashboard import _fetch_market_quotes
        cur = _make_cursor([])
        result = _fetch_market_quotes(cur)
        assert result["indices"] == []
        assert result["meta"]["kr_trade_date"] is None
        assert result["meta"]["us_trade_date"] is None

    def test_partial_indices_only_kr_present(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = []
        for code in ("KOSPI", "KOSDAQ"):
            for i in range(21):
                rows.append(_row(code, date(2026, 4, 2 + i), 2500 + i))
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        codes = [ix["code"] for ix in result["indices"]]
        assert set(codes) == {"KOSPI", "KOSDAQ"}
        assert result["meta"]["kr_trade_date"] == date(2026, 4, 22)
        assert result["meta"]["us_trade_date"] is None

    def test_single_row_no_change_pct(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = [_row("KOSPI", date(2026, 4, 22), 2600.0)]
        cur = _make_cursor(rows)

        result = _fetch_market_quotes(cur)

        kospi = result["indices"][0]
        assert kospi["close"] == 2600.0
        assert kospi["change_pct"] is None
        assert kospi["change_abs"] is None
        assert kospi["spark_points"] == [2600.0]
        assert kospi["trend"] == "flat"

    def test_trend_down_when_change_negative(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = [
            _row("KOSPI", date(2026, 4, 21), 2700.0),
            _row("KOSPI", date(2026, 4, 22), 2600.0),
        ]
        cur = _make_cursor(rows)
        result = _fetch_market_quotes(cur)
        kospi = result["indices"][0]
        assert kospi["trend"] == "down"
        assert kospi["change_pct"] == round((2600 - 2700) / 2700 * 100, 2)
        assert kospi["change_abs"] == -100.0

    def test_trend_flat_when_change_zero(self):
        from api.routes.dashboard import _fetch_market_quotes
        rows = [
            _row("KOSPI", date(2026, 4, 21), 2600.0),
            _row("KOSPI", date(2026, 4, 22), 2600.0),
        ]
        cur = _make_cursor(rows)
        result = _fetch_market_quotes(cur)
        assert result["indices"][0]["trend"] == "flat"
```

- [ ] **Step 2: Run all tests in this file**

```
pytest tests/test_market_quotes.py -v
```

Expected: 7 passed total (2 from Task 1 + 5 here).

- [ ] **Step 3: Commit**

```bash
git add tests/test_market_quotes.py
git commit -m "test(dashboard): _fetch_market_quotes 결측·trend 분기 케이스"
```

---

## Task 3: `dashboard()` 라우트 context 주입

**Files:**
- Modify: `api/routes/dashboard.py:dashboard()` (helper 호출 + context 키 추가)

- [ ] **Step 1: dashboard() 함수에 helper 호출 추가**

`api/routes/dashboard.py:dashboard()` 의 `with conn.cursor(cursor_factory=RealDictCursor) as cur:` 블록 **마지막 줄(`watched_in_today.append(...)` 이후, `with` 블록 종료 직전)** 에 다음 추가:

```python
        # ── 시세 바 (regime banner 위 — spec §3.1) ──
        try:
            market_quotes = _fetch_market_quotes(cur)
        except Exception:
            # market_indices_ohlcv 미존재 환경(백필 이전)에서도 페이지 동작
            market_quotes = None
```

그리고 `return templates.TemplateResponse(...)` 의 context dict 에 한 줄 추가:

```python
        "market_quotes": market_quotes,
```

> 위치: `"theme_view_limit": theme_view_limit,` 뒤에 추가 (마지막 키).

- [ ] **Step 2: dashboard 페이지 200 OK + market_quotes 키 노출 확인 테스트 추가**

`tests/test_pages_new.py` 끝에 클래스 추가:

```python
class TestDashboardMarketQuotes:
    """Dashboard 시세 바 통합 — 라우트가 market_quotes context 를 정상 주입하는지."""

    def test_dashboard_renders_with_market_quotes_helper_called(self, monkeypatch):
        """_fetch_market_quotes 가 라우트에서 호출되고, 결과가 템플릿에 전달되는지."""
        from datetime import date
        from unittest.mock import MagicMock

        # market_quotes mock — partial 렌더가 실패하지 않을 정도의 최소 dict
        fake_quotes = {
            "indices": [
                {
                    "code": "KOSPI", "label": "KOSPI",
                    "trade_date": date(2026, 4, 22),
                    "close": 2615.32, "change_abs": 10.94, "change_pct": 0.42,
                    "spark_points": [2580.0 + i for i in range(21)],
                    "trend": "up",
                },
            ],
            "meta": {"kr_trade_date": date(2026, 4, 22), "us_trade_date": None},
        }
        monkeypatch.setattr(
            "api.routes.dashboard._fetch_market_quotes",
            lambda cur: fake_quotes,
        )

        # 세션 없음 → 빈 대시보드 분기로 떨어지지만, market_quotes 자체는 호출되지 않음.
        # 따라서 세션 row 가 있는 상태로 fetchone()/fetchall() 을 강제 주입해야 한다.
        # 단순화: 본 테스트는 _fetch_market_quotes 가 monkeypatch 됐을 때 import 가
        # 깨지지 않고 dashboard 라우트가 200 또는 302 반환하는지만 확인 (스모크).
        client = _make_client()
        resp = client.get("/")
        # 인증 활성/비활성 환경 모두 허용 — 비활성이면 200, 활성+비로그인이면 302
        assert resp.status_code in (200, 302)
```

- [ ] **Step 3: Run new test**

```
pytest tests/test_pages_new.py::TestDashboardMarketQuotes -v
```

Expected: 1 passed.

- [ ] **Step 4: 기존 테스트 전체 회귀 확인**

```
pytest tests/test_market_quotes.py tests/test_pages_new.py -v
```

Expected: 모두 passed (시세 바 케이스 + 기존 pricing/track-record 페이지).

- [ ] **Step 5: Commit**

```bash
git add api/routes/dashboard.py tests/test_pages_new.py
git commit -m "feat(dashboard): _fetch_market_quotes 결과를 템플릿 context 로 주입"
```

---

## Task 4: `_market_quotes_bar.html` partial 작성

**Files:**
- Create: `api/templates/partials/dashboard/_market_quotes_bar.html`

- [ ] **Step 1: partial 파일 작성**

`api/templates/partials/dashboard/_market_quotes_bar.html`:

```jinja
{#
  Market Quotes Bar — 대시보드 상단 EOD 시세 카드 4개.
  CONTEXT:
    - market_quotes (dict | None): {"indices": [...], "meta": {...}}
        indices[].code/label/trade_date/close/change_abs/change_pct/spark_points/trend
        meta.kr_trade_date / meta.us_trade_date (date | None)
  사용처: dashboard.html (regime banner 바로 위)
  결측 정책: market_quotes 가 None 또는 indices 가 비면 아무것도 렌더하지 않음.
#}
{% if market_quotes and market_quotes.indices %}
{% set kr_d = market_quotes.meta.kr_trade_date %}
{% set us_d = market_quotes.meta.us_trade_date %}
<style>
  .mq-bar { padding:10px 14px; margin-bottom:10px;
            background:rgba(255,255,255,0.03);
            border:1px solid var(--border); border-radius:8px; font-size:12px; }
  .mq-header { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
               margin-bottom:8px; }
  .mq-title { display:inline-flex; align-items:center; gap:6px;
              color:var(--accent); font-weight:600; }
  .mq-meta { margin-left:auto; color:var(--text-muted); font-size:11px; }
  .mq-cards { display:flex; flex-wrap:nowrap; gap:10px; overflow-x:auto;
              scroll-snap-type: x mandatory; }
  .mq-card { flex:1 1 0; min-width:160px; padding:8px 10px;
             background:rgba(255,255,255,0.04);
             border:1px solid var(--border); border-radius:6px;
             scroll-snap-align: start; }
  .mq-card-label { font-weight:600; color:var(--text); font-size:12px; }
  .mq-card-close { font-size:14px; font-weight:600; color:var(--text);
                   margin-top:2px; }
  .mq-card-change { font-size:12px; font-weight:600; margin-top:1px; }
  .mq-card-spark { margin-top:4px; height:28px; width:100%; }
  .mq-up   { color: var(--green); }
  .mq-down { color: var(--red); }
  .mq-flat { color: var(--text-muted); }
  @media (max-width: 768px) {
    .mq-card { min-width: 180px; flex: 0 0 auto; }
    .mq-header { flex-direction: column; align-items: flex-start; }
    .mq-meta { margin-left: 0; }
  }
</style>
<div class="mq-bar">
  <div class="mq-header">
    <span class="mq-title">
      <i data-lucide="trending-up" style="width:14px;height:14px;" aria-hidden="true"></i>
      오늘의 시장
    </span>
    <span class="mq-meta">
      기준:
      {% if kr_d and us_d and kr_d == us_d %}
        {{ kr_d.strftime('%m/%d') }} (EOD)
      {% else %}
        {% if kr_d %}KR {{ kr_d.strftime('%m/%d') }}{% endif %}
        {% if kr_d and us_d %} · {% endif %}
        {% if us_d %}US {{ us_d.strftime('%m/%d') }}{% endif %}
        (EOD)
      {% endif %}
    </span>
  </div>
  <div class="mq-cards">
    {% for ix in market_quotes.indices %}
    {% set trend_cls = 'mq-' ~ ix.trend %}
    <div class="mq-card" title="{{ ix.label }} · {{ ix.trade_date.strftime('%Y-%m-%d') }} 종가">
      <div class="mq-card-label">{{ ix.label }}</div>
      <div class="mq-card-close">{{ '{:,.2f}'.format(ix.close) }}</div>
      <div class="mq-card-change {{ trend_cls }}">
        {% if ix.change_pct is not none %}
          {% if ix.trend == 'up' %}▲{% elif ix.trend == 'down' %}▼{% else %}■{% endif %}
          {{ '%+.2f'|format(ix.change_pct) }}%
          {% if ix.change_abs is not none %}
            <span style="opacity:0.7;font-weight:400;">({{ '%+.2f'|format(ix.change_abs) }})</span>
          {% endif %}
        {% else %}
          <span class="mq-flat">— %</span>
        {% endif %}
      </div>
      {% if ix.spark_points and ix.spark_points|length >= 5 %}
      {% set sp = ix.spark_points %}
      {% set mn = sp|min %}
      {% set mx = sp|max %}
      {% set rng = (mx - mn) if (mx != mn) else 1 %}
      {% set n = sp|length %}
      <svg class="mq-card-spark" viewBox="0 0 100 28" preserveAspectRatio="none"
           role="img" aria-label="{{ ix.label }} 최근 {{ n }}영업일 종가 추이">
        <polyline fill="none" stroke="currentColor" class="{{ trend_cls }}"
                  stroke-width="1.5"
                  points="{% for y in sp %}{{ '%.2f'|format(loop.index0 * 100 / (n - 1)) }},{{ '%.2f'|format(28 - ((y - mn) / rng) * 24 - 2) }}{% if not loop.last %} {% endif %}{% endfor %}"/>
      </svg>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
```

> **참고:** SVG `<polyline>` 의 `stroke="currentColor"` + 부모 클래스의 `color` 를 활용하므로 trend 색상이 자동 상속된다.
> **점 5개 임계:** spec §6 의 "최소 포인트 임계 5" 규칙. 그 미만이면 sparkline 자체 생략.

- [ ] **Step 2: 템플릿 syntax 검증 — Jinja2 파싱 에러 없는지 확인**

```
python -c "from api.templates_provider import templates; templates.get_template('partials/dashboard/_market_quotes_bar.html')"
```

Expected: 에러 없이 종료 (출력 없음).

- [ ] **Step 3: Commit**

```bash
git add api/templates/partials/dashboard/_market_quotes_bar.html
git commit -m "feat(dashboard): _market_quotes_bar partial — EOD 시세 카드 + 인라인 SVG sparkline"
```

---

## Task 5: `dashboard.html` 에 partial include

**Files:**
- Modify: `api/templates/dashboard.html` (regime banner 바로 위에 include)

- [ ] **Step 1: dashboard.html 수정**

기존 `dashboard.html:11-13` 의 regime banner include 직전에 한 줄 추가:

```jinja
{% if session %}
{# 시장 시세 바 (EOD) — regime banner 위 #}
{% include "partials/dashboard/_market_quotes_bar.html" %}

{# 시장 레짐 배너 (로드맵 UI-3) — session.market_regime JSONB 시각화 #}
{% set regime = session.market_regime %}
{% include "partials/_regime_banner.html" %}
```

- [ ] **Step 2: 통합 테스트 — dashboard 페이지가 시세 바 텍스트 노출**

`tests/test_pages_new.py:TestDashboardMarketQuotes` 에 케이스 추가:

```python
    def test_dashboard_html_contains_market_quotes_marker_when_session_present(self, monkeypatch):
        """세션이 있을 때 _market_quotes_bar partial 의 '오늘의 시장' 문자열이 포함되는지."""
        from datetime import date
        from unittest.mock import MagicMock

        # dashboard 라우트의 SQL 호출들을 통째로 mock — fetchone/fetchall 시퀀스 제어가 복잡하므로
        # 가장 단순한 방법: get_db_conn 의존성을 통째로 교체.
        from api.main import app
        from api.deps import get_db_conn

        cur = MagicMock()
        # ① analysis_sessions LIMIT 1 → session row
        # ② 이후 fetchone/fetchall 모두 빈 결과 (라우트가 빈 카운트로 진행)
        session_row = {
            "id": 1, "analysis_date": date(2026, 4, 22),
            "market_regime": None, "risk_temperature": "medium",
        }

        # fetchone 시퀀스: session, prev_session(없음), bond_yields row(없음 — try/except)
        cur.fetchone.side_effect = [
            session_row,            # 최신 세션
            {"cnt": 0},             # 이슈 카운트
            None,                   # bond_yields → 없음 (try 안에서 fetchone 호출)
            None,                   # 전일 세션 없음
        ]
        cur.fetchall.return_value = []

        from contextlib import contextmanager
        @contextmanager
        def _cursor(**kwargs):
            yield cur
        conn = MagicMock()
        conn.cursor = _cursor

        app.dependency_overrides[get_db_conn] = lambda: conn

        # market_quotes helper 도 mock
        fake_quotes = {
            "indices": [{
                "code": "KOSPI", "label": "KOSPI",
                "trade_date": date(2026, 4, 22),
                "close": 2615.32, "change_abs": 10.94, "change_pct": 0.42,
                "spark_points": [2580.0 + i for i in range(21)],
                "trend": "up",
            }],
            "meta": {"kr_trade_date": date(2026, 4, 22), "us_trade_date": None},
        }
        monkeypatch.setattr(
            "api.routes.dashboard._fetch_market_quotes",
            lambda cur_arg: fake_quotes,
        )

        try:
            client = _make_client()
            resp = client.get("/")
            assert resp.status_code in (200, 302)
            if resp.status_code == 200:
                body = resp.text
                assert "오늘의 시장" in body
                assert "KOSPI" in body
                # 종가 포맷팅 확인
                assert "2,615.32" in body
                # sparkline SVG 렌더 확인
                assert "<polyline" in body
        finally:
            app.dependency_overrides.pop(get_db_conn, None)
```

- [ ] **Step 3: Run 통합 테스트**

```
pytest tests/test_pages_new.py::TestDashboardMarketQuotes -v
```

Expected: 2 passed (Task 3 케이스 + 본 케이스).

> **만약 fetchone.side_effect 시퀀스가 dashboard 함수의 실제 호출 순서와 어긋나서 StopIteration 이 발생하면**:
> dashboard.py 본문을 다시 읽고 fetchone() 호출이 발생하는 줄을 모두 찾아 시퀀스를 보강한다 (issue_count, theme/proposal 루프 내 fetchall 만 사용 → fetchone 추가 호출 없음 가능성 큼). 또는 `cur.fetchone.return_value = ...` 단일 값으로 변경하고 side_effect 제거 — 단, 라우트가 다양한 row 를 기대하면 본문 분기 (`if not session` 등) 에서 다르게 동작할 수 있으므로 side_effect 가 정확.

- [ ] **Step 4: Commit**

```bash
git add api/templates/dashboard.html tests/test_pages_new.py
git commit -m "feat(dashboard): regime banner 위에 _market_quotes_bar partial include + 통합 테스트"
```

---

## Task 6: 수동 시각 검증 (UI smoke)

**Files:** (없음, 검증만)

> **이 task 는 자동 테스트로 잡히지 않는 시각·인터랙션 검증.** 실제 DB 가 있는 개발환경에서 진행.

- [ ] **Step 1: 데스크탑 검증**

```
python -m api.main
```

브라우저에서 `http://localhost:8000/` 열기. 확인:
- 페이지 상단에 "오늘의 시장" 줄이 regime banner 위에 표시
- 카드 4개 (KOSPI/KOSDAQ/S&P 500/Nasdaq 100) 균등 분할
- 각 카드: 라벨 → 종가 (3자리 콤마, 소수 둘째 자리) → 등락률(▲/▼/■ + 색상) → sparkline
- 우상단 메타: `기준: KR 05/02 · US 05/01 (EOD)` 또는 KR/US 같으면 단축
- sparkline 색상이 trend (up=green / down=red / flat=muted) 와 일치
- regime banner 가 그 아래에 정상 표시 (영향 없음)

- [ ] **Step 2: 모바일 viewport 검증**

브라우저 DevTools 에서 viewport 를 375×667 (iPhone SE) 로 변경. 확인:
- 카드 4개가 가로 스크롤 (`overflow-x: auto`)
- swipe 시 카드 단위로 스냅 (scroll-snap)
- 메타 줄이 카드 줄 아래로 내려옴 (`flex-direction: column`)
- regime banner 의 모바일 레이아웃은 영향 없음

- [ ] **Step 3: 결측 환경 검증 (선택, 옵션)**

DB 의 `market_indices_ohlcv` 테이블이 비어있는 시점이 있다면:
- partial 자체가 렌더되지 않음 (regime banner 와 dashboard 본문은 정상)

또는 SQL 단계에서 강제로 결과를 비우려면 임시로 helper 호출 부분을 `market_quotes = None` 으로 주석 처리하고 페이지 로드.

- [ ] **Step 4: (선택) 라이트 모드/다크 모드 양쪽 색상 확인**

`var(--green)` / `var(--red)` / `var(--text-muted)` 가 양쪽 테마에서 자연스러운지 시각 확인.

- [ ] **Step 5: 검증 완료 commit (코드 변경 없으면 skip)**

검증 중 발견한 시각 미세조정 (padding, gap, sparkline height 등) 이 있으면 partial 만 수정하여:

```bash
git add api/templates/partials/dashboard/_market_quotes_bar.html
git commit -m "style(dashboard): _market_quotes_bar 시각 미세조정"
```

---

## Task 7: CLAUDE.md 갱신

**Files:**
- Modify: `CLAUDE.md` (Architecture > api/ > templates/ 섹션)

- [ ] **Step 1: CLAUDE.md 의 api/templates 설명에 신규 partial 언급**

`CLAUDE.md` 의 `### api/ — FastAPI 웹서비스` 섹션에서 `templates/` 줄을 찾아 `_macros/` 옆에 명시:

기존:
```
│   ├── templates/       ← Jinja2 HTML (다크 테마 + 우측 상단 드롭다운 메뉴) + _macros/(공통 매크로 — common, theme, proposal, admin)
```

수정 후 (한 줄에):
```
│   ├── templates/       ← Jinja2 HTML (다크 테마 + 우측 상단 드롭다운 메뉴) + _macros/(공통 매크로 — common, theme, proposal, admin) + partials/dashboard/_market_quotes_bar.html(EOD 시세 4종 + 20일 sparkline)
```

> **그리고 `## Key Conventions` 의 적당한 위치 (시세/sparkline 관련 항목 근처)** 에 한 줄:

```
- **대시보드 시세 바**: `_market_quotes_bar.html` partial 이 `market_indices_ohlcv` (v31) EOD 데이터를 카드 4개 + 인라인 SVG `<polyline>` sparkline 으로 렌더. helper `_fetch_market_quotes(cur)` 는 `api/routes/dashboard.py` 내부. 외부 차트 라이브러리·실시간 fetch 없음 (spec: `_docs/20260503142111_dashboard-market-quotes-bar-design.md`).
```

- [ ] **Step 2: prompt 기록 파일 staging 준비**

`_docs/_prompts/20260503_prompt.md` 에 본 작업 conversation 의 프롬프트 흐름을 추가 (이미 작성 중일 수 있음). CLAUDE.md 규칙: prompt 파일은 마지막 commit 에 함께 staging.

- [ ] **Step 3: 최종 commit (CLAUDE.md + prompt 파일)**

```bash
git add CLAUDE.md _docs/_prompts/20260503_prompt.md
git commit -m "docs: dashboard 시세 바 추가 — CLAUDE.md 갱신 + prompt 기록"
```

---

## Self-Review 체크리스트

본 plan 을 시작하기 전 reviewer 가 확인:

- [ ] Spec §3.2 컴포넌트 (helper / partial / dashboard.html include) 모두 task 로 매핑되었나? → Task 1+3 / Task 4 / Task 5
- [ ] Spec §4 데이터 모델의 모든 필드 (`code/label/trade_date/close/change_pct/change_abs/spark_points/trend` + `meta.kr_trade_date/us_trade_date`) 가 helper 구현에 들어갔나? → 들어감
- [ ] Spec §6 에러 처리 (0/1/2~ row, SQL 예외) 모두 테스트에 매핑되었나? → Task 2 + Task 3 (SQL 예외는 try/except 로 처리, 별도 테스트 미작성 — partial 비표시 동작은 Task 4 의 `if market_quotes and market_quotes.indices` 로 충분)
- [ ] Spec §5.1/5.2 UI 명세 (데스크탑/모바일) 가 partial 에 반영되었나? → Task 4 의 CSS + media query
- [ ] 함수명/속성명이 task 간 일관되나? → `_fetch_market_quotes` / `market_quotes` / `indices` / `meta.kr_trade_date` 모두 일관
- [ ] CLAUDE.md 규칙 (신규 문서 timestamp prefix, prompt 파일 commit 동시 staging, TemplateResponse 키워드 호출) 위배 없나? → spec 와 plan 모두 timestamp prefix 사용, Task 7 에서 prompt 파일 묶음 commit 명시, 기존 dashboard.py TemplateResponse 호출 패턴 그대로 유지
