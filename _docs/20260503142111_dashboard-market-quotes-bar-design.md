# Dashboard 상단 시장 시세 바 (Market Quotes Bar) — Design

작성일: 2026-05-03 KST
관련 코드: `api/templates/dashboard.html`, `api/templates/partials/_regime_banner.html`
관련 데이터: `market_indices_ohlcv` (v31)

---

## 1. 배경 / 목적

대시보드 상단의 `_regime_banner.html` 은 KOSPI/KOSDAQ/S&P500/NDX100 4종 인덱스를 표시하지만, **장기 추세(200MA 이격, σ60 변동성)** 위주이고 **단기 시세(전일 대비 등락, 종가 수치, 추세 그래픽)** 가 부족하다.

본 작업은 regime banner 위에 **별도의 "오늘의 시장" 시세 바**를 추가하여, 사용자가 대시보드 진입 즉시 시장 단기 동향을 한눈에 파악하도록 한다.

> ⚠ **본 시스템의 본질은 분석 결과 조회 대시보드이지 실시간 시세 모니터링이 아니다.** 따라서 EOD(End-of-Day) 데이터만 사용하며, 장중 실시간 fetch는 도입하지 않는다. 자세한 의사결정 근거는 §2.2 참조.

---

## 2. 의사결정 요약

| # | 결정 | 채택안 | 근거 |
|---|------|--------|------|
| 1 | 정보 방향 | **A. 단기 시세** | "오늘 얼마나 올랐나" 표면 노출 (long-term regime은 기존 banner 유지) |
| 2 | 데이터 시점 | **B-1. EOD 만** | 외부 API 의존 0, 06:30 sync와 정렬, 시스템 본질에 부합 |
| 3 | 배치 | **P-1. regime banner 위 별도 줄** | 시세/레짐 정보 위계 분리, 기존 banner 영향 0 |
| 4 | 카드 구성 | **C-1. `라벨\|종가\|±%\|sparkline`** | 네이버/HTS 표준 레이아웃, 정보 풍부 |
| 5 | 그래픽 | **S-1. 라인 sparkline, 20일** | 1-day 등락률 보완에 적정 윈도우, SVG `<polyline>` 외부 라이브러리 0 |
| 6 | 시점 라벨 | **T-2. 줄 우측 통합 메타** | 카드 표면 깔끔, regime banner의 `computed_at` 패턴 일관 |
| 7 | 모바일 | **M-1. 가로 스크롤** | 정보 손실 0, 한국 사용자 익숙 (네이버/HTS 모바일) |
| 8 | 데이터 주입 | **D-1. 페이지 라우트 → context** | 데이터 양 작음(84 row), 깜빡임 없음, regime과 동일 패턴 |

---

## 3. 아키텍처

### 3.1 데이터 흐름

```
[market_indices_ohlcv]  ← 일배치 (universe-sync-indices.timer)
        │
        ▼
[pages.py:_fetch_market_quotes(db_cfg)]  ← 1회 SQL (4개 인덱스 × 21일)
        │
        ▼
[dashboard()]  ← TemplateResponse context["market_quotes"]
        │
        ▼
[_market_quotes_bar.html partial]  ← Jinja2 렌더 (서버 사이드)
        │
        ▼
[brower]  SVG sparkline 정적 렌더, JS 0
```

### 3.2 컴포넌트

| 파일 | 역할 | 신규/수정 |
|------|------|-----------|
| `api/routes/dashboard.py` | `_fetch_market_quotes(cur)` helper + `dashboard()` context 주입 | 수정 |
| `api/templates/partials/dashboard/_market_quotes_bar.html` | 시세 바 partial | **신규** |
| `api/templates/dashboard.html` | partial include 1줄 추가 | 수정 |

> **모듈 분리 시점**: 첫 사용처는 dashboard 1곳뿐이므로 `dashboard.py` 내부 helper로 시작 (YAGNI). 다른 페이지(예: sessions 목록 상단)에서 재사용 시점 도래하면 `shared/market_quotes.py` 로 추출.

> **기존 helper 재사용**: `dashboard.py:258` 의 `_spark_points(values, w, h)` 가 이미 SVG 좌표 문자열을 생성한다. 이번 작업에선 **새 helper 가 spark_points 를 raw 숫자 배열로 반환**하고, 템플릿에서 `_spark_points()` 와 동일한 비율 계산 로직을 인라인 SVG에 사용한다 (Jinja2 매크로화는 추후 재사용 시점 도래하면 분리).

