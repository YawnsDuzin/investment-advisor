# OHLCV 이력 활용 로드맵 — TODO

> **작성일**: 2026-04-23 12:58 KST
> **목적**: Phase 7에서 투입된 `stock_universe_ohlcv` 이력 데이터를 분석 파이프라인·UI에서 실제 소비하기 위한 단계적 활성화 체크리스트.
> **현 상황**: 이력 테이블 인프라·백필·cleanup은 완료(커밋 `5710711`까지). 소비 코드 ZERO — yfinance/pykrx 실시간 호출에 여전히 의존.
> **관련 문서**:
> - 설계 원안: [`20260422235016_ohlcv-history-table-plan.md`](20260422235016_ohlcv-history-table-plan.md)
> - 운영 매뉴얼: [`20260423101419_ohlcv-operations.md`](20260423101419_ohlcv-operations.md)
> - 백필 검증 SQL: [`20260423105335_ohlcv-backfill-verification-sql.md`](20260423105335_ohlcv-backfill-verification-sql.md)

---

## 범례

- 난이도: 🟢 Low / 🟡 Mid / 🔴 High
- ROI: ⭐~⭐⭐⭐⭐⭐
- 상태: ⬜ 미착수 / 🟨 진행중 / ✅ 완료 / ⏸ 보류

---

## 우선순위 A — 단기 (1~2일, 즉시 착수 가능)

> **공통 목표**: 이미 채워진 OHLCV 이력을 분석 파이프라인이 **소비만 하면** 효과를 내는 영역. 설계·아키텍처 변경 최소. 라즈베리파이 안정성·속도 즉시 개선.

### A1. Stage 1 모멘텀 체크 DB 리팩터 🟢 ⭐⭐⭐⭐⭐

- [ ] **상태**: ⬜
- [ ] `analyzer/stock_data.py`에 `fetch_momentum_from_db(tickers, markets)` 함수 추가
  - [ ] 단일 SQL로 (1m/3m/6m/1y 수익률, `current_price`, `price_source`) 일괄 조회
  - [ ] 기준일: OHLCV 최신 trade_date. 개월 환산은 21/63/126/252 거래일.
- [ ] `analyzer/stock_data.py` `fetch_momentum_batch` 호출 지점에 옵션 flag 추가
  - [ ] 기본 DB 우선, fallback yfinance/pykrx (OHLCV 결측 종목)
  - [ ] `.env`에 `MOMENTUM_SOURCE=db|live` 스위치 (기본 `db`)
- [ ] 단위 테스트: OHLCV 채워진 케이스 vs 결측 케이스
- [ ] 체감 테스트: 기존 대비 분석 배치 소요 시간 비교 로그

**기대 효과**
- 분석 배치에서 모멘텀 조회 단계 10x 이상 단축 예상 (yfinance 루프 제거)
- 외부 API 장애 시에도 분석 지속 가능 → 24/7 운영 안정성 상승
- rate-limit 이슈 사라짐

---

### A2. Stage 1-B 스크리너 이력 필터 확장 🟢 ⭐⭐⭐⭐

- [ ] **상태**: ⬜
- [ ] `analyzer/screener.py`에 OHLCV 기반 필터 CTE/서브쿼리 추가
  - [ ] 유동성: `AVG(close * volume) OVER (rows 60)` 하위 N% 제외
  - [ ] 변동성: `STDDEV(change_pct) OVER (rows 60)` 상위 N% 제외 (옵션)
  - [ ] 단기 모멘텀: `(close / LAG(close, 20) - 1) * 100`
  - [ ] 52주 고저 근접도: `close / MAX(close) OVER (rows 252)`
  - [ ] 낙폭 필터: `close / MAX(close) OVER (rows 60)`
- [ ] `shared/config.py`의 `ScreenerConfig`에 임계값 파라미터 추가
  - [ ] `SCREENER_MIN_LIQUIDITY_KRW`, `SCREENER_MAX_VOL60`, `SCREENER_MOMENTUM_20D_RANGE` 등
- [ ] Stage 1-B 로그에 스크리너 필터별 탈락 수 누적 출력
- [ ] `_docs/_exception/` 패턴 점검 — hallucination 종목 사후 감지 통계

