# OHLCV 이력 테이블 운영 매뉴얼 (Phase 7)

> **작성일**: 2026-04-23 10:14 KST
> **대상**: `stock_universe_ohlcv` 테이블 (스키마 v27)
> **설계 원문**: [`20260422235016_ohlcv-history-table-plan.md`](20260422235016_ohlcv-history-table-plan.md)
> **확정 설정**: 2년(800일) rolling 보관 / KRX+US / 원시 OHLCV + change_pct 1개
> **진행 브랜치**: `feature/ohlcv-history`

---

## 0. 빠른 시작 체크리스트

처음 도입 시 반드시 순서대로 진행.

```bash
# 1) 스키마 v27 마이그레이션 적용
python -c "from shared.config import AppConfig; from shared.db import init_db; init_db(AppConfig().db)"

# 2) 최초 meta sync (OHLCV는 stock_universe 종목을 대상으로 하므로 meta가 선행)
python -m analyzer.universe_sync --mode meta --market ALL

# 3) 초기 800일 백필 (KRX 약 20분, US 약 5분)
python -m analyzer.universe_sync --mode backfill --days 800 --market KRX
python -m analyzer.universe_sync --mode backfill --days 800 --market US

# 4) 검증
python -m tools.ohlcv_health_check

# 5) systemd timer 활성화 (라즈베리파이)
# deploy/systemd/README.md 의 설치 절차 참고
```

---

## 1. 아키텍처 한 줄 요약

- 테이블: `stock_universe_ohlcv (ticker, market, trade_date) PK`
- 수집: `pykrx.get_market_ohlcv` (KRX 일별 배치) + `yfinance.download` (US batch)
- 보존: 800일 rolling. 상폐 종목은 400일로 축소.
- 파생: `change_pct`는 LAG() window로 on-insert 후 재계산 (빈도 낮음)
- 통합: `--mode price`가 자동으로 OHLCV를 묻어서 수집 → 전용 타이머 불필요

---

## 2. CLI 모드 상세

| 모드 | 목적 | 대표 옵션 | 소요 시간 |
|---|---|---|---|
| `meta` | 섹터/시총 주간 갱신 (기존 기능) | `--market ALL` | ~5분 |
| `price` | 일별 가격 + OHLCV 수집 (기본 자동) | `--market ALL` | ~1분 |
| `auto` | stale 판별 후 자동 meta/price | (기본값) | 가변 |
| `ohlcv` | **특정 1일 강제 재수집 (장애 복구)** | `--date 2026-04-20` | ~10초 |
| `backfill` | **과거 N일 일괄 수집 (초기/신규)** | `--days 800 --market KRX` 또는 `--ticker 005930` | KRX 10~25분 / US 5분 |
| `cleanup` | retention 초과 row 삭제 | (자동) `--days N`으로 override 가능 | ~10초 |

### 2.1 일상 운영 (자동)

- `universe-sync-price.timer` 가 매일 02:30 KST에 `--mode price` 실행
- `OHLCV_ON_PRICE_SYNC=true` (기본)이면 동일 실행 안에서 OHLCV 1일치 자동 UPSERT
- 끝에 `recompute_change_pct`도 자동 호출 — change_pct가 NULL인 row만 대상이라 비용 ↓

### 2.2 장애 복구 (수동)

**시나리오**: "2026-04-20 price sync가 실패해서 그 날 OHLCV 누락됨"

```bash
# 단일 날짜 재수집 (UPSERT이므로 기존 row는 덮어씀)
python -m analyzer.universe_sync --mode ohlcv --date 2026-04-20 --market ALL

# 누락 범위가 넓으면 backfill로 구간 수집
python -m analyzer.universe_sync --mode backfill --days 7 --market ALL
```

### 2.3 신규 상장 종목 백필 (수동)

**시나리오**: "meta sync에서 신규 종목 005555 감지됨 → 이력 추가 필요"

```bash
python -m analyzer.universe_sync --mode backfill --ticker 005555 --days 800
```

- 단건 ticker 백필은 `stock_universe`에서 market을 조회하여 pykrx/yfinance 자동 선택
- 800일 전부가 아니라 해당 종목의 상장일 이후 구간만 수집됨 (pykrx/yfinance가 상장 이전 데이터는 반환하지 않음)

