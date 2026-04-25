# Stock Cockpit Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cockpit 페이지에 § 2-B 정량 팩터 레이더 + § 3 시장 레짐 + 섹터 팩터 분위 표 + § 5 KRX 확장 추가. Phase 1 backlog (CKPT-1/2/3) 동시 처리. JS 파일 분리.

**Architecture:** 프론트는 `static/js/stock_cockpit.js` 신설로 인라인 ~700줄 → 외부 파일. § 2-B/§ 5 차트는 Chart.js v4 CDN. § 3 시장 레짐은 기존 `partials/_regime_banner.html` server-side include 재사용. 섹터 팩터 분위는 신규 `compute_sector_pctiles()` + `/api/stocks/{ticker}/sector-stats` API. `factor_snapshot` raw 와 `krx_extended` 는 기존 `/overview` 응답에 필드 추가로 노출.

**Tech Stack:** FastAPI, psycopg2, Jinja2, lightweight-charts (기존), Chart.js v4.4.0 (신규), Vanilla JS. 테스트 pytest + tests/conftest.py mock.

**Spec:** [`_docs/20260425190117_stock-cockpit-phase2-design.md`](20260425190117_stock-cockpit-phase2-design.md)

---

## File Structure

| 액션 | 경로 | 책임 |
|---|---|---|
| Create | `api/static/js/stock_cockpit.js` | Phase 1 인라인 IIFE 4개 통째 + Phase 2 추가 IIFE 3개 (§ 2-B / § 3 섹터표 / § 5) |
| Modify | `api/templates/stock_cockpit.html` | 인라인 `<script>` 본문 → external 참조. § 3 자리에 `{% include "partials/_regime_banner.html" %}`. § 2-B / § 5 마크업 추가. Chart.js CDN 추가. |
| Modify | `api/routes/stocks.py` | `stock_fundamentals_page` 가 latest session 의 `market_regime` 을 ctx 에 주입. `get_stock_overview` 응답에 `factor_snapshot` raw + `krx_extended` 추가. 신규 `get_stock_sector_stats` 핸들러. |
| Modify | `analyzer/factor_engine.py` | `compute_sector_pctiles(db_cfg, ticker, market) -> dict` 신규 함수 + `_compute_sector_factors()` 헬퍼. |
| Modify | `tests/test_stock_cockpit.py` | 각 task 마다 신규 테스트 (TDD). |

---

## Task 1: JS 파일 분리 — Phase 1 인라인 IIFE 통째 이동

**Files:**
- Create: `api/static/js/stock_cockpit.js`
- Modify: `api/templates/stock_cockpit.html` (인라인 `<script>` 본문 제거 + external 참조)

회귀 0 보장. CSS 와 lightweight-charts CDN 은 그대로 인라인 유지.

- [ ] **Step 1.1: Write the failing test (외부 JS 파일 fetch + 시그니처)**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 추가:

```python
    def test_cockpit_page_uses_external_js(self, patched_base_ctx_conn):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # external JS 파일 참조
        assert '/static/js/stock_cockpit.js' in body
        # 인라인 IIFE 시그니처가 페이지 HTML 에서 제거됨
        # (Hero 의 fetch '/overview' 호출이 인라인 코드에 없어야 함 — 외부 파일로 이동)
        assert "fetch('/api/stocks/' + encodeURIComponent(ticker) + '/overview'" not in body

    def test_external_js_file_served(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        assert resp.status_code == 200
        body = resp.text
        # Phase 1 의 4 개 IIFE 시그니처 모두 외부 파일에 존재
        assert "window.__cockpit" in body
        assert "function _compute" not in body  # 백엔드 함수가 아닌지 확인
        assert "// ── § 1 가격 차트 ──" in body
        assert "// ── § 2-A 벤치마크 상대성과 ──" in body
        assert "// ── § 6 추천 이력 타임라인 ──" in body
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_uses_external_js tests/test_stock_cockpit.py::TestStockCockpitPage::test_external_js_file_served -v`
Expected: FAIL — 외부 JS 파일 없음, 인라인 코드 그대로 존재.

- [ ] **Step 1.3: Create `api/static/js/stock_cockpit.js`**

`api/templates/stock_cockpit.html` 의 `{% block scripts %}` 안 `<script>` 본문(IIFE-0 + § 1 + § 2-A + § 6, 약 480줄) 통째를 새 파일로 이동.

`api/templates/stock_cockpit.html` 에서 현재 `<script>` 안 본문(```javascript ... ```) 을 정확히 발췌해서 `api/static/js/stock_cockpit.js` 의 내용으로 사용. 이전 파일 내용 그대로, 한 글자도 변경 없이 복사.

`stock_cockpit.js` 첫 줄에 헤더 주석 추가:

```javascript
/* Stock Cockpit — Phase 1 (Hero/§1/§2-A/§6) + Phase 2 (§2-B/§3 sector/§5)
 * Phase 1 인라인에서 분리됨 (Phase 2 Task 1).
 * 의존: lightweight-charts (CDN, 페이지 인라인), Chart.js (Phase 2 Task 6 부터).
 */
```

- [ ] **Step 1.4: Modify `api/templates/stock_cockpit.html` `{% block scripts %}`**

`{% block scripts %}` 안에서 `<script>...</script>` 본문 (인라인 IIFE 4개) 을 통째 제거하고, 그 자리에 외부 참조로 교체.

CDN 라인은 유지하되, 인라인 IIFE 들은 외부 파일 참조로 대체:

```html
{% block scripts %}
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
/* ... 기존 인라인 CSS 그대로 ... */
</style>
<script src="/static/js/stock_cockpit.js" defer></script>
{% endblock %}
```

`defer` 속성은 외부 JS 가 DOM 파싱 후 실행되도록 보장 (`document.getElementById('stock-cockpit')` 가 항상 존재).

- [ ] **Step 1.5: Run all cockpit tests + smoke**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 14 (Phase 1) + 2 (신규) = 16 PASSED

- [ ] **Step 1.6: Commit**

```bash
git add api/static/js/stock_cockpit.js api/templates/stock_cockpit.html tests/test_stock_cockpit.py
git commit -m "refactor(cockpit): Phase 1 인라인 IIFE → static/js/stock_cockpit.js 분리"
```

---

## Task 2: CKPT-1 — 차트 에러 경로 overlay 패턴

**Files:**
- Modify: `api/static/js/stock_cockpit.js` (§ 1 + § 2-A IIFE)

문제: 에러 경로의 `container.innerHTML = '...'` 가 lightweight-charts 인스턴스의 DOM 을 파괴 → 이후 토글 시 setData() 예외. overlay div 패턴으로 차트 보존.

- [ ] **Step 2.1: Write the failing test (overlay 시그니처)**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 추가:

```python
    def test_chart_uses_overlay_pattern(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        # overlay 패턴 시그니처 — innerHTML 에러 출력 제거
        assert "container.innerHTML = '<div class=\"chart-placeholder\">차트 데이터 조회 실패</div>'" not in body
        assert "container.innerHTML = '<div class=\"chart-placeholder\">벤치마크 데이터 조회 실패</div>'" not in body
        # 새 overlay 시그니처 — class="chart-overlay"
        assert "chart-overlay" in body
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_chart_uses_overlay_pattern -v`
Expected: FAIL

- [ ] **Step 2.3: Refactor § 1 chart IIFE**

`api/static/js/stock_cockpit.js` 의 § 1 IIFE 안. 다음 위치를 수정:

