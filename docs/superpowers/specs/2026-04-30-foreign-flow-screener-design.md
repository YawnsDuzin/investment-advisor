# Screener — 외국인 수급 PIT 시계열 + 추세 필터 (KRX)

작성일: 2026-04-30
관련 작업 폴더: `analyzer/`, `api/routes/screener.py`, `api/templates/screener.html`, `api/static/js/`, `shared/db/migrations/versions.py`, `deploy/systemd/`
관련 기존 spec: `docs/superpowers/specs/2026-04-26-screener-investor-strategies-design.md`
연관 이슈/대화: `_docs/_prompts/20260429_prompt.md` ("외국인 보유율이 늘고 있는 업체 조회")

---

## 1. 배경 / 동기

현행 Screener (`api/routes/screener.py`) 는 `stock_universe` (정적 메타) + `stock_universe_ohlcv` (가격 메트릭 CTE) + `stock_universe_fundamentals` (PER/PBR/배당 latest) 3종을 LEFT JOIN 한다. **외국인 수급 데이터는 어디에도 시계열로 저장되지 않는다.**

기존 외국인 데이터 보유 현황:
- `investment_proposals.foreign_ownership_pct` (v20) — 추천된 종목 한정, 추천 시점 1회 스냅샷. 시계열 추출 불가.
- `analyzer/krx_data.py:_fetch_krx_series()` — Cockpit 페이지 진입 시 종목 1개씩 60일치 lazy-fetch (실시간, DB 미저장). 1시간 메모리 캐시.

→ "외국인 보유율이 *늘고 있는* 종목" 같은 추세 기반 스크리닝은 **현재 시스템에서 불가능**. PIT 시계열 테이블 + 일별 sync + 스크리너 신규 필터 키 3종을 도입한다.

## 2. 목표 / 비-목표

### 2.1 목표
- KOSPI/KOSDAQ 종목별 외국인 보유율 + 외국인 순매수액 일별 시계열을 신규 테이블 `stock_universe_foreign_flow` (v44) 로 90일 백필 + 매일 sync.
- Screener `/api/screener/run` 에 신규 spec 키 5종 추가:
  1. `min_foreign_ownership_pct` (현재값 하한)
  2. `min_foreign_ownership_delta_pp` + `delta_window_days ∈ {5, 20, 60}` (보유율 변화)
  3. `min_foreign_net_buy_krw` + `net_buy_window_days ∈ {5, 20, 60}` (누적 순매수)
- 정렬 옵션 3종 추가: `foreign_delta_desc`, `foreign_net_buy_desc`, `foreign_ownership_desc`.
- 스크리너 사이드패널에 신규 collapsible 그룹 "외국인 수급" 추가 + chips 매핑.
- `analyzer/foreign_flow_sync.py` 신규 모듈 + `analyzer/universe_sync.py --mode foreign` 모드 합류.
- systemd unit `foreign-flow-sync.service/.timer` (KST 06:40 — fundamentals sync 직후) 추가, 운영자 웹 UI 화이트리스트 등록.
- `tools/foreign_flow_health_check.py` 결측률 진단 도구 추가.

### 2.2 비-목표 (YAGNI)
- **US 종목 미지원** — yfinance `info.heldPercentInstitutions` 는 단일 시점 스냅샷이라 "추세" 정의 자체가 어렵고, 신뢰성 낮음. backlog.
- **외국인 한도 종목 분리 처리** — KT/한국전력 등 한도 별도 종목은 `foreign_ownership_pct` 가 *한도소진율* 의미. v1 에서는 동일 컬럼 사용, alias 분리는 backlog.
- **개인/기관 수급 UI 노출** — 데이터는 같은 pykrx 호출 1회로 무료로 받으므로 **데이터 레이어에 같이 저장** (`inst_net_buy_value`, `retail_net_buy_value` 컬럼). 미래 재백필 회피 목적. UI 필터/표시는 backlog.
- **시드 프리셋 신규 출시** — "외국인 매집 종목 Top 20" 같은 프리셋은 후속 (UI/필터 동작 안정화 후).
- **백테스트 / 알림 자동화** — 본 spec 은 데이터 + 필터 UI 까지. 자동 워치리스트 추가 등은 후속.

## 3. § 1 — 데이터 레이어 + 스키마

### 3.1 수집 소스

| 메트릭 | pykrx API | 컬럼 |
|---|---|---|
| 외국인 보유율 (%) | `pykrx.stock.get_exhaustion_rates_of_foreign_investment(date, date, ticker)` | "지분율" 또는 "보유비중" 컬럼 (DataFrame) |
| 외국인 일별 순매수액 (원) | `pykrx.stock.get_market_trading_value_by_date(start, end, ticker)` | "외국인합계" 컬럼 |

