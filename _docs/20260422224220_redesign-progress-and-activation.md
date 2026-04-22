# 분석 파이프라인 재설계 — 진행 현황 + 활성화 가이드

> **작성일**: 2026-04-22 22:42 KST
> **원 설계서**: [`20260422172248_recommendation-engine-redesign.md`](20260422172248_recommendation-engine-redesign.md)
> **대상 브랜치**: `dev` (origin/dev 기준 +7 커밋)
> **목적**: 2026-04-22 세션에서 구현한 Phase 1a~3 변경 내역, 미구현 항목, 운영 활성화에 필요한 추가 작업을 단일 문서로 정리

---

## 1. 한 눈에 보기

### 1.1 Phase 진행 현황

| Phase | 범위 | 상태 | 비고 |
|---|---|---|---|
| **0** | Pi 하드웨어 / SSD / PG 튜닝 | ❌ 미실행 | 물리 작업 — 사용자 영역 |
| **1a** | `stock_universe` + KRX 동기화 | ✅ 코드 완료 | KRX 가격 sync 검증 / 메타 sync 1회 실행 필요 |
| **1b** | US 동기화 (S&P500 + Nasdaq100) | ✅ 코드 완료 | 시드 516종목 / 10종목 샘플 검증 / 전체 sync 미실행 |
| **2** | Stage 1-B 분해 (1-B1/2/3) | ✅ 코드 완료 | **토글 OFF (기본)** — 활성화하려면 ENV 설정 필요 |
| **3** | Evidence Validation Layer | ✅ 코드 완료 | 토글 ON (기본) — universe 메타가 채워져야 의미 있음 |
| **4** | Factor Feedback Loop | ❌ 미구현 | 추천 사후 성과 → 가중치 튜닝 |
| **5** | Regime-Aware Thresholds | ❌ 미구현 | KOSPI/VIX 기반 동적 임계값 |
| **6** | Audit Trail UI / 품질 메트릭 / 개인화 / Counterfactual / Market Insights | ❌ 미구현 | 부가 기능 묶음 |

### 1.2 핵심 결론

- **백엔드 골격(Phase 1~3) 완성** — Universe-First 파이프라인이 코드 레벨에서 동작 검증됨
- **현재 분석 실행 시 동작은 여전히 Legacy 모드** — `ENABLE_UNIVERSE_FIRST_B=true` 설정 필요
- **사전 작업 필수**: KRX 메타 동기화 1회 실행해야 스크리너가 매칭 가능
- **남은 작업 ~3주(1인 기준)**: Phase 4~6 + 운영 자동화 (systemd 타이머)

---

## 2. 구현된 변경 내역

### 2.1 신규 파일 (5개)

| 파일 | 라인 | 역할 |
|---|---|---|
| [`shared/sector_mapping.py`](../shared/sector_mapping.py) | ~270 | KRX↔GICS↔sector_norm 14표준키 + industry override + 시총 버킷 |
| [`analyzer/universe_sync.py`](../analyzer/universe_sync.py) | ~600 | KRX(pykrx) + US(yfinance) 메타·가격 동기화 CLI |
| [`tools/refresh_us_universe.py`](../tools/refresh_us_universe.py) | ~140 | Wikipedia 1회 fetch → US 시드 JSON 생성 |
| [`shared/seeds_data/us_universe.json`](../shared/seeds_data/us_universe.json) | 5800줄 | S&P500 503 + NDX100 101 = 516 unique 종목 |
| [`analyzer/screener.py`](../analyzer/screener.py) | ~280 | 스펙 JSON → SQL ILIKE → 후보 추출 + fallback 단계적용 |
| [`analyzer/validator.py`](../analyzer/validator.py) | ~270 | AI 제시값 vs stock_universe 크로스체크 + DB persist |

### 2.2 수정된 파일

