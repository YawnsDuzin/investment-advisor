# Screener — 거장 전략 프리셋 + 펀더멘털 PIT 시계열 (B-Lite)

작성일: 2026-04-26
관련 작업 폴더: `analyzer/`, `api/routes/screener.py`, `api/templates/screener.html`, `shared/db/migrations/versions.py`, `deploy/systemd/`
관련 기존 spec: `docs/superpowers/specs/2026-04-25-screener-redesign-design.md`

## 1. 배경 / 동기

현행 Screener는 OHLCV 기반 모멘텀·기술 필터(`r1m/r3m/r6m/r1y/ytd`, `vol60`, `volume_ratio`, `MA20/60/200`, `52w/60d` 고저점, `drawdown`)는 풍부하지만 펀더멘털(PER/PBR/EPS/BPS/배당률)이 전무하다. UI 상에도 **Fundamental 탭은 `disabled`** 상태로 "데이터 준비 중"이라고만 표기돼 있다.

이 상태에서는 가치투자 거장(Graham, Schloss, Neff 등)의 룰을 재현할 수 없어 사용자에게 "추천 조회 조건 옵션 / 거장 투자 재현"을 제공하지 못한다. 본 spec은 **B-Lite** 노선 — pykrx 5종(KRX) + yfinance.info 6키(US) 펀더멘털만 PIT 시계열로 수집해 거장 시드 프리셋 8종을 출시한다.

ROE / 영업이익률 / 부채비율 / FCF / 매출 성장률 등 DART XBRL 파싱이 필요한 항목은 **수집하지 않는다** (B-Full 영역, 후속).

## 2. 목표 / 비-목표

### 2.1 목표
- KOSPI/KOSDAQ/NASDAQ/NYSE 종목의 PER/PBR/EPS/BPS/배당률을 PIT 시계열(`stock_universe_fundamentals`)로 보관
- Screener `/api/screener/run` 에 펀더 필터 신규 키 7종 추가 — top-level 6: `per_range`, `pbr_range`, `dividend_yield_min`, `eps_min`, `bps_min`, `fundamentals_required` + `ma_alignment` (Minervini 정렬 룰). `exclude_negative`는 `per_range`/`pbr_range` 내부 옵션 키.
- Fundamental 탭 활성화
- 거장 시드 프리셋 8종 (Graham Defensive Lite / Schloss Deep Value / Neff Low-PE Yield / KR·US 고배당 / Druckenmiller Trend / Minervini Trend Template / Bottom Fishing) 출시
- Investors 탭 신설 — 카드 갤러리 + 시드 복제 동선
- 관리자 페이지에서 펀더 sync systemd unit 제어 + 즉시 실행 + 위험구역 truncate + 결측률 모니터링

### 2.2 비-목표 (YAGNI)
- ROE / 영업이익률 / 부채비율 / FCF — **수집 안 함**
- Buffett / Lynch / Greenblatt 거장 — **시드에 포함 안 함** (데이터 부재)
- 분기 결산 추세 룰("PER 5년 평균보다 30% 낮음") — **후속**
- 백테스트 UI / "이 룰로 작년 수익률" — **후속** (PIT 인프라는 깔지만 활용 안 함)
- DART OpenAPI / 네이버 금융 스크래핑 — **건드리지 않음**

## 3. § 1 — 데이터 레이어 + 스키마

### 3.1 수집 소스 분기

| 시장 | 소스 | 메트릭 | 주기 |
|---|---|---|---|
| KOSPI/KOSDAQ | `pykrx.stock.get_market_fundamental_by_date(date, ticker)` | PER / PBR / EPS / BPS / DPS / 배당률 | 일별 (영업일 종가 후) |
| NASDAQ/NYSE | `yfinance.Ticker(t).info` 일부 키 | trailingPE / priceToBook / trailingEps / bookValue / dividendRate / dividendYield | 일별 |

- pykrx는 거래일별 PER/PBR/EPS를 매일 산출 → 일별 시계열 자체가 의미. EPS 자체는 분기 결산 기점으로 단계 함수처럼 변화 (snapshot_date에 EPS·BPS 같이 보관해 변경 시점 추적).
- yfinance.info는 "현재 스냅샷" — 매일 호출 시 그날 값을 누적해 일별 PIT 구성. `Ticker.financials` / `Ticker.quarterly_financials` 시계열은 사용하지 않는다.
- 주기: 기존 `analyzer/universe_sync.py --mode ohlcv` 일별 실행에 `--mode fundamentals` 새 모드 추가, systemd timer (KST 06:35 — OHLCV sync 직후)에 합류.

### 3.2 신규 테이블 — `stock_universe_fundamentals` (v39)