- 두 API 모두 `analyzer/krx_data.py` 에 이미 존재하는 `_fetch_krx_series()` 가 사용 중. 신규 모듈은 *배치 친화적*으로 재구성 (병렬 호출 + 일괄 UPSERT).
- 동일 (ticker, market, snapshot_date) 멱등 UPSERT.
- pykrx 인증 실패 시 `_check_pykrx()` / `_disable_pykrx()` shared guard 로 세션 단위 short-circuit (fundamentals sync 와 동일 패턴).

### 3.2 신규 테이블 — `stock_universe_foreign_flow` (v44)

```sql
CREATE TABLE stock_universe_foreign_flow (
    ticker                  TEXT NOT NULL,
    market                  TEXT NOT NULL,
    snapshot_date           DATE NOT NULL,            -- KRX 영업일 (PIT 기준)
    foreign_ownership_pct   NUMERIC(7,4),             -- 0~100, NULL 허용
    foreign_net_buy_value   BIGINT,                   -- 외국인합계 일별 순매수액 (원, 음수=순매도)
    inst_net_buy_value      BIGINT,                   -- 기관합계 일별 순매수액 (v1 UI 미노출, 미래 확장 대비)
    retail_net_buy_value    BIGINT,                   -- 개인 일별 순매수액 (v1 UI 미노출)
    data_source             TEXT NOT NULL DEFAULT 'pykrx',
    fetched_at              TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, market, snapshot_date)
);
CREATE INDEX idx_foreign_flow_latest ON stock_universe_foreign_flow(ticker, market, snapshot_date DESC);
CREATE INDEX idx_foreign_flow_date   ON stock_universe_foreign_flow(snapshot_date);
```

설계 결정:
- `stock_universe` 와 FK 미설정 — PIT 원칙. 상폐 종목 이력 보존 (`stock_universe_fundamentals/_ohlcv` 와 동일 정책).
- KRX 종목만 row 생성. US 종목은 row 자체가 존재하지 않음 → LEFT JOIN 시 NULL → 필터 적용 시 자연 제외.
- 모든 메트릭 컬럼 NULL 허용 — 한 쪽만 수집 성공한 경우에도 row 보존.
- Retention: `FOREIGN_FLOW_RETENTION_DAYS=400` (기본). 상폐 종목은 cleanup 시 `FOREIGN_FLOW_DELISTED_RETENTION_DAYS=200`.
- 단위: 보유율은 % (예: `30.45`), 순매수액은 원 (예: `15_000_000_000`). UI 표시 시 통화 분기는 KRW 고정 (KRX 한정).
- **`snapshot_date` = KRX 영업일** (데이터의 *기준일*, 수집 일자가 아님). `foreign_ownership_pct` 는 **KSD T+2 결제 룰**로 인해 보통 `snapshot_date - 2 영업일`의 보유 상태를 반영. 순매수액은 `snapshot_date` 당일 확정치.
- **공휴일/거래정지일은 row 자체 미생성** — pykrx 가 영업일이 아닌 날짜를 응답에서 제외해서 반환. 시계열은 영업일만으로 구성됨 (CTE 의 `rn` 카운팅도 자동으로 영업일 기준).
- **기관/개인 컬럼은 v1 UI 에 노출하지 않음** — 같은 pykrx 호출 1회로 무료로 같이 받기 때문에 **데이터 레이어에 미리 저장**. 미래 "기관 매집 종목" 필터 추가 시 재백필 회피.

### 3.3 신규 모듈 — `analyzer/foreign_flow_sync.py`

`analyzer/fundamentals_sync.py` 와 동형 구조:

```python
def fetch_kr_investor_flow(ticker: str, start_date: date, end_date: date) -> list[dict]:
    """단일 종목 N일 일괄 수집. pykrx 두 API 호출:
      - get_exhaustion_rates_of_foreign_investment(start, end, ticker)
      - get_market_trading_value_by_date(start, end, ticker)

    Returns: [{snapshot_date, foreign_ownership_pct, foreign_net_buy_value,
               inst_net_buy_value, retail_net_buy_value, data_source}, ...]
    영업일별 row 생성. 한 API 만 성공해도 다른 컬럼은 NULL 로 row 보존.
    실패 시 빈 리스트. _check_pykrx() / _disable_pykrx() 가드.
    """

def upsert_investor_flow(cur, rows: list[dict]) -> None:
    """일괄 UPSERT (execute_values). PK 충돌 시 모든 메트릭 컬럼 덮어쓰기."""

def sync_market_investor_flow(cur, market: str, tickers: list[str],
                              start_date: date, end_date: date,
                              *, max_workers: int = 4) -> int:
    """병렬 fetch → upsert. 성공 row 수 반환."""

def run_foreign_flow_sync(db_cfg, *, snapshot_date: Optional[date] = None,
                          markets: tuple[str, ...] = ("KOSPI", "KOSDAQ"),
                          backfill_days: int = 0) -> dict:
    """엔트리.
      - backfill_days=0: snapshot_date(default=오늘) 1일만 sync
      - backfill_days=N>0: snapshot_date 기준 과거 N일 일괄 sync

    Returns: {"start_date": ..., "end_date": ..., "by_market": {...}, "total": int}
    """
```

