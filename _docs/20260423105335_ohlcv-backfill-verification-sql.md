# OHLCV 백필 검증 SQL 스크립트 모음

> **작성일**: 2026-04-23 10:53 KST
> **목적**: 라즈베리파이 최초 OHLCV 마이그레이션/백필 실행 시 정상 동작 여부를 `psql`로 직접 확인
> **관련 문서**: [`20260423101419_ohlcv-operations.md`](20260423101419_ohlcv-operations.md) (운영 매뉴얼)
> **대상 커맨드**:
> ```bash
> # 1. 마이그레이션 적용
> python -c "from shared.config import AppConfig; from shared.db import init_db; init_db(AppConfig().db)"
>
> # 2. 초기 800일 백필 (~25분: KRX 20분 + US 5분)
> python -m analyzer.universe_sync --mode backfill --days 800 --market KRX
> python -m analyzer.universe_sync --mode backfill --days 800 --market US
> ```

---

## 0. psql 접속 (한 번만)

```bash
# 방법 A — .env 비밀번호를 읽어 psql 세션 접속 (interactive)
cd /home/pi/investment-advisor   # 본인 경로
set -a && source .env && set +a
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"

# 방법 B — 한 줄 쿼리 (반복 모니터링용)
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c "<SQL>"
```

---

## 1. 마이그레이션 적용 확인 (백필 시작 전, 한 번만)

```sql
-- 1-1) 현재 스키마 버전 — 기대: 27
SELECT MAX(version) AS current_version,
       MAX(applied_at) AS latest_applied
FROM schema_version;

-- 1-2) v25~v27 적용 이력
SELECT version, applied_at
FROM schema_version
WHERE version >= 25
ORDER BY version;
-- 기대: v25/v26/v27 모두 존재

-- 1-3) 테이블 생성 확인
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'stock_universe_ohlcv';
-- 기대: 1 row

-- 1-4) 컬럼 구조 상세 (psql 내장 명령)
\d stock_universe_ohlcv
-- 기대 컬럼 : ticker/market/trade_date/open/high/low/close/volume/
--             change_pct/data_source/adjusted/created_at
-- 기대 PK   : (ticker, market, trade_date)

-- 1-5) 인덱스 — 3개 (PK + idx_ohlcv_date + idx_ohlcv_ticker_desc) 기대
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'stock_universe_ohlcv'
ORDER BY indexname;
```

---

## 2. 백필 실행 중 — 진행률 모니터링 (별도 터미널, 반복)

### 2-A. 현재 진행 스냅샷

```sql
SELECT market,
       COUNT(DISTINCT trade_date) AS days,
       COUNT(DISTINCT ticker)     AS tickers,
       COUNT(*)                   AS total_rows,
       MIN(trade_date)            AS first_date,
       MAX(trade_date)            AS last_date,
       pg_size_pretty(pg_total_relation_size('stock_universe_ohlcv')) AS table_size
FROM stock_universe_ohlcv
GROUP BY market
ORDER BY market;
```

**800일 완료 기준 기대치:**

| market | days | tickers | rows (근사) |
|---|---|---|---|
| KOSPI  | ~560 | ~949   | ~530,000 |
| KOSDAQ | ~560 | ~1,820 | ~1,020,000 |
| NASDAQ | ~560 | ~174   | ~97,000 |
| NYSE   | ~560 | ~342   | ~190,000 |
| **합계** |  |  | **~1,840,000 (~200MB)** |

### 2-B. 실시간 INSERT 속도

```sql
-- 최근 30초 insert 수
SELECT COUNT(*) AS rows_last_30s
FROM stock_universe_ohlcv
WHERE created_at > NOW() - INTERVAL '30 seconds';

-- 최근 5분 분당 insert 속도
SELECT date_trunc('minute', created_at) AS minute,
       COUNT(*)                         AS rows_inserted
FROM stock_universe_ohlcv
WHERE created_at > NOW() - INTERVAL '5 minutes'
GROUP BY 1
ORDER BY 1 DESC;
```

### 2-C. 터미널 실시간 watch (2초마다 갱신)

```bash
# 별도 터미널에서 실행 — Ctrl+C 종료
watch -n 2 "PGPASSWORD=\"$DB_PASSWORD\" psql -h \"$DB_HOST\" -U \"$DB_USER\" -d \"$DB_NAME\" -c \"
SELECT market, COUNT(DISTINCT trade_date) AS days, COUNT(DISTINCT ticker) AS tickers,
       COUNT(*) AS rows
FROM stock_universe_ohlcv GROUP BY market ORDER BY market;\""
```

