# 종목별 일별 OHLCV 이력 테이블 추가 — 설계·작업·활용 방안

> **상태**: 결정 확정 대기 (v1 draft)
> **작성일**: 2026-04-22 23:50 KST
> **범위**: 신규 스키마 v27 + `analyzer/universe_sync.py` 확장 + 운영 자동화
> **선행 조건**: Phase 1a/1b 완료 (stock_universe 구축됨)
> **후속 의존**: Phase 4(Factor Feedback)·Phase 5(Regime) 구현 전제 인프라

---

## 1. 개요 및 목적

`stock_universe`는 종목별 **현재 상태** 1 row만 관리한다. 시계열 분석(팩터 백테스트, 레짐 판별, 모멘텀 계산)을 위해 **일별 OHLCV를 1년 rolling 보관**하는 이력 테이블을 별도로 추가한다.

### 1.1 왜 필요한가

| 용도 | 현재 한계 | 이력 테이블 도입 후 |
|---|---|---|
| Phase 4 Factor Feedback | `post_return_*_pct`만 사용 → 샘플 부족 | 모든 universe 종목 cross-sectional IC 계산 |
| Phase 5 Regime 판별 | KOSPI 이평·변동성을 매번 pykrx 호출 | DB에서 즉시 계산 (~수십ms) |
| Stage 1 모멘텀 체크 | 종목당 개별 yfinance/pykrx 호출 | DB 배치 쿼리 1회 (수백 배 빠름) |
| Stage 2 심층분석 prefetch | RSI/MACD를 AI가 추정 | DB 기반 계산 후 프롬프트 주입 |
| 스크리너 확장 | 시총·섹터 필터만 | "최근 1y 수익률 하위 N%" 같은 이력 필터 |
| 차트 UI | 현재가만 표시 | 가격/거래량 시계열 차트 |
| 생존편향 보정 | 상폐 종목 가격 이력 X | 상폐 전까지 이력 보관 → -100% 수익률 반영 |

### 1.2 비목표 (Out of Scope)

- Intraday(분봉·틱) 데이터
- 배당락/무상증자 자동 조정 (yfinance adjust_close 옵션만 사용)
- 재무제표(PER/PBR/EPS) 시계열 (별도 테이블 또는 on-demand 호출)
- 실시간 스트리밍 (일별 close까지만 저장)

---

## 2. 일반적인 금융 분석 서비스의 관행 (벤치마크)

설계 결정의 기준점으로 업계 표준을 정리한다.

### 2.1 Bloomberg / FactSet / Refinitiv 같은 시장데이터 공급자 패턴

| 구분 | 관행 |
|---|---|
| **Market Data vs Portfolio Data** | 언제나 **분리**. universe timeseries와 holdings/추천 데이터는 별도 테이블 |
| **보존 주기** | 가격: 5~30년 / 지표: 2~5년 / 분봉: 수개월 |
| **원본 vs 파생** | 원시 OHLCV는 원형 보존, 지표(RSI/MACD/이평)는 재계산 또는 materialized view |
| **Point-in-time (PIT)** | 상폐·합병 종목도 과거 데이터는 그대로 보관 — 백테스트 생존편향 방지 |
| **조정(adjusted) 가격** | 원시와 조정을 별도 컬럼 또는 별도 소스. 통상 `close`+`close_adj` 2컬럼 |

### 2.2 오픈소스·SaaS 대안 (참고)

| 제품 | 저장 방식 | 특징 |
|---|---|---|
| QuantConnect / LEAN | parquet 파일 / LocalDatabase | 일별·분별 분리, 심볼당 파일 |
| Zipline (Quantopian) | bcolz columnar | adjusted price 별도 |
| yfinance + SQLite (개인용) | 단일 테이블 OHLCV | 배당 조정은 런타임 계산 |
| InfluxDB / TimescaleDB | 시계열 DB | 대량 시계열 특화 |
| **PostgreSQL 일반 테이블** | 일반 테이블 + `(ticker, date)` PK | **중소규모(<수백만 rows)에 충분** — 본 프로젝트 선택 |

### 2.3 본 프로젝트 선택 근거

- Pi 제약 + universe 3,300종목 × 1년 ≈ 80만 rows → **PostgreSQL 일반 테이블로 충분**
- TimescaleDB는 초소형 환경 오버엔지니어링
- 조정가(adjusted) vs 원시(raw)는 **`data_source`별 기본 정책**으로 단순화:
  - pykrx는 adjusted close 반환
  - yfinance는 `auto_adjust=False` 시 raw, `True` 시 adjusted. 우리는 **False → raw** 저장 (종목 수급·변동성 계산에 raw가 정확)