> ※ spec 초안의 v36/v37/v38은 충돌 (education content 갱신용으로 사용 중). 신규 마이그레이션은 v39/v40/v41로 시프트.

```sql
CREATE TABLE stock_universe_fundamentals (
    ticker          TEXT NOT NULL,
    market          TEXT NOT NULL,
    snapshot_date   DATE NOT NULL,           -- 수집 거래일 (PIT 기준)
    per             NUMERIC(12,4),           -- 음수/NULL 허용 (적자/자본잠식)
    pbr             NUMERIC(12,4),
    eps             NUMERIC(18,4),
    bps             NUMERIC(18,4),
    dps             NUMERIC(18,4),
    dividend_yield  NUMERIC(8,4),            -- %
    data_source     TEXT NOT NULL,           -- 'pykrx' | 'yfinance_info'
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, market, snapshot_date)
);
CREATE INDEX idx_fund_latest ON stock_universe_fundamentals(ticker, market, snapshot_date DESC);
CREATE INDEX idx_fund_date   ON stock_universe_fundamentals(snapshot_date);
```

설계 결정:
- `stock_universe`와 FK 미설정 (PIT 원칙 — 상폐 종목 이력 보존, `stock_universe_ohlcv`와 동일 정책).
- 단위: 한국은 원, 미국은 USD — EPS/BPS/DPS는 표시 시 통화 분기 (`stock_universe.last_price_ccy` 활용).
- Retention: `FUNDAMENTALS_RETENTION_DAYS=800`. 상폐 종목은 cleanup 모드에서 400일로 축소.
- UPSERT 멱등성: `INSERT ... ON CONFLICT (ticker, market, snapshot_date) DO UPDATE`.

### 3.3 스크리너 SQL 통합

`api/routes/screener.py:run_screener()` CTE에 펀더 latest snapshot 추가:

```sql
fund_latest AS (
    SELECT DISTINCT ON (ticker, market)
           ticker, UPPER(market) AS market,
           per, pbr, eps, bps, dividend_yield, snapshot_date
    FROM stock_universe_fundamentals
    WHERE snapshot_date >= CURRENT_DATE - 30   -- 최근 30영업일 내 최신값
    ORDER BY ticker, market, snapshot_date DESC
)
```

Minervini / Bottom Fishing 시드를 위해 OHLCV CTE도 보강:
- `ma50`, `ma150` (현재 ma20/60/200만 존재)
- `low_52w_proximity = close_latest / NULLIF(low_252d, 0)` (현재 low_252d만 있고 비율 계산 없음)

### 3.4 `/api/screener/run` 신규 필터 스펙

```json
{
  "per_range":           {"min": null, "max": 15, "exclude_negative": true},
  "pbr_range":           {"min": null, "max": 1.5, "exclude_negative": true},
  "dividend_yield_min":  3.0,
  "eps_min":             0,
  "bps_min":             0,
  "fundamentals_required": true,
  "ma_alignment":        "minervini"  // 'minervini' | null — MA50>MA150>MA200 강제
}
```

- `exclude_negative=true`이면 `per IS NOT NULL AND per > 0` 같은 가드 자동 적용.
- `dividend_yield_min`은 `dividend_yield IS NOT NULL AND dividend_yield >= %s` (NULL=결측, 0=무배당 명시 구분).
- `fundamentals_required=true`이면 `fund_latest.snapshot_date IS NOT NULL` 강제.

### 3.5 폴백 정책

- 펀더 결측 종목은 펀더 필터 적용 시 자동 제외. 거장 룰은 본질적으로 펀더 강제이므로 fallback 없음.
- 운영 도구(`tools/fundamentals_health_check.py`)로 결측 추적, 임계 초과 시 admin UI에 빨간 뱃지.

## 4. § 2 — 거장 프리셋 시스템 + UI

### 4.1 프리셋 정의 위치 — 코드 (Single Source of Truth)

```
analyzer/
└── investor_strategies.py    # 거장 프리셋 정의
```

이유:
- Git 추적 → 룰 변경 이력이 코드 리뷰로.
- 새 거장 추가는 PR 단위.
- 마이그레이션에서 `screener_presets`에 `is_seed=TRUE, user_id=NULL` 시드 row UPSERT (사용자 복제본 무영향).

### 4.2 `screener_presets` 스키마 확장 (v40)