---

## 4. 데이터 모델

`_fetch_market_quotes()` 가 반환하는 in-memory 구조:

```python
{
    "indices": [
        {
            "code": "KOSPI",                 # market_indices_ohlcv.index_code
            "label": "KOSPI",                # 표시용 (regime._INDEX_LABELS 재사용)
            "trade_date": date(2026, 5, 2),
            "close": 2615.32,                # 최신 종가
            "change_pct": 0.42,              # (close - prev_close) / prev_close * 100
            "change_abs": 10.94,             # close - prev_close
            "spark_points": [2580.1, 2585.4, ..., 2615.32],   # 최근 21영업일 종가 배열 (오래된→최신, 마지막 원소 = close)
            "trend": "up",                   # "up" / "down" / "flat" — sparkline·텍스트 색상
        },
        ...
    ],
    "meta": {
        "kr_trade_date": date(2026, 5, 2),   # KOSPI/KOSDAQ 중 가장 최신 trade_date
        "us_trade_date": date(2026, 5, 1),   # SP500/NDX100 중 가장 최신 trade_date
    },
}
```

- 인덱스 결측 시 해당 인덱스만 `indices` 배열에서 제외 (전체 페이지 영향 없음)
- 모든 인덱스 결측 시 `indices=[]` → partial 자체를 렌더하지 않음 (regime banner와 동일 정책)

### 4.1 SQL 개요

```sql
-- 인덱스별 최근 21영업일 (sparkline 20포인트 + 전일 대비 계산용 1포인트)
WITH recent AS (
    SELECT index_code, trade_date, close::float AS close,
           ROW_NUMBER() OVER (PARTITION BY index_code ORDER BY trade_date DESC) AS rn
    FROM market_indices_ohlcv
    WHERE index_code = ANY(%s)
      AND trade_date >= CURRENT_DATE - 60     -- 영업일 21개 확보용 여유 윈도우
)
SELECT index_code, trade_date, close, rn
FROM recent
WHERE rn <= 21
ORDER BY index_code, trade_date ASC;
```

- 한 번의 쿼리로 4개 인덱스 모두 조회 (`index_code = ANY(...)`)
- Python 측에서 `index_code` 별로 group → spark_points 배열 + 전일 대비 등락 계산
- **21 row 매핑 규칙**: ORDER BY ASC 정렬된 21 row 중 **마지막 1개 = `close` (최신 종가)**, **마지막에서 두 번째 = `prev_close` (전일 종가, change_pct 계산용)**, **전체 21 row의 close 배열 = `spark_points` (오래된→최신, 길이 21)**. sparkline은 추세 컨텍스트에 1 row 더 있다고 그래프 해석에 영향 없으므로 21 포인트 그대로 사용 (포인트당 ~4.7px). 별도 슬라이싱 안 함.
- 단일 row만 있는 인덱스: sparkline 생략, 등락률 NULL → 카드에 라벨+종가만 표시
- **sparkline 최소 포인트 임계: 5** (5 영업일 = 1주). 그 미만이면 "추세"라고 부르기 민망하므로 생략. 임계는 운영 중 결측 패턴 보고 조정 가능.

---

## 5. UI 명세

### 5.1 데스크탑 (≥769px)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚡ 오늘의 시장                                          기준: KR 05/02 · US 05/01 (EOD) │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│ │ KOSPI        │ │ KOSDAQ       │ │ S&P 500      │ │ Nasdaq 100   │           │
│ │ 2,615.32     │ │   848.71     │ │  5,827.04    │ │ 20,841.55    │           │
│ │ ▲ +0.42%     │ │ ▼ -0.18%     │ │ ▲ +1.05%     │ │ ▲ +1.28%     │           │
│ │  ╱╲╱╲╱╲ ╱    │ │  ╲╱╲╱╲╱╲╱    │ │     ╱╲╱╱╱    │ │     ╱╲╱╱╱    │           │
│ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘
[기존 _regime_banner.html — 200MA, σ60, KRX 시장폭 …]
```

- 카드 4개 가로 정렬 (`display: flex; gap: 10px;`)
- 카드 폭: `flex: 1 1 0;` (균등 분할), 최소 폭 160px
- sparkline: SVG, 가로 100%, 높이 28px, stroke 1.5px
- 색상: trend "up" → `var(--green)`, "down" → `var(--red)`, "flat" → `var(--text-muted)`
- 우측 메타: KR/US trade_date 가 같으면 `기준: 05/02 (EOD)` 한 줄로 단순화

### 5.2 모바일 (≤768px)

- `flex-wrap: nowrap; overflow-x: auto;` → 가로 스크롤
- 카드 고정 폭: `min-width: 180px; flex: 0 0 auto;`
- `scroll-snap-type: x mandatory;` + 카드 `scroll-snap-align: start;` → swipe 멈춤 자연스럽게
- 우측 메타는 카드 줄 아래로 줄바꿈 (`flex-direction: column;` on mobile)

### 5.3 SVG sparkline 구현

```jinja
{% set min_y = data.spark_points|min %}
{% set max_y = data.spark_points|max %}
{% set range = (max_y - min_y) or 1 %}
<svg viewBox="0 0 100 28" preserveAspectRatio="none" style="width:100%;height:28px;">
  <polyline
    fill="none"
    stroke="{{ trend_color }}"
    stroke-width="1.5"
    points="{% for y in data.spark_points %}{{ loop.index0 * 100 / (data.spark_points|length - 1) }},{{ 28 - ((y - min_y) / range) * 24 - 2 }} {% endfor %}"
  />
