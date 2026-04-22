# 분석 파이프라인 재설계 — Universe-First Recommendation Engine

> **상태**: 설계 확정 대기 (v1 draft)
> **작성일**: 2026-04-22
> **범위**: `analyzer/` Stage 1-B 이하 + Top Picks 스코어링 + 운영 피드백 루프
> **대상 배포 환경**: Raspberry Pi 4 (4GB+) + USB 3.0 SSD + PostgreSQL

## 1. 배경 및 목적

### 1.1 현재 구조의 근본 문제

현재 시스템은 Stage 1-B에서 **LLM이 종목 티커를 직접 생성**한다. 이로 인해:

| 문제 | 영향 |
|---|---|
| LLM이 자유 텍스트로 티커 출력 | 존재하지 않는 티커 / 오타 / 상장폐지 종목 혼입 |
| 해외 티커 화이트리스트 없음 | 미국 종목은 검증 없이 yfinance로 진입 |
| Top Picks 가중치(30/20/15/…)에 백테스트 근거 없음 | 경험적 휴리스틱. 검증 루프 부재 |
| `post_return_*_pct`(v19)는 수집하나 활용 경로 없음 | 피드백 루프 단절 |
| 모멘텀 임계값(1m +20%, 3m +40%) 정적 | 강세장/약세장 레짐 무관하게 동일 |
| 섹터 taxonomy 불일치 (yfinance GICS vs pykrx KRX업종) | 다양성 제약 왜곡 |

### 1.2 설계 목표

- **신뢰성**: LLM hallucination을 구조적으로 차단 (검증된 유니버스에서만 선택)
- **AI 인사이트 강화**: AI가 잘하는 영역(테마 추론, 내러티브, 차별화 포인트)에 역할 집중
- **피드백 가능성**: 추천 성과로 가중치를 튜닝할 수 있는 데이터 파이프라인 완성
- **Pi 제약 준수**: Pi 4 + USB SSD + 단일 배치 윈도우(03:00) 내 완료

### 1.3 비목표 (Out of Scope)

- 실시간(intraday) 추천
- 자동 주문 연동
- 비상장/장외 종목 추천
- 파생상품·채권·원자재 확장 (현재 자산군 `stock`/`etf` 유지)

## 2. 설계 원칙

1. **생성과 선택의 분리** — LLM은 "어떤 조건을 가진 회사가 수혜인가"를 설계하고, 시스템은 "유니버스에서 그 조건에 맞는 회사"를 선택한다.
2. **Evidence over Opinion** — LLM 주장에는 `evidence_source` 태그를 강제하고, 실제 데이터와 자동 크로스체크한다.
3. **피드백 루프 내장** — 모든 추천의 사후 성과가 팩터별로 집계되어 가중치 튜닝에 재사용된다.
4. **Pi 운영 제약 준수** — 추가되는 AI 호출은 테마당 배치 호출로 압축, 외부 API 호출은 증분 동기화.
5. **점진적 마이그레이션** — 각 Phase가 독립 기능. ENV 스위치로 기존 로직과 공존 가능.

## 3. 타겟 아키텍처

### 3.1 Before / After

**Before (현재)**
```
RSS → Stage 1-A (이슈+테마, AI)
    → Stage 1-B (종목 제안 생성, AI 자유 생성)        ← hallucination 지점
    → 모멘텀 체크 (pykrx/yfinance)
    → Stage 2 (심층분석, AI)
    → DB 저장 + Stage 3 Top Picks (룰 + AI 재정렬)
    → Stage 4 가격 추적
```

**After (개선)**
```
RSS → Stage 1-A (이슈+테마, AI)                       ← 변경 없음
    → Stage 1-B1 (테마별 투자 스펙 생성, AI → JSON)    ← 신규
    → Stage 1-B2 (결정적 스크리너, Python, 유니버스 조회) ← 신규
    → Stage 1-B3 (테마당 1회 배치 분석, AI → rationale)  ← 신규
    → Evidence Validation Layer                      ← 신규
    → 모멘텀 체크 (기존)
    → Stage 2 (심층분석, 기존)
    → DB 저장 + Stage 3 Top Picks (레짐 반영)
    → Stage 4 가격 추적 (기존)
    → [주간] Factor Performance Job                   ← 신규
```