### 2-D. 백필 프로세스 로그

```bash
# 직접 실행 터미널에 로그가 보임. systemd로 돌릴 때는:
journalctl -u universe-sync-price.service -f --since "10 min ago"

# 현재 실행 중 프로세스 확인
ps aux | grep universe_sync
```

---

## 3. 백필 완료 후 — 최종 검증

### 3-A. 커버리지

```sql
-- 시장별 거래일 수 — 800일 백필 = 530~560 거래일 기대
SELECT market,
       COUNT(DISTINCT trade_date)          AS days,
       MIN(trade_date)                     AS first_date,
       MAX(trade_date)                     AS last_date,
       CURRENT_DATE - MIN(trade_date)      AS calendar_span_days
FROM stock_universe_ohlcv
GROUP BY market
ORDER BY market;

-- 종목별 평균/최소/최대 거래일 수 (신규 상장 등 짧은 종목 파악)
SELECT market,
       COUNT(DISTINCT ticker)                   AS tickers,
       ROUND(AVG(days_per_ticker)::numeric, 1)  AS avg_days,
       MIN(days_per_ticker)                     AS min_days,
       MAX(days_per_ticker)                     AS max_days
FROM (
  SELECT market, ticker, COUNT(*) AS days_per_ticker
  FROM stock_universe_ohlcv
  GROUP BY market, ticker
) t
GROUP BY market
ORDER BY market;
```

### 3-B. 누락 종목 검출

```sql
-- universe 에는 있으나 OHLCV 에 한 번도 등장하지 않은 종목 수
SELECT u.market, COUNT(*) AS missing
FROM stock_universe u
LEFT JOIN (SELECT DISTINCT ticker, market FROM stock_universe_ohlcv) o USING (ticker, market)
WHERE u.listed = TRUE
  AND u.has_preferred = FALSE
  AND o.ticker IS NULL
GROUP BY u.market
ORDER BY u.market;
-- 기대: 극소수(신규 상장 IPO 당일 등). 많으면 ticker 단위 backfill 필요

-- 누락 종목 샘플 20건
SELECT u.ticker, u.market, u.asset_name, u.market_cap_bucket
FROM stock_universe u
LEFT JOIN (SELECT DISTINCT ticker, market FROM stock_universe_ohlcv) o USING (ticker, market)
WHERE u.listed = TRUE
  AND u.has_preferred = FALSE
  AND o.ticker IS NULL
ORDER BY u.market, u.ticker
LIMIT 20;

-- 누락 종목을 단건 백필하려면 (ticker 하나씩)
-- python -m analyzer.universe_sync --mode backfill --ticker 005555 --days 800
```

### 3-C. change_pct 재계산 상태

```sql
WITH first_day AS (
  SELECT ticker, market, MIN(trade_date) AS min_d
  FROM stock_universe_ohlcv
  GROUP BY ticker, market
)
SELECT
  COUNT(*) FILTER (WHERE o.change_pct IS NULL)                            AS total_null,
  COUNT(*) FILTER (WHERE o.change_pct IS NULL AND o.trade_date = f.min_d) AS null_first_day_ok,
  COUNT(*) FILTER (WHERE o.change_pct IS NULL AND o.trade_date > f.min_d) AS null_unexpected,
  COUNT(*) FILTER (WHERE ABS(o.change_pct) > 30)                          AS abs_over_30pct
FROM stock_universe_ohlcv o
JOIN first_day f USING (ticker, market);
-- 기대:
--   null_unexpected  = 0  (>0 이면 recompute_change_pct 자동 호출 안 된 것)
--   null_first_day_ok ≈ 종목 수 합계
--   abs_over_30pct    = 소수 (상한가 2연속·공시 이벤트 — 실제 값 샘플은 3-E에서 확인)
```

### 3-D. 디스크 사용량

```sql
SELECT
  pg_size_pretty(pg_total_relation_size('stock_universe_ohlcv')) AS total,
  pg_size_pretty(pg_relation_size('stock_universe_ohlcv'))        AS heap,
  pg_size_pretty(pg_total_relation_size('stock_universe_ohlcv')
               - pg_relation_size('stock_universe_ohlcv'))        AS indexes;
-- 기대 (800일 완료): total ≈ 200~250MB
```

### 3-E. change_pct 이상치 샘플

