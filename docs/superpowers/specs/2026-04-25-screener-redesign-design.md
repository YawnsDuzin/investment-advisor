# Screener UI Redesign — Finviz-Style (B-3 Hybrid / L-1 Layout)

- **Status**: Approved (brainstorming) — pending implementation plan
- **Date**: 2026-04-25
- **Owner**: yawnsduzin
- **Scope**: `api/routes/screener.py`, `api/templates/screener.html`, `api/static/css/src/<new>`
- **Predecessors**: roadmap UI-6 (existing screener), `_docs/20260422235016_ohlcv-history-table-plan.md`
- **Successor (out-of-scope here)**: B-2 펀더멘털 데이터 파이프라인 (PER/PBR/배당/ROE) — 별도 spec으로 후속 진행

---

## 1. 목표

기존 스크리너(UI-6)는 가용한 OHLCV/팩터 데이터의 일부만 노출 + 필터·결과 모두 단조롭다. 사용자 요청에 따라 **Finviz/TradingView 스타일**로 풀 리뉴얼하되, 펀더멘털 데이터 부재 문제는 단계 분리(B-3 Hybrid)로 회피한다.

성공 기준:

1. 사용자가 **티커/이름 검색**으로 단건/유사 종목을 바로 찾을 수 있다.
2. 필터가 5개 탭(Search / Descriptive / Performance / Technical / Fundamental*)으로 그룹화되어 있다 (\*Fundamental 탭은 비활성 placeholder).
3. 결과는 **3개 View 프리셋**(Overview / Performance / Technical) + Custom 컬럼 토글로 보고 싶은 정보를 즉시 전환할 수 있다.
4. 기존 저장 프리셋(`screener_presets.spec` JSONB)은 그대로 동작 (필드 누락 무시 = forward-compatible).
5. 모바일에서도 가로 스크롤 + sticky 첫 컬럼으로 사용 가능.
6. **펀더멘털 데이터(PER/PBR 등)는 이번 작업에 포함하지 않는다** — UI 자리만 만들어두고 다음 단계로 넘긴다.

---

## 2. 현재 상태 (As-Is)

- 필터 9종이 단일 grid에 평탄하게 나열 (`screener.html` line 12~78)
- 티커/이름 검색 입력 **없음**
- 섹터는 raw text input — sector_norm 화이트리스트 알기 어려움
- 결과 7컬럼: 티커/이름/섹터/시총/r1y/Vol60/VolRatio
- 노출되지 않은 가용 메트릭: r1m/r3m/r6m/YTD, MA20/60/200, drawdown, 52주 high/low, 현재가, 거래대금
- View 프리셋·컬럼 토글·정렬 가능 헤더 없음
- 페이지네이션 없음 (단일 fetch — 이건 유지)

---

## 3. 설계 (To-Be)

### 3.1 레이아웃 (L-1 Finviz 스타일)

```
┌─ base.html sidebar (그대로) ─┬───────────────────────────────────────┐
│                              │ [page_title 프리미엄 스크리너]          │
│                              ├───────────────────────────────────────┤
│  네비게이션 (변경 없음)        │ ┌─ Filter card ──────────────────────┐│
│                              │ │ 🔍 Search · 📊 Descr · 🚀 Perf ·   ││
│                              │ │ 📈 Tech · 💰 Fund*                 ││
│                              │ │ ─────────────────────────────────── ││
│                              │ │ (active tab body)                   ││
│                              │ │ [실행] [프리셋 저장] [내 프리셋]      ││
│                              │ └────────────────────────────────────┘│
│                              │ ┌─ Result card ──────────────────────┐│
│                              │ │ View: [Overview][Perf][Tech][Cust] ││
│                              │ │ ─── 결과 N건 / 한도 K (tier=...) ── ││
│                              │ │ 정렬가능 헤더 + 결과 테이블          ││
│                              │ └────────────────────────────────────┘│
└──────────────────────────────┴───────────────────────────────────────┘
```