### 3.2 컴포넌트 목록 (신규/수정)

| 모듈 | 상태 | 역할 |
|---|---|---|
| `analyzer/universe_sync.py` | **신규** | KRX/US 종목 메타데이터 동기화 |
| `analyzer/screener.py` | **신규** | 스펙(JSON)을 SQL 쿼리로 변환하여 유니버스에서 후보 추출 |
| `analyzer/validator.py` | **신규** | LLM 주장 vs 실측 데이터 크로스체크 |
| `analyzer/factor_analysis.py` | **신규** | 팩터별 IC/Sharpe/Hit Rate 계산 (주 1회) |
| `analyzer/regime.py` | **신규** | 시장 레짐 판별 (bull/neutral/bear) |
| `shared/sector_mapping.py` | **신규** | KRX 업종 ↔ GICS ↔ sector_norm 매핑 마스터 |
| `analyzer/prompts.py` | 수정 | Stage 1-B를 1-B1·1-B3 프롬프트로 분리 |
| `analyzer/analyzer.py` | 수정 | 파이프라인 오케스트레이션 재구성 |
| `analyzer/recommender.py` | 수정 | 레짐·팩터 피드백 반영 |
| `shared/db.py` | 수정 | 스키마 v23~v26 마이그레이션 추가 |

## 4. Phase별 상세 설계

### Phase 0. Pi 하드웨어 / OS 준비 (사전 필수)

**요구 사양**:
- Pi 4 (4GB 이상 권장, 8GB 쾌적)
- **USB 3.0 SSD 64GB 이상 — Postgres `data_directory`를 SD카드에서 SSD로 이전**
- 패시브 히트싱크 + 저속 팬 (thermal throttling 예방)

**PostgreSQL 튜닝** (`postgresql.conf`):
```ini
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 20
wal_buffers = 8MB
checkpoint_completion_target = 0.9
random_page_cost = 1.1          # USB SSD 사용 시
synchronous_commit = off        # 선택: 쓰기 성능↑ (크래시 시 최근 커밋 일부 손실 허용)
```

**로그 retention**: `app_logs` 테이블에 30일 이상 자동 삭제 정책 (crontab 또는 pg_cron 확장).

### Phase 1. Stock Universe 도입 (스키마 v23)

#### 1.1 신규 테이블

```sql
CREATE TABLE stock_universe (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    market          TEXT NOT NULL,                 -- KRX / KOSPI / KOSDAQ / NASDAQ / NYSE
    asset_name      TEXT NOT NULL,
    asset_name_en   TEXT,
    sector_gics     TEXT,                          -- yfinance 기준 (US 전용)
    sector_krx      TEXT,                          -- pykrx 업종 (KR 전용)
    sector_norm     TEXT,                          -- 내부 정규화 키 (양 시장 공통, 다양성 제약에 사용)
    industry        TEXT,
    market_cap_krw  BIGINT,
    market_cap_bucket TEXT,                        -- small / mid / large / mega
    last_price      NUMERIC(18,4),
    last_price_ccy  TEXT,
    last_price_at   TIMESTAMPTZ,
    listed          BOOLEAN DEFAULT TRUE,
    delisted_at     DATE,
    aliases         JSONB,                          -- {"ko": [...], "en": [...], "related_tickers": [...]}
    data_source     TEXT,                           -- pykrx / yfinance
    meta_synced_at  TIMESTAMPTZ,                   -- 메타데이터(섹터/시총) 동기화 시각
    price_synced_at TIMESTAMPTZ,                   -- 가격 동기화 시각
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, market)
);
CREATE INDEX idx_universe_sector_norm ON stock_universe(sector_norm);
CREATE INDEX idx_universe_market_cap ON stock_universe(market_cap_krw);
CREATE INDEX idx_universe_listed ON stock_universe(listed) WHERE listed = TRUE;
```

**범위(옵션 B 확정)**:
- KRX: KOSPI + KOSDAQ 보통주 ≈ 2,500종목 (우선주·KONEX 제외)
- US: S&P 500 + Nasdaq 100 (중복 제거) ≈ 520종목
- **총 ~3,020종목**