| 파일 | 핵심 변경 |
|---|---|
| [`shared/db/migrations/versions.py`](../shared/db/migrations/versions.py) | `_migrate_to_v25` (stock_universe), `_migrate_to_v26` (proposal_validation_log + 컬럼) |
| [`shared/db/migrations/__init__.py`](../shared/db/migrations/__init__.py) | v25, v26 등록 |
| [`shared/db/schema.py`](../shared/db/schema.py) | `SCHEMA_VERSION = 26` |
| [`shared/db/session_repo.py`](../shared/db/session_repo.py) | `save_analysis`에 `spec_snapshot`/`screener_match_reason` INSERT, commit 후 별도 트랜잭션으로 `validate_and_persist` 호출 |
| [`shared/config.py`](../shared/config.py) | `UniverseConfig`, `ScreenerConfig`, `ValidationConfig` 추가 + `AppConfig` 노출 |
| [`analyzer/prompts.py`](../analyzer/prompts.py) | `STAGE1B1_SYSTEM/PROMPT` (스펙 생성, ticker 금지) + `STAGE1B3_SYSTEM/PROMPT` (배치 분석) |
| [`analyzer/analyzer.py`](../analyzer/analyzer.py) | `stage1b1_generate_spec`, `stage1b2_screen_candidates`, `stage1b3_analyze_candidates`, `stage1b_universe_first` 추가 + `_generate_proposals_for_theme` 듀얼 모드 분기 + 화이트리스트 검증 |
| [`analyzer/recommender.py`](../analyzer/recommender.py) | `score_proposal`에 `validation_penalty` 추가, `compute_rule_based_picks`에 `validation_mismatches` 인자 |
| [`analyzer/main.py`](../analyzer/main.py) | Top Picks 산정 전 `fetch_mismatch_counts` 호출 + 주입 |
| [`requirements.txt`](../requirements.txt) | `lxml>=5.0.0` (refresh tool 전용) |
| [`.env.example`](../.env.example) | `UNIVERSE_*`, `ENABLE_UNIVERSE_FIRST_B`, `SPEC_SCREENER_*`, `STAGE1B3_TOP_N`, `ENABLE_EVIDENCE_VALIDATION`, `VALIDATION_*` 등 환경변수 ~15종 추가 |

### 2.3 DB 스키마 변경 (v25 + v26)

> **버전 시프트 주의**: 원 설계서의 v23/v24가 이미 다른 용도로 선점되어 있어 모든 신규 마이그레이션을 +2 시프트 적용. v25(=원 v23), v26(=원 v24).

**v25 — `stock_universe` (Phase 1a)**
```sql
CREATE TABLE stock_universe (
  id, ticker, market, asset_name, asset_name_en,
  sector_gics, sector_krx, sector_norm, industry,
  market_cap_krw, market_cap_bucket,
  last_price, last_price_ccy, last_price_at,
  listed, delisted_at, has_preferred,
  aliases JSONB, data_source,
  meta_synced_at, price_synced_at, created_at,
  UNIQUE(ticker, market)
);
-- 인덱스 4종: sector_norm / market_cap / listed / market
```

**v26 — Evidence Validation (Phase 3)**
```sql
CREATE TABLE proposal_validation_log (
  id, proposal_id FK, field_name, ai_value, actual_value,
  evidence_source, mismatch BOOL, mismatch_pct, checked_at
);
-- 인덱스 3종: proposal / mismatch / field

ALTER TABLE investment_proposals
  ADD COLUMN spec_snapshot JSONB,
  ADD COLUMN screener_match_reason TEXT;
```

### 2.4 환경변수 추가 (15종)

#### Universe 동기화 (5종)
- `UNIVERSE_KRX_ENABLED=true`
- `UNIVERSE_US_ENABLED=true`
- `UNIVERSE_SYNC_PRICE_SCHEDULE=daily`
- `UNIVERSE_SYNC_META_SCHEDULE=weekly`
- `UNIVERSE_META_STALE_DAYS=7`