함수명 — 외국인만이 아니라 기관/개인 같이 받으므로 `investor_flow` 로 명명. 파일명/테이블명은 `foreign_flow` 유지 (외국인 필터가 v1 의 *주 사용 사례*, 호환성 위해 변경 최소화).

UPSERT SQL:
```sql
INSERT INTO stock_universe_foreign_flow (
    ticker, market, snapshot_date,
    foreign_ownership_pct, foreign_net_buy_value,
    inst_net_buy_value, retail_net_buy_value,
    data_source
) VALUES %s
ON CONFLICT (ticker, market, snapshot_date) DO UPDATE SET
    foreign_ownership_pct = EXCLUDED.foreign_ownership_pct,
    foreign_net_buy_value = EXCLUDED.foreign_net_buy_value,
    inst_net_buy_value    = EXCLUDED.inst_net_buy_value,
    retail_net_buy_value  = EXCLUDED.retail_net_buy_value,
    data_source           = EXCLUDED.data_source,
    fetched_at            = NOW()
```

배치 동작:
- `stock_universe` 에서 `listed=TRUE AND has_preferred=FALSE AND market IN ('KOSPI','KOSDAQ')` 조건으로 ticker 추출 → ~2,500종목 (KOSPI ~950 + KOSDAQ ~1,550, 보통주만).
- `ThreadPoolExecutor(max_workers=4)` 로 pykrx 병렬 호출 (krx_data.py 와 동일 동시성).
- 두 API 모두 날짜 범위 인자(`start, end, ticker`) 지원 → 종목당 2 호출로 N일치 일괄 수신. 1일분 sync ≈ 5,000 호출, 90일 백필도 동일 호출량 (응답 크기만 큼).
- 예상 소요: pykrx 평균 ~200ms/call, max_workers=4 가정 시 ~5분 (1일 sync). 90일 백필 ~7~10분 (응답 파싱 부담 +).
- `FOREIGN_FLOW_SYNC_ENABLED=false` 면 skip (config 토글).
- `FOREIGN_FLOW_MAX_CONSECUTIVE_FAILURES=50` (기본) — 연속 실패 시 조기 종료 (pykrx throttling 회피).

### 3.4 universe_sync.py 통합

기존 `--mode` choices 에 `"foreign"` 추가:
```python
p.add_argument("--mode",
               choices=("meta", "price", "auto", "ohlcv", "backfill", "cleanup",
                        "indices", "industry_kr", "fundamentals", "foreign"),
               ...)
```
`if args.mode == "foreign":` 분기에서 `run_foreign_flow_sync(cfg.db, backfill_days=args.days or 0)` 호출. 즉:
- `--days` 미지정 → `backfill_days=0` → 오늘 1일 sync.
- `--days 90` → 과거 90일 일괄 백필.

(`fundamentals` 모드와 동일한 args 위임 패턴.)

CLI 예시:
```bash
python -m analyzer.universe_sync --mode foreign                # 오늘 1일 sync
python -m analyzer.universe_sync --mode foreign --days 90      # 과거 90일 백필 (초기 1회)
```

### 3.5 환경 변수 (`.env.example`)

신규 키:
```
FOREIGN_FLOW_SYNC_ENABLED=true
FOREIGN_FLOW_RETENTION_DAYS=400
FOREIGN_FLOW_DELISTED_RETENTION_DAYS=200
FOREIGN_FLOW_MAX_CONSECUTIVE_FAILURES=50
FOREIGN_FLOW_STALENESS_DAYS=2          # health check — 최근 N일 내 row 보유 = 신선
FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI=5.0
FOREIGN_FLOW_MISSING_THRESHOLD_KOSDAQ=10.0
```

`shared/config.py` 에 `ForeignFlowConfig` 데이터클래스 추가 → `AppConfig.foreign_flow` 노출. `FundamentalsConfig` 패턴 그대로 본뜸.

## 4. § 2 — 스크리너 SQL/API 통합

### 4.1 신규 spec 키 (전체)