```sql
SELECT ticker, market, trade_date, close, change_pct
FROM stock_universe_ohlcv
WHERE ABS(change_pct) > 30
ORDER BY ABS(change_pct) DESC
LIMIT 20;
-- 기대: 상한가 2연속·신규 상장 첫날·액면분할 등 실제 이벤트
```

---

## 4. 자동화된 종합 검증 (추천)

개별 쿼리 대신 통합 health-check 도구가 §3-A~3-E를 한 번에 점검합니다.

```bash
# 사람용 리포트
python -m tools.ohlcv_health_check

# 자동화/알림용 — 경고 있으면 exit 1 (cron/CI)
python -m tools.ohlcv_health_check --strict && echo "✅ OK" || echo "⚠ 경고 있음"

# JSON (대시보드 연동)
python -m tools.ohlcv_health_check --json
```

출력 예시:
```
======================================================================
[OHLCV Health Check] stock_universe_ohlcv 무결성 검사
======================================================================

■ 시장별 커버리지
  - KOSDAQ : 560일 × 1820종목 = 1,019,200행  (2024-04-23 ~ 2026-04-22)
  - KOSPI  : 560일 ×  949종목 =   531,440행  (2024-04-23 ~ 2026-04-22)
  - NASDAQ : 560일 ×  174종목 =    97,440행  (2024-04-23 ~ 2026-04-22)
  - NYSE   : 560일 ×  342종목 =   191,520행  (2024-04-23 ~ 2026-04-22)

■ universe 대비 누락 종목 (최대 20건 표시)
  (없음 — 완전 커버리지)

■ change_pct NULL 분석
  전체 NULL   :    3,285
  └ 첫 거래일 :    3,285  (정상)
  └ 비정상    :        0  (>0이면 recompute_change_pct 필요)

■ 경고
  (이상 없음)
======================================================================
```

---

## 5. 빠른 진단 — 단일 한 줄 명령

백필 완료 후 상태를 한 번에 보는 명령:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" <<'SQL'
\echo ==== 스키마 버전 ====
SELECT MAX(version) FROM schema_version;
\echo
\echo ==== OHLCV 커버리지 ====
SELECT market,
       COUNT(DISTINCT trade_date) AS days,
       COUNT(DISTINCT ticker)     AS tickers,
       COUNT(*)                   AS rows,
       MIN(trade_date)            AS first,
       MAX(trade_date)            AS last
FROM stock_universe_ohlcv GROUP BY market ORDER BY market;
\echo
\echo ==== 테이블 크기 ====
SELECT pg_size_pretty(pg_total_relation_size('stock_universe_ohlcv')) AS total_size;
SQL
```

---

## 6. 트러블슈팅

### Q1. `SELECT MAX(version) FROM schema_version` 결과가 27 미만이다
→ `init_db()` 가 실행되지 않았거나 예외로 중단됨. Python 명령을 다시 실행하고 stderr 로그 확인.

### Q2. 백필은 돌았는데 `stock_universe_ohlcv` 가 비어 있다
→ `stock_universe` 메타가 먼저 채워져야 함 (US의 경우 더욱).
```bash
python -m analyzer.universe_sync --mode meta --market ALL
```
→ 이후 backfill 재실행.

### Q3. KRX 백필이 도중에 멈춘다 (pykrx 세션 타임아웃)
→ `.env` 의 `KRX_ID` / `KRX_PW` 확인. 재실행하면 UPSERT 이므로 이미 들어간 row 는 덮어쓰기만 됨(비용 낮음).

### Q4. `null_unexpected` 가 0 이 아니다
→ `recompute_change_pct` 자동 호출이 실패한 경우. 아무 증분 sync 한 번 돌리면 자동 재계산:
```bash
python -m analyzer.universe_sync --mode price --market ALL
```

### Q5. `abs_over_30pct` 가 너무 많다
→ §3-E 의 샘플을 확인. 상한가 2연속 / IPO 첫날 / 액면분할 등 실제 이벤트라면 정상. 다수가 비상식 값이면 `data_source` 별로 분리해 점검.

---

## 7. 연결 문서

- 운영 매뉴얼: [`20260423101419_ohlcv-operations.md`](20260423101419_ohlcv-operations.md)
- 설계 원본: [`20260422235016_ohlcv-history-table-plan.md`](20260422235016_ohlcv-history-table-plan.md)
- systemd 설치: [`../deploy/systemd/README.md`](../deploy/systemd/README.md)
- health-check 도구: [`../tools/ohlcv_health_check.py`](../tools/ohlcv_health_check.py)