### 3.2 필터 탭 구조

| 탭 | 항목 | 데이터 소스 |
|---|---|---|
| **🔍 Search** | 검색어 박스 (한·영 모두) | `stock_universe.ticker / asset_name / asset_name_en` LIKE `%q%` |
| **📊 Descriptive** | 시장(체크박스 KOSPI/KOSDAQ/NASDAQ/NYSE), 섹터(드롭다운, `/api/screener/sectors` 분포), 시총범위(억원·min/max), 시총버킷(small/mid/large 멀티) | `stock_universe` |
| **🚀 Performance** | r1m·r3m·r6m·r1y·YTD 5종 (각 min/max 입력), 정렬 셀렉터 | `stock_universe_ohlcv` CTE |
| **📈 Technical** | 60일 변동성 상한, 거래량비율(20d/60d) 하한, 52주 고점근접도(0~1), 60d max drawdown 상한, MA200 근접도 하한, 일평균 거래대금(KRX·억 / US·천달러) | `stock_universe_ohlcv` CTE |
| **💰 Fundamental*** | 비활성. PER/PBR/EPS/배당수익률/ROE 자리만 dimmed + "곧 출시" 툴팁 | (B-2 단계에서 채움) |

탭 전환은 클라이언트 측 `display:none` 토글. **탭 전환과 무관하게 모든 spec 필드는 보존** — 다른 탭에서 입력한 필터도 합쳐서 `/api/screener/run` 한 번에 전송.

### 3.3 결과 View 프리셋

각 View는 컬럼 화이트리스트만 다르고 같은 응답 데이터에서 골라 렌더링.

| View | 컬럼 |
|---|---|
| **Overview** | 티커·시장 / 이름 / 섹터 / 시총 / 현재가·통화 / r1m / r1y / 거래대금(60d평균) / **스파크라인 60d** |
| **Performance** | 티커 / 이름 / r1m / r3m / r6m / r1y / YTD / 52w-high / 52w-low / drawdown_60d |
| **Technical** | 티커 / 이름 / vol60 / volRatio / ma20 / ma60 / ma200 / 52w근접도 / 현재가 |
| **Custom** | 위 모든 컬럼 + 시총·섹터·등을 체크박스 모달로 토글. localStorage `screener.custom.cols` 저장 |

- 헤더 클릭 → 클라이언트 측 단일 컬럼 정렬 (asc/desc 토글)
- sticky 첫 컬럼 (티커·시장) — 가로 스크롤 시 고정
- 행 클릭 → `/pages/proposals/history/{ticker}` 이동

### 3.4 스파크라인 (60일)

- Overview View 한정. SVG 60×16 px 인라인 polyline.
- 데이터: API가 `sparkline_60d: [60개 close 배열]` 반환 (요청 시 `include_sparkline=true`).
- 토글: 우측 상단 작은 chip `📉 sparkline [on/off]` — `localStorage screener.sparkline = true|false`. off 시 API에도 false 전달 (응답 페이로드 절감).

### 3.5 티어/페이지네이션

- 한도: `SCREENER_RESULT_ROW_LIMIT` 그대로 (Free 50 / Pro 200 / Premium 500).
- 페이지네이션 도입하지 않음. 한 번에 fetch.
- `count == limit_applied` 면 결과 카드 헤더에 노란 배너 — "한도 K건에 도달했습니다. 더 좁은 필터를 사용하세요".

### 3.6 모바일 (≤ 768px)

- 탭: `display:flex; overflow-x:auto; gap:0; padding-bottom:4px;` — 가로 스크롤. 활성 탭 underline.
- View 프리셋: CSS media query로 버튼 그룹 → `<select>` 변환.
- 결과 테이블: 가로 스크롤, sticky 첫 컬럼.
- 컬럼 토글 모달: 풀스크린 sheet 형태.

### 3.7 접근성