---

## 3. 결정사항 (5개 — 확정 제안)

| # | 항목 | 제안 결정 | 근거 |
|---|---|---|---|
| **1** | v19 `proposal_price_snapshots`와의 관계 | **Option A — 분리 유지** | Market data vs Portfolio data 분리는 업계 표준. 용도(성과 추적 vs 범용 분석)가 명확히 다름 |
| **2** | 백필 범위 / 보존 주기 | **환경변수 `OHLCV_RETENTION_DAYS=400` (기본 400일 ≈ 1.1년)** | 1년 + 52주 고저 계산용 버퍼 50일. 2년 이상은 초기에는 불필요, 필요 시 환경변수로 확장 |
| **3** | OHLCV vs 기술지표 사전 계산 | **원시 OHLCV만 저장 + `change_pct` 1개** | 정규화 원칙. 지표 로직 변경 시 재계산 불필요. pandas-ta로 on-demand 메모리 계산이 빠름 |
| **4** | US 시장 포함 여부 | **KRX + US 둘 다 포함, CLI에서 선택 가능** | universe_sync 구조상 추가 비용 적음. 동시 진행이 일관성 유지에 유리 |
| **5** | 우선주 / 상폐 종목 | **모두 수집. 스크리너만 필터링** | PIT(Point-in-time) 원칙. 생존편향 방지 (Phase 4.2 계획서 §4.2 명시). FK CASCADE 해제하여 universe에서 삭제돼도 OHLCV 유지 옵션 재검토 필요 (§6.1 참조) |

---

## 4. 스키마 설계 (v27)

### 4.1 테이블 DDL

```sql
CREATE TABLE stock_universe_ohlcv (
    ticker        TEXT NOT NULL,
    market        TEXT NOT NULL,
    trade_date    DATE NOT NULL,
    open          NUMERIC(18,4),
    high          NUMERIC(18,4),
    low           NUMERIC(18,4),
    close         NUMERIC(18,4) NOT NULL,
    volume        BIGINT,
    change_pct    NUMERIC(7,4),               -- (close - prev_close) / prev_close * 100
    data_source   TEXT NOT NULL,              -- 'pykrx' | 'yfinance'
    adjusted      BOOLEAN DEFAULT FALSE,      -- 배당·액면분할 조정 여부
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, market, trade_date)
    -- FK는 아래 §6.1 결정 후 추가 여부 확정
);
CREATE INDEX idx_ohlcv_date           ON stock_universe_ohlcv(trade_date);
CREATE INDEX idx_ohlcv_ticker_desc    ON stock_universe_ohlcv(ticker, market, trade_date DESC);
```

### 4.2 설계 결정 근거

- **PK `(ticker, market, trade_date)`**: 멱등 UPSERT 가능 (재실행·백필 안전)
- **`change_pct` 사전계산**: 가장 빈번한 파생값. 저장 비용 vs 조회 속도 trade-off에서 저장 승리
- **`data_source` 명시**: 출처 추적 + 조정 기준 해석
- **`adjusted` 플래그**: 향후 adjusted price 별도 저장 여지 (현재 기본 False)
- **FK 미설정(잠정)**: universe 마스터에서 delisted 처리된 종목의 OHLCV도 보관 필요 (§6.1)

### 4.3 예상 데이터량

| 항목 | 값 |
|---|---|
| 종목 수 (KRX+US) | ~3,300 |
| 거래일 / 년 | ~250 |
| 연간 row 수 | ~825,000 |
| row 크기 | ~60 bytes |
| 테이블 크기 | ~50 MB |
| 인덱스 2종 포함 | ~100 MB |
| Pi SSD 64GB 대비 | **0.16%** (무시 가능) |

---

## 5. 데이터 수집 전략

### 5.1 KRX (pykrx)

#### 일별 증분 (빠름)
```python
# pykrx batch API — 특정 날짜, 전체 시장
pykrx_stock.get_market_ohlcv(date, market='KOSPI')   # 1회 호출 → 949종목
pykrx_stock.get_market_ohlcv(date, market='KOSDAQ')  # 1회 호출 → 1,820종목
# 총 2 API 호출 / 일
```