#### Stage 1-B 분해 (5종)
- `ENABLE_UNIVERSE_FIRST_B=false` ← **활성화 필요**
- `SPEC_SCREENER_MAX_RETRIES=3`
- `SPEC_SCREENER_FALLBACK_EXPAND_PCT=50`
- `SPEC_SCREENER_CANDIDATES_MAX=20`
- `STAGE1B3_TOP_N=20`

#### Evidence Validation (5종)
- `ENABLE_EVIDENCE_VALIDATION=true` (기본 활성)
- `VALIDATION_MARKET_CAP_TOLERANCE_PCT=20`
- `VALIDATION_PRICE_TOLERANCE_PCT=5`
- `VALIDATION_MISMATCH_PENALTY=10`
- `VALIDATION_PENALTY_THRESHOLD=2`

### 2.5 커밋 이력 (origin/dev → dev)

| Hash | 메시지 |
|---|---|
| `f885a1d` | feat(analyzer): Phase 1a - stock_universe 인프라 + KRX universe_sync |
| `1d91be2` | chore(docs): 작업 프롬프트 로그 갱신 (Phase 1a 진행/커밋 지시) |
| `9ef9051` | feat(analyzer): Phase 1b - US universe 동기화 (S&P500 + Nasdaq100) |
| `09eaa9a` | chore(docs): 작업 프롬프트 로그 갱신 (Phase 1b 옵션 A 결정) |
| `34e0ea0` | feat(analyzer): Phase 2 - Universe-First Stage 1-B 분해 (스펙 → 스크리너 → 배치 분석) |
| `bcaf407` | chore(docs): 작업 프롬프트 로그 갱신 (Phase 2 진행/커밋 지시) |
| `2527558` | feat(analyzer): Phase 3 - Evidence Validation Layer (스키마 v26) |

---

## 3. 검증 결과 요약

### 3.1 자동 검증된 항목

| 항목 | 결과 |
|---|---|
| v25/v26 마이그레이션 적용 | ✅ schema_version=26 + 모든 컬럼·인덱스 생성 확인 |
| KRX 가격 sync (KOSPI+KOSDAQ) | ✅ 949 + 1820 = **2,769종목** 가격 upsert 성공 |
| KRX 업종 매핑 전수 | ✅ 28개 업종 모두 sector_norm으로 정규화 (other fallback 0건) |
| US 시드 생성 (Wikipedia) | ✅ S&P500 503 + NDX100 101 → unique **516종목** |
| US sync 샘플 (10종목) | ✅ meta 3초 / price 1.2초, NASDAQ 5 + NYSE 5로 정확 분류 |
| screener fallback 4단계 | ✅ 시총 확장 → 키워드 제거 → 시총 재확장으로 결국 매칭 |
| validator end-to-end | ✅ 525% mcap mismatch + sector 불일치 검출 → DB persist → fetch_mismatch_counts |
| import 체인 | ✅ analyzer.main까지 모든 import 정상 |

### 3.2 미검증 항목 (실제 운영 시 확인 필요)

- KRX **메타** sync 전체 실행 (~5–10분, 종목명·시총·업종 일괄 채우기)
- US **메타** sync 전체 실행 (~5–10분, 516종목)
- 실제 분석 1회 실행 with `ENABLE_UNIVERSE_FIRST_B=true` (Stage 1-B1→1-B2→1-B3 end-to-end)
- Validator의 실제 mismatch 검출률 (현재는 universe 메타가 비어 있어 무의미)

---

## 4. 운영 활성화 가이드 (4단계)

### Step 1. KRX 메타 동기화 (필수, ~5–10분)

```bash
# venv 활성화
source venv/Scripts/activate     # Windows
source venv/bin/activate          # Linux

# KRX 전체 메타 sync (KOSPI + KOSDAQ)
python -m analyzer.universe_sync --mode meta --market KRX
```