#### 1.2 Sector Taxonomy 정규화

`sector_norm`으로 양 시장 통합:

| sector_norm | KRX 업종 예시 | GICS 예시 |
|---|---|---|
| `semiconductors` | 반도체·반도체장비 | Semiconductors |
| `it_software` | 소프트웨어 | Software |
| `finance` | 금융업·은행 | Financials |
| `healthcare` | 의약품·의료정밀 | Health Care |
| ... | ... | ... |

**매핑 파일**: `shared/sector_mapping.py` (dict + pytest 회귀 테스트). 누락 매핑 시 `sector_norm = "other"`로 fallback + 경고 로그.

#### 1.3 동기화 전략 (증분)

`analyzer/universe_sync.py`:

```
상시(일별, 03:00 배치 직전):
  - stock_universe.last_price  ← 유니버스 전체 가격 (pykrx batch + yfinance)

주간(일요일 02:00):
  - sector_*, market_cap_*, listed 등 느린 메타데이터 전면 갱신
  - 신규 상장·상폐 반영

On-demand:
  - 분석 파이프라인이 Stage 1-B2에서 조회 시 meta_synced_at이 7일 초과 종목만 refresh
```

**Rate limit 대응**: yfinance는 `httpx` semaphore(동시 5)로 제한, 실패 시 지수 백오프. KRX는 pykrx 배치 API(`get_market_cap_by_date` 등)로 1-2회 호출로 전체 조회.

#### 1.4 Aliases / 우선주 / ADR 처리

- 보통주 기준으로 유니버스 구성. 우선주는 **별도 `has_preferred` 플래그**로 표시하되 추천 대상에서 제외.
- ADR(예: TSM, BABA)은 미국 티커로 등록, `aliases.related_tickers`에 본토 티커 기록.
- 동일 회사 복수 상장은 유동성 큰 상장지 1개만 유니버스 포함.

### Phase 2. AI 역할 재정의 (Stage 1-B 분해)

#### 2.1 Stage 1-B1: 테마별 투자 스펙 생성 (AI)

**입력**: Stage 1-A 테마, 최근 7일 추천 이력, 현재 레짐
**출력**: JSON 스펙
```json
{
  "theme_key": "ai_chip_test_equipment",
  "thesis": "AI 가속기 생산 확대로 테스트·검사 장비 수요 급증",
  "value_chain_tier": ["secondary", "tertiary"],
  "sector_norm": ["semiconductors", "it_hardware"],
  "market_cap_bucket": ["small", "mid"],
  "market_cap_range_krw": [300000000000, 2000000000000],
  "required_keywords": ["반도체", "테스트", "검사장비", "프로브"],
  "exclude_keywords": ["파운드리_완성품"],
  "quality_filters": {
    "min_roe_pct": 5,
    "max_debt_ratio_pct": 150
  },
  "expected_catalyst_window_months": 6,
  "max_candidates": 20
}
```

**프롬프트 요점**:
- 티커를 직접 언급 **금지** (유일한 출력은 JSON 스펙)
- 밸류체인 위치 명시 강제
- 이전 날짜의 동일 `theme_key`가 있으면 스펙 일부를 계승 (연속성 유지)

#### 2.2 Stage 1-B2: 결정적 스크리너 (Python)

`analyzer/screener.py`:

```python
def screen(spec: dict, db: Connection) -> list[dict]:
    """스펙 JSON을 SQL로 변환해 stock_universe에서 후보 추출.
    AI 호출 없음. <1초.
    """
    # WHERE sector_norm IN (...)
    # AND market_cap_krw BETWEEN (...)
    # AND listed = TRUE
    # AND (required_keywords 중 하나라도 asset_name/aliases/industry 매칭)
    # AND NOT EXISTS exclude_keywords
    # ORDER BY market_cap_krw ASC (소형주 우선)
    # LIMIT spec["max_candidates"]
```