| 키 | 타입 | 단위 | 의미 |
|---|---|---|---|
| `min_foreign_ownership_pct` | number | % | 최신 보유율 ≥ 입력값 |
| `min_foreign_ownership_delta_pp` | number | %p (음수 허용) | 윈도우 내 보유율 변화 ≥ 입력값 |
| `delta_window_days` | int ∈ {5, 20, 60} | 거래일 | 필터 2 윈도우 (default: 20) |
| `min_foreign_net_buy_krw` | number | 원 (음수 허용) | 윈도우 내 누적 순매수 ≥ 입력값 |
| `net_buy_window_days` | int ∈ {5, 20, 60} | 거래일 | 필터 3 윈도우 (default: 20) |

UI 입력 단위 표시 vs spec 키 단위:
- 필터 2: UI "%p", spec "pp" — 동일.
- 필터 3: UI "억원" (사용자 입력), client JS 에서 ×1e8 후 spec `min_foreign_net_buy_krw` (원) 로 전송.
- 윈도우는 라디오 버튼 (필터 2/3 각각 독립 — 보통 같은 윈도우 쓰지만 강제하지 않음).

### 4.2 SQL CTE 추가

`api/routes/screener.py:run_screener()` 의 `common_ctes` 에 신규 CTE 2개 추가:

```sql
WITH
  -- (기존 latest_fund, top_picks_recent, my_watchlist 유지)

  foreign_flow_ranked AS (
      SELECT ticker, UPPER(market) AS market, snapshot_date,
             foreign_ownership_pct::float AS ownership_pct,
             foreign_net_buy_value AS net_buy,
             ROW_NUMBER() OVER (PARTITION BY ticker, UPPER(market)
                                ORDER BY snapshot_date DESC) AS rn
      FROM stock_universe_foreign_flow
      WHERE snapshot_date >= CURRENT_DATE - 90
  ),
  foreign_flow_metrics AS (
      SELECT ticker, market,
             MAX(CASE WHEN rn=1   THEN ownership_pct END) AS own_latest,
             MAX(CASE WHEN rn=6   THEN ownership_pct END) AS own_d5,
             MAX(CASE WHEN rn=21  THEN ownership_pct END) AS own_d20,
             MAX(CASE WHEN rn=61  THEN ownership_pct END) AS own_d60,
             SUM(net_buy) FILTER (WHERE rn<=5)  AS net_buy_5d,
             SUM(net_buy) FILTER (WHERE rn<=20) AS net_buy_20d,
             SUM(net_buy) FILTER (WHERE rn<=60) AS net_buy_60d
      FROM foreign_flow_ranked
      GROUP BY ticker, market
  )
```

`stock_universe u` 에 LEFT JOIN:
```sql
LEFT JOIN foreign_flow_metrics ff
    ON UPPER(u.ticker) = UPPER(ff.ticker) AND UPPER(u.market) = ff.market
```

신규 spec 키 → WHERE 절 (`include_foreign_flow=True` 면):
- 필터 1: `(ff.own_latest IS NOT NULL AND ff.own_latest >= %s)`
- 필터 2: `(ff.own_latest - ff.own_d{N}) >= %s` — `delta_window_days` 가 5/20/60 중 하나에 따라 컬럼 동적 선택
- 필터 3: `ff.net_buy_{N}d >= %s` — 동일 패턴

JOIN 비용 절감: `ff.*` 가 SELECT 또는 WHERE/ORDER BY 어디서도 참조 안 되면 CTE/JOIN 자체를 생략 (현행 `join_ohlcv` 플래그와 동일 lazy 기법).

### 4.3 신규 정렬 옵션 — 윈도우 자동 연동

`sort_map` 동적 조립 (필터 윈도우와 정렬 윈도우 자동 동기화):

```python
delta_win = int(spec.get("delta_window_days") or 20)
netbuy_win = int(spec.get("net_buy_window_days") or 20)
# 화이트리스트 가드 (SQL injection 방어 + invalid 값 fallback)
if delta_win not in (5, 20, 60): delta_win = 20
if netbuy_win not in (5, 20, 60): netbuy_win = 20

sort_map["foreign_ownership_desc"] = "ff.own_latest DESC NULLS LAST"
sort_map["foreign_delta_desc"]     = f"(ff.own_latest - ff.own_d{delta_win}) DESC NULLS LAST"
sort_map["foreign_net_buy_desc"]   = f"ff.net_buy_{netbuy_win}d DESC NULLS LAST"
```

설계 결정 — **정렬 윈도우 = 필터 윈도우 자동 연동**:
- 사용자가 사이드패널 라디오 "5일" 선택 → spec `delta_window_days=5` → 정렬 ORDER BY 도 `own_d5` 자동 매핑.
- 의도와 일치하는 자연스러운 동작 — "5일 강한 종목 보고 싶다 → 라디오 5일 + 정렬 desc" 일관.
- spec 키 추가 0, sort_map 옵션 수 그대로 3개 유지.
- SQL injection 위험 0 (정수 화이트리스트 인터폴레이션).
- 두 윈도우 (delta/netbuy) 가 다르면 각자 독립 매핑.