#### 초기 백필 (느림)
```python
# 250 거래일 × 2 시장 = 500 호출. 세션 로그인 1회 유지 시 ~5분
for day in trading_days:
    for mk in ('KOSPI', 'KOSDAQ'):
        df = pykrx_stock.get_market_ohlcv(day, market=mk)
        upsert(df)
```

### 5.2 US (yfinance)

#### 일별 증분 + 백필 모두 동일 패턴
```python
# batch download API — 여러 ticker, 기간 한 번에
yf.download(
    tickers=all_us_tickers,  # 516개
    period='1d' if incremental else '400d',
    interval='1d',
    group_by='ticker',
    threads=True,
    progress=False,
    auto_adjust=False,        # raw OHLCV 저장
)
```

### 5.3 신규 상장 종목 자동 포함

- `universe_sync --mode meta` 실행 시 신규 종목 감지
- 해당 종목만 따로 `--backfill-days N` 실행하는 helper 제공
- 또는 일별 증분이 신규 종목을 자동으로 포함 (pykrx/yfinance가 상장 이후만 반환)
- 상장일 이전 NULL row는 만들지 않음

### 5.4 상폐 종목

- pykrx `get_market_ohlcv` 결과에서 사라짐 → 자연스럽게 증분 수집 중단
- universe_sync가 `listed=FALSE`로 마킹해도 기존 OHLCV 이력은 그대로 보존
- Retention 정책만 400일 지나면 자동 삭제 (또는 `listed=FALSE`는 즉시 삭제 옵션)

---

## 6. 작업 항목 (수동 / 자동 분리)

### 6.1 사전 결정 필요 (★ = 즉시 답변 가능한 간단 결정)

| # | 항목 | 제안 기본값 | ★ |
|---|---|---|---|
| 1 | FK 정책 — universe 종목 삭제 시 OHLCV도 삭제? | **FK 미설정** (PIT 원칙) | ★ |
| 2 | 재분석 시 덮어쓰기 정책 | UPSERT (PK 중복 시 UPDATE) | ★ |
| 3 | yfinance `auto_adjust` | False (raw 저장) | ★ |
| 4 | Retention 실행 주체 | cron/systemd(자동) or 수동 CLI | 양쪽 모두 지원 |
| 5 | 증분 실행 타이밍 | universe_sync price 모드와 **동시 실행** | ★ |

### 6.2 수동 처리 작업 (사용자/운영자가 1회성 또는 장애 시 실행)

| # | 작업 | 명령 / 방법 | 빈도 |
|---|---|---|---|
| M1 | 스키마 v27 마이그레이션 적용 | `python -c "from shared.db import init_db; from shared.config import AppConfig; init_db(AppConfig().db)"` | **최초 1회** |
| M2 | 초기 400일 백필 | `python -m analyzer.universe_sync --mode backfill --days 400 --market KRX` <br> `python -m analyzer.universe_sync --mode backfill --days 400 --market US` | **최초 1회** (~20분) |
| M3 | 특정 날짜 재수집 (장애 복구) | `python -m analyzer.universe_sync --mode ohlcv --date 2026-04-22 --force` | 필요 시 |
| M4 | Retention 수동 실행 | `python -m analyzer.universe_sync --mode cleanup --days 400` | 주 1회 자동 외 수동 필요 시 |
| M5 | 신규 종목 개별 백필 | `python -m analyzer.universe_sync --mode backfill --ticker 005930 --days 400` | 신규 상장 감지 시 |
| M6 | 데이터 무결성 검사 | `python -m tools.ohlcv_health_check` (신규 도구) | 월 1회 또는 장애 의심 시 |

### 6.3 자동 처리 작업 (systemd / cron으로 상시 기동)

| # | 작업 | 트리거 | 구현 |
|---|---|---|---|
| A1 | 일별 OHLCV 증분 수집 | systemd timer 02:30 KST | `universe_sync --mode price` 내부에 OHLCV UPSERT 로직 추가 (기존 타이머에 묻어가기) |
| A2 | 신규 상장 자동 백필 | meta sync(주간) 직후 diff 감지 | `universe_sync --mode meta`가 새 종목 발견 시 자동 backfill 호출 |
| A3 | Retention cleanup | cron 주 1회 (일요일 04:00) | `DELETE FROM stock_universe_ohlcv WHERE trade_date < CURRENT_DATE - INTERVAL 'N days'` |
| A4 | 상폐 종목 OHLCV 정리 | meta sync 직후 | `listed=FALSE` 종목 중 400일 지난 row 일괄 삭제 (A3 로직과 통합 가능) |
| A5 | 수집 실패 알림 | 일별 sync 후 row 수 검증 | 전일 대비 -50%↓ 감지 시 `app_logs`에 WARNING 기록 + 관리자 알림 |