- 탭은 `<button role="tab">` + `aria-selected`. 키보드 좌/우 화살표로 탐색.
- View 프리셋도 동일 패턴.
- 정렬 헤더는 `aria-sort="ascending|descending|none"`.

---

## 4. API 명세

### 4.1 `POST /api/screener/run` 확장

기존 spec 필드는 100% 유지. 추가:

```jsonc
{
  // 기존 필드 (변경 없음): markets, sectors, market_cap_krw, min_daily_value_krw,
  // min_daily_value_usd, return_1y_range, volume_ratio_min, max_vol60_pct,
  // high_52w_proximity_min, sort, limit

  // 신규
  "q": "삼성",                              // ticker/asset_name/asset_name_en LIKE %q%
  "market_cap_buckets": ["large","mid"],   // small/mid/large multi
  "return_ranges": {                       // r1m·r3m·r6m·r1y·ytd
    "1m": {"min": -10, "max": 50},
    "3m": {...},
    "6m": {...},
    "1y": {...},
    "ytd": {...}
  },
  "max_drawdown_60d_pct": 15,              // 60d 고점 대비 최대 낙폭 절대값 상한(%) — drawdown_60d_pct ≥ -15 와 동치
  "ma200_proximity_min": 0.95,             // close_latest / ma200 ≥ 값
  "include_sparkline": true,               // true 시 응답에 sparkline_60d 포함 (false면 응답 페이로드에서 제거)
  "view": "overview"                       // 클라이언트 렌더링 hint — 서버는 무시 (모든 컬럼 항상 반환, sparkline 만 include_sparkline 으로 분기)
}
```

응답:

```jsonc
{
  "count": 27,
  "tier": "pro",
  "limit_applied": 200,
  "rows": [
    {
      "ticker": "005930", "market": "KOSPI",
      "asset_name": "삼성전자", "asset_name_en": "Samsung Electronics",
      "sector_norm": "semiconductors",
      "market_cap_krw": 480000000000000, "market_cap_bucket": "large",
      "last_price": 71500, "last_price_ccy": "KRW",
      // OHLCV 메트릭 (조인된 경우)
      "close_latest": 71500, "high_252d": 88000, "low_252d": 51000,
      "ma20": 70200, "ma60": 68800, "ma200": 65300,
      "avg_daily_value": 412000000000, "vol60_pct": 1.84,
      "volume_ratio": 1.12, "high_52w_proximity": 0.812,
      "ma200_proximity": 1.095, "drawdown_60d_pct": -3.2,   // 60d 고점 대비 -3.2% (peak-to-current)
      "r1m": 4.1, "r3m": 9.2, "r6m": -1.5, "r1y": 22.4, "ytd": 7.8,
      "sparkline_60d": [69200, 69800, ..., 71500]   // include_sparkline 시만
    }
  ]
}
```

### 4.2 `GET /api/screener/sectors` (신규)

`stock_universe`에서 `sector_norm` 분포 반환 — 드롭다운 옵션 채우기용.

```jsonc
{
  "count": 28,
  "sectors": [
    {"key": "semiconductors", "label": "반도체", "count": 47},
    {"key": "energy", "label": "에너지", "count": 23},
    ...
  ]
}
```

- 캐시: 응답 헤더 `Cache-Control: public, max-age=1800` (30분). 28버킷이 자주 안 바뀜.
- label은 sector_norm key의 한국어 매핑 (없으면 key 그대로). 매핑 사전은 `analyzer/screener.py` 에 이미 있는 sector_norm 목록과 일관되게 — 별도 dict 추가 (간단한 const).

### 4.3 프리셋 CRUD — 변경 없음

`screener_presets.spec` JSONB는 자유 형식이라 신규 필드 자동 호환.

---

## 5. DB 쿼리 변경

`/api/screener/run` CTE 확장:

```sql
WITH ranked AS (
    SELECT ticker, UPPER(market) AS market, trade_date,
           close::float AS close, volume, change_pct::float AS change_pct,
           ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                              ORDER BY trade_date DESC) AS rn
    FROM stock_universe_ohlcv
    WHERE trade_date >= CURRENT_DATE - 400          -- 1y + buffer
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
    -- 작년 마지막 거래일 close (휴장 대비 30일 윈도우)
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
    SELECT m.*, y.close_ytd,
        (m.close_latest - m.close_1m) / NULLIF(m.close_1m,0) * 100 AS r1m,
        (m.close_latest - m.close_3m) / NULLIF(m.close_3m,0) * 100 AS r3m,
        (m.close_latest - m.close_6m) / NULLIF(m.close_6m,0) * 100 AS r6m,
        (m.close_latest - m.close_1y) / NULLIF(m.close_1y,0) * 100 AS r1y,
        (m.close_latest - y.close_ytd) / NULLIF(y.close_ytd,0) * 100 AS ytd,
        -- 60일 고점 대비 현재 낙폭 (peak-to-current). 음수가 정상. ex) high=100, now=85 → -15
        (m.close_latest - m.high_60d) / NULLIF(m.high_60d,0) * 100  AS drawdown_60d_pct,
        m.close_latest / NULLIF(m.ma200,0)                          AS ma200_proximity,
        m.close_latest / NULLIF(m.high_252d,0)                      AS high_52w_proximity,
        CASE WHEN m.v60>0 THEN m.v20/m.v60 END                      AS volume_ratio
    FROM metrics m
    LEFT JOIN ytd_anchor y ON y.ticker=m.ticker AND y.market=m.market
)
SELECT u.ticker, u.market, u.asset_name, u.asset_name_en, u.sector_norm,
       u.market_cap_krw, u.market_cap_bucket, u.last_price, u.last_price_ccy,
       om.close_latest, om.high_252d, om.low_252d,
       om.ma20, om.ma60, om.ma200,
       om.avg_daily_value, om.vol60_pct, om.volume_ratio,
       om.high_52w_proximity, om.ma200_proximity, om.drawdown_60d_pct,
       om.r1m, om.r3m, om.r6m, om.r1y, om.ytd,
       om.sparkline_60d   -- 응답 직전 Python 단에서 include_sparkline=False 면 row.pop('sparkline_60d')
FROM stock_universe u
LEFT JOIN ohlcv_metrics om
  ON UPPER(u.ticker)=UPPER(om.ticker) AND UPPER(u.market)=om.market
WHERE u.listed=TRUE AND u.has_preferred=FALSE
  AND <dynamic where>
ORDER BY <dynamic order>
LIMIT <limit>
```

WHERE 동적 추가:

- `q`: `(u.ticker ILIKE %s OR u.asset_name ILIKE %s OR u.asset_name_en ILIKE %s)` (각 `%q%`).
- `market_cap_buckets`: `u.market_cap_bucket = ANY(%s)`.
- `return_ranges.{1m,3m,6m,1y,ytd}.min/max`: `om.r{period} IS NOT NULL AND om.r{period} BETWEEN %s AND %s`.
- `max_drawdown_60d_pct`: 사용자가 보내는 값은 **절대값(양수)**. WHERE에는 부호 뒤집어 비교 — `om.drawdown_60d_pct IS NOT NULL AND om.drawdown_60d_pct >= -%s` (drawdown_60d_pct 자체는 음수가 정상, peak-to-current). 예: 입력 15 → 낙폭 15% 이내 종목.
- `ma200_proximity_min`: `om.ma200_proximity >= %s`.

ORDER BY 추가:
- `r1m_desc / r3m_desc / r6m_desc / ytd_desc / drawdown_asc / liquidity_desc` 등.

### 5.1 인덱스

기존 인덱스로 충분 — 동적 WHERE는 `u.listed`, `u.market`, `u.market_cap_krw`, `u.sector_norm` 모두 인덱스가 있고, OHLCV는 `(ticker, market, trade_date)` PK + `trade_date` 인덱스로 CTE 스캔 가능. `q` LIKE는 트라이그램 인덱스 미설치 — Free `q` 입력은 짧은 키워드(2자 이상)만 허용해 풀스캔 영향 제한 (서버 측 가드 `len(q) >= 2`).