```sql
ALTER TABLE screener_presets
    ALTER COLUMN user_id DROP NOT NULL,                    -- NULL = 시드 프리셋
    ADD COLUMN IF NOT EXISTS is_seed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS strategy_key TEXT,            -- 'graham_defensive_lite' 같은 식별자
    ADD COLUMN IF NOT EXISTS persona TEXT,                 -- '벤저민 그레이엄' 표시명
    ADD COLUMN IF NOT EXISTS persona_summary TEXT,         -- 1~2줄 설명
    ADD COLUMN IF NOT EXISTS markets_supported TEXT[],     -- ['KOSPI','KOSDAQ','NASDAQ','NYSE']
    ADD COLUMN IF NOT EXISTS risk_warning TEXT;            -- 면책 문구

CREATE UNIQUE INDEX IF NOT EXISTS uq_screener_presets_strategy_key
    ON screener_presets(strategy_key) WHERE is_seed = TRUE;
```

기존 user 프리셋은 `is_seed=FALSE` 기본으로 변동 없음.

기존 `UNIQUE(user_id, name)` 제약은 PostgreSQL의 NULL 비교 정책상 `user_id IS NULL`(시드)인 row끼리 동일 name이어도 충돌하지 않는다. 시드 식별은 `strategy_key` 부분 UNIQUE 인덱스로 별도 보장하며, 동일 시드를 두 번 INSERT 시 `ON CONFLICT (strategy_key) WHERE is_seed=TRUE` UPSERT로 멱등 처리한다.

### 4.3 시드 프리셋 8종

| # | strategy_key | persona | markets | 핵심 spec |
|---|---|---|---|---|
| 1 | `graham_defensive_lite` | 벤저민 그레이엄 | KR·US | per_range.max=15, pbr_range.max=1.5, exclude_negative=true, market_cap_buckets=[large,mid], dividend_yield_min=0.01 |
| 2 | `schloss_deep_value` | 월터 슐로스 | KR·US | pbr_range.max=0.7, exclude_negative=true, market_cap_buckets=[small,mid] |
| 3 | `neff_low_pe_yield` | 존 네프 | KR·US | per_range.max=10, dividend_yield_min=3.0, exclude_negative=true |
| 4 | `dividend_yield_kr` | 한국 고배당 | KOSPI, KOSDAQ | dividend_yield_min=4.0, market_cap_buckets=[mid,large] |
| 5 | `dividend_yield_us` | 미국 고배당 | NASDAQ, NYSE | dividend_yield_min=4.0, market_cap_buckets=[mid,large] |
| 6 | `druckenmiller_trend` | 드러켄밀러 풍 | KR·US | ma200_proximity_min=1.0, return_ranges.6m.min=15, return_ranges.1y.min=25, high_52w_proximity_min=0.85 |
| 7 | `minervini_trend_template` | 마크 미너비니 | KR·US | ma200_proximity_min=1.0 + ma_alignment="minervini" (MA50>MA150>MA200) |
| 8 | `bottom_fishing` | 역추세 매수 | KR·US | return_ranges.1y.max=-30, low_52w_proximity_max=1.2 (52w 저점 근접) |

면책 (`risk_warning`) — 모든 시드에 강제:
> "원전략의 단순화된 변형. 실제 거장의 정성적 판단(경영진·해자·산업 사이클)은 반영되지 않음. 참고용 후보 발굴 도구."

### 4.4 UI 변경

**탭 구조 갱신:**
```
[Search] [Descriptive] [Performance] [Technical] [Fundamental ✅] [🎩 Investors]
```

**Fundamental 탭 활성화** (`disabled` 제거):
- PER 범위 (min/max + 음수 제외 체크박스)
- PBR 범위 (동일)
- EPS 하한 (적자 제외 단축 토글)
- BPS 하한 (자본잠식 제외)
- 배당률 하한
- "펀더 데이터 없는 종목 제외" 토글

**Investors 탭 신설:**
- 카드 그리드 — 8개 시드 + (별도 섹션) 사용자 복제본
- 카드 구성: persona 아이콘 + 이름 + 1줄 설명 + 지원 시장 뱃지(🇰🇷/🇺🇸) + 룰 요약 칩 (`PER<15`, `PBR<1.5`) + "적용" 버튼
- 카드 클릭: 해당 spec을 다른 탭들에 자동 입력 + 즉시 `/run`
- 카드 우측 하단 ⓘ → 호버 툴팁으로 `risk_warning` 노출

**프리셋 패널 분리:**
- 상단 "🎩 거장 프리셋 (시드)" 섹션 — `is_seed=TRUE` 정렬 우선, 시각 구분
- 시드는 수정/삭제 버튼 숨김, "복제하여 내 프리셋으로 저장" 버튼만

### 4.5 신규 API