**확인**: 실행 후 DB에서 `sector_norm`이 채워졌는지 확인
```sql
SELECT market, COUNT(*) AS total,
       COUNT(sector_norm) AS with_sector,
       COUNT(market_cap_krw) AS with_mcap
FROM stock_universe
WHERE market IN ('KOSPI', 'KOSDAQ')
GROUP BY market;
```
기대값: ~2,500종목 / 거의 전부에 sector_norm·market_cap 채워짐

### Step 2. US 메타 동기화 (선택, ~5–10분)

US 종목 추천을 원하면 실행. 한국 시장만으로 충분하면 스킵 가능.
```bash
python -m analyzer.universe_sync --mode meta --market US
```

### Step 3. Universe-First 모드 활성화 (필수)

`.env` 파일을 편집하여 토글 변경:
```bash
ENABLE_UNIVERSE_FIRST_B=true
```

추가 검토 권장 환경변수 (필요 시 조정):
```bash
SPEC_SCREENER_CANDIDATES_MAX=20   # 스크리너 후보 최대 수
STAGE1B3_TOP_N=20                  # AI 배치 분석에 넘길 후보 수
```

### Step 4. 분석 1회 실행 + 결과 검수

```bash
python -m analyzer.main
```

**검수 포인트**:
1. 로그에 `[Stage 1-B] ... 모드: Universe-First (1-B1+1-B2+1-B3)` 표시
2. 각 테마별로 `[1-B1]` → `[screener]` → `[stage1b3]` 순서로 로그 출력
3. screener 매칭 0건이 많으면 `required_keywords`가 너무 좁거나 universe 메타가 부실 → 재검토
4. `proposal_validation_log` 테이블에 검증 row가 쌓이는지 확인:
   ```sql
   SELECT field_name, COUNT(*), SUM(CASE WHEN mismatch THEN 1 ELSE 0 END) AS mismatches
   FROM proposal_validation_log
   GROUP BY field_name;
   ```

### Step 5. (이후) systemd 자동화

라즈베리파이 운영 시 `/etc/systemd/system/`에 universe sync 타이머 추가:

**daily price sync (예: 02:30 KST, 분석 배치 03:00 직전)**
```ini
# universe-sync-price.timer
[Timer]
OnCalendar=*-*-* 02:30:00 Asia/Seoul

# universe-sync-price.service
[Service]
ExecStart=/path/to/venv/bin/python -m analyzer.universe_sync --mode price
WorkingDirectory=/path/to/investment-advisor
```

**weekly meta sync (예: 일요일 02:00 KST)**
```ini
OnCalendar=Sun *-*-* 02:00:00 Asia/Seoul
ExecStart=... --mode meta
```

US 시드 갱신은 분기 1회 또는 인덱스 리밸런싱 후:
```bash
python -m tools.refresh_us_universe
```

---

## 5. 미구현 항목 (Phase 4~6)

### 5.1 Phase 4 — Factor Feedback Loop

**목표**: 추천 후 실제 수익률(`post_return_*_pct`, v19에서 이미 수집 중)을 팩터별로 집계 → 스코어링 가중치 근거 확보 → 자동 튜닝

**필요 작업**
- 스키마 v27: `factor_performance` 테이블 신설
- `analyzer/factor_analysis.py` 신규 — 주 1회(일요일 04:00) 실행
  - 최근 180일 추천 + `post_return_*_pct` 조인
  - 팩터별 (discovery_type, conviction, vendor_tier 등) 평균수익률·히트율·IC·Sharpe
  - 통계적 유의성 분류 (insufficient/weak/moderate/strong)
- 관리자 대시보드 페이지: `/admin/factor-performance`
- (Phase 4.2) 자동 가중치 조정 (강 시그널만, 사람 승인 절차 포함)

**예상 일정**: 4일 (대시보드만) + 통계 유의성 확보까지 ~6개월 데이터 축적

### 5.2 Phase 5 — Regime-Aware Thresholds