**Fallback 전략** (매칭 0개 / 과다):
- **0개 매칭**: `market_cap_range`를 ±50% 확장 → 그래도 0이면 `required_keywords`에서 1개 제거 → 3회까지 재시도 → 실패 시 해당 테마 스킵 + 로그
- **max_candidates 초과**: 시총 오름차순 상위 N개만 (얼리시그널 우선 원칙)

**키워드 매칭**: `ILIKE`(PostgreSQL) 우선, 향후 `pg_trgm` 확장으로 퍼지 매칭 지원 여지.

#### 2.3 Stage 1-B3: 테마당 1회 배치 분석 (AI)

**중요**: Pi 제약상 **후보별 개별 호출이 아닌 테마당 1회 배치 호출**.

**입력**: 테마 thesis + 스펙 + 후보 리스트 20개 + 각 후보의 실시간 데이터(시총/섹터/52주 고저/PER/PBR)
**출력**: 후보별 `rationale` + `key_risk` + `conviction` + `discovery_type`
```json
{
  "theme_key": "...",
  "proposals": [
    {
      "ticker": "...",
      "market": "...",
      "conviction": "high|medium|low",
      "discovery_type": "consensus|early_signal|contrarian|deep_value",
      "action": "buy|hold|sell",
      "investment_rationale": "...",
      "key_risk": "...",
      "target_allocation_pct": 5.0
    }
  ]
}
```

**안전장치**:
- 화이트리스트 검증: 출력 티커가 입력 후보 리스트에 없으면 자동 제외 (hallucination 차단)
- `target_price_low/high`는 Stage 2 완료 종목만 의미 있음 — Stage 1-B3에서는 선택 필드로만

### Phase 3. Evidence Validation Layer (스키마 v24)

#### 3.1 신규 테이블 및 컬럼

```sql
CREATE TABLE proposal_validation_log (
    id              SERIAL PRIMARY KEY,
    proposal_id     INT REFERENCES investment_proposals(id) ON DELETE CASCADE,
    field_name      TEXT NOT NULL,         -- market_cap / sector / price / current_price
    ai_value        TEXT,
    actual_value    TEXT,
    evidence_source TEXT,                  -- pykrx_20260422 / yfinance_realtime / ai_estimated
    mismatch        BOOLEAN DEFAULT FALSE,
    mismatch_pct    NUMERIC(10,4),         -- 수치 필드 괴리율
    checked_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_validation_mismatch ON proposal_validation_log(mismatch) WHERE mismatch = TRUE;

ALTER TABLE investment_proposals
  ADD COLUMN spec_snapshot JSONB,
  ADD COLUMN screener_match_reason TEXT;
```

#### 3.2 검증 규칙

| 필드 | 검증 방법 | 불일치 기준 |
|---|---|---|
| `market_cap` | `stock_universe` vs AI 제시값 | ±20% 초과 |
| `sector` | `stock_universe.sector_norm` vs AI 제시값 | 불일치 |
| `current_price` | 실시간 vs AI 추정 | ±5% 초과 |

#### 3.3 Top Picks 스코어링 연동

`score_proposal()`에 추가:
```python
# 검증 실패 종목 감점
if mismatch_count >= 2:
    breakdown["validation_penalty"] = -10
    total -= 10
```

### Phase 4. Factor Feedback Loop (스키마 v25)

#### 4.1 신규 테이블

```sql
CREATE TABLE factor_performance (
    id              SERIAL PRIMARY KEY,
    eval_date       DATE NOT NULL,
    factor_name     TEXT NOT NULL,           -- discovery_type_early_signal / conviction_high / ...
    sample_size     INT NOT NULL,
    avg_return_3m   NUMERIC(10,4),
    avg_return_6m   NUMERIC(10,4),
    hit_rate_pct    NUMERIC(5,2),            -- 양수 수익률 비율
    sharpe_proxy    NUMERIC(10,4),
    ic_score        NUMERIC(10,4),           -- Information Coefficient (스피어만 상관)
    statistical_significance TEXT,           -- insufficient / weak / moderate / strong
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(eval_date, factor_name)
);
```

#### 4.2 분석 Job (`analyzer/factor_analysis.py`, 주 1회 일요일 04:00)