### 6.4 구현 체크리스트 (개발 작업)

1. **스키마 v27 마이그레이션** — `shared/db/migrations/versions.py` `_migrate_to_v27` 추가
2. **`analyzer/universe_sync.py` 확장**
   - `sync_ohlcv_krx(db_cfg, *, start_date, end_date)` — pykrx 배치
   - `sync_ohlcv_us(db_cfg, *, tickers, period)` — yfinance batch download
   - `backfill_ohlcv(db_cfg, *, days, market)` — 위 두 함수 오케스트레이션
   - `cleanup_ohlcv(db_cfg, *, retention_days)` — DELETE 쿼리
   - CLI 옵션 추가: `--mode {ohlcv, backfill, cleanup}`, `--days N`, `--date YYYY-MM-DD`, `--ticker T`
3. **`shared/config.py`** — `OhlcvConfig` 추가
   - `retention_days`, `auto_adjust`, `ohlcv_on_price_sync: bool`
4. **`.env.example`** — `OHLCV_RETENTION_DAYS=400`, `OHLCV_AUTO_ADJUST=false`, `OHLCV_ON_PRICE_SYNC=true`
5. **systemd / cron 설정** (라즈베리파이)
   - 기존 `universe-sync-price.service`에 OHLCV가 묻어가도록 동작 수정
   - 신규 `universe-sync-cleanup.timer` (주간)
6. **Health check 도구** — `tools/ohlcv_health_check.py`
7. **테스트 / 검증 쿼리** — 본 문서 §9에 정리

### 6.5 예상 소요 (1인 기준)

| 단계 | 시간 |
|---|---|
| 스키마 + 마이그레이션 | 30분 |
| universe_sync 확장 (KRX/US/backfill/cleanup) | 3~4시간 |
| CLI + Config + env | 1시간 |
| systemd/cron 설정 + 문서화 | 1시간 |
| Health check 도구 | 1시간 |
| 1회 백필 실행 + 검증 | 30~60분 (I/O 대기 포함) |
| **총** | **~1일** |

---

## 7. 저장 공간 / 성능 프로파일

### 7.1 쓰기 성능

| 시나리오 | 예상 |
|---|---|
| 일별 증분 (3,300종목) | ~5초 (INSERT batch) |
| 초기 백필 (3,300종목 × 400일) | ~20분 (대부분 API 대기) |
| Retention cleanup (월 1회 수만 row 삭제) | ~5초 |

### 7.2 읽기 성능 (인덱스 기반)

| 쿼리 패턴 | 예상 |
|---|---|
| 특정 종목 1년 이력 | <10ms (`idx_ohlcv_ticker_desc` hit) |
| 특정 날짜 전체 종목 | <50ms (`idx_ohlcv_date` hit) |
| KOSPI 200일 이평 계산 | <200ms (집계 쿼리) |
| Cross-sectional 1개월 수익률 TOP 100 | <500ms |

### 7.3 Pi 리소스 영향

- 디스크: ~100MB (재설계 대상 전체 DB 대비 작음)
- 메모리: INSERT 배치 시 ~50MB peak (execute_values page_size=500 가정)
- CPU: 배치 중 피크 30% 수준, 평시 무시 가능

---

## 8. 리스크 / 트레이드오프 / 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| pykrx 로그인 세션 만료 (백필 중) | 백필 중단 | 기존 `_safe_pykrx_call` 패턴 + 50일 단위 chunk로 분할 |
| yfinance rate limit | US 수집 지연·실패 | threads=True + 실패 시 개별 재시도 + exponential backoff |
| 휴장일 / 부분거래일 | NULL row 시도 | DATE PK로 자연 skip. 빈 DataFrame 반환 시 insert skip |
| 배당락·액면분할 | raw 가격 왜곡 | `adjusted` 컬럼 + 필요시 별도 조정가 수집 (향후) |
| 상폐 처리 지연 | 고아 OHLCV 누적 | A4 자동 정리 + `listed=FALSE` 종목은 retention 축소(옵션) |
| 데이터 공급자 변경 (pykrx 버전 업·API 변경) | 수집 실패 | `data_source` 컬럼으로 migration 경로 추적 가능 |
| 대용량 DELETE 성능 | cleanup 시 블로킹 | 월별 파티션 대신 `DELETE ... WHERE trade_date < ... LIMIT 10000` 반복 |