### 2.4 Retention 정리 (주간 자동)

- `ohlcv-cleanup.timer` 가 매주 일요일 04:00 KST에 `--mode cleanup` 실행
- `OHLCV_RETENTION_DAYS=800` 초과 → 일괄 DELETE (10,000건 단위 반복)
- `OHLCV_DELISTED_RETENTION_DAYS=400` 초과 → 상폐 종목 추가 DELETE

**수동 실행**:
```bash
python -m analyzer.universe_sync --mode cleanup            # config 기반 (800/400일)
python -m analyzer.universe_sync --mode cleanup --days 365 # override
```

---

## 3. 검증 쿼리 모음

### 3.1 공식 health-check 도구

```bash
python -m tools.ohlcv_health_check           # 사람용 출력
python -m tools.ohlcv_health_check --json    # 기계 판독
python -m tools.ohlcv_health_check --strict  # 경고 있으면 exit 1 (cron용)
```

### 3.2 시장별 날짜 커버리지

```sql
SELECT market,
       COUNT(DISTINCT trade_date) AS days,
       MIN(trade_date) AS first_date,
       MAX(trade_date) AS last_date
FROM stock_universe_ohlcv
GROUP BY market
ORDER BY market;
-- 기대 (800일 백필 후): days ≈ 530~560 (2년 거래일)
```

### 3.3 종목별 커버리지

```sql
SELECT market, COUNT(DISTINCT ticker) AS tickers
FROM stock_universe_ohlcv
GROUP BY market;
-- 기대: KOSPI ~949, KOSDAQ ~1820, NASDAQ ~174, NYSE ~342 (2026-04 기준)
```

### 3.4 누락 종목 탐지

```sql
SELECT u.ticker, u.market, u.asset_name
FROM stock_universe u
LEFT JOIN (SELECT DISTINCT ticker, market FROM stock_universe_ohlcv) o
       ON u.ticker = o.ticker AND u.market = o.market
WHERE u.listed = TRUE AND u.has_preferred = FALSE AND o.ticker IS NULL;
-- 기대: 신규 상장/IPO 당일 등 극소수
```

### 3.5 change_pct 이상치

```sql
-- |등락률| > 30% 인 row (상한가 2연속·공시 이벤트 검증 필요)
SELECT ticker, market, trade_date, close, change_pct
FROM stock_universe_ohlcv
WHERE ABS(change_pct) > 30
ORDER BY ABS(change_pct) DESC
LIMIT 20;
```

### 3.6 change_pct NULL 진단

```sql
-- 이상 NULL (첫 거래일 제외하고도 NULL인 row) → recompute 필요
WITH first_day AS (
  SELECT ticker, market, MIN(trade_date) AS min_d
  FROM stock_universe_ohlcv GROUP BY ticker, market
)
SELECT COUNT(*) FROM stock_universe_ohlcv o
JOIN first_day f USING (ticker, market)
WHERE o.change_pct IS NULL AND o.trade_date > f.min_d;
-- 기대: 0
```

복구:
```bash
# recompute_change_pct는 --mode price/auto/ohlcv/backfill 끝에 자동 호출되지만,
# 수동으로 돌리려면 아래처럼 1일치 빈 재수집으로 유도
python -m analyzer.universe_sync --mode ohlcv --date $(date +%Y-%m-%d) --market ALL
```

### 3.7 디스크 사용량

```sql
SELECT pg_size_pretty(pg_total_relation_size('stock_universe_ohlcv')) AS total,
       pg_size_pretty(pg_relation_size('stock_universe_ohlcv'))        AS heap,
       pg_size_pretty(pg_total_relation_size('stock_universe_ohlcv')
                    - pg_relation_size('stock_universe_ohlcv'))         AS indexes;
-- 기대 (800일 × ~3,300종목): total ≈ 200~250MB
```

---