</svg>
```

- 외부 차트 라이브러리 불필요 (Chart.js, ApexCharts 등 도입 안 함)
- `viewBox`/`preserveAspectRatio="none"` 으로 컨테이너에 자동 fit
- 상하 padding 2px 로 stroke가 카드 경계에 닿지 않게

---

## 6. 에러 처리

| 상황 | 동작 |
|------|------|
| `market_indices_ohlcv` 자체가 비어있음 (백필 미수행 환경) | partial 자체 렌더 안 함 (`if market_quotes and market_quotes.indices`) |
| 일부 인덱스만 결측 (예: NDX100 데이터 없음) | 해당 인덱스 카드만 생략, 나머지는 정상 표시 |
| 인덱스 데이터가 1 row 만 있음 (전일 대비 계산 불가) | 카드에 라벨+종가만, 등락률·sparkline 생략 |
| 인덱스 데이터가 2~20 row (sparkline 미달) | 등락률은 표시, sparkline은 가용한 포인트로 그림 (최소 5포인트 미만은 생략) |
| SQL 예외 | `_fetch_market_quotes()` try/except → `None` 반환, 로그 WARNING, 배너 자체 비표시. 페이지 전체 영향 0. |

---

## 7. 테스트 전략

`tests/test_market_quotes.py` (신규):

1. **happy path**: 4개 인덱스 모두 21+ row → indices 4개 반환, 각 spark_points 길이 20, change_pct 정확
2. **single row**: 1 row만 있는 인덱스 → `change_pct=None`, `spark_points=[]` 또는 길이 1
3. **partial missing**: 2개 인덱스만 데이터 존재 → `indices` 배열에 2개만 포함
4. **all missing**: 빈 테이블 → `indices=[]`
5. **trend 분기**: change_pct > 0 → "up", < 0 → "down", == 0 → "flat"
6. **trade_date split**: KR과 US trade_date 다를 때 `meta.kr_trade_date != meta.us_trade_date`

기존 `tests/conftest.py` 의 psycopg2 mock 방식 따라 cursor.fetchall() 결과 주입.

---

## 8. Non-goals (이번 작업에서 안 하는 것)

- ❌ 장중 실시간 시세 fetch (yfinance/pykrx live call)
- ❌ 별도 시세 폴링 systemd timer 추가 (B-3 안 채택)
- ❌ 다른 인덱스 추가 (DAX, Nikkei 등 — 데이터 소스 부재)
- ❌ 종목 단위 워치리스트 시세 표시 (별도 기능)
- ❌ Chart.js 등 차트 라이브러리 도입 (SVG `<polyline>` 으로 충분)
- ❌ regime banner 재설계/통합 (P-3 안 채택, 별도 줄로 분리)
- ❌ 캐싱 레이어 (페이지 로드당 1 SQL — 충분히 빠름, premature optimization)

---

## 9. 후속 작업 (이번 PR 범위 외, 추후 검토)

- 다른 페이지(sessions 목록, briefing 등)에서 재사용 시점 도래하면 `shared/market_quotes.py` 추출
- 카드 클릭 시 인덱스 상세 페이지(아직 없음)로 이동 — 인덱스 상세 페이지가 만들어진 시점에 추가
- 사용자 선호 인덱스 커스터마이징 (예: 일본 사용자는 Nikkei 추가) — 글로벌 사용자 발생 시