a) IIFE 초입(차트 인스턴스 생성 직후)에 overlay 셋업 추가:

```javascript
  var container = document.getElementById('price-chart');
  container.innerHTML = '';
  container.style.position = 'relative';
  var chart = LightweightCharts.createChart(container, {
    /* ... 기존 옵션 그대로 ... */
  });

  // 에러 표시용 overlay (차트 인스턴스 보존)
  var overlay = document.createElement('div');
  overlay.className = 'chart-overlay';
  overlay.style.cssText =
    'position:absolute;top:0;left:0;width:100%;height:100%;display:none;' +
    'align-items:center;justify-content:center;text-align:center;color:var(--text-muted);' +
    'background:var(--bg-card);border-radius:10px;z-index:10;';
  container.appendChild(overlay);

  function showOverlay(msg) {
    overlay.textContent = msg;
    overlay.style.display = 'flex';
  }
  function hideOverlay() {
    overlay.style.display = 'none';
  }
```

b) `applyData(d)` 에서 빈 시리즈 처리 — `container.innerHTML = '...'` 제거하고 overlay 사용:

```javascript
  function applyData(d) {
    if (!d.series || !d.series.length) {
      showOverlay('OHLCV 데이터 수집 대기 중');
      return;
    }
    hideOverlay();
    /* ... 나머지 setData 로직 그대로 ... */
  }
```

c) 초기 로드 catch 블록 — innerHTML 대신 overlay:

```javascript
  loadOhlcv(currentRange).then(applyData)
    .catch(function() {
      showOverlay('차트 데이터 조회 실패');
    });
```

d) 기간 토글 catch — console.warn 만 (overlay 띄우지 않음, 이전 차트 유지):

기존 코드 유지 (`console.warn('차트 재로드 실패')`) — overlay 안 띄우는 이유는 토글 실패 시 직전 데이터가 화면에 남아있으므로 사용자가 새로고침하면 됨.

- [ ] **Step 2.4: Refactor § 2-A benchmark IIFE — 동일 패턴**

§ 2-A IIFE 의 `var container = document.getElementById('benchmark-chart');` 직후에 동일한 overlay 셋업. `loadAndRender(benchCode)` 의 두 에러 경로 (`'데이터 부족'` 과 `'벤치마크 데이터 조회 실패'`) 모두 `container.innerHTML = '...'` → `showOverlay('...')` 로 교체.

성공 경로 첫 줄에 `hideOverlay();` 추가하여 이전 에러 overlay 가 남아있을 경우 클리어.

- [ ] **Step 2.5: Run all cockpit tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 17 PASSED

- [ ] **Step 2.6: Commit**

```bash
git add api/static/js/stock_cockpit.js tests/test_stock_cockpit.py
git commit -m "fix(cockpit): CKPT-1 차트 에러 경로 overlay 패턴 — 차트 인스턴스 보존"
```

---

## Task 3: CKPT-2 + CKPT-3 — § 2-A 정규화 갭 처리 + stockCache

**Files:**
- Modify: `api/static/js/stock_cockpit.js` (§ 2-A IIFE)

CKPT-2: 한국·미국 거래일 캘린더 차이로 `stockData[0].date` 와 `benchData[0].date` 가 commonStart 후에도 첫 행이 어긋날 수 있음 → 양쪽 모두 존재하는 첫 거래일을 기준으로 정규화.

CKPT-3: 토글마다 stock OHLCV 재조회 제거 — IIFE 스코프 `stockCache`.

- [ ] **Step 3.1: Write the failing test**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 추가:

```python
    def test_benchmark_iife_uses_cache_and_alignment(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        # § 2-A 의 stockCache 시그니처
        assert "stockCache" in body
        # 양쪽 모두 존재하는 첫 거래일 정렬 시그니처
        assert "commonAlignedStart" in body or "alignFirstCommonDate" in body
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_benchmark_iife_uses_cache_and_alignment -v`
Expected: FAIL

- [ ] **Step 3.3: Apply CKPT-3 stockCache + CKPT-2 alignment**

§ 2-A IIFE 안. 다음 두 가지 변경:

a) **stockCache** — IIFE 스코프 변수 도입, `loadAndRender` 가 첫 호출 시만 stock OHLCV fetch:

```javascript
  var stockCache = null;  // {series: [...], ...}

  function fetchStock() {
    if (stockCache) return Promise.resolve(stockCache);
    var stockUrl = '/api/stocks/' + encodeURIComponent(c.ticker) + '/ohlcv?days=360' +
                   (c.market ? '&market=' + encodeURIComponent(c.market) : '');
    return fetch(stockUrl).then(function(r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function(d) { stockCache = d; return d; });
  }

  function loadAndRender(benchCode) {
    var benchUrl = '/api/indices/' + benchCode + '/ohlcv?days=360';
    Promise.all([
      fetchStock(),
      fetch(benchUrl).then(function(r) { return r.ok ? r.json() : Promise.reject(); }),
    ]).then(function(results) {
      /* ... */
    }).catch(function() {
      showOverlay('벤치마크 데이터 조회 실패');
    });
  }
```

b) **CKPT-2 alignment** — 양쪽에 모두 존재하는 첫 거래일 기준 정규화:

`loadAndRender` 의 then 본문 안에서 정렬 로직을 다음으로 교체:

```javascript
      var stockData = results[0].series || [];
      var benchData = results[1].series || [];
      if (!stockData.length || !benchData.length) {
        showOverlay('데이터 부족');
        return;
      }
      hideOverlay();

      // CKPT-2: 양쪽 모두 존재하는 첫 거래일을 기준일로 통일
      var benchDates = new Set(benchData.map(function(p) { return p.date; }));
      var commonAlignedStart = null;
      for (var i = 0; i < stockData.length; i++) {
        if (benchDates.has(stockData[i].date)) {
          commonAlignedStart = stockData[i].date;
          break;
        }
      }
      if (!commonAlignedStart) {
        showOverlay('두 시리즈에 공통 거래일이 없음');
        return;
      }
      var s = stockData.filter(function(p) { return p.date >= commonAlignedStart; });
      var b = benchData.filter(function(p) { return p.date >= commonAlignedStart; });
      stockLine.setData(normalize(s));
      benchLine.setData(normalize(b));
      benchLine.applyOptions({ title: benchCode });
      chart.timeScale().fitContent();
```

기존 `commonStart = stockData[0].date > ...` 라인 삭제.

- [ ] **Step 3.4: Run tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 18 PASSED

- [ ] **Step 3.5: Commit**

```bash
git add api/static/js/stock_cockpit.js tests/test_stock_cockpit.py
git commit -m "fix(cockpit): CKPT-2 정규화 기준일 갭 처리 + CKPT-3 § 2-A stock OHLCV 캐싱"
```

---

## Task 4: 백엔드 묶음 — `compute_sector_pctiles` + `/sector-stats` API + `/overview` 확장

**Files:**
- Modify: `analyzer/factor_engine.py` (신규 `compute_sector_pctiles` + 헬퍼)
- Modify: `api/routes/stocks.py` (신규 `get_stock_sector_stats` + `get_stock_overview` 응답 확장)
- Modify: `tests/test_stock_cockpit.py`

