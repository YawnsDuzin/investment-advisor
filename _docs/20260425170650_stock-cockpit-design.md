# Stock Cockpit — 종목 페이지 정보 밀도 강화 설계

- 작성: 2026-04-25 KST
- 상태: Phase 1 구현 완료 (커밋 c5a5a87 ~ 42c154f) — Phase 2 별도 spec으로 분리 예정. Backlog: CKPT-1/2/3 후속 patch 필요 (아래 § "Phase 1 후속 backlog" 참조)
- 범위: `/pages/stocks/{ticker}` 페이지를 "투자 종목 선정에 필요한 모든 정보를 한 화면"으로 재구성
- 결정: **기존 페이지 in-place 교체 (A 안)** — 동일 URL, 기존 8카드는 § 4로 흡수

---

## 1. 배경 / As-Is

현재 `/pages/stocks/{ticker}` 페이지([stock_fundamentals.html](api/templates/stock_fundamentals.html))는 yfinance `.info` 단일 소스로 펀더멘털 8카드만 표시한다.

| 항목 | 현황 |
|---|---|
| 데이터 소스 | yfinance `.info` 만 (1시간 in-memory 캐시, DB 영속화 없음) |
| 백엔드 | [`fetch_fundamentals()`](analyzer/stock_data.py#L815) → `yf.Ticker(...).info` |
| API | `GET /api/stocks/{ticker}/fundamentals` |
| 차트 | 없음 (52주 게이지 1줄만) |
| 자사 추천 이력 | 헤더 "추천 이력" 링크로 별도 페이지로 이동 |
| 미사용 자산 | 이미 구현된 [`/api/stocks/{ticker}/ohlcv`](api/routes/stocks.py#L34), [`/api/indices/{code}/ohlcv`](api/routes/stocks.py#L115)가 화면에서 호출되지 않음 |

문제점:
- yfinance `.info` 가 fail 하면 페이지 통째로 "조회 실패" — DB OHLCV 폴백 없음
- 자사 분석 자산(`investment_proposals`, `factor_snapshot`, `proposal_price_snapshots`, `market_indices_ohlcv` 등)이 종목 단위 조회에 활용되지 않음
- "이 종목을 우리가 몇 번 추천했고 사후 성과가 어땠는지" 가 종목 페이지에 노출되지 않음

## 2. 목표

1. 종목 1개에 대해 **펀더멘털 + 가격행동 + 자사 추천 성과 + 시장 컨텍스트 + 정량 팩터 + KRX 수급**을 한 페이지에서 동시 조망
2. 자사 분석 시스템(`investment_proposals` + `factor_snapshot` + `market_regime`)을 **차별화 정보**로 노출 — "AI가 본 이 종목"
3. yfinance 의존도 축소 — DB OHLCV/메타를 1차 소스로, yfinance를 보강 소스로 재배치

## 3. 결정 사항

### 3.1 페이지 교체 방식 — A (in-place)

| 결정 | 근거 |
|---|---|
| URL 동일 (`/pages/stocks/{ticker}`) | 워치리스트·외부링크·검색결과·사용자 북마크 모두 호환 유지 |
| 기존 fundamentals 8카드 → § 4 섹션으로 흡수 | 정보 손실 0 |
| 단일 페이지 스크롤 + 좌측 sticky ToC | "최대한 한꺼번에" 요구사항 직접 충족 |
| 탭 분할 ❌ | 사용자가 직전에 명시적으로 거부한 방향 |

### 3.2 구현 단계 분할 — Phase 1 부터

본 spec은 Phase 1~3 전체를 정의하되, **Phase 1만 즉시 구현 대상**. Phase 2/3 은 후속 작업.

## 4. 페이지 구조 (전체 청사진)

```
┌─────────────────────────────────────────────────────────────────────┐
│ § Hero (sticky 시 압축)                                             │
│  TXN  Texas Instruments / Tech-Semi / NASDAQ                        │
│  $277.14 ▼-1.80%   시총 $252B   거래량 9.2M                         │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┐                │
│  │ AI 종합 │ 우리추천│ 평균사후│ 벤치알파│ 팩터분위│                │
│  │ 78/100  │ 4회     │ +12.4%  │ +5.1%   │ 상위22% │                │
│  └─────────┴─────────┴─────────┴─────────┴─────────┘                │
├─────────────────────────────────────────────────────────────────────┤
│ § 1. 가격 차트 메인 (높이 380px)         [1M·3M·6M·1Y·3Y·All]       │
│   close + MA50 + MA200 + ▲추천마커                                   │
│   하단 볼륨바 + max_drawdown 음영                                    │
├──────────────────────────┬──────────────────────────────────────────┤
│ § 2-A. 벤치마크 상대성과  │ § 2-B. 정량 팩터 레이더 (6축)            │
│ vs KOSPI/SP500 (=100)     │  r1m·r3m·r6m·r12m·vol60·volume_ratio     │
├──────────────────────────┴──────────────────────────────────────────┤
│ § 3. 시장 레짐 + 섹터 컨텍스트                                       │
│  레짐 배지 + 섹터 평균 PER/PBR vs 종목                               │
├─────────────────────────────────────────────────────────────────────┤
│ § 4. 펀더멘털 카드 그리드 (기존 8카드 흡수)                          │
├─────────────────────────────────────────────────────────────────────┤
│ § 5. KRX 확장 (한국주만 표시)                                        │
├─────────────────────────────────────────────────────────────────────┤
│ § 6. AI 추천 이력 타임라인                                           │
│  가로 시간축 + 노드 펼침 카드 (entry/post_return/drawdown/validation)│
├─────────────────────────────────────────────────────────────────────┤
│ § 7. 등장 테마 + 시나리오                                            │
├─────────────────────────────────────────────────────────────────────┤
│ § 8. 액션 (워치/구독/메모/외부링크)                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## 5. 데이터 매핑 (섹션별)

| § | 섹션 | 1차 소스 | 보강 소스 |
|---|---|---|---|
| Hero | 종목 메타 | `stock_universe` | yfinance `.info` (sector/industry) |
| Hero | 현재가/변동률 | `stock_universe_ohlcv` (latest 2 rows) | yfinance `.info` 폴백 |
| Hero | AI 종합 점수 | 합성 (factor_pctile 평균 0.5w + post_return 평균 0.3w + 컨센 BUY 0.2w) | — |
| Hero | 우리추천 횟수·평균 사후수익 | `investment_proposals` GROUP BY ticker | — |
| 1 | OHLCV/MA/볼륨 | `stock_universe_ohlcv` | — |
| 1 | 추천 마커 | `investment_proposals(created_at, entry_price, post_return_3m_pct)` | — |
| 1 | drawdown 음영 | `stock_universe_ohlcv` rolling max | `investment_proposals.max_drawdown_*` |
| 2-A | 벤치마크 라인 | `market_indices_ohlcv` (시장 자동 선택) × `stock_universe_ohlcv` | — |
| 2-B | 팩터 레이더 | `investment_proposals.factor_snapshot` (최신 1건) | — |
| 3 | 레짐 배지 | `analysis_sessions.market_regime` (최신) | — |
| 3 | 섹터 평균 | `stock_universe.sector` × sector_norm 월간 평균 | — |
| 4 | 펀더멘털 8카드 | yfinance `.info` (기존) | — |
| 5 | KRX 확장 | `investment_proposals` (foreign_*/squeeze_risk/index_membership) 최신값 | — |
| 6 | 추천 타임라인 | `investment_proposals` + `proposal_price_snapshots` + `proposal_validation_log` | — |
| 7 | 등장 테마 | `investment_themes` ⨝ `investment_proposals` ⨝ `theme_scenarios`/`macro_impacts` | — |
| 8 | 액션 | `user_watchlist`, `user_subscriptions`, `_macros.external_links` | — |

### 5.1 시장 자동 선택 규칙 (§ 2-A 벤치마크)

| `stock_universe.market` | 벤치마크 1순위 | 2순위 |
|---|---|---|
| `KOSPI` | KOSPI | KOSDAQ |
| `KOSDAQ` | KOSDAQ | KOSPI |
| `NASDAQ` | NDX100 | SP500 |
| `NYSE` | SP500 | NDX100 |
| 그 외/미상 | SP500 | — |

UI 토글로 사용자가 두 벤치마크 중 1개 선택. 기본값=1순위.

### 5.2 AI 종합 점수 산식

```
score = 100 × (0.5 × factor_score + 0.3 × hist_score + 0.2 × consensus_score)

factor_score    = factor_snapshot 의 r1m/r3m/r6m/r12m_pctile 평균 (0~1)
                  → factor_snapshot 없으면 0.5 중립
hist_score      = 이 종목 모든 추천의 post_return_3m_pct 평균 / 30 을 [0,1] clamp
                  → 추천 이력 없으면 0.5 중립
consensus_score = analyst.recommendation 매핑
                  STRONG_BUY=1.0, BUY=0.75, HOLD=0.5, SELL=0.25, STRONG_SELL=0.0
                  → null 이면 0.5 중립
```

가중치는 코드 상수로 두되, `Hero` 점수 옆에 mouseover tooltip으로 산식과 각 컴포넌트 값 노출 (자기설명적 UI 원칙 — 직전 regime 패널 같은 패턴).

## 6. 신규 백엔드 API

다음 4개 엔드포인트만 신규. 기존 `/ohlcv`, `/fundamentals`, `/api/indices/.../ohlcv` 는 그대로 활용.

| 메서드 | 경로 | 책임 |
|---|---|---|
| GET | `/api/stocks/{ticker}/overview` | Hero용 종합 1쿼리 응답 (메타 + 최신가 + 종합점수 + 추천통계) |
| GET | `/api/stocks/{ticker}/proposals` | 이 종목의 모든 `investment_proposals` 시계열 (post_return + validation_log 조인) |
| GET | `/api/stocks/{ticker}/themes` | 이 종목이 등장한 테마 리스트 (테마 클릭 → 테마 상세 페이지) |
| GET | `/api/stocks/{ticker}/sector-stats` | 섹터 평균 (PER/PBR 등) vs 종목 비교 |

응답 JSON 스키마는 implementation plan 단계에서 확정.

## 7. 기술 / 라이브러리

| 용도 | 라이브러리 | 비고 |
|---|---|---|
| 메인 가격 차트 (§ 1) | [lightweight-charts](https://github.com/tradingview/lightweight-charts) | TradingView 오픈소스, ~45KB, CDN 1줄. 가격+볼륨+마커+영역음영 지원 |
| 벤치마크 라인 (§ 2-A) | lightweight-charts | § 1 과 동일 라이브러리로 통일 |
| 레이더/도넛 (§ 2-B, § 5) | Chart.js (CDN) | 폴리필 없음, Cockpit 페이지에서만 로드 |
| sticky ToC | 순수 CSS + IntersectionObserver | 외부 라이브러리 없음 |

`base.html` 에 전역 로드는 금지 — 다른 페이지 무게 증가 방지. Cockpit 페이지의 `{% block scripts %}` 안에서만 CDN 로드.

## 8. 단계별 범위

### Phase 1 (즉시 구현 — 본 plan 대상)
- § Hero (메타 + 가격바 + 통계 5칩)
- § 1 가격 차트 + 추천 마커 + 볼륨
- § 2-A 벤치마크 상대성과
- § 6 추천 이력 타임라인
- 신규 API: `/overview`, `/proposals`
- yfinance `.info` 실패 시 § 4(펀더멘털 8카드) 만 부분 비활성화하고 나머지 섹션은 정상 표시 (DB-first 폴백)

### Phase 2 (후속)
- § 2-B 정량 팩터 레이더
- § 3 시장 레짐 + 섹터 비교
- § 5 KRX 확장
- 신규 API: `/sector-stats`

### Phase 3 (후속)
- § 7 등장 테마 카드 + 신규 API `/themes`
- Hero 압축 sticky 모드 + 모바일 반응형 정밀 조정

### 인증 정책

기존 [`stock_fundamentals_page`](api/routes/stocks.py#L169) 와 동일하게 페이지 자체는 인증 미강제 (`make_page_ctx("proposals")`). 다음만 인증 필요:
- § 8 워치리스트 토글 → `/api/watchlist/*` (기존 정책)
- § 8 알림 구독 토글 → `/api/subscriptions/*` (기존 정책)
- § 8 메모 → `/api/proposals/{id}/memo` (기존 정책)
- 미로그인 사용자에게는 § 8 액션 영역에 "로그인하면 워치리스트/알림 사용 가능" 안내

## 9. 엣지케이스 / 리스크

| 케이스 | 처리 |
|---|---|
| 신규 종목 — OHLCV 미수집 | § 1/2 차트 자리에 "OHLCV 데이터 수집 대기 중" 안내, 나머지 섹션은 정상 |
| 자사 추천 0건 | Hero "우리추천" 칩 "—" 표시, § 6 빈 상태 안내, AI 종합 점수의 hist_score는 중립값(0.5) 사용 |
| `factor_snapshot` 없는 추천 | factor_score 중립값(0.5) 사용 — 점수 산식 견고성 |
| yfinance `.info` 실패 | § 4 카드 영역에 "외부 데이터 일시 조회 실패" 표시. § Hero 가격은 OHLCV latest 로 폴백, 시총은 NULL 허용 |
| KRX 확장 컬럼 NULL (외국주) | § 5 통째 hide |
| 동일 ticker 가 여러 market 에 존재 | 페이지 진입 시 `?market=` 쿼리 우선, 없으면 `stock_universe` 조회 결과 1개로 결정 |
| `change_pct` overflow 가드된 NULL row | 차트 자체에는 영향 없음 (close 만 사용), 변동률 라벨은 "—" |

## 10. 비범위 (Out of Scope)

- 실시간 스트리밍 시세 (WebSocket) — 현재 시스템에 미구현
- 사용자 커스텀 지표 추가 (RSI/MACD 등) — 향후 검토
- 기간 비교 모드 ("이 종목 2024 vs 2025") — Phase 4 후보
- 종목 알림 임계값 설정 (현재 알림은 추천 발생 기반만)
- 모바일 우선(Mobile-first) 재설계 — Phase 3 에서 반응형 조정만

## 11. 성공 기준

Phase 1 완료 시점 검증:
1. 임의의 ticker (KRX/US 각 1종 — 예: `005930`/KOSPI, `TXN`/NASDAQ) 진입 시 5초 이내 모든 섹션 렌더 완료
2. 추천 이력 있는 종목 (`SELECT ticker FROM investment_proposals GROUP BY ticker HAVING COUNT(*) >= 3 LIMIT 5`) 에서 § 6 타임라인 + § 1 마커 정상 표시
3. yfinance `.info` 강제 실패 (mock) 상태에서도 § Hero/§ 1/§ 2-A/§ 6 정상 동작
4. Lighthouse Performance ≥ 80 (메인 차트 라이브러리 lazy-load 확인)
5. 신규 API 4종 중 Phase 1 대상 2개 (`/overview`, `/proposals`) 의 단위 테스트 추가 — 빈 결과/정상/yfinance fail 폴백 3 케이스

## 12. 후속 작업 (Phase 1 완료 후)

- Phase 2 implementation plan 신규 spec 으로 분리 (`_docs/<ts>_stock-cockpit-phase2.md`)
- 사용 패턴 텔레메트리 (옵션) — 어느 섹션이 가장 많이 보이는지로 Phase 3 우선순위 재조정

---

## Phase 1 후속 Backlog (구현 후 발견)

이번 Phase 1 구현 중 코드 리뷰에서 발견된 후속 patch 항목들. Phase 2 시작 전 또는 별도 cleanup 커밋으로 처리.

### CKPT-1: 차트 에러 경로에서 차트 DOM 보존

**문제:** Task 4 (§ 1 가격 차트) + Task 5 (§ 2-A 벤치마크) 둘 다 에러 경로에서 `container.innerHTML = '...'`로 placeholder 텍스트를 출력 — 이 과정에서 lightweight-charts 인스턴스의 DOM이 파괴됨. 이후 토글로 재시도하면 chart.setData() 호출이 예외를 던지고 console에 silent 에러 반복.

**재현 시나리오:** 인덱스 OHLCV 미수집 상태에서 § 2-A "데이터 부족" placeholder 출현 → 사용자가 다른 벤치마크 토글 클릭 → 차트 호출 실패.

**Patch 방안:** `container.style.position = 'relative'` + 차트 위에 `position:absolute` overlay div를 띄워서 에러 메시지 표시. 차트 인스턴스는 보존, 토글 재시도 가능.

**대상 파일:** `api/templates/stock_cockpit.html` (§ 1 + § 2-A IIFE 둘 다)

### CKPT-2: 벤치마크 정규화 기준일 갭 처리

**문제:** § 2-A 에서 `commonStart = max(stockData[0].date, benchData[0].date)` 후 각 시리즈의 `[0]`을 100 기준으로 정규화. 한국 주식과 US 인덱스의 거래일 캘린더가 어긋나면 (공휴일 차이 등) 두 시리즈의 정규화 기준일이 하루~며칠 차이날 수 있음 — 미세한 의미상 오차.

**Patch 방안:** `commonStart` 이후 두 시리즈에서 **동일한 날짜로 모두 존재하는 첫 거래일**을 찾아 그 날을 기준으로 통일. 또는 fallback으로 `console.warn` 출력 추가.

**대상 파일:** `api/templates/stock_cockpit.html` (§ 2-A IIFE)

### CKPT-3: § 2-A 벤치마크 토글 시 stock OHLCV 재조회 제거

**문제:** 벤치마크 토글 클릭마다 `loadAndRender(benchCode)`가 stock OHLCV도 함께 재조회. 같은 종목 OHLCV는 캐싱하면 충분.

**Patch 방안:** `var stockCache = null;` IIFE 스코프 변수 도입, 첫 호출 시만 fetch.

**대상 파일:** `api/templates/stock_cockpit.html` (§ 2-A IIFE)

### CKPT-4: `test_pages_new.py` pre-existing 깨짐

**문제:** Phase 1과 무관하게 이미 깨진 상태. `api.routes.pages` 모듈이 더 이상 존재하지 않음(`api.routes.dashboard` 등으로 분리 추정). 테스트 import path 갱신 필요.

**대상 파일:** `tests/test_pages_new.py`

### CKPT-5: `test_track_record.py` pre-existing 깨짐

**문제:** `7b5f203` 커밋(2026-04-20)에서 `track_record.py`가 `get_connection` → `Depends(get_db_conn)` 패턴으로 리팩터링됐으나 테스트가 구버전 patch 경로 사용.

**대상 파일:** `tests/test_track_record.py`