---

## 9. 검증 기준 (배포 후)

### 9.1 초기 백필 완료 후

```sql
-- 시장별 날짜 커버리지
SELECT market, COUNT(DISTINCT trade_date) AS days,
       MIN(trade_date), MAX(trade_date)
FROM stock_universe_ohlcv GROUP BY market;
-- 기대: days ≈ 250~280 (1년 거래일), max = 전일

-- 종목별 커버리지
SELECT market, COUNT(DISTINCT ticker) AS tickers
FROM stock_universe_ohlcv GROUP BY market;
-- 기대: KOSPI ~949, KOSDAQ ~1820, NASDAQ ~174, NYSE ~342

-- 누락 종목 탐지
SELECT u.ticker, u.market FROM stock_universe u
LEFT JOIN (SELECT DISTINCT ticker, market FROM stock_universe_ohlcv) o
  ON u.ticker = o.ticker AND u.market = o.market
WHERE u.listed = TRUE AND u.has_preferred = FALSE AND o.ticker IS NULL;
-- 기대: 극소수 (신규 상장·IPO 당일)

-- change_pct 분포 점검 (±30% 초과는 이벤트 검증 필요)
SELECT COUNT(*) FROM stock_universe_ohlcv
WHERE ABS(change_pct) > 30;
```

### 9.2 일별 증분 모니터링

- 전일 대비 row 수 변화율: `|Δrows| < 5%` 정상
- `data_source='pykrx'` row 수 / 거래일 = 대략 일정
- `last_error` 로그 (`app_logs`에 기록) 발생률 < 1%

---

## 10. 마이그레이션 경로 (단계적 활성화)

| 순서 | 작업 | 기간 | 위험도 |
|---|---|---|---|
| 1 | 스키마 v27 + 코드 배포 (CLI만, systemd 미연결) | 1일 | 낮음 |
| 2 | 수동 백필 1회 실행 + 검증 | 1일 | 낮음 |
| 3 | systemd timer 연결 (price sync에 OHLCV 묻어가기) | 1일 | 중간 (기존 sync 시간 증가) |
| 4 | cleanup cron 연결 + 월 1회 동작 확인 | 1주 후 | 낮음 |
| 5 | 기존 `fetch_momentum_batch`를 DB 기반으로 리팩터 (선택) | 2일 | 중간 |

각 단계는 독립 배포 가능. OHLCV 테이블만 채워두고 실제 소비는 Phase 4/5 구현 시로 미뤄도 됨.

---

## 11. 환경변수 추가 (제안)

```bash
# =========================================
# OHLCV 이력 (OhlcvConfig — Phase 7)
# =========================================
# 보존 일수 (기본 400 ≈ 1.1년, 52주 고저 버퍼 포함)
OHLCV_RETENTION_DAYS=400

# yfinance 조정 가격 사용 여부 (false 권장 — raw 가격이 변동성/수급 계산에 정확)
OHLCV_AUTO_ADJUST=false

# universe_sync price 모드 실행 시 OHLCV도 함께 수집 (true 권장)
OHLCV_ON_PRICE_SYNC=true

# 백필 기본 일수 (CLI --days 생략 시)
OHLCV_BACKFILL_DAYS=400

# 상폐 종목 OHLCV 축소 retention (0이면 기본 retention 동일)
OHLCV_DELISTED_RETENTION_DAYS=0
```

---

## 12. 활용 방안 (저장 후 — 이게 본 테이블을 만드는 이유)

이력 테이블이 채워지고 나면 다음과 같이 활용 가능하다.

### 12.1 즉시 활용 (Phase 4 구현 전에도 가능)

#### A. Stage 1 모멘텀 체크 DB 리팩터

**현재**: 종목당 pykrx/yfinance 개별 호출 → 100종목 기준 수십 초
**개선**:
```python
# 현재 분석의 proposal ticker 리스트 기반 1회 쿼리
SELECT ticker, market,
       close / LAG(close, 22) OVER (PARTITION BY ticker, market ORDER BY trade_date) - 1 AS return_1m,
       close / LAG(close, 66) OVER (PARTITION BY ticker, market ORDER BY trade_date) - 1 AS return_3m,
       ...
FROM stock_universe_ohlcv
WHERE trade_date = <last_trading_day>;
```
→ 동일 결과를 <100ms로 반환. API 의존도 대폭 감소.