세 가지 백엔드 변경을 한 commit 으로 (모두 frontend Task 5~7 의 의존):
1. `compute_sector_pctiles()` — sector 단위 cross-section
2. `GET /api/stocks/{ticker}/sector-stats` — 위 함수 노출
3. `GET /api/stocks/{ticker}/overview` 응답에 `factor_snapshot` raw + `krx_extended` 필드 추가

### 4-A. `compute_sector_pctiles` (factor_engine)

- [ ] **Step 4A.1: Write the failing test**

`tests/test_stock_cockpit.py` 에 신규 클래스:

```python
class TestComputeSectorPctiles:
    """analyzer.factor_engine.compute_sector_pctiles — 섹터 단위 cross-section pctile."""

    def test_returns_six_axis_pctiles_for_normal_sector(self):
        from analyzer.factor_engine import compute_sector_pctiles

        # SQL 결과 행 — 시장 그룹 결정에 사용 (1개 row)
        market_group_row = {"sector": "Technology"}
        # 섹터 cross-section 결과 — TXN 한 행, sector_size=12
        sector_row = {
            "ticker": "TXN", "market": "NASDAQ",
            "r1m": 8.4, "r3m": 12.4, "r6m": 25.1, "r12m": 48.0,
            "vol60": 18.5, "volume_ratio": 1.42,
            "r1m_pctile": 0.78, "r3m_pctile": 0.85, "r6m_pctile": 0.70, "r12m_pctile": 0.92,
            "low_vol_pctile": 0.55, "volume_pctile": 0.88,
            "sector_size": 12,
        }

        conn = _fake_conn([market_group_row, [sector_row]])

        with patch("analyzer.factor_engine.get_connection", return_value=conn):
            result = compute_sector_pctiles(_fake_db_cfg(), "TXN", "NASDAQ")

        assert result["ticker"] == "TXN"
        assert result["sector"] == "Technology"
        assert result["sector_size"] == 12
        assert result["ranks"]["r3m"]["sector_pctile"] == 0.85
        assert result["ranks"]["r3m"]["sector_top_pct"] == 15  # round((1-0.85)*100)
        assert result["ranks"]["r3m"]["value_pct"] == 12.4
        assert result["ranks"]["volume"]["value_ratio"] == 1.42
        assert result["ranks"]["low_vol"]["sector_pctile"] == 0.55

    def test_small_sector_returns_null_pctiles(self):
        from analyzer.factor_engine import compute_sector_pctiles

        market_group_row = {"sector": "ObscureSector"}
        # sector_size=3 (< 5 임계) — pctile 계산 skip
        sector_row = {
            "ticker": "TXN", "market": "NASDAQ",
            "r1m": 5.0, "r3m": 10.0, "r6m": 20.0, "r12m": 30.0,
            "vol60": 15.0, "volume_ratio": 1.0,
            "r1m_pctile": None, "r3m_pctile": None, "r6m_pctile": None, "r12m_pctile": None,
            "low_vol_pctile": None, "volume_pctile": None,
            "sector_size": 3,
        }
        conn = _fake_conn([market_group_row, [sector_row]])
        with patch("analyzer.factor_engine.get_connection", return_value=conn):
            result = compute_sector_pctiles(_fake_db_cfg(), "TXN", "NASDAQ")

        assert result["sector_size"] == 3
        assert result["ranks"]["r3m"]["sector_pctile"] is None
        assert result["ranks"]["r3m"]["sector_top_pct"] is None
        # value 는 여전히 채워짐 (raw factor 는 sector size 무관)
        assert result["ranks"]["r3m"]["value_pct"] == 10.0

    def test_unknown_sector_returns_none(self):
        from analyzer.factor_engine import compute_sector_pctiles

        # 첫 쿼리 (sector + market_group 결정) 가 빈 결과
        conn = _fake_conn([None])
        with patch("analyzer.factor_engine.get_connection", return_value=conn):
            result = compute_sector_pctiles(_fake_db_cfg(), "UNKNOWN", "NASDAQ")
        assert result is None
```

테스트 helper `_fake_db_cfg()` 를 `tests/test_stock_cockpit.py` 상단의 `_fake_conn` 함수 정의 바로 다음에 추가 (이미 있으면 skip):

```python
def _fake_db_cfg():
    """factor_engine 등 외부 모듈에 넘길 가짜 DatabaseConfig — 어차피 get_connection 이 patch 됨."""
    from shared.config import DatabaseConfig
    return DatabaseConfig()
```