---

## 6. 클라이언트 구조

### 6.1 모듈화 — 단일 `screener.html` 안에서 IIFE로 분리

기존 함수가 전역에 흩뿌려져 있음. 리팩터링하면서 묶는다:

```js
(function() {
  const SpecBuilder = { fromDOM(): spec, toDOM(spec) };
  const TabSwitcher = { activate(tabId) };
  const ViewRenderer = { render(rows, view), columns: {...} };
  const SortState = { col, dir, apply(rows) };
  const SparklineSVG = { build(arr) -> SVG string };
  const PresetUI = { save, load, list, delete };
  const Screener = { run(), debounceQ() };
  window.Screener = Screener;   // onclick 호환용 일부만 노출
})();
```

기존 inline `onclick` 핸들러는 최소 유지 (`onclick="Screener.run()"` 식). 이 파일에 한해 점진 모듈화.

### 6.2 CSS

신규 파일: `api/static/css/src/14_screener.css` (CSS 빌드 파이프라인 — `tools/build_css.py` 자동 통합).

- `.screener-tabs`, `.screener-tab`, `.screener-tab.active`
- `.view-toggle`, `.view-btn`
- `.result-table` (sticky 첫 컬럼, 가로 스크롤)
- `.sparkline-svg`
- 모바일 미디어 쿼리

기존 `.card` 활용. 인라인 스타일은 가능한 클래스로 이동.

### 6.3 sector 라벨 매핑

`api/routes/screener.py` 모듈 상수:

```python
SECTOR_LABELS = {
    "semiconductors": "반도체",
    "energy": "에너지",
    "financials": "금융",
    # ... (28개 sector_norm 한국어 라벨)
}
```

라벨 누락 시 fallback = key 그대로. 매핑은 `analyzer/screener.py` 의 sector_norm 정의와 일관되게.

---

## 7. 호환성·롤백

- DB 스키마 변경 **없음**. 마이그레이션 추가 없음.
- `/api/screener/run` 신규 spec 필드는 모두 optional → 기존 클라이언트가 보내는 spec도 동일하게 동작.
- 기존 `screener_presets` 행은 그대로 로드됨 (신규 필드 없으면 빈 값으로 표시).
- 롤백: 템플릿/라우트 git revert로 즉시 복원 가능.

---

## 8. 테스트 (가벼움)

- `tests/test_screener_run.py` — `q`, `return_ranges`, `max_drawdown_60d_pct`, `include_sparkline` 각각이 spec에 들어갔을 때 SQL이 정상 생성·실행되는지 smoke 테스트 (기존 conftest psycopg2 mock 활용 / 또는 sqlite 미러 어렵다면 SQL string assertion 위주).
- `tests/test_screener_sectors.py` — `/api/screener/sectors` 응답 형태/캐시 헤더 확인.
- 기존 프리셋 호환성: 빈 spec(`{}`)으로 `/run` 호출 시 정상 응답 (이건 기존 테스트로 충분하면 skip).

---

## 9. Out of Scope (후속 작업)

- **Fundamental 데이터 (B-2)** — `stock_fundamentals` 테이블 + `analyzer/universe_sync.py --mode fundamentals` (yfinance/pykrx). 별도 spec.
- **다중 컬럼 정렬** — 단일만.
- **CSV/Excel export** — 필요 시 후속.
- **유사 종목 추천 / Smart Filter (자연어)** — 후속.
- **인덱스/ETF 비교 차트** — 후속.

---

## 10. AI Top Picks ↔ Screener 정체성 정리

본 작업과 함께 두 도구의 정체성을 명확히 하기 위해 **라벨 변경 + 양방향 cross-link**를 포함한다.

### 10.1 분리 유지 결정