기존 sort 옵션 (예: `market_cap_desc`, `r1m_desc`)에서는 `ff.*` 미참조 → CTE/JOIN 자동 생략 (현행 `join_ohlcv` lazy 패턴과 동일).

### 4.4 응답 row 신규 필드

`SELECT` 에 ff 메트릭 노출 (UI 표시용, 윈도우 동적):
```sql
ff.own_latest AS foreign_ownership_pct,
(ff.own_latest - ff.own_d{delta_win})  AS foreign_ownership_delta_pp,
ff.net_buy_{netbuy_win}d               AS foreign_net_buy_krw
```

응답에 윈도우 표기를 위해 별도 필드 추가:
```sql
{delta_win}::int  AS foreign_ownership_delta_window_days,
{netbuy_win}::int AS foreign_net_buy_window_days
```

UI 표 라벨은 응답의 윈도우 필드 값으로 동적 렌더 ("외국인 보유 변화 (%p) — 5일" 등).

## 5. § 3 — UI 통합

### 5.1 사이드패널 신규 그룹

`api/templates/screener.html` 의 기존 `<details class="filter-group" data-group="...">` 그룹 6개 (search/market/cap/perf/tech/fund) → 7개로 확장. 신규 그룹 `data-group="foreign"`, 라벨 "외국인 수급". 위치는 `data-group="fund"` 다음 (마지막).

내부 입력:
```html
<details id="f-group-foreign" data-group="foreign">
  <summary>외국인 수급 <span class="active-dot"></span></summary>
  <label>현재 보유율 ≥ <input id="f-foreign-own-min" type="number" step="0.1" placeholder="예: 30">%</label>

  <fieldset class="window-radio">
    <legend>윈도우</legend>
    <label><input type="radio" name="foreign-delta-window" value="5"> 5일</label>
    <label><input type="radio" name="foreign-delta-window" value="20" checked> 20일</label>
    <label><input type="radio" name="foreign-delta-window" value="60"> 60일</label>
  </fieldset>
  <label>보유율 변화 ≥ <input id="f-foreign-delta-min" type="number" step="0.1" placeholder="예: 1.5 (음수=감소)">%p</label>

  <fieldset class="window-radio">
    <legend>윈도우</legend>
    <label><input type="radio" name="foreign-netbuy-window" value="5"> 5일</label>
    <label><input type="radio" name="foreign-netbuy-window" value="20" checked> 20일</label>
    <label><input type="radio" name="foreign-netbuy-window" value="60"> 60일</label>
  </fieldset>
  <label>누적 순매수 ≥ <input id="f-foreign-netbuy-min" type="number" step="10" placeholder="예: 500 (음수=순매도)">억원</label>
</details>
```

`screener_groups_open_v2` localStorage 키 변경 없음 (기존 toggle 로직 재활용).

### 5.2 SpecBuilder 매핑 (JS)

`fromDOM` / `toDOM` 양방향 매핑 추가:
- DOM `f-foreign-own-min` ↔ spec `min_foreign_ownership_pct`
- DOM `f-foreign-delta-min` ↔ spec `min_foreign_ownership_delta_pp`
- DOM `foreign-delta-window` (radio) ↔ spec `delta_window_days`
- DOM `f-foreign-netbuy-min` 입력값 × 1e8 ↔ spec `min_foreign_net_buy_krw`
- DOM `foreign-netbuy-window` (radio) ↔ spec `net_buy_window_days`

음수 입력은 `<input type="number">` 자연 허용 (제한 안 함).

### 5.3 활성 필터 chips

`CHIP_DEFS` 에 추가:
```js
{ key: 'min_foreign_ownership_pct',     label: (v) => `외국인 보유 ≥ ${v}%` },
{ key: 'min_foreign_ownership_delta_pp', label: (v, spec) => `외국인 ${spec.delta_window_days || 20}일 변화 ≥ ${v >= 0 ? '+' : ''}${v}%p` },
{ key: 'min_foreign_net_buy_krw',       label: (v, spec) => `외국인 ${spec.net_buy_window_days || 20}일 순매수 ≥ ${v >= 0 ? '+' : ''}${(v / 1e8).toFixed(0)}억` },
```

윈도우 키 (`delta_window_days`, `net_buy_window_days`) 자체는 chips 에 단독 표시 안 함 — 변화/순매수 chip 라벨에 동봉. × 클릭 시 같이 reset.

### 5.4 결과 표 신규 컬럼