**기대 효과**
- LLM hallucination 원천 차단 강화
- Stage 2 투입 종목 품질↑ → AI 분석 자원 효율↑
- `proposal_validation_log` mismatch율 감소

---

### A3. Post-Return 추적을 OHLCV로 통합 🟢 ⭐⭐⭐

- [ ] **상태**: ⬜
- [ ] `analyzer/price_tracker.py`에 `compute_post_returns_from_ohlcv()` 추가
  - [ ] `entry_price`를 기준으로 post-return 1m/3m/6m/1y 계산
  - [ ] 추가 메트릭: **Max Drawdown / 평균 보유기간 수익률 / 벤치마크 대비 alpha**
  - [ ] 벤치마크: 한국(KOSPI) / 해외(S&P 500). KOSPI OHLCV는 `pykrx` index ticker 별도 수집 필요
- [ ] `proposal_price_snapshots` 중복 적재 여부 검토
  - [ ] OHLCV에 해당 날짜 데이터 있으면 snapshots 적재 생략 (옵션)
- [ ] DB v29 마이그레이션 (선택): `investment_proposals`에 `max_drawdown_pct`, `alpha_vs_benchmark_pct` 컬럼 추가
- [ ] 트랙 레코드 페이지 표시 로직에 신규 메트릭 반영

**기대 효과**
- 추천 성과 지표 품질 대폭 확장 (승률만 있던 현재 → Sharpe 근사·Max DD·alpha)
- `proposal_price_snapshots` 저장량 감소 (장기)

---

## 우선순위 B — 중기 (3~7일, A 완료 후 착수)

### B1. 정량 팩터 사전 계산 → 프롬프트 주입 🟡 ⭐⭐⭐⭐⭐

- [ ] **상태**: ⬜
- [ ] 팩터 산출 모듈 `analyzer/factor_engine.py` 신설
  - [ ] 모멘텀 z-score (3m/6m/12m 결합)
  - [ ] 저변동 z-score (vol60 역순위)
  - [ ] 단기 반전 시그널 (1m 수익률)
  - [ ] 거래량 이상 z-score (최근 20d 거래량 / 60d 평균)
  - [ ] 산출은 **cross-sectional z-score** (동일 섹터 내 순위 기반)
- [ ] `analyzer/prompts.py` STAGE2 템플릿에 `{quant_factors}` 블록 추가
  - [ ] LLM은 수치 추정 금지 → 실측 팩터를 해석·스토리화
- [ ] `investment_proposals`에 `factor_snapshot JSONB` 컬럼 추가 (v29 마이그레이션 한 번에 처리)
- [ ] `proposal_validation_log`의 mismatch 필드 확장 — 팩터 cross-check

**기대 효과**
- AI 수치 환각 제거. 숫자는 DB 산출, 해석만 AI.
- 분석 결과 설득력·재현성↑
- LLM 토큰 소비 감소 (계산 안 해도 되니까)

---

### B2. 시장 레짐 판별 레이어 🟡 ⭐⭐⭐⭐

- [ ] **상태**: ⬜
- [ ] KOSPI·S&P 500 인덱스 OHLCV 수집 태스크 추가
  - [ ] `universe_sync.py --mode ohlcv --index`(신규 플래그)
  - [ ] 또는 별도 `analyzer/index_sync.py` 모듈
- [ ] `analyzer/regime.py` 신설
  - [ ] `above_200ma`, `vol_regime`(저/중/고), `drawdown_from_peak_pct`, `breadth`(universe 상승 종목 비율)
  - [ ] 출력은 `dict` → `analysis_sessions.market_regime JSONB`
- [ ] v29 마이그레이션: `analysis_sessions.market_regime JSONB` 컬럼 추가
- [ ] Stage 1 `STAGE1_SYSTEM` 프롬프트에 `{market_regime}` 주입
  - [ ] 고변동·약세장에서 컨트래리안 비중↓, 강세장에서 모멘텀 비중↑ 자동 가이드

**기대 효과**
- 추천 성향이 시장 국면에 맞춰 자동 조정
- 방어 국면에서 무리한 추천 감소

---