메이저 서비스(Yahoo / Seeking Alpha / Zacks / Morningstar / 한국 증권사) 모두 큐레이션 추천과 스크리너를 **별도 메뉴로 분리** 운영. 본질적으로 다른 사용자 의도(수동 큐레이션 vs 능동 발굴)를 다루기 때문. 우리도 분리 유지.

| 차원 | AI Top Picks (`/pages/proposals`) | Screener (`/pages/screener`) |
|---|---|---|
| 데이터 소스 | `daily_top_picks` + `investment_proposals` Stage 1·2 분석 | `stock_universe` + `stock_universe_ohlcv` 메트릭 |
| AI 개입 | 높음 (Stage 1·2·3 Claude SDK) | 낮음 (룰 기반 필터) |
| 갱신 | 매일 1회 배치 | 사용자 트리거 즉시 |
| 결과 폭 | 좁음 (TopN + 근거/리스크 텍스트) | 넓음 (수십~수백 매칭) |
| 사용자 의도 | "골라줘" | "내 기준으로 찾기" |

### 10.2 라벨 변경 — `Stock Picks` → `AI Top Picks`

이유:
- "Stock Picks"는 정체성이 약함 (사용자가 만든 픽인지 AI가 큐레이션한 것인지 모호).
- 사이드바 다른 항목(`AI Tutor`, `Theme Chat`)과 "AI" 접두 패턴 일관.
- "AI"를 명시함으로써 Stage 1·2·3 분석 결과라는 점이 명확.

영향 파일 (라벨 텍스트만 변경, URL/active_page 키는 유지):
- `api/templates/base.html` (sidebar 1곳)
- `api/templates/watchlist.html` (CTA/empty state 3곳)
- `api/templates/dashboard.html` 또는 `partials/dashboard/_top_picks.html` (혹시 "Stock Picks" 라벨이 있는 경우 일괄 점검)
- `api/templates/proposals.html` (페이지 헤더 타이틀)

`active_page='proposals'` / URL `/pages/proposals` 는 변경하지 않음 — 라우트 정합성 깨면 안 됨. **표기 라벨만**.

### 10.3 Cross-link (양방향)

#### (a) Screener → AI Top Picks 식별 뱃지

오늘의 `daily_top_picks` 에 포함된 종목이 Screener 결과에 등장하면 행에 **🏆 AI Pick** 뱃지 표시.

API 변경 — `/api/screener/run` 응답에 `is_top_pick: bool` 추가:

```sql
-- CTE 추가 — 가장 최근 analysis_date 의 Top Picks 사용
-- (KST 06:30 이전엔 오늘 픽이 없으므로 어제·지난 N일 픽으로 폴백)
top_picks_recent AS (
    SELECT DISTINCT UPPER(p.ticker) AS ticker, UPPER(p.market) AS market
    FROM investment_proposals p
    JOIN daily_top_picks d ON d.proposal_id = p.id
    WHERE d.analysis_date = (
        SELECT MAX(analysis_date)
        FROM daily_top_picks
        WHERE analysis_date >= CURRENT_DATE - INTERVAL '7 days'
    )
)
-- SELECT 추가
(tp.ticker IS NOT NULL) AS is_top_pick
-- LEFT JOIN
LEFT JOIN top_picks_recent tp
  ON tp.ticker = UPPER(u.ticker) AND tp.market = UPPER(u.market)
```

`daily_top_picks.proposal_id INT REFERENCES investment_proposals(id)` (v15) 로 JOIN 키 확정. 7일 이상 픽이 없으면 결과는 모두 `is_top_pick=false` (스크리너는 정상 동작, 뱃지만 안 보임).

뱃지 클릭 시 `/pages/proposals/history/{ticker}` 로 이동 (이미 행 클릭과 동일 — 별도 `aria-label` 만 추가).

#### (b) AI Top Picks → Screener "비슷한 종목" CTA

`api/templates/proposals.html` (또는 detail 매크로 `_macros/proposal.html`) 의 종목 카드에 작은 CTA 버튼:

> 🔍 비슷한 종목 더 찾기 (Screener)

링크 형태: `/pages/screener?sectors=<sector_norm>&market_cap_buckets=<bucket>` (URL 쿼리스트링으로 prefilled)

Screener 페이지가 로드 시 `URLSearchParams` 를 읽어 spec 으로 변환 + 자동 실행. 클라이언트 한 줄 추가.

### 10.4 작업 영향 요약

- 신규 파일 없음
- DB 스키마 변경 없음 (CTE 추가만)
- 영향 파일: `base.html`, `watchlist.html`, `dashboard.html`/partials, `proposals.html`/macros, `screener.html`, `screener.py`
- 사용자 입장에서: 사이드바 라벨 1자리 + 결과 표 뱃지 + 픽 카드 버튼 1개 (가벼운 변화)

---

## 11. 작업 단위 (Implementation Plan에서 세분화)

1. `api/routes/screener.py` — `/run` CTE/WHERE 확장, `/sectors` 신설, sector 라벨 const, `top_picks_today` CTE + `is_top_pick`
2. `api/templates/screener.html` — 탭/View/검색박스/컬럼 토글/스파크라인 렌더 + 모듈 IIFE 정리, `is_top_pick` 뱃지, URL 쿼리스트링 prefill
3. `api/static/css/src/14_screener.css` — 탭/뷰/sticky/모바일 미디어 + AI Pick 뱃지 스타일
4. **라벨 변경**: `api/templates/base.html`, `api/templates/watchlist.html`, `api/templates/dashboard.html` (+ partials), `api/templates/proposals.html` 의 "Stock Picks" → "AI Top Picks"
5. **Cross-link**: `api/templates/proposals.html` 또는 `_macros/proposal.html` 에 "🔍 비슷한 종목 더 찾기" 버튼
6. `tests/test_screener_run.py` — 신규 spec 필드 + `is_top_pick` 플래그 smoke
7. (옵션) `tools/build_css.py` 한 번 실행해서 빌드 산출물 갱신

---

## 12. 결정 기록

| # | 질문 | 선택 | 이유 |
|---|---|---|---|
| 1 | 펀더멘털 데이터 포함? | **B-3 Hybrid** | DB에 PER/PBR 등 없음. UI 자리만 두고 데이터는 후속 spec. |
| 2 | 레이아웃 패턴? | **L-1 Finviz** | base.html 좌측 sidebar와 충돌 없음. 모바일 자연. View 프리셋이 본질적 강점. |
| 3 | 결과 페이지네이션? | **없음** (한 번 fetch) | 티어 한도가 이미 50~500 — 일반 DOM 테이블로 충분. |
| 4 | 정렬? | 단일 컬럼 클라이언트 정렬 | 다중 정렬은 UX 무거움 + 현 데이터로 가치 적음. |
| 5 | 스파크라인? | Overview만, on/off 토글 | 시각적 가치 vs 페이로드 트레이드오프. 토글로 사용자 선택. |
| 6 | DB 스키마 변경? | 없음 | 기존 `stock_universe` + `stock_universe_ohlcv` 만으로 모든 메트릭 산출. |
| 7 | Stock Picks vs Screener 분리/통합? | **분리 유지** | Yahoo/Zacks/Morningstar/한국증권사 모두 분리. 사용자 의도(수동 큐레이션 vs 능동 발굴)가 본질적으로 다름. |
| 8 | Stock Picks 라벨? | **`AI Top Picks` 로 변경** | 정체성 강화(AI 큐레이션 명시) + 사이드바 "AI" 접두 패턴(`AI Tutor` 등) 일관. URL/active_page 키는 유지. |
| 9 | Cross-link? | **양방향** (Screener 결과에 AI Pick 뱃지, AI Top Picks 카드에 "비슷한 종목" Screener prefilled CTA) | 분리는 유지하되 두 도구를 잇는 가벼운 path 제공 — 사용자가 한 도구에서 다른 도구로 자연스럽게 흐름. |