`screener.html` 결과 `<table>` 헤더에 3개 컬럼 추가 (default 숨김 + 컬럼 토글 패널에서 on/off 가능):
- "외국인 보유율 (%)" → `row.foreign_ownership_pct`
- "보유 변화 (%p) — N일" → `row.foreign_ownership_delta_pp` (헤더 "N일" 부분은 `row.foreign_ownership_delta_window_days` 로 동적 렌더, 양수 녹색·음수 빨강)
- "순매수 (억) — N일" → `row.foreign_net_buy_krw / 1e8` (헤더 "N일" 동적, 양수 녹색·음수 빨강)

기존 컬럼 토글 패턴 그대로 재활용. localStorage 키만 추가.

### 5.5 정렬 드롭다운

기존 `<select id="f-sort">` 에 신규 옵션 3개 추가:
- "외국인 보유율 ↓" (`foreign_ownership_desc`)
- "외국인 보유 변화 ↓" (`foreign_delta_desc`) — 사이드패널 라디오 윈도우와 자동 연동
- "외국인 순매수 ↓" (`foreign_net_buy_desc`) — 동일

## 6. § 4 — 마이그레이션 (v44)

`shared/db/migrations/versions.py` 에 `_migrate_to_v44(cur)` 추가 + `__init__.py` registry 등록 + `shared/db/schema.py:SCHEMA_VERSION = 44` 갱신.

```python
def _migrate_to_v44(cur) -> None:
    """v44: stock_universe_foreign_flow — KRX 종목 투자자별 수급 PIT 시계열.

    pykrx 2종 API 일배치 수집:
      - get_exhaustion_rates_of_foreign_investment → foreign_ownership_pct
      - get_market_trading_value_by_date          → foreign/inst/retail net_buy_value

    v1 UI 는 외국인 컬럼만 노출, 기관/개인은 데이터 레이어에만 저장 (재백필 회피).

    Spec: docs/superpowers/specs/2026-04-30-foreign-flow-screener-design.md §3.2
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe_foreign_flow (
            ticker                TEXT NOT NULL,
            market                TEXT NOT NULL,
            snapshot_date         DATE NOT NULL,
            foreign_ownership_pct NUMERIC(7,4),
            foreign_net_buy_value BIGINT,
            inst_net_buy_value    BIGINT,
            retail_net_buy_value  BIGINT,
            data_source           TEXT NOT NULL DEFAULT 'pykrx',
            fetched_at            TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (ticker, market, snapshot_date)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_foreign_flow_latest
            ON stock_universe_foreign_flow(ticker, market, snapshot_date DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_foreign_flow_date
            ON stock_universe_foreign_flow(snapshot_date);
    """)
    cur.execute("""
        INSERT INTO schema_version (version) VALUES (44)
        ON CONFLICT (version) DO NOTHING;
    """)
    print("[DB] v44 마이그레이션 완료 — stock_universe_foreign_flow")
```

CLAUDE.md "테이블 관계 (CASCADE)" 섹션 + "DB Schema" 키 정의 섹션에 v44 row 추가 (문서 동기화).

## 7. § 5 — systemd / 운영

### 7.1 신규 unit (`deploy/systemd/`)

`foreign-flow-sync.service.in`:
```ini
[Unit]
Description=Investment Advisor — Foreign Flow Sync
After=network-online.target

[Service]
Type=oneshot
User={{ SYSTEM_USER }}
WorkingDirectory={{ INSTALL_DIR }}
ExecStart={{ INSTALL_DIR }}/venv/bin/python -m analyzer.universe_sync --mode foreign
StandardOutput=journal
StandardError=journal
```

`foreign-flow-sync.timer.in`:
```ini
[Unit]
Description=Investment Advisor — Foreign Flow Sync (Daily KST 06:40)

[Timer]
OnCalendar=*-*-* 21:40:00 UTC      # KST 06:40
Persistent=true

[Install]
WantedBy=timers.target
```

`fundamentals-sync.timer` (06:35) 직후 5분 gap. KRX 마감 후 충분한 마진.

**데이터 가용성 주의** — 외국인 보유율은 KSD T+2 결제 룰로 인해 보통 `snapshot_date - 2 영업일`의 보유 상태. 외국인 순매수액은 당일 확정치. 본 sync 는 *분석 파이프라인 (06:30 시작)* 에 의존하지 않음 — 분석은 lazy fetch (`krx_data.py:fetch_investor_trading()`) 유지. 향후 분석에서 PIT 데이터 활용 시 timer 시각 (또는 `analyzer.timer` 시각) 재조정 필요.

### 7.2 웹 UI 화이트리스트 등록

`api/routes/admin_systemd.py:MANAGED_UNITS` 에 추가:
```python
MANAGED_UNITS["foreign-flow-sync"] = {
    "service": "investment-advisor-foreign-flow-sync.service",
    "timer":   "investment-advisor-foreign-flow-sync.timer",
    "self_protected": False,
    "description": "외국인 수급 PIT 일배치 sync",
}
```