#### B. 스크리너 확장 (Phase 2 연동)

`analyzer/screener.py`의 스펙에 `price_filters` 섹션 추가:
```json
"price_filters": {
  "return_1y_max_pct": 50,         // 최근 1년 수익률 50% 이하 (과열 제외)
  "return_1y_min_pct": -30,        // -30% 이상 (폭락주 제외)
  "volatility_90d_max": 0.05,      // 90일 일변동성 5% 이하
  "volume_avg_20d_min_krw": 1000000000  // 20일 평균 거래대금 10억원 이상 (유동성)
}
```

AI가 스펙 생성 시 테마·시계에 맞춰 이 필터를 조합하도록 프롬프트 가이드 추가.

#### C. Stage 2 심층분석 프롬프트 강화

현재 프롬프트에 실시간 OHLCV만 주입. 개선안:
- 최근 6개월 일별 종가 시계열 요약 (최고/최저/평균/표준편차)
- RSI(14) 현재값
- MACD 신호 방향
- 50일·200일 이평 대비 현재가 위치 (골든크로스 여부)
- 거래량 급증 여부

→ AI의 퀀트 스코어 `momentum` / `size_liquidity` 평가 품질 향상.

#### D. 차트 UI

`api/routes/proposals.py` 또는 `pages.py`에 차트 엔드포인트 추가:
```python
@router.get("/api/ohlcv/{ticker}")
def get_ohlcv(ticker: str, market: str, days: int = 180):
    # stock_universe_ohlcv에서 직접 조회
    ...
```

Jinja2 템플릿에 Chart.js 삽입 → proposal 상세·theme 히스토리에 가격 차트.

### 12.2 Phase 4 (Factor Feedback) 활용

#### Cross-Sectional Information Coefficient (IC) 계산

```sql
-- 특정 날짜에 추천된 종목들의 이후 1개월 수익률
WITH recs AS (
  SELECT proposal_id, ticker, market, quant_score, conviction, discovery_type,
         p.created_at::date AS rec_date
  FROM investment_proposals p
  WHERE p.created_at >= NOW() - INTERVAL '180 days'
),
returns AS (
  SELECT r.proposal_id,
         (o2.close - o1.close) / o1.close AS return_1m
  FROM recs r
  JOIN stock_universe_ohlcv o1
    ON r.ticker = o1.ticker AND r.market = o1.market AND o1.trade_date = r.rec_date
  JOIN stock_universe_ohlcv o2
    ON r.ticker = o2.ticker AND r.market = o2.market
   AND o2.trade_date = r.rec_date + INTERVAL '30 days'
)
-- 팩터별 상관계수 = IC
SELECT ... CORR(quant_score, return_1m) AS ic ...
```

→ 현재는 `post_return_*_pct` 기반만 가능했으나, 이력 테이블로 **특정 시점 기준 과거 재분석**도 가능.

#### 생존편향 보정

상폐된 종목도 `listed=FALSE + delisted_at` 유지하고 OHLCV도 상폐일까지 보관 →
```sql
-- 1년 전 추천 종목 중 지금 상폐된 종목 = -100% 수익률 반영
SELECT p.proposal_id, CASE WHEN u.listed = FALSE THEN -1.0 ELSE ... END AS return
```

### 12.3 Phase 5 (Regime 판별) 활용

#### KOSPI 200일 이평 / 60일 변동성

```sql
-- KOSPI 지수를 별도로 저장하거나, KOSPI 시총가중평균 근사
WITH kospi AS (
  SELECT trade_date,
         AVG(close) OVER (ORDER BY trade_date ROWS 199 PRECEDING) AS ma200,
         STDDEV(change_pct) OVER (ORDER BY trade_date ROWS 59 PRECEDING) AS vol60
  FROM stock_universe_ohlcv
  WHERE ticker = '__KOSPI_INDEX__'  -- 별도 저장 필요 (pykrx get_index_ohlcv)
)
SELECT * FROM kospi ORDER BY trade_date DESC LIMIT 1;
```

→ `analysis_sessions.regime` 판별 시 즉시 사용.