**목표**: 시장 레짐(bull/neutral/bear)을 자동 판별하여 모멘텀 기준·discovery_type 믹스를 동적 조정

**필요 작업**
- 스키마 v27 동시: `analysis_sessions.regime`, `regime_confidence` 컬럼 추가
- `analyzer/regime.py` 신규
  - 입력: KOSPI 200일 이평·60일 변동성·외국인 20일 누적순매수 + (선택) VIX/SPY
  - 출력: `{"regime", "confidence", "indicators"}`
- Stage 1-B1 프롬프트에 regime 주입 (현재는 `regime="neutral"` 고정)
- `RecommendationConfig.upside_high_threshold` 등을 레짐별 dict로 변경

**예상 일정**: 3일

### 5.3 Phase 6 — 부가 개선

**6.1 Recommendation Audit Trail UI**
- `spec_snapshot` + `proposal_validation_log` + `screener_match_reason`을 결합
- 제안 상세 페이지에 "이 종목이 추천된 이유" 드릴다운 (3클릭 내 도달)

**6.2 품질 메트릭 자동 수집**
- `app_logs`에 구조화 이벤트 기록 (`spec_match_empty_count`, `validation_mismatch_rate`, `pipeline_duration_seconds`)
- 관리자 대시보드 일별 차트

**6.3 개인화 Top Picks**
- 유저 `watchlist`/`subscriptions`와 매칭되는 종목에 `+5` 가산
- `Your Picks` 섹션 별도 표시

**6.4 Counterfactual / 반례 추론**
- Stage 3 AI 재정렬 시 "이 추천이 틀릴 수 있는 3가지 시나리오" 추가 출력
- `daily_top_picks.counterfactuals` JSONB 컬럼

**6.5 Market Insights (유니버스 외)**
- 스키마 v28: `market_insights` 테이블 (IPO 워치/규제 시그널 등)
- AI가 "비상장/IPO 준비 기업" 리서치 노트 작성 — 추천 아닌 정보 제공

**예상 일정**: 1주

---

## 6. 알려진 한계 / 후속 결정 필요

### 6.1 즉시 해결 가능

- **CLAUDE.md SCHEMA_VERSION 표기 갱신** — 현재 "v22"로 적혀있으나 실제는 v26
- **CLAUDE.md Project Structure 갱신** — `analyzer/screener.py`, `analyzer/validator.py`, `analyzer/universe_sync.py`, `shared/sector_mapping.py`, `tools/refresh_us_universe.py`, `shared/seeds_data/`, `shared/db/migrations/seeds_education/` 추가 반영
- **임시 테스트 row 정리** — `stock_universe`에서 `067310` (KOSDAQ)의 asset_name="하나마이크론(테스트)" — 다음 KRX meta sync에서 자동 덮어써짐, 별도 작업 불필요

### 6.2 운영 정책 결정 필요 (원 설계서 §10)

1. **Sector mapping 마스터 관리** — 현재 `shared/sector_mapping.py` 수동 작성. pytest 회귀 테스트 추가 여부?
2. **Factor 자동 튜닝 진입 시점** (Phase 4.2) — 모든 팩터 `moderate` 이상일 때? 관리자 개별 승인?
3. **다중 시장 레짐** (Phase 5) — 미국 종목 포함 시 SPY도 함께 보는가? 한국 우선?
4. **유저 개인화** (Phase 6.3) — Phase 1에 포함? 별도 프로젝트로 분리?
5. **우선주·KONEX 편입 정책** — 현재 `has_preferred=TRUE`로 추천 제외. 옵션화 미리 설계?

### 6.3 운영 모니터링 권장 쿼리