`deploy/systemd/README.md` 의 sudoers 화이트리스트 예시도 갱신 (CLAUDE.md "systemd unit 화이트리스트" 규칙).

### 7.3 cleanup 모드 통합

기존 `--mode cleanup` 분기 (`_run_mode_cleanup`) 에 foreign_flow retention 적용 추가:
```python
def _cleanup_foreign_flow(db_cfg, retention_days, delisted_retention_days):
    """Same pattern as cleanup_ohlcv / cleanup_fundamentals."""
```
별도 timer 불필요 — 기존 ohlcv-cleanup timer 가 weekly 로 도는 곳에 합류.

## 8. § 6 — 결측 모니터링

### 8.1 health check 도구 — `tools/foreign_flow_health_check.py`

`tools/fundamentals_health_check.py` 와 동형 구조. 출력 예:
```
[Foreign Flow Health] snapshot_date=2026-04-30
  KOSPI : 950 / 980 (96.9% coverage, 1.2일 평균 지연)
  KOSDAQ: 1,420 / 1,650 (86.1%, 2.1일 평균 지연)  ⚠ KOSDAQ 결측률 임계 초과
  최근 7일 trend: KOSPI ▁▂▂▃▂▂▂  KOSDAQ ▃▄▄▅▄▄▄
```

임계: `FOREIGN_FLOW_MISSING_THRESHOLD_KOSPI=5.0%`, `FOREIGN_FLOW_MISSING_THRESHOLD_KOSDAQ=10.0%`. 초과 시 stderr 경고 + non-zero exit (cron 알림 트리거 가능).

### 8.2 admin 페이지 통합

`api/routes/admin.py` "도구" 탭에 "Foreign Flow 결측률 진단" 버튼 추가 — `subprocess.run(["python", "-m", "tools.foreign_flow_health_check"])` 결과를 SSE 로 스트림. fundamentals 진단 버튼 패턴 그대로 재활용.

## 9. § 7 — 테스트

`tests/test_foreign_flow_sync.py` 신규:
- `_to_float`, `_check_pykrx` 가드 — fundamentals 테스트 mock 패턴 그대로.
- `fetch_kr_investor_flow` — pykrx 모킹 + 정상 row / 빈 DataFrame / 인증 실패 / 한 API 만 성공 (다른 컬럼 NULL) 분기 검증.
- `upsert_investor_flow` — 중복 (ticker, market, snapshot_date) UPSERT 멱등성. inst/retail 컬럼도 같이 덮어씀 검증.
- `run_foreign_flow_sync` — 다중 종목 dry-run, 부분 실패 카운트, `backfill_days=N` 분기.

`tests/test_screener_foreign_flow.py` 신규:
- 신규 spec 키 5종 SQL 생성 검증 (`generated_clause_includes(...)`).
- `delta_window_days` 5/20/60 → `own_d5`/`own_d20`/`own_d60` 컬럼 분기 매핑.
- `delta_window_days` invalid 값 (예: 99, "abc") → fallback 20 보장.
- **정렬 자동 연동** — `sort=foreign_delta_desc` + `delta_window_days=5` → ORDER BY 가 `own_d5` 참조.
- 음수 입력 (`min_foreign_ownership_delta_pp = -2`) WHERE 절 정상 생성.
- LEFT JOIN 결측 종목 (`ff.own_latest IS NULL`) — 필터 적용 시 제외, 미적용 시 row 보존.
- spec 키 전무 + sort 가 ff 미참조 → CTE/JOIN 생략 검증 (lazy 패턴).

`tests/conftest.py` 의 psycopg2 mock 패턴 그대로. 실 DB / pykrx 토큰 불필요.

## 10. § 8 — 배포 순서 (운영기 적용)

1. **로컬 dev**: 마이그레이션 v44 테스트 → `python -m analyzer.universe_sync --mode foreign --days 5` 소규모 백필 검증.
2. **PR 머지 → 운영기 pull → systemctl restart investment-advisor-api**: API 기동 시 `init_db()` 가 v44 자동 적용.
3. **운영기 백필**: `python -m analyzer.universe_sync --mode foreign --days 90` 1회 실행 (예상 ~7~10분, KRX ~2,500 보통주 × 2 API, 날짜 범위 호출로 1회당 90일분 회수).
4. **systemd 등록**: `deploy/systemd/install.sh` 재실행하여 `foreign-flow-sync.timer` enable.
5. **sudoers 갱신**: `/etc/sudoers.d/investment-advisor-systemd` 에 신규 unit 화이트리스트 추가.
6. **health check 1회**: `python -m tools.foreign_flow_health_check` — 결측률 임계 이내 확인.
7. **UI 검증**: 스크리너 페이지 "외국인 수급" 그룹에서 필터·정렬·chips 동작 확인.