```
GET  /api/screener/strategies                    # 시드 프리셋 (인증 불필요, 30분 캐시)
                                                 # 응답: [{strategy_key, persona, persona_summary,
                                                 #         markets_supported, risk_warning, spec}]
GET  /api/screener/presets                       # 기존 — is_seed=TRUE 함께 반환 (owned=false)
POST /api/screener/presets/{strategy_key}/clone  # 시드를 본인 프리셋으로 복제 (Free 403)
```

### 4.6 티어 정책

- **Free**: Investors 탭 조회 가능, 적용 시 결과 상위 5건만 + "전체 보기는 Pro" 배너
- **Pro/Premium**: 전체 결과 + 시드 복제 + 본인 프리셋 저장 (기존 `SCREENER_PRESETS_MAX` 한도)

## 5. § 3 — 마이그레이션 / 테스트 / 운영

### 5.1 마이그레이션 단계 (점진 출시)

| 단계 | 변경 | PR 단위 | 예상 |
|---|---|---|---|
| **M1: DB 인프라** | v39 `stock_universe_fundamentals`, v40 `screener_presets` 확장 | 마이그레이션 단독 | 0.5주 |
| **M2: 수집 + systemd** | `analyzer/fundamentals_sync.py`, `universe_sync.py --mode fundamentals`, systemd unit `investment-advisor-fundamentals.{service,timer}`, `MANAGED_UNITS` 화이트리스트, `tools/fundamentals_health_check.py`, `deploy/systemd/README.md` sudoers 갱신 | 백엔드 + 인프라 | **1.5주** |
| **M3: 스크리너 백엔드** | `screener.py` CTE에 `fund_latest` / `ma50` / `ma150` / `low_52w_proximity` 추가, `/run` 펀더 필터 7종, 응답 필드 확장 | 백엔드 (UI 영향 없음) | 0.5주 |
| **M4: 거장 시드** | `analyzer/investor_strategies.py` 8종, v41 마이그레이션 UPSERT, `/api/screener/strategies`, `/presets/{key}/clone` | 백엔드 | 0.5주 |
| **M5: UI** | Fundamental 탭 활성화, Investors 탭 신설, 카드 갤러리, 시드/유저 프리셋 패널 분리, 티어 게이팅, **admin 운영 탭에 펀더 sync 카드 + 도구 탭에 즉시 sync 버튼 + 위험구역 펀더 truncate 버튼** | 프론트 + admin | **1.2주** |
| **M6: 검증/모니터링** | `proposal_validation_log` 펀더 cross-check 룰 (PER/PBR ±5% 허용), 시드 프리셋 결과 카운트 일별 로깅, **admin 운영 탭에 펀더 결측률 health 카드**, README 운영 매뉴얼 | 운영 | **0.7주** |

총 **4.5주**. M1~M3는 기존 사용자 무영향(읽기 안 됨), M4~M5에서 처음 노출.

### 5.2 테스트 전략

기존 `tests/conftest.py`는 `psycopg2` / `feedparser` / `claude_agent_sdk`만 mock — pykrx 펀더 fixture 신규.

```python
@pytest.fixture
def mock_pykrx_fundamental(monkeypatch):
    def _fake(date, ticker, market="KOSPI"):
        return pd.DataFrame({"BPS": [1000], "PER": [10.5], "PBR": [0.8],
                             "EPS": [950], "DIV": [3.2], "DPS": [320]})
    monkeypatch.setattr("pykrx.stock.get_market_fundamental_by_date", _fake)
```

추가 테스트 파일:
- `test_fundamentals_sync.py` — pykrx + yfinance mock, 단일 종목 sync, UPSERT 멱등성, 결측 종목 skip
- `test_screener_fundamental_filter.py` — `per_range` / `pbr_range` / `dividend_yield_min` SQL 빌더 + 음수 제외 토글
- `test_investor_strategies.py` — 8종 시드 spec JSON 스키마, `/api/screener/strategies` 응답
- `test_screener_strategies_clone.py` — 복제 → 본인 프리셋, Free 403, 한도 초과 403
- `test_screener_seed_idempotent.py` — v38 두 번 실행 시 사용자 복제본 보존, 시드 row만 UPSERT
- 기존 `test_screener_run` 펀더 케이스 4~5개 추가

**실데이터 sanity check** (운영 1회):
- KOSPI 200 종목 sync → pykrx PER/PBR vs DART PER/PBR 표본 10개 비교 (5% 오차 이내)
- NASDAQ 100 종목 sync → yfinance.info dividendYield vs Yahoo Finance 웹 표시값 표본 5개 비교