- [ ] **Step 4A.2: Run tests to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestComputeSectorPctiles -v`
Expected: FAIL — `ImportError: cannot import name 'compute_sector_pctiles'`

- [ ] **Step 4A.3: Implement `compute_sector_pctiles`**

`analyzer/factor_engine.py` 끝에 추가:

```python
def compute_sector_pctiles(
    db_cfg: DatabaseConfig,
    ticker: str,
    market: str,
    *,
    window_days: int = 300,
    min_sector_size: int = 5,
) -> dict | None:
    """단일 종목에 대한 섹터 내 6축 팩터 분위 계산.

    같은 sector_norm + 같은 시장 그룹(KRX/US) 안에서 cross-section PERCENT_RANK.
    섹터 표본이 min_sector_size 미만이면 pctile NULL (원시값은 채움).

    Returns:
        {
            "ticker": str, "sector": str | None, "sector_size": int,
            "ranks": {
                "r1m": {"value_pct": float|None, "sector_pctile": float|None, "sector_top_pct": int|None},
                "r3m": {...}, "r6m": {...}, "r12m": {...},
                "low_vol": {"value_pct": float|None, "sector_pctile": ..., "sector_top_pct": ...},
                "volume": {"value_ratio": float|None, "sector_pctile": ..., "sector_top_pct": ...},
            },
            "computed_at": ISO datetime str,
        }
        or None if ticker 가 stock_universe 에 없거나 sector_norm NULL.
    """
    tk = ticker.strip().upper()
    mk = (market or "").strip().upper()
    grp = _market_group(mk)
    if not grp:
        return None
    members = _MARKET_GROUPS[grp]

    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            # 1) 종목의 sector_norm 조회
            cur.execute(
                "SELECT sector_norm AS sector FROM stock_universe "
                "WHERE UPPER(ticker) = %s AND UPPER(market) = %s "
                "  AND sector_norm IS NOT NULL LIMIT 1",
                (tk, mk),
            )
            sector_row = cur.fetchone()
            if not sector_row or not sector_row[0]:
                return None
            sector = sector_row[0]

            # 2) 섹터 cross-section
            sql = f"""
            WITH ranked AS (
                SELECT o.ticker, UPPER(o.market) AS market, o.trade_date, o.close,
                       o.volume, o.change_pct,
                       ROW_NUMBER() OVER (
                           PARTITION BY o.ticker, UPPER(o.market)
                           ORDER BY o.trade_date DESC
                       ) AS rn
                FROM stock_universe_ohlcv o
                JOIN stock_universe u
                  ON UPPER(u.ticker) = UPPER(o.ticker)
                 AND UPPER(u.market) = UPPER(o.market)
                WHERE o.trade_date >= CURRENT_DATE - (%s::int)
                  AND UPPER(o.market) = ANY(%s)
                  AND u.sector_norm = %s
                  AND u.listed = TRUE
            ),
            univ AS (
                SELECT ticker, market,
                       MAX(CASE WHEN rn = 1 THEN close END) AS close_latest,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r1m']} THEN close END) AS close_1m,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r3m']} THEN close END) AS close_3m,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r6m']} THEN close END) AS close_6m,
                       MAX(CASE WHEN rn = {_PERIOD_OFFSETS['r12m']} THEN close END) AS close_12m,
                       STDDEV(LEAST(GREATEST(change_pct, -50), 50))
                           FILTER (WHERE rn <= 60) AS vol60,
                       AVG(volume) FILTER (WHERE rn <= 20) AS vol_avg_20,
                       AVG(volume) FILTER (WHERE rn <= 60) AS vol_avg_60
                FROM ranked
                GROUP BY ticker, market
            ),
            factors AS (
                SELECT ticker, market,
                       CASE WHEN close_1m  IS NOT NULL AND close_1m  > 0 THEN (close_latest - close_1m)  / close_1m  * 100 END AS r1m,
                       CASE WHEN close_3m  IS NOT NULL AND close_3m  > 0 THEN (close_latest - close_3m)  / close_3m  * 100 END AS r3m,
                       CASE WHEN close_6m  IS NOT NULL AND close_6m  > 0 THEN (close_latest - close_6m)  / close_6m  * 100 END AS r6m,
                       CASE WHEN close_12m IS NOT NULL AND close_12m > 0 THEN (close_latest - close_12m) / close_12m * 100 END AS r12m,
                       vol60,
                       CASE WHEN vol_avg_60 IS NOT NULL AND vol_avg_60 > 0
                            THEN vol_avg_20 / vol_avg_60 END AS volume_ratio
                FROM univ
                WHERE close_latest IS NOT NULL
            ),
            ranked_pctile AS (
                SELECT f.*,
                       PERCENT_RANK() OVER (ORDER BY r1m  NULLS FIRST) AS r1m_pctile,
                       PERCENT_RANK() OVER (ORDER BY r3m  NULLS FIRST) AS r3m_pctile,
                       PERCENT_RANK() OVER (ORDER BY r6m  NULLS FIRST) AS r6m_pctile,
                       PERCENT_RANK() OVER (ORDER BY r12m NULLS FIRST) AS r12m_pctile,
                       1 - PERCENT_RANK() OVER (ORDER BY vol60 DESC NULLS LAST) AS low_vol_pctile,
                       PERCENT_RANK() OVER (ORDER BY volume_ratio NULLS FIRST) AS volume_pctile,
                       COUNT(*) OVER () AS sector_size
                FROM factors f
            )
            SELECT ticker, market, r1m, r3m, r6m, r12m, vol60, volume_ratio,
                   r1m_pctile, r3m_pctile, r6m_pctile, r12m_pctile,
                   low_vol_pctile, volume_pctile, sector_size
            FROM ranked_pctile
            WHERE UPPER(ticker) = %s AND UPPER(market) = %s
            """
            cur.execute(sql, (int(window_days), list(members), sector, tk, mk))
            rows = cur.fetchall()
    except Exception as e:
        _log.warning(f"[factor] sector_pctile {tk}/{mk}/{sector} 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()

    if not rows:
        return {
            "ticker": tk, "sector": sector, "sector_size": 0,
            "ranks": {k: {"value_pct": None, "value_ratio": None,
                          "sector_pctile": None, "sector_top_pct": None}
                      for k in ("r1m", "r3m", "r6m", "r12m", "low_vol", "volume")},
            "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
        }

    # row 형태가 RealDictCursor 인지 일반 cursor 인지 둘 다 지원
    r = rows[0]
    def _g(k):
        try:
            return r[k]
        except (KeyError, TypeError):
            # tuple cursor 인 경우 인덱스 매핑
            cols = ["ticker", "market", "r1m", "r3m", "r6m", "r12m",
                    "vol60", "volume_ratio", "r1m_pctile", "r3m_pctile",
                    "r6m_pctile", "r12m_pctile", "low_vol_pctile",
                    "volume_pctile", "sector_size"]
            return r[cols.index(k)]

    sector_size = int(_g("sector_size") or 0)
    sufficient = sector_size >= min_sector_size

    def _pctile_pkg(value_key, value_label, pctile_key):
        v = _g(value_key)
        p = _g(pctile_key) if sufficient else None
        out = {value_label: float(v) if v is not None else None,
               "sector_pctile": float(p) if p is not None else None,
               "sector_top_pct": int(round((1 - float(p)) * 100)) if p is not None else None}
        return out

    return {
        "ticker": tk,
        "sector": sector,
        "sector_size": sector_size,
        "ranks": {
            "r1m":     _pctile_pkg("r1m", "value_pct", "r1m_pctile"),
            "r3m":     _pctile_pkg("r3m", "value_pct", "r3m_pctile"),
            "r6m":     _pctile_pkg("r6m", "value_pct", "r6m_pctile"),
            "r12m":    _pctile_pkg("r12m", "value_pct", "r12m_pctile"),
            "low_vol": _pctile_pkg("vol60", "value_pct", "low_vol_pctile"),
            "volume":  _pctile_pkg("volume_ratio", "value_ratio", "volume_pctile"),
        },
        "computed_at": datetime.now(_KST).isoformat(timespec="seconds"),
    }
```

- [ ] **Step 4A.4: Run tests**

Run: `pytest tests/test_stock_cockpit.py::TestComputeSectorPctiles -v`
Expected: 3 PASSED

### 4-B. Route `GET /api/stocks/{ticker}/sector-stats`

- [ ] **Step 4B.1: Write the failing test**

`tests/test_stock_cockpit.py` 에 클래스 추가:

```python
class TestStockSectorStatsAPI:
    """GET /api/stocks/{ticker}/sector-stats"""

    def test_sector_stats_returns_payload(self):
        from api.routes.stocks import get_stock_sector_stats

        sample = {
            "ticker": "TXN", "sector": "Technology", "sector_size": 12,
            "ranks": {
                "r1m": {"value_pct": 8.4, "sector_pctile": 0.78, "sector_top_pct": 22},
                "r3m": {"value_pct": 12.4, "sector_pctile": 0.85, "sector_top_pct": 15},
                "r6m": {"value_pct": 25.1, "sector_pctile": 0.70, "sector_top_pct": 30},
                "r12m": {"value_pct": 48.0, "sector_pctile": 0.92, "sector_top_pct": 8},
                "low_vol": {"value_pct": 18.5, "sector_pctile": 0.55, "sector_top_pct": 45},
                "volume": {"value_ratio": 1.42, "sector_pctile": 0.88, "sector_top_pct": 12},
            },
            "computed_at": "2026-04-25T19:00:00+09:00",
        }
        with patch("api.routes.stocks.compute_sector_pctiles", return_value=sample):
            result = get_stock_sector_stats(ticker="TXN", market="NASDAQ")
        assert result == sample

    def test_sector_stats_404_for_unknown(self):
        from fastapi import HTTPException
        from api.routes.stocks import get_stock_sector_stats

        with patch("api.routes.stocks.compute_sector_pctiles", return_value=None):
            try:
                get_stock_sector_stats(ticker="UNKNOWN", market="NASDAQ")
                assert False, "expected HTTPException"
            except HTTPException as e:
                assert e.status_code == 404
```

- [ ] **Step 4B.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockSectorStatsAPI -v`
Expected: FAIL — `cannot import name 'get_stock_sector_stats'`

- [ ] **Step 4B.3: Implement route**

`api/routes/stocks.py` 상단 import 에 추가:
```python
from analyzer.factor_engine import compute_sector_pctiles
```

`get_stock_proposals` 핸들러 다음 위치에 추가:

```python
@router.get("/{ticker}/sector-stats")
def get_stock_sector_stats(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
):
    """섹터 내 6축 팩터 분위 — § 3 섹터 컨텍스트."""
    cfg = AppConfig()
    result = compute_sector_pctiles(cfg.db, ticker, market)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"종목 '{ticker}' 의 섹터 정보가 없습니다",
        )
    return result
```

- [ ] **Step 4B.4: Run test**

Run: `pytest tests/test_stock_cockpit.py::TestStockSectorStatsAPI -v`
Expected: 2 PASSED

### 4-C. `/overview` 응답 확장 — `factor_snapshot` raw + `krx_extended`

- [ ] **Step 4C.1: Write the failing test**

`tests/test_stock_cockpit.py::TestStockOverviewAPI::test_overview_returns_hero_payload` 의 fixture 와 assertion 을 다음으로 보강:

기존 `factor_row` 옆에 `krx_row` 추가:

```python
        factor_row = {
            "factor_snapshot": {
                "r1m_pctile": 0.7, "r3m_pctile": 0.8, "r6m_pctile": 0.85, "r12m_pctile": 0.78,
                "low_vol_pctile": 0.55, "volume_pctile": 0.88,
            },
        }
        krx_row = {
            "foreign_ownership_pct": 18.5,
            "foreign_net_buy_signal": "positive",
            "squeeze_risk": "low",
            "index_membership": ["KOSPI200", "KRX300"],
        }
        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row, krx_row])
```

assertion 추가:

```python
        # factor_snapshot raw exposure (Phase 2 § 2-B 가 사용)
        assert result["factor_snapshot"] == factor_row["factor_snapshot"]
        # krx_extended (Phase 2 § 5 가 사용)
        assert result["krx_extended"]["foreign_ownership_pct"] == 18.5
        assert result["krx_extended"]["foreign_net_buy_signal"] == "positive"
        assert result["krx_extended"]["squeeze_risk"] == "low"
        assert result["krx_extended"]["index_membership"] == ["KOSPI200", "KRX300"]
```

zero-proposals test 도 fixture 5번째 entry 추가:

```python
        krx_row = None
        conn = _fake_conn([meta_row, latest_rows, stats_row, factor_row, krx_row])
```

assertion 추가:
```python
        assert result["factor_snapshot"] is None
        assert result["krx_extended"] is None
```

- [ ] **Step 4C.2: Run tests to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockOverviewAPI -v`
Expected: FAIL — 응답에 factor_snapshot/krx_extended 키 없음.

- [ ] **Step 4C.3: Extend `get_stock_overview` handler**

`api/routes/stocks.py` 의 `get_stock_overview` 핸들러 안. factor_snapshot 쿼리 직후, conn.close 전에 한 쿼리 추가:

```python
            # 5) KRX 확장 (한국주만 채워짐, 외국주는 모든 컬럼 NULL → 응답 None)
            cur.execute("""
                SELECT
                    foreign_ownership_pct, foreign_net_buy_signal,
                    squeeze_risk, index_membership
                FROM investment_proposals
                WHERE UPPER(ticker) = %s
                  AND (
                      foreign_ownership_pct IS NOT NULL OR
                      foreign_net_buy_signal IS NOT NULL OR
                      squeeze_risk IS NOT NULL OR
                      index_membership IS NOT NULL
                  )
                ORDER BY created_at DESC LIMIT 1
            """, (tk,))
            krx_row = cur.fetchone()
```

응답 dict 끝에 두 필드 추가:

```python
    return {
        "ticker": tk,
        # ... 기존 필드 ...
        "score_breakdown": { /* ... */ },
        # Phase 2 추가
        "factor_snapshot": factor_row.get("factor_snapshot") if factor_row else None,
        "krx_extended": (
            {
                "foreign_ownership_pct": (
                    float(krx_row["foreign_ownership_pct"])
                    if krx_row.get("foreign_ownership_pct") is not None else None
                ),
                "foreign_net_buy_signal": krx_row.get("foreign_net_buy_signal"),
                "squeeze_risk": krx_row.get("squeeze_risk"),
                "index_membership": list(krx_row["index_membership"]) if krx_row.get("index_membership") else None,
            }
            if krx_row else None
        ),
    }
```

- [ ] **Step 4C.4: Run all overview + sector-stats tests**

Run: `pytest tests/test_stock_cockpit.py::TestStockOverviewAPI tests/test_stock_cockpit.py::TestStockSectorStatsAPI tests/test_stock_cockpit.py::TestComputeSectorPctiles -v`
Expected: 2 + 2 + 3 = 7 PASSED

- [ ] **Step 4D: Commit**

```bash
git add analyzer/factor_engine.py api/routes/stocks.py tests/test_stock_cockpit.py
git commit -m "feat(cockpit): 섹터 팩터 분위 + sector-stats API + overview 응답 확장 (factor_snapshot, krx_extended)"
```

---

## Task 5: § 3 시장 레짐 + 섹터 팩터 분위 표 — 라우트 ctx + partial include + IIFE

**Files:**
- Modify: `api/routes/stocks.py` (`stock_fundamentals_page` 가 latest session 의 `market_regime` 을 ctx 에 주입)
- Modify: `api/templates/stock_cockpit.html` (§ 3 자리 추가 + `_regime_banner.html` include + 섹터 표 마크업)
- Modify: `api/static/js/stock_cockpit.js` (§ 3 섹터 표 IIFE — sector-stats API fetch + 6축 표 렌더)

### 5-A. 라우트 ctx 주입

- [ ] **Step 5A.1: Write the failing test**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 추가:

```python
    def test_cockpit_page_includes_regime_banner(self):
        # 페이지에 _regime_banner.html partial 시그니처가 노출되는지
        # (regime context 가 None 이라도 includ 자체는 일어나야 — partial 내부에서 if regime 가드)
        client = _make_client()
        # base_ctx fixture 가 fetchone=[0] 반환하므로 session 도 없음 → regime None
        # 그래도 페이지 200 + 섹션 자리 마크업은 존재해야
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        assert 'id="sec-regime"' in body or 'class="regime-banner"' in body or 'sec-regime' in body
        # 섹터 분위 표 자리
        assert 'id="sector-stats-table"' in body
```

- [ ] **Step 5A.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_includes_regime_banner -v`
Expected: FAIL

- [ ] **Step 5A.3: Modify route to inject regime ctx**

`api/routes/stocks.py` 의 `stock_fundamentals_page` 핸들러 수정:

```python
@pages_router.get("/{ticker}")
def stock_fundamentals_page(
    ticker: str,
    market: str = Query(default="", description="시장 코드"),
    ctx: dict = Depends(make_page_ctx("proposals")),
):
    """Stock Cockpit — 종합 종목 페이지 (in-place 교체)."""
    # § 3 시장 레짐 — 최신 분석 세션의 market_regime JSONB
    regime = None
    conn = ctx.get("_conn")
    if conn is not None:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT market_regime FROM analysis_sessions "
                    "ORDER BY analysis_date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    regime = row.get("market_regime")
        except Exception:
            regime = None  # 마이그레이션 v31 이전이면 컬럼 없음 — silent fallback
    return templates.TemplateResponse(request=ctx["request"], name="stock_cockpit.html", context={
        **ctx,
        "ticker": ticker.upper(),
        "market": market.upper(),
        "regime": regime,
    })
```

### 5-B. 템플릿 § 3 자리 + partial include + 섹터 표 마크업

- [ ] **Step 5B.1: Add § 3 section to `stock_cockpit.html`**

`§ 2-B` (Task 6 에서 구현 예정인 자리) 와 `§ 4 펀더멘털` 사이에 새 섹션 삽입. `<section class="cockpit-section" id="sec-benchmark">` 끝나는 `</section>` 다음에:

```html
  {# ─────────────── § 3. 시장 레짐 + 섹터 컨텍스트 ─────────────── #}
  <section class="cockpit-section" id="sec-regime">
    <div class="cockpit-section-head"><h3>시장 레짐 + 섹터 컨텍스트</h3></div>
    {% include "partials/_regime_banner.html" %}
    <div id="sector-stats-wrap" style="margin-top:10px;">
      <div id="sector-stats-empty" style="display:none;color:var(--text-muted);
           padding:14px;background:var(--bg-card);border-radius:10px;
           border:1px solid var(--border);font-size:13px;">
        섹터 분위 데이터를 조회 중입니다.
      </div>
      <table id="sector-stats-table" style="display:none;width:100%;
             background:var(--bg-card);border:1px solid var(--border);
             border-radius:10px;border-collapse:separate;border-spacing:0;
             font-size:13px;">
        <thead>
          <tr style="color:var(--text-muted);font-size:12px;">
            <th style="text-align:left;padding:8px 12px;">팩터</th>
            <th style="text-align:right;padding:8px 12px;">값</th>
            <th style="text-align:right;padding:8px 12px;">섹터 분위</th>
            <th style="text-align:right;padding:8px 12px;">섹터 내 순위</th>
          </tr>
        </thead>
        <tbody id="sector-stats-body"></tbody>
      </table>
    </div>
  </section>
```

### 5-C. § 3 섹터 분위 표 IIFE

- [ ] **Step 5C.1: Write the failing test**

`tests/test_stock_cockpit.py::TestStockCockpitPage` 에 추가:

```python
    def test_external_js_has_sector_stats_iife(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        assert "// ── § 3 섹터 팩터 분위 ──" in body
        assert "/sector-stats" in body
        assert "sector-stats-table" in body
```

- [ ] **Step 5C.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_external_js_has_sector_stats_iife -v`
Expected: FAIL

- [ ] **Step 5C.3: Append IIFE to `static/js/stock_cockpit.js`**

§ 6 추천 타임라인 IIFE 다음(파일 끝 부분)에 추가:

```javascript
// ── § 3 섹터 팩터 분위 ──
(function() {
  var c = window.__cockpit;
  if (!c) return;

  var FACTORS = [
    { key: "r1m", label: "1개월 모멘텀", unit: "%" },
    { key: "r3m", label: "3개월 모멘텀", unit: "%" },
    { key: "r6m", label: "6개월 모멘텀", unit: "%" },
    { key: "r12m", label: "12개월 모멘텀", unit: "%" },
    { key: "low_vol", label: "저변동성 (60d σ)", unit: "%" },
    { key: "volume", label: "거래량 비율 (20d/60d)", unit: "x" },
  ];

  var emptyEl = document.getElementById('sector-stats-empty');
  var tableEl = document.getElementById('sector-stats-table');
  var bodyEl = document.getElementById('sector-stats-body');
  if (!emptyEl || !tableEl || !bodyEl) return;

  emptyEl.style.display = 'block';

  var qs = c.market ? ('?market=' + encodeURIComponent(c.market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/sector-stats' + qs)
    .then(function(r) {
      if (r.status === 404) return null;
      return r.ok ? r.json() : Promise.reject();
    })
    .then(function(d) {
      if (!d) {
        emptyEl.textContent = '섹터 정보 없음';
        return;
      }
      if (!d.sector_size || d.sector_size < 5) {
        emptyEl.textContent = '섹터 표본 부족 (' + (d.sector_size || 0) + '개) — 분위 계산 불가';
        return;
      }
      emptyEl.style.display = 'none';
      tableEl.style.display = 'table';

      FACTORS.forEach(function(f) {
        var rank = (d.ranks || {})[f.key] || {};
        var valKey = f.key === "volume" ? "value_ratio" : "value_pct";
        var rawVal = rank[valKey];
        var pctile = rank.sector_pctile;
        var topPct = rank.sector_top_pct;

        var row = document.createElement('tr');
        row.innerHTML =
          '<td style="padding:8px 12px;border-top:1px solid var(--border);">' + c.escHtml(f.label) + '</td>' +
          '<td style="padding:8px 12px;border-top:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;">' +
            (rawVal != null ? c.fmtNum(rawVal) + (f.unit === '%' ? '%' : 'x') : '-') +
          '</td>' +
          '<td style="padding:8px 12px;border-top:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;">' +
            (pctile != null ? c.fmtNum(pctile * 100) + '%ile' : '-') +
          '</td>' +
          '<td style="padding:8px 12px;border-top:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;">' +
            (topPct != null ? '상위 ' + topPct + '%' : '-') +
          '</td>';
        bodyEl.appendChild(row);
      });
    })
    .catch(function() {
      emptyEl.textContent = '섹터 분위 조회 실패';
    });
})();
```

- [ ] **Step 5D: Run all tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 25 PASSED (Phase 1 14 + Task 1 +2 + Task 2 +1 + Task 3 +1 + Task 4 +5 + Task 5 +2 = 25)

- [ ] **Step 5E: Commit**

```bash
git add api/routes/stocks.py api/templates/stock_cockpit.html api/static/js/stock_cockpit.js tests/test_stock_cockpit.py
git commit -m "feat(cockpit): § 3 시장 레짐(_regime_banner 재사용) + 섹터 팩터 분위 표"
```

---

## Task 6: § 2-B 정량 팩터 레이더 (Chart.js radar)

**Files:**
- Modify: `api/templates/stock_cockpit.html` (Chart.js CDN + § 2-B 자리 마크업)
- Modify: `api/static/js/stock_cockpit.js` (§ 2-B IIFE)

### 6-A. Chart.js CDN + § 2-B 마크업

- [ ] **Step 6A.1: Write the failing test**

```python
    def test_cockpit_page_loads_chartjs(self):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        assert "chart.js" in body or "chart.umd.min.js" in body
        assert 'id="factor-radar"' in body
```

- [ ] **Step 6A.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_loads_chartjs -v`
Expected: FAIL

- [ ] **Step 6A.3: Add Chart.js CDN to template**

`api/templates/stock_cockpit.html` 의 `{% block scripts %}` 안, lightweight-charts CDN 다음 줄에 추가:

```html
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

- [ ] **Step 6A.4: Add § 2-B markup**

§ 2-A 벤치마크 섹션 (`<section ... id="sec-benchmark">...</section>`) 다음, § 3 시장 레짐 섹션 앞에 삽입:

```html
  {# ─────────────── § 2-B. 정량 팩터 레이더 ─────────────── #}
  <section class="cockpit-section" id="sec-factor-radar">
    <div class="cockpit-section-head"><h3>정량 팩터 분위 (시장 cross-section)</h3></div>
    <div style="background:var(--bg-card);border:1px solid var(--border);
         border-radius:10px;padding:12px;position:relative;">
      <canvas id="factor-radar" style="max-height:340px;"></canvas>
      <div id="factor-radar-empty" style="display:none;text-align:center;
           padding:60px 0;color:var(--text-muted);font-size:13px;">
        팩터 데이터 부족 — 첫 추천 발생 후 채워집니다.
      </div>
    </div>
  </section>
```

### 6-B. § 2-B IIFE

- [ ] **Step 6B.1: Write the failing test**

```python
    def test_external_js_has_factor_radar_iife(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        assert "// ── § 2-B 정량 팩터 레이더 ──" in body
        assert "factor-radar" in body
        assert "type: 'radar'" in body or 'type: "radar"' in body
```

- [ ] **Step 6B.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_external_js_has_factor_radar_iife -v`
Expected: FAIL

- [ ] **Step 6B.3: Append IIFE to `static/js/stock_cockpit.js`**

§ 3 섹터 IIFE 다음에 추가:

```javascript
// ── § 2-B 정량 팩터 레이더 ──
(function() {
  var c = window.__cockpit;
  if (!c || typeof Chart === 'undefined') return;

  var canvas = document.getElementById('factor-radar');
  var emptyEl = document.getElementById('factor-radar-empty');
  if (!canvas) return;

  var qs = c.market ? ('?market=' + encodeURIComponent(c.market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/overview' + qs)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function(d) {
      var snap = d.factor_snapshot;
      if (!snap) {
        canvas.style.display = 'none';
        emptyEl.style.display = 'block';
        return;
      }

      var labels = ['1m', '3m', '6m', '12m', '저변동', '거래량'];
      var values = [
        snap.r1m_pctile, snap.r3m_pctile, snap.r6m_pctile,
        snap.r12m_pctile, snap.low_vol_pctile, snap.volume_pctile,
      ].map(function(v) { return v != null ? +(v * 100).toFixed(1) : 0; });

      // 시장 중앙선 (0.5) — 점선 데이터셋
      var midline = labels.map(function() { return 50; });

      new Chart(canvas, {
        type: 'radar',
        data: {
          labels: labels,
          datasets: [
            {
              label: c.ticker,
              data: values,
              backgroundColor: 'rgba(78, 163, 255, 0.18)',
              borderColor: '#4ea3ff',
              borderWidth: 2,
              pointBackgroundColor: '#4ea3ff',
            },
            {
              label: '시장 중앙 (50%ile)',
              data: midline,
              borderColor: 'rgba(160, 160, 160, 0.6)',
              borderWidth: 1,
              borderDash: [4, 4],
              pointRadius: 0,
              fill: false,
            },
          ],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { labels: { color: '#a0a0a0', font: { size: 11 } } },
            tooltip: {
              callbacks: {
                label: function(ctx) {
                  return ctx.dataset.label + ': ' + ctx.raw + '%ile';
                },
              },
            },
          },
          scales: {
            r: {
              min: 0, max: 100,
              ticks: { display: false, stepSize: 20 },
              grid: { color: '#2a2a2a' },
              angleLines: { color: '#2a2a2a' },
              pointLabels: { color: '#a0a0a0', font: { size: 12 } },
            },
          },
        },
      });
    })
    .catch(function() {
      canvas.style.display = 'none';
      emptyEl.textContent = '레이더 데이터 조회 실패';
      emptyEl.style.display = 'block';
    });
})();
```

- [ ] **Step 6C: Run all tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 27 PASSED (Task 5 25 + Task 6 +2)

- [ ] **Step 6D: Commit**

```bash
git add api/templates/stock_cockpit.html api/static/js/stock_cockpit.js tests/test_stock_cockpit.py
git commit -m "feat(cockpit): § 2-B 정량 팩터 레이더 (Chart.js radar 6축, 시장 중앙선 점선)"
```

---

## Task 7: § 5 KRX 확장 (한국주만)

**Files:**
- Modify: `api/templates/stock_cockpit.html` (§ 5 자리 마크업)
- Modify: `api/static/js/stock_cockpit.js` (§ 5 IIFE)

### 7-A. § 5 마크업

- [ ] **Step 7A.1: Write the failing test**

```python
    def test_cockpit_page_includes_krx_section(self):
        client = _make_client()
        resp = client.get("/pages/stocks/TXN?market=NASDAQ")
        body = resp.text
        # § 5 자리 마크업 (외국주 페이지여도 마크업은 존재 — JS가 hide)
        assert 'id="sec-krx"' in body
        assert 'id="krx-foreign-donut"' in body
```

- [ ] **Step 7A.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_cockpit_page_includes_krx_section -v`
Expected: FAIL

- [ ] **Step 7A.3: Add § 5 markup**

§ 4 펀더멘털 섹션 (`<section ... id="sec-fundamentals">...</section>`) 다음, § 6 추천 타임라인 (`id="sec-timeline"`) 앞에 삽입:

```html
  {# ─────────────── § 5. KRX 확장 (한국주만) ─────────────── #}
  <section class="cockpit-section" id="sec-krx" style="display:none;">
    <div class="cockpit-section-head"><h3>KRX 확장 (외국인·공매도·지수)</h3></div>
    <div id="krx-content" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;">
      <div class="fund-card">
        <h4 class="fund-card-title">외국인 보유</h4>
        <div style="position:relative;height:160px;">
          <canvas id="krx-foreign-donut"></canvas>
          <div id="krx-foreign-pct" style="position:absolute;top:50%;left:50%;
               transform:translate(-50%,-50%);font-size:18px;font-weight:700;"></div>
        </div>
      </div>
      <div class="fund-card">
        <h4 class="fund-card-title">외국인 순매수 신호</h4>
        <div id="krx-foreign-signal" style="text-align:center;padding:32px 0;font-size:18px;font-weight:700;"></div>
      </div>
      <div class="fund-card">
        <h4 class="fund-card-title">숏스퀴즈 위험</h4>
        <div style="padding:14px 0;">
          <div id="krx-squeeze-bar" style="height:14px;background:var(--bg);
               border:1px solid var(--border);border-radius:7px;overflow:hidden;">
            <div id="krx-squeeze-fill" style="height:100%;width:0%;background:var(--green);transition:width 0.3s;"></div>
          </div>
          <div id="krx-squeeze-label" style="text-align:center;margin-top:8px;font-size:13px;"></div>
        </div>
      </div>
      <div class="fund-card">
        <h4 class="fund-card-title">지수 편입</h4>
        <div id="krx-index-membership" style="display:flex;flex-wrap:wrap;gap:6px;padding:10px 0;"></div>
      </div>
    </div>
    <div id="krx-empty" style="display:none;color:var(--text-muted);
         padding:14px;background:var(--bg-card);border-radius:10px;
         border:1px solid var(--border);font-size:13px;text-align:center;">
      추천 데이터 없음 — 한국주 KRX 수급 정보는 추천 발생 후 누적됩니다.
    </div>
  </section>
```

### 7-B. § 5 IIFE

- [ ] **Step 7B.1: Write the failing test**

```python
    def test_external_js_has_krx_iife(self):
        client = _make_client()
        resp = client.get("/static/js/stock_cockpit.js")
        body = resp.text
        assert "// ── § 5 KRX 확장 ──" in body
        assert "krx-foreign-donut" in body
        # 외국주 hide 로직
        assert "KOSPI" in body and "KOSDAQ" in body
```

- [ ] **Step 7B.2: Run test to verify failure**

Run: `pytest tests/test_stock_cockpit.py::TestStockCockpitPage::test_external_js_has_krx_iife -v`
Expected: FAIL

- [ ] **Step 7B.3: Append IIFE to `static/js/stock_cockpit.js`**

§ 2-B 레이더 IIFE 다음에 추가:

```javascript
// ── § 5 KRX 확장 ──
(function() {
  var c = window.__cockpit;
  if (!c) return;

  var KRX_MARKETS = ['KOSPI', 'KOSDAQ'];
  var section = document.getElementById('sec-krx');
  if (!section) return;

  // 한국주가 아니면 섹션 통째 hide
  if (KRX_MARKETS.indexOf((c.market || '').toUpperCase()) < 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';

  var contentEl = document.getElementById('krx-content');
  var emptyEl = document.getElementById('krx-empty');

  var qs = c.market ? ('?market=' + encodeURIComponent(c.market)) : '';
  fetch('/api/stocks/' + encodeURIComponent(c.ticker) + '/overview' + qs)
    .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function(d) {
      var krx = d.krx_extended;
      if (!krx) {
        contentEl.style.display = 'none';
        emptyEl.style.display = 'block';
        return;
      }

      // 외국인 보유 도넛
      var fp = krx.foreign_ownership_pct;
      if (fp != null && typeof Chart !== 'undefined') {
        var canvas = document.getElementById('krx-foreign-donut');
        new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: ['외국인', '내국인'],
            datasets: [{
              data: [fp, Math.max(0, 100 - fp)],
              backgroundColor: ['#4ea3ff', 'rgba(255,255,255,0.08)'],
              borderWidth: 0,
            }],
          },
          options: {
            responsive: true,
            cutout: '70%',
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
          },
        });
        document.getElementById('krx-foreign-pct').textContent = c.fmtNum(fp) + '%';
      } else {
        document.getElementById('krx-foreign-pct').textContent = '-';
      }

      // 외국인 순매수 신호
      var sigEl = document.getElementById('krx-foreign-signal');
      var sig = krx.foreign_net_buy_signal;
      var sigMap = {
        'positive': { text: '▲ 순매수 우세', color: 'var(--green)' },
        'neutral': { text: '◆ 중립', color: 'var(--text-muted)' },
        'negative': { text: '▼ 순매도 우세', color: 'var(--red)' },
      };
      var sigInfo = sigMap[sig] || { text: '-', color: 'var(--text-muted)' };
      sigEl.textContent = sigInfo.text;
      sigEl.style.color = sigInfo.color;

      // 숏스퀴즈 게이지
      var sq = krx.squeeze_risk;
      var sqMap = {
        'low':  { width: 25, color: 'var(--green)',  label: '낮음' },
        'mid':  { width: 60, color: '#eab308',       label: '중간' },
        'high': { width: 90, color: 'var(--red)',    label: '높음' },
      };
      var sqInfo = sqMap[sq] || { width: 0, color: 'var(--text-muted)', label: '-' };
      var fillEl = document.getElementById('krx-squeeze-fill');
      fillEl.style.width = sqInfo.width + '%';
      fillEl.style.background = sqInfo.color;
      document.getElementById('krx-squeeze-label').textContent = sqInfo.label;

      // 지수 편입 배지
      var idxEl = document.getElementById('krx-index-membership');
      var indices = krx.index_membership || [];
      if (indices.length === 0) {
        idxEl.innerHTML = '<span style="color:var(--text-muted);font-size:13px;">미편입</span>';
      } else {
        indices.forEach(function(idx) {
          var span = document.createElement('span');
          span.textContent = idx;
          span.style.cssText = 'display:inline-block;padding:3px 8px;background:rgba(78,163,255,0.15);' +
                               'border:1px solid rgba(78,163,255,0.4);border-radius:4px;font-size:12px;color:var(--accent);';
          idxEl.appendChild(span);
        });
      }
    })
    .catch(function() {
      contentEl.style.display = 'none';
      emptyEl.textContent = 'KRX 확장 데이터 조회 실패';
      emptyEl.style.display = 'block';
    });
})();
```

- [ ] **Step 7C: Run all tests**

Run: `pytest tests/test_stock_cockpit.py -v`
Expected: 29 PASSED (Task 6 27 + Task 7 +2)

- [ ] **Step 7D: Commit**

```bash
git add api/templates/stock_cockpit.html api/static/js/stock_cockpit.js tests/test_stock_cockpit.py
git commit -m "feat(cockpit): § 5 KRX 확장 — 외국인 보유 도넛/순매수 신호/숏스퀴즈/지수 편입 (한국주만)"
```

---

## Task 8: 통합 검증 + 문서 업데이트

**Files:**
- Modify: `CLAUDE.md`
- Modify: `_docs/20260425190117_stock-cockpit-phase2-design.md` (status)

수동 smoke 는 사용자 환경 (dev 서버)이라 implementer 는 문서만.

- [ ] **Step 8.1: Update `CLAUDE.md` if needed**

Read `CLAUDE.md` 의 templates 라인 — `stock_cockpit` 이 Phase 1 Task 8 에서 추가됐으니 변경 불필요. 그러나 static js 추가 사항 (`static/js/stock_cockpit.js`) 을 같은 라인에 명시할지 검토. CLAUDE.md 의 static js 언급 패턴 확인:

```bash
grep -n "static/js" CLAUDE.md
```

이미 `sse_log_viewer.js 공용 SSE 컨트롤러` 가 언급된 라인에 같은 형식으로 추가:

```
static/css/ + static/js/(sse_log_viewer.js 공용 SSE 컨트롤러, stock_cockpit.js Cockpit 페이지 전용)
```

- [ ] **Step 8.2: Update Phase 2 spec status**

`_docs/20260425190117_stock-cockpit-phase2-design.md` 헤더 상태 라인을 다음으로 변경:

```
- 상태: Phase 2 구현 완료 (커밋 <range>) — Phase 3 별도 spec 으로 분리 예정
```

`<range>` 는 `git log --oneline 45d299e..HEAD --reverse` 의 첫·끝 SHA 사용.

- [ ] **Step 8.3: Final commit**

```bash
git add CLAUDE.md _docs/20260425190117_stock-cockpit-phase2-design.md
git commit -m "docs(cockpit): Phase 2 구현 완료 + CLAUDE.md static js 라인 갱신"
```

---

## 완료 기준 (spec § 11 검증)

1. ✅ KRX 한국주 + US 종목 양쪽에서 § 2-B / § 3 정상 렌더 — Task 5/6 + 수동 smoke
2. ✅ 섹터 표본 부족 종목에서 § 3 섹터 표 "표본 부족" 정상 — Task 4 unit test + Task 5 IIFE 분기
3. ✅ CKPT-1 검증: 차트 인스턴스 보존 — Task 2 overlay 패턴
4. ✅ CKPT-2 검증: 같은 기준일 사용 — Task 3 alignment
5. ✅ CKPT-3 검증: stock OHLCV 추가 요청 없음 — Task 3 stockCache
6. ✅ 신규 API `/sector-stats` 단위 테스트 (정상/표본 부족/외국주) — Task 4
7. ✅ JS 분리 회귀 — Task 1 (페이지 200 + 외부 JS 200 + Phase 1 14 테스트 + Phase 2 신규 모두 통과)

## 비범위 (재확인)

- § 7 등장 테마 카드 → Phase 3
- Hero 압축 sticky 모드 → Phase 3
- 모바일 반응형 정밀 조정 → Phase 3
- 섹터 평균 PER/PBR 인프라 (yfinance 배치) → Phase 2 § 3 가 채택한 B 안 의 보완재로 후속 검토