### B3. 섹터 로테이션 힌트 🟡 ⭐⭐⭐

- [ ] **상태**: ⬜
- [ ] 섹터별 최근 20d/60d 평균 수익률 집계 쿼리
- [ ] 집계 결과를 `analyzer/macro_builder.py`(기존 매크로 조립 로직)에 통합
- [ ] Stage 1 `macro_impacts` 프롬프트 컨텍스트에 섹터 강·약 힌트 주입
- [ ] UI 대시보드에도 "섹터 모멘텀 히트맵" 연동 (UI #3과 합쳐도 됨)

**기대 효과**
- AI의 "좋아 보이는 섹터" 직관 vs 실제 수급 cross-check
- 섹터 편향(AI가 IT·배터리 위주로만 편향되는 현상) 완화

---

## 우선순위 C — 장기 (1~2주 이상, B 완료 + 데이터 쌓인 후)

### C1. Factor Feedback Loop (계획서 Phase 4) 🔴 ⭐⭐⭐⭐⭐

- [ ] **상태**: ⏸ (데이터 누적 6개월~ 대기)
- [ ] 과거 추천 × 실제 수익률 cross-sectional IC 계산 배치
- [ ] IC 결과를 `recommender.RecommendationConfig` 가중치에 반영
- [ ] 자동 튜닝 주기 (예: 월 1회)
- [ ] 관리자 UI에서 튜닝 이력 조회

**기대 효과**
- 시스템 자가 개선 루프 완성
- 장기 경쟁 우위 핵심

---

### C2. PIT 백테스트 엔진 🔴 ⭐⭐⭐⭐

- [ ] **상태**: ⏸
- [ ] 스펙 + 당시 universe snapshot + OHLCV로 재현 엔진
- [ ] "신규 스크리너/팩터를 6개월 전부터 돌렸다면?" 시뮬레이션
- [ ] 관리자 페이지에 백테스트 요청 UI
- [ ] 결과를 `_docs/_exception/` 패턴처럼 리포트 파일로 저장

**기대 효과**
- 신규 전략 도입 전 검증 가능
- 마케팅 자료용 백테스트 리포트 생성 가능

---

## UI 활용 TODO

### UI-1. 종목 상세 페이지 강화 🟢 ⭐⭐⭐⭐⭐

대상: [`api/templates/ticker_history.html`](../api/templates/ticker_history.html) + [`_macros.html`](../api/templates/_macros.html)

- [ ] 미니 200일 주가 스파크라인 (SVG 또는 Chart.js)
- [ ] 추천 시점 마커 (confidence별 색상)
- [ ] 52주 고/저 위치 게이지 (현재가가 range 어디에)
- [ ] 팩터 배지 (`모멘텀 A | 변동성 C | 유동성 A+`) — B1 완료 후
- [ ] 거래량 이상도 표시 (평균 대비 배수)
- [ ] API 엔드포인트: `GET /api/stocks/{ticker}/ohlcv?days=200`

### UI-2. 테마 히스토리 강화 🟡 ⭐⭐⭐

대상: [`api/templates/theme_history.html`](../api/templates/theme_history.html)

- [ ] 테마 구성 종목 평균 수익률 추이 라인 차트 (3개월)
- [ ] 테마 vs KOSPI 상대 성과 오버레이
- [ ] 테마 활성도(구성 종목 중 20d 수익률 > 0 비율) 배지

### UI-3. 대시보드 위젯 신설 🟢 ⭐⭐⭐⭐

대상: [`api/templates/dashboard.html`](../api/templates/dashboard.html), `base.html`

- [ ] **레짐 배지** (상단) — B2 연동. `"시장: 중립·저변동"` 1줄 요약
- [ ] **오늘의 이상 시그널** 카드 — 52주 신고가 돌파 / 거래량 폭증 / Gap up 리스트
- [ ] **섹터 모멘텀 히트맵** — 섹터 × 기간(20d/60d/1y) 색상 매트릭스

### UI-4. 트랙 레코드 확장 🟡 ⭐⭐⭐⭐

대상: [`api/routes/track_record.py`](../api/routes/track_record.py), `api/templates/track_record.html`

- [ ] 벤치마크 대비 alpha (KOSPI 기준)
- [ ] 승률 + 평균 보유기간 수익률 + Max DD
- [ ] Sharpe 근사치
- [ ] 시간대별 누적 성과 curve

### UI-5. 워치리스트 알림 강화 🟡 ⭐⭐⭐

대상: [`api/routes/watchlist.py`](../api/routes/watchlist.py), `shared/db/` 알림 생성 로직

- [ ] 200일 이평 돌파·이탈 알림
- [ ] 변동성 급등 경보 (vol20 > vol60 × 2)
- [ ] 섹터 상대강도 급변 알림
- [ ] `user_subscriptions`에 `sub_type='signal'` 신설

### UI-6. 프리미엄 스크리너 (신규 페이지) 🔴 ⭐⭐⭐⭐⭐

대상: 신규 `api/routes/screener.py`, `api/templates/screener.html`

- [ ] 티어 차등 필터 세트
  - [ ] Free: 섹터/시총/가격
  - [ ] Pro: 이력 필터 (1y 수익률, 거래대금, 변동성)
  - [ ] Premium: 팩터 조합 (B1 연동), 저장·알림
- [ ] `shared/tier_limits.py` 확장
- [ ] 프리셋 저장/공유 기능

### UI-7. AI 투명성 섹션 🟢 ⭐⭐⭐⭐⭐

대상: Stage 2 분석 상세 페이지 (`api/templates/session_detail.html` 또는 proposals 카드)

- [ ] **"AI가 본 실측 데이터" 섹션** 추가
  - B1 완료 후 `factor_snapshot` JSONB를 표로 표시
  - 예: "6개월 모멘텀 z = +1.42 / 거래량 z = +0.8 / 200일 이평 위"
- [ ] 데이터 출처 명시 (`data_source`, `trade_date` 표기)

**마케팅 효과**: 경쟁사 "AI가 알아서 판단" vs "우리는 AI가 **검증된 실측 데이터**로 판단". 차별화 포인트.

---

## 추천 착수 순서 (로드맵)

```
Week 1 (단기):
  A1 (모멘텀 DB 리팩터) → A2 (스크리너 확장) → UI-1 (종목 상세)

Week 2 (중기 진입):
  A3 (Post-Return 통합) → B1 (팩터 엔진) + UI-7 (AI 투명성)

Week 3:
  B2 (Regime 레이어) + UI-3 (대시보드) → B3 (섹터 로테이션) + UI-2

Week 4:
  UI-4 (트랙 레코드) + UI-5 (알림 강화)

Month 2+:
  UI-6 (프리미엄 스크리너) — 수익화 포인트

Month 6+:
  C1 (Factor Feedback), C2 (백테스트 엔진) — 데이터 누적 후
```

### 즉시 효과 최대화 경로 (라즈베리파이 기준)

**A1 → A2 → UI-1 → UI-7** 순으로만 붙여도 체감 분석 품질·속도·신뢰도 확 올라간다. 나머지는 여유롭게.

---

## 마이그레이션 의존성 요약

| 항목 | 필요 마이그레이션 |
|------|-------------------|
| A1 | 없음 — 기존 테이블만 사용 |
| A2 | 없음 |
| A3 | v29 (선택): `investment_proposals.max_drawdown_pct / alpha_vs_benchmark_pct` |
| B1 | v29: `investment_proposals.factor_snapshot JSONB` |
| B2 | v29: `analysis_sessions.market_regime JSONB` |
| B3 | 없음 — 프롬프트·UI만 변경 |
| C1 | v30~: factor_weights_history 테이블 |
| C2 | v30~: backtest_runs 테이블 |

**v29는 B1·B2·A3 컬럼을 묶어서 한 번에 추가하는 걸 권장** — 개별 마이그레이션 파편화 방지.

---

## 관리 메모

- 이 TODO는 작업 진행 시마다 체크박스 업데이트. 완료 항목은 ⬜ → ✅, 커밋 해시 명시.
- 범위 변경·신규 아이디어 추가 시 해당 우선순위 섹션에 append.
- 전체 구조 리팩터가 필요해지면 새 로드맵 문서 생성하고 본 파일은 `archived` 표기.