- 최근 180일 추천 + `post_return_*_pct` 조인
- 각 팩터별 평균 수익률, 히트율, IC, Sharpe 계산
- **통계적 유의성 임계값**:
  - `sample_size < 30` → `insufficient` (분석 불가)
  - `30 ≤ n < 100` → `weak` (참고용)
  - `100 ≤ n < 300` → `moderate`
  - `n ≥ 300` → `strong`
- `weak` 이상만 관리자 대시보드에 노출
- **생존편향 보정**: 상장폐지 종목도 -100% 수익률로 포함 (기간 중 delisting 발생 시)

#### 4.3 운영 모드

- **Phase 4.1 (대시보드만)**: 가중치는 사람이 수동 조정, 데이터만 보여줌
- **Phase 4.2 (추천 튜닝 루프)**: `strong` 시그널 팩터의 가중치 자동 조정 (별도 승인 절차 필요)

### Phase 5. Regime-Aware Thresholds (스키마 v25 동시)

#### 5.1 신규 모듈 `analyzer/regime.py`

**입력 지표**:
- KOSPI 200일 이평선 대비 현재가 (한국 중심 시스템이므로 KOSPI 우선)
- KOSPI 60일 변동성
- 외국인 순매수 20일 누적 (pykrx)
- (선택) VIX, SPY 200일 이평 — 글로벌 참고

**출력**: `{"regime": "risk_on_bull" | "neutral" | "risk_off_bear", "confidence": 0.0~1.0, "indicators": {...}}`

**저장**: `analysis_sessions.regime`, `analysis_sessions.regime_confidence` 컬럼 추가.

#### 5.2 레짐별 동적 기준

| 파라미터 | Bull | Neutral | Bear |
|---|---|---|---|
| `momentum_overheated_pct` | 30 | 20 | 12 |
| `upside_high_threshold` | 25 | 20 | 15 |
| 목표 discovery_type 믹스 | early 70% / 기타 30% | early 60% / deep_value 20% / 기타 20% | deep_value 50% / contrarian 20% / 기타 30% |

Stage 1-B1 프롬프트에 `regime` 정보를 주입하여 AI가 스펙 생성 시 반영.

### Phase 6. 부가 개선 (스키마 v26)

#### 6.1 Recommendation Audit Trail

기존 `ai_query_archive` + 신규 `proposal_validation_log` + `investment_proposals.spec_snapshot/screener_match_reason`을 결합하여 **분석 세션 단위로 완전 추적**. UI의 제안 상세 페이지에 "이 종목이 추천된 근거" 드릴다운 추가.

#### 6.2 품질 메트릭 자동 수집

`app_logs`에 구조화된 이벤트 기록:
- `spec_match_empty_count` — 당일 0 매칭된 스펙 수
- `stage1b3_rationale_length_outlier` — 200자 미만 또는 2000자 초과
- `validation_mismatch_rate` — 검증 실패 비율
- `pipeline_duration_seconds` — 단계별 소요 시간

관리자 대시보드에 일별 차트.

#### 6.3 개인화 Top Picks (선택, Phase 6b)

유저의 `user_watchlist` / `user_subscriptions` 데이터를 활용:
- 공통 Top Picks는 유지
- 유저별 "Your Picks": watchlist 섹터·테마와 매칭되는 종목에 추가 가중치 +5

#### 6.4 Counterfactual / 반례 추론 (선택)

Stage 3 AI 재정렬 시 프롬프트에 추가:
> "각 Top Pick에 대해 '이 추천이 틀릴 수 있는 3가지 시나리오'를 간단히 기술하라."

결과를 `daily_top_picks.counterfactuals` JSONB에 저장, UI에 "약점/반례" 섹션 신설.

#### 6.5 Market Insights (유니버스 외 인사이트)

```sql
CREATE TABLE market_insights (
    id              SERIAL PRIMARY KEY,
    analysis_date   DATE NOT NULL,
    theme_key       TEXT,
    insight_type    TEXT,               -- ipo_watch / unlisted_peer / regulatory_signal
    title           TEXT,
    content_md      TEXT,
    source_urls     JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

AI가 "이 테마에서 주목할 비상장/IPO 준비 기업"을 제시하면 여기 저장. 추천 아님 — 리서치 노트.

## 5. 환경변수 추가

```bash
# Universe
UNIVERSE_KRX_ENABLED=true
UNIVERSE_US_ENABLED=true
UNIVERSE_SYNC_PRICE_SCHEDULE=daily        # daily | weekly
UNIVERSE_SYNC_META_SCHEDULE=weekly