```sql
-- universe 신선도
SELECT market, MAX(meta_synced_at) AS last_meta, MAX(price_synced_at) AS last_price
FROM stock_universe GROUP BY market;

-- 검증 mismatch 추세 (최근 7일)
SELECT DATE(checked_at), field_name,
       COUNT(*) AS total,
       SUM(CASE WHEN mismatch THEN 1 ELSE 0 END) AS mismatches
FROM proposal_validation_log
WHERE checked_at >= NOW() - INTERVAL '7 days'
GROUP BY 1, 2 ORDER BY 1 DESC, 2;

-- 스크리너 매칭 0건 비율 (proposal_validation_log에 universe_hits 없음 — Phase 6.2에서 app_logs로 수집 예정)
-- 임시: 직접 sample run 후 로그 grep
```

---

## 7. 다음 권장 작업 우선순위

### A. 운영 즉시 활성화 (이번 주)
1. KRX meta sync 1회 실행
2. `.env`에 `ENABLE_UNIVERSE_FIRST_B=true` 설정
3. 분석 1회 실행 + 결과 검수
4. systemd 타이머 추가 (price 일별 + meta 주간)

### B. 단기 개선 (1~2주)
5. CLAUDE.md 갱신 (SCHEMA_VERSION, 신규 모듈)
6. 임시 테스트 row 정리 확인
7. Phase 6.1 (Audit Trail UI) — `spec_snapshot` 시각화

### C. 중기 개선 (3~6주)
8. Phase 5 (Regime) — 룰 기반이라 단기간 가능
9. Phase 4 (Factor Feedback) — 데이터 축적 6개월 기다리는 동안 대시보드만 먼저
10. Phase 6.2~6.5 (품질 메트릭, 개인화, Counterfactual, Market Insights)

### D. 사용자 결정 대기
11. 원 설계서 §10 오픈 이슈 5건
12. CLAUDE.md 명명 규칙 외 기타 운영 정책

---

## 부록 A. 주요 함수 호출 그래프 (Universe-First 모드)

```
analyzer.main.run_full_analysis()
  └─ analyzer.analyzer.run_pipeline()
      ├─ stage1a_extract_macro()                    [기존]
      ├─ Stage 1-B 분기 (ENABLE_UNIVERSE_FIRST_B)
      │   ├─ [legacy]  stage1b_generate_proposals()
      │   └─ [new]     stage1b_universe_first()
      │                 ├─ stage1b1_generate_spec()      → AI (스펙 JSON)
      │                 ├─ stage1b2_screen_candidates()  → screener.screen()
      │                 │                                   └─ SQL ILIKE + fallback
      │                 └─ stage1b3_analyze_candidates() → AI (배치 + 화이트리스트 검증)
      ├─ validate_krx_tickers()                     [기존, KRX만]
      ├─ fetch_momentum_batch()                     [기존]
      ├─ stage2_analyze_stock() × N                 [기존]
      └─ shared.db.session_repo.save_analysis()
          ├─ INSERT investment_proposals (+ spec_snapshot)
          ├─ _update_tracking()
          ├─ _generate_notifications()
          └─ analyzer.validator.validate_and_persist()  [Phase 3, 별도 트랜잭션]

analyzer.main (post-analysis)
  └─ Top Picks 단계
      ├─ analyzer.validator.fetch_mismatch_counts()
      └─ analyzer.recommender.compute_rule_based_picks(validation_mismatches=...)
          └─ score_proposal()  → validation_penalty 적용
```

## 부록 B. 시장 코드 매핑 표

| Universe.market | 분석 코드 입력값 | yfinance suffix | 정규화 |
|---|---|---|---|
| `KOSPI` | KRX, KSE, KOSPI | `.KS` | KOSPI |
| `KOSDAQ` | KQ, KOSDAQ | `.KQ` | KOSDAQ |
| `NASDAQ` | NMS, NCM, NGM, NAS | (없음) | NASDAQ |
| `NYSE` | NYQ, NYS, PCX, ASE, AMEX, ARCA | (없음) | NYSE |

`analyzer.validator._fetch_universe_meta`는 위 별칭을 자동 보정.

---

*본 문서는 `_docs/_prompts/20260422_home_prompt.md` 세션 결과물의 운영 안내 자료입니다.*