### 12.4 고급 활용 (장기)

#### A. 백테스트 엔진

특정 과거 날짜 기준으로:
1. 당시 스크리너 스펙 재실행 (OHLCV 기반 filter 복원)
2. 선정된 종목의 이후 N일 수익률 집계
3. 벤치마크 대비 알파 측정

→ 설계 개선(프롬프트 튜닝, 가중치 조정)의 효과를 **역사 데이터로 정량 평가**.

#### B. 섹터 로테이션 분석

```sql
SELECT sector_norm, trade_date,
       AVG(change_pct) AS sector_daily_return
FROM stock_universe_ohlcv o
JOIN stock_universe u USING (ticker, market)
WHERE trade_date >= NOW() - INTERVAL '180 days'
GROUP BY sector_norm, trade_date;
```

→ 매크로 대시보드에 "최근 섹터 강세·약세" 차트 (Phase 6.2 품질 메트릭 확장).

#### C. 유사 종목 클러스터링 (상관계수)

- 동일 테마 내 종목들의 수익률 상관관계 → 다양성 제약 동적 조정
- 예: 같은 테마라도 상관계수 0.9 이상인 종목 쌍은 Top Picks에서 2개 중 1개만 선정

#### D. 정기 리포트 자동화

- 매주 일요일: 지난 주 sector_norm별 수익률 / 52주 신고가 돌파 종목 / 거래량 급증 종목
- `api/routes/` 또는 별도 리포트 생성 스크립트

### 12.5 ROI 추정 (활용 가치)

| 활용 | 효과 | 정량 추정 |
|---|---|---|
| 모멘텀 체크 API → DB | 배치 실행 시간 단축 | 110종목 기준 30초 → 0.1초 (**300배**) |
| Stage 2 프롬프트 강화 | 분석 품질 향상 | AI 토큰 사용량 +20%, 답변 정확도 질적 향상 |
| 스크리너 확장 | 후보 품질 | 과열 종목 자동 제외로 early_signal 비율↑ |
| Phase 4 IC 계산 | 가중치 튜닝 근거 | 6개월 → 2개월 운영으로 유의 샘플 확보 |
| Phase 5 Regime | 동적 임계값 | 약세장 폭락 후 혹한기 추천을 자동 축소 |
| 차트 UI | UX | 유저 engagement ↑ |

---

## 13. 오픈 이슈 / 후속 결정 필요

1. **KOSPI/KOSDAQ 지수 자체 저장 여부** — 개별 종목 집계로 근사 vs pykrx `get_index_ohlcv`로 별도 저장 (추천: 별도 저장, 단 Phase 5 구현 시 같이)
2. **배당 이벤트 저장** — 배당락 조정을 위해 배당 지급 이벤트 테이블 추가 여부
3. **분봉(intraday) 확장** — 현재 비목표. 수요 생기면 별도 Phase
4. **TimescaleDB 도입 고려 시점** — row 수 500만 초과 시 재평가 (현재는 연 80만이라 멀음)
5. **데이터 공급자 다각화** — pykrx 장애 시 fallback으로 alpha-vantage/krx 공식 API 추가? (별도 리서치)

---

## 14. 결론 및 다음 액션

### 권장 진행 경로

1. **이 문서의 §3 결정사항 5건 승인** (수정 의견 있으면 논의)
2. **구현 1일**: 스키마 v27 + universe_sync 확장 + CLI
3. **백필 1회 + 검증**: 라즈베리파이 수동 실행 (~30분)
4. **systemd 연결**: 일별 증분을 기존 price sync에 통합
5. **1주일 모니터링**: row 수·커버리지·에러 로그 확인
6. **활용 경로 선택**: §12 중 Phase 4/5 의존도 높은 것부터 (모멘텀 리팩터 또는 Regime)

### 본 문서와 연결되는 타 문서

- 원 설계서: [`20260422172248_recommendation-engine-redesign.md`](20260422172248_recommendation-engine-redesign.md)
- 진행 현황: [`20260422224220_redesign-progress-and-activation.md`](20260422224220_redesign-progress-and-activation.md)
- 본 문서는 **Phase 7 (가칭 "OHLCV History")** 로 원 설계서에 추가 섹션으로 편입될 예정.

---

*본 문서 작성 시점: 2026-04-22 23:50 KST — 결정 확정되면 §3 표를 "✅ 확정"으로 업데이트하고 구현 착수.*