## 11. § 9 — 리스크 / 우려 사항

| 리스크 | 대응 |
|---|---|
| pykrx API throttling — 외국인 보유율 조회는 fundamentals 보다 무거움 (단일 row 조회 vs 일별 누적 구조 차이) | `max_workers=4` 유지, `MAX_CONSECUTIVE_FAILURES=50` 가드, 재시도 백오프는 v2 |
| 거래정지/폐장 직전 종목 | snapshot_date row 자체가 없을 수 있음 → IS NULL 결측 처리로 자연 제외, 별도 처리 불필요 |
| 외국인 한도 변경 (희귀) | KT/한국전력 등 한도가 정책 변경되면 보유율 jump → 노이즈. v1 에선 알려진 종목 alias 분리 안 함 (backlog) |
| `foreign_net_buy_value` 단위 혼동 | 백엔드 spec key 는 *원* (`min_foreign_net_buy_krw`), UI 입력은 *억원* — JS 변환 단일 지점에서만 수행, server 검증 시 명확한 단위 주석 |
| 90일 백필 시간 | KRX ~2,500 보통주 × 2 API = 5,000 호출 (날짜 범위 호출 활용). `max_workers=4` + pykrx 평균 응답 200ms 가정 → 실측 7~10분. 응답 파싱 부담만 1일 sync 보다 약간 큼 |
| 정렬 윈도우 인터폴레이션 시 SQL injection | spec 의 `delta_window_days`/`net_buy_window_days` 는 정수 화이트리스트 가드 (5/20/60 외 값 → fallback 20). f-string 인터폴레이션 안전 |
| 외국인 보유율 PIT 의미 혼동 | `foreign_ownership_pct` 는 KSD T+2 결제 룰로 인해 `snapshot_date - 2 영업일`의 보유 상태. UI 툴팁 + spec 주석으로 명시 |

## 12. § 10 — 후속 (Backlog)

- US 종목 외국인/기관 시계열 (yfinance 단일 시점 한계로 별도 데이터 소스 필요)
- 외국인 한도 종목 분리 컬럼 (`foreign_limit_pct`, `is_limited_foreign_ownership`)
- **기관/개인 수급 UI 노출** — 데이터는 v1 부터 같이 수집되므로 SQL CTE 컬럼 추가 + 사이드패널 UI 만 작업하면 됨 (재백필 불필요)
- 시드 프리셋 추가 — "외국인 매집 종목 Top 20" 등
- 보유율 급변 알림 자동화 (워치리스트 푸시)
- 분석 파이프라인 통합 — recommender 가중치에 외국인 수급 점수 반영 (timer 시각 재조정 동반)

---

## 변경 영향 파일 요약

| 파일 | 변경 종류 |
|---|---|
| `shared/db/migrations/versions.py` | `_migrate_to_v44()` 신규 |
| `shared/db/migrations/__init__.py` | v44 registry 등록 |
| `shared/db/schema.py` | `SCHEMA_VERSION = 44` |
| `shared/config.py` | `ForeignFlowConfig` dataclass + `AppConfig.foreign_flow` |
| `analyzer/foreign_flow_sync.py` | 신규 모듈 |
| `analyzer/universe_sync.py` | `--mode foreign` 분기 + cleanup 통합 |
| `api/routes/screener.py` | CTE 2개 + WHERE 절 5종 + sort_map 3종 + SELECT 3컬럼 |
| `api/templates/screener.html` | 사이드패널 그룹 1종 + 결과 컬럼 3종 + 정렬 옵션 3종 |
| `api/templates/screener.html` (inline `<script>`, 575줄~) | SpecBuilder 양방향 매핑 + chips 정의 |
| `tools/foreign_flow_health_check.py` | 신규 도구 |
| `deploy/systemd/foreign-flow-sync.service.in` | 신규 unit |
| `deploy/systemd/foreign-flow-sync.timer.in` | 신규 unit |
| `deploy/systemd/install.sh` | unit 설치 추가 |
| `deploy/systemd/README.md` | sudoers 예시 갱신 |
| `api/routes/admin_systemd.py` | `MANAGED_UNITS` 등록 |
| `api/routes/admin.py` | "도구" 탭 health check 버튼 |
| `.env.example` | 신규 환경변수 7종 |
| `CLAUDE.md` | DB Schema 섹션 v44 row 추가, 환경변수 표 갱신 |
| `tests/test_foreign_flow_sync.py` | 신규 |
| `tests/test_screener_foreign_flow.py` | 신규 |