### 5.3 환경변수 추가 (`shared/config.py:FundamentalsConfig`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `FUNDAMENTALS_RETENTION_DAYS` | `800` | PIT 보관일 (OHLCV와 동일) |
| `FUNDAMENTALS_DELISTED_RETENTION_DAYS` | `400` | 상폐 종목 축소 retention |
| `FUNDAMENTALS_SYNC_ENABLED` | `true` | 일괄 끄기 스위치 (운영 비상시) |
| `FUNDAMENTALS_PYKRX_BATCH_SIZE` | `200` | pykrx rate limit 회피 |
| `FUNDAMENTALS_VALIDATION_TOLERANCE_PCT` | `5.0` | cross-check 허용 오차 |
| `SHOW_INVESTORS_TAB` | `true` | UI feature flag (M5 롤백용) |

### 5.4 관리자 메뉴 통합 (M2 + M5 + M6)

기존 `api/routes/admin_systemd.py:MANAGED_UNITS` 7개에 신규 unit 추가:
- `investment-advisor-fundamentals.service` (oneshot, `self_protected=False`)
- `investment-advisor-fundamentals.timer` (KST 06:35)

`deploy/systemd/README.md` sudoers 화이트리스트 예시에 신규 unit 추가 — `/etc/sudoers.d/investment-advisor-systemd` 운영기 사전 등록 필수.

**admin 운영 탭** (M5):
- 기존 systemd 카드 영역에 펀더 sync 카드 1장 (sector-refresh 카드와 동일 패턴 — start/stop/journalctl SSE)
- **도구 탭**: "펀더멘털 즉시 sync" 버튼 — `routes/admin.py` γ 정책 동일(systemctl 위임 또는 subprocess fallback)
- **위험구역 탭**: "펀더 데이터 전체 삭제" 버튼 — `stock_universe_fundamentals` truncate, CSRF + 2단계 확인

**admin 운영 탭 health 카드** (M6):
- "펀더 결측률" — `tools/fundamentals_health_check.py` 산출 (예: KOSPI 1.2%, NASDAQ 0.4%)
- 임계 초과 시 빨간 뱃지 + 마지막 sync 시각

### 5.5 운영 매뉴얼 신규

`_docs/<YYYYMMDDHHMMSS>_fundamentals-operations.md`:
- 일별 sync 시간 (KST 06:35 — OHLCV 직후)
- pykrx 401 / yfinance Too Many Requests 대응
- 시드 프리셋 변경 절차 (코드 수정 → v++ 마이그레이션 → PR)
- 사용자가 복제한 시드 영향 분석 쿼리

### 5.6 롤백 안전장치

- M1~M3는 신규 컬럼/테이블만 — drop 불필요. 비활성화 시 `FUNDAMENTALS_SYNC_ENABLED=false`.
- M4 시드 문제 발견 시 v42에서 `is_seed=TRUE` row만 DELETE (사용자 복제본 무영향).
- M5 UI 문제 시 Investors 탭만 `SHOW_INVESTORS_TAB=false`로 숨김.

## 6. 의존성 / 리스크

### 6.1 외부 의존성
- pykrx 1.2.7+ (`get_market_fundamental_by_date` 정상 동작 확인 필요 — 기존 KRX_ID/KRX_PW 환경변수 활용)
- yfinance 최신 (rate limit + `.info` 키 안정성 — 분기마다 키 deprecation 모니터링)

### 6.2 리스크
- **yfinance rate limit**: 일 ~600 종목 호출 시 IP throttling 가능. 배치 분할 + 지수 백오프 + `FUNDAMENTALS_YFINANCE_BATCH_SIZE` (별도 환경변수) 도입 검토 (M2 구현 시 결정).
- **pykrx 한국 음력 휴장 처리**: 거래일 아닌 날 호출 시 빈 DataFrame — 결측으로 기록하지 말고 skip.
- **일부 KOSPI 종목 PER 산출 안 됨**: 적자 기업, 우선주, 신규 상장 — `has_preferred=TRUE` 종목은 기존대로 universe 단계에서 제외. 결측은 NULL로 기록.
- **시드 프리셋이 너무 좁아 결과 0건**: KR 시장 룰을 "개선"하다 보면 추가 보완 룰 누적 위험 — 시드는 "의도적으로 단순"하게 유지, 사용자가 복제 후 튜닝.

## 7. 출시 후 관찰 지표

- 펀더 sync 결측률 (KR/US 시장별)
- Investors 탭 클릭률 (이벤트 로그 추가 — 별도 마이그레이션 불필요, 기존 `app_logs` 활용)
- 시드 프리셋 복제율 (Pro/Premium 사용자 중 복제 1회 이상 비율)
- Pro 전환 유도 효과 (Free 사용자가 Investors 탭에서 "Pro 업그레이드" 클릭률)