## 4. 환경변수 요약

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OHLCV_RETENTION_DAYS` | `800` | 보존 일수 (2년) |
| `OHLCV_DELISTED_RETENTION_DAYS` | `400` | 상폐 종목 축소 retention (0=일반 retention 적용) |
| `OHLCV_AUTO_ADJUST` | `false` | yfinance raw 가격 사용 |
| `OHLCV_ON_PRICE_SYNC` | `true` | price sync 실행 시 OHLCV 함께 수집 |
| `OHLCV_BACKFILL_DAYS` | `800` | 백필 기본 일수 (CLI `--days` 생략 시) |

---

## 5. 라즈베리파이 systemd 설치

상세 절차: [`../deploy/systemd/README.md`](../deploy/systemd/README.md)

핵심 요약:
```bash
cd /home/pi/investment-advisor/deploy/systemd
# 템플릿의 플레이스홀더를 본인 환경으로 치환 후 /etc/systemd/system/ 복사
sudo systemctl daemon-reload
sudo systemctl enable --now universe-sync-price.timer \
                              universe-sync-meta.timer \
                              ohlcv-cleanup.timer
sudo systemctl list-timers | grep -E "universe|ohlcv"
```

---

## 6. 장애 대응 흐름 (FAQ)

### Q1. 특정 날짜 sync가 실패해서 해당 날짜 OHLCV가 없다
→ `python -m analyzer.universe_sync --mode ohlcv --date YYYY-MM-DD` 으로 재수집

### Q2. universe에 있는데 OHLCV에 한 번도 안 나타난 종목이 있다
→ `python -m tools.ohlcv_health_check` 로 목록 확인 →
   `python -m analyzer.universe_sync --mode backfill --ticker TICKER --days 800`

### Q3. change_pct가 이상하게 NULL로 남아 있다
→ price/backfill 모드가 끝에 자동 호출하지만, 수동 recompute 원하면 ohlcv 모드 1일치 돌리면 됨.

### Q4. 디스크가 부족해진다
→ `OHLCV_RETENTION_DAYS` 를 400~600으로 줄이고 `cleanup` 모드 실행. 또는 `OHLCV_DELISTED_RETENTION_DAYS=90` 으로 상폐 종목 축소.

### Q5. Phase 4/5 구현 시 2년이 부족하다
→ `.env`에서 `OHLCV_RETENTION_DAYS=1100`(3년)으로 변경 후 `backfill --days 1100` 실행.
   UPSERT 구조라서 기존 row는 그대로 유지, 부족 구간만 새로 채워짐.

### Q6. pykrx 로그인 오류 (`KRX_ID`/`KRX_PW`)
→ data.krx.co.kr 회원가입 후 `.env`에 기재. pykrx 1.2.7+ 필요.

### Q7. yfinance rate limit
→ `sync_ohlcv_us`는 chunk_size=100으로 나눠 호출. 여전히 실패 시 chunk_size를 50으로 줄여 재실행. 코드 변경 없이 `.env`에 추가 제어값이 필요하면 추후 요구사항으로 논의.

---

## 7. 알려진 한계 (오픈 이슈)

- **KOSPI/KOSDAQ 지수 자체는 미수집** — Phase 5 (Regime 판별) 구현 시 `pykrx.get_index_ohlcv`로 별도 저장 예정
- **배당 이벤트 미저장** — 배당락 조정이 필요하면 별도 이벤트 테이블 추가 필요
- **분봉(intraday) 미지원** — 현재 비목표
- **데이터 공급자 장애 시 fallback 없음** — pykrx 미제공 종목은 yfinance로도 시도하지 않음

---

## 8. 본 문서와 연결되는 타 문서

- 설계서 원본: [`20260422235016_ohlcv-history-table-plan.md`](20260422235016_ohlcv-history-table-plan.md)
- 분석 재설계 원설계: [`20260422172248_recommendation-engine-redesign.md`](20260422172248_recommendation-engine-redesign.md)
- 진행 현황: [`20260422224220_redesign-progress-and-activation.md`](20260422224220_redesign-progress-and-activation.md)
- systemd 템플릿: [`../deploy/systemd/README.md`](../deploy/systemd/README.md)
- 라즈베리파이 OS/PG 설치: [`raspberry-pi-setup.md`](raspberry-pi-setup.md)