# Stage 1-B 분해
ENABLE_UNIVERSE_FIRST_B=false             # 마이그레이션용 토글 (false: 기존 로직)
SPEC_SCREENER_MAX_RETRIES=3
SPEC_SCREENER_FALLBACK_EXPAND_PCT=50

# Validation
ENABLE_EVIDENCE_VALIDATION=true
VALIDATION_MARKET_CAP_TOLERANCE_PCT=20
VALIDATION_PRICE_TOLERANCE_PCT=5
VALIDATION_MISMATCH_PENALTY=10

# Factor Analysis
FACTOR_ANALYSIS_WINDOW_DAYS=180
FACTOR_MIN_SAMPLE_WEAK=30
FACTOR_MIN_SAMPLE_MODERATE=100
FACTOR_MIN_SAMPLE_STRONG=300

# Regime
ENABLE_REGIME_ADJUSTMENT=true
REGIME_KOSPI_MA_DAYS=200
REGIME_VOL_WINDOW_DAYS=60
```

## 6. 마이그레이션 경로 (단계적, 백워드 호환)

각 Phase는 **독립 배포 가능**하며 ENV 스위치로 on/off.

| 순서 | 작업 | 기간 (예상) | 위험도 | 기존 기능 영향 |
|---|---|---|---|---|
| **0** | Pi 하드웨어 업그레이드 + PG 튜닝 + 로그 retention | 1일 | 낮음 | 영향 없음 (성능 개선) |
| **1a** | 스키마 v23 + `universe_sync.py` + KRX만 동기화 | 2일 | 낮음 | 읽기 전용 테이블 추가 |
| **1b** | US 동기화 추가 (S&P500 + Nasdaq100) | 1일 | 낮음 | 읽기 전용 |
| **2** | `screener.py` + Stage 1-B 듀얼 모드 (ENV 스위치) | 1주 | 중간 | 스위치 OFF 시 기존 동작 |
| **3** | Evidence validation (경고만, Top Picks 감점 없이) | 3일 | 낮음 | 경고 로그만 |
| **3b** | 검증 실패 감점 활성화 | 1일 | 중간 | Top Picks 순위 일부 변동 |
| **4a** | Factor performance Job + 관리자 대시보드 | 4일 | 낮음 | 추가 기능 |
| **4b** | 팩터 기반 가중치 수동 조정 | — | 낮음 | 데이터만 본 뒤 사람이 결정 |
| **5** | Regime 모듈 + 동적 임계값 | 3일 | 낮음 | 스위치 OFF 시 정적 기준 유지 |
| **6** | Audit trail + 품질 메트릭 + 선택 개선 | 1주 | 낮음 | 추가 기능 |

**총 예상 기간**: 4-6주 (1인 개발 기준).

## 7. DB 스키마 변경 요약

> **버전 시프트 주의 (2026-04-22 적용)**: 본 문서 작성 후 검토 결과 v23/v24가 이미 다른 용도로
> 선점되어 있어, 모든 신규 마이그레이션 번호를 +2 시프트했다 (v23→v25, v24→v26, v25→v27, v26→v28).
> 아래 표는 시프트 후 실제 번호 기준이다.

| 계획상 번호 | 실제 번호 | 변경 내용 | 상태 |
|---|---|---|---|
| ~~v23~~ | **v25** | `stock_universe` 테이블 신설 + `shared/sector_mapping.py` | ✅ 적용됨 (Phase 1a) |
| ~~v24~~ | **v26** | `proposal_validation_log` + `investment_proposals.spec_snapshot`/`screener_match_reason` | 대기 |
| ~~v25~~ | **v27** | `factor_performance` + `analysis_sessions.regime`/`regime_confidence` | 대기 |
| ~~v26~~ | **v28** | `market_insights` + `daily_top_picks.counterfactuals` | 대기 |

## 8. 트레이드오프

| 이점 | 비용 |
|---|---|
| Hallucination 티커 구조적 차단 | Universe 동기화 인프라 (일일/주간 배치) |
| AI가 "투자 스펙 설계" + "후보 해설"에 집중 → 분석 품질↑ | Stage 1-B 프롬프트 전면 재작성 + 듀얼 모드 기간 동안 복잡도↑ |
| 피드백 루프로 가중치 근거 확보 | 통계 유의성 확보까지 수개월 데이터 축적 필요 |
| 레짐별 동적 기준 | 레짐 판별 로직 자체의 검증 (오탐 시 추천 품질 저하) |
| Evidence validation으로 LLM 주장 검증 | 매 분석마다 DB 교차검증 쿼리 추가 (수십 ms, 무시 가능) |

## 9. 성공 기준

### 9.1 정량 KPI

- **Hallucination rate**: LLM이 유니버스 외 티커를 "생성" 시도하는 비율 → **0%** (화이트리스트로 차단)
- **Spec 매칭 실패율**: 0개 매칭 테마 비율 → **<10%** (fallback 포함)
- **Validation mismatch rate**: AI 제시 데이터의 실측 괴리율 → **<15%** (시총/섹터 기준)
- **Pipeline 총 시간**: 03:00 시작 → **05:30 이전 완료**
- **팩터 유의성**: 6개월 운영 후 `moderate` 이상 팩터 **5개 이상** 확보

### 9.2 정성 기준

- 관리자 대시보드에서 "왜 이 종목이 추천됐는가"를 3클릭 내 드릴다운 가능
- 유저 이탈률 변화 모니터링 (개선으로 인한 추천 변화가 부정적 영향 없는지)

## 10. 오픈 이슈 / 후속 결정 필요

1. **Sector mapping 마스터 테이블** 누가 관리하나? 초기 버전을 수동 작성 후 pytest 회귀 테스트로 방어?
2. **Factor 자동 튜닝(Phase 4.2)** 진입 시점 — 모든 팩터가 `moderate` 이상 될 때? 관리자가 개별 승인?
3. **다중 시장 레짐** — 미국 종목도 포함되므로 SPY도 함께 본다면 가중 평균? 단순히 Korea 우선?
4. **유저 개인화(Phase 6.3)** — Phase 1에 포함할지, MVP 검증 후 별도 프로젝트로 분리할지?
5. **우선주·KONEX 편입 정책** — 유저 요청 시 ENV 스위치로 확장 가능하게 미리 설계?

---

## 부록 A. 현재 코드와의 매핑

| 현재 파일 | 재설계 후 역할 |
|---|---|
| [analyzer/analyzer.py:1209-1233](../analyzer/analyzer.py#L1209-L1233) (Stage 2 대상 선별) | 유지 (정렬 기준에 레짐 반영만 추가) |
| [analyzer/recommender.py:82-149](../analyzer/recommender.py#L82-L149) (`score_proposal`) | `validation_penalty` 추가, `upside_*`/`already_priced` 임계값을 regime에서 받음 |
| [analyzer/prompts.py](../analyzer/prompts.py) `STAGE1B_SYSTEM/PROMPT` | **Stage 1-B1(스펙 생성) / 1-B3(후보 분석) 프롬프트로 분리** |
| [analyzer/stock_data.py](../analyzer/stock_data.py) | `stock_universe` 참조하도록 수정, 자체 캐시 축소 |
| [shared/db.py](../shared/db.py) `_validate_proposal()` 유사 로직 | `validator.py`로 이관 + 검증 로그 DB 저장 |

## 부록 B. Pi 운영 체크리스트 (주간)

```bash
df -h                               # SD/SSD 사용률 < 80%
free -h                             # available > 1GB
vcgencmd measure_temp               # < 75°C
systemctl status investment-advisor-*
journalctl -u investment-advisor-analyzer --since "1 week ago" | grep -i error
psql -c "SELECT pg_size_pretty(pg_database_size('investment_advisor'));"
psql -c "SELECT eval_date, factor_name, sample_size, statistical_significance FROM factor_performance ORDER BY eval_date DESC LIMIT 20;"
```
