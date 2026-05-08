# 투자 인사이트 제안 — 추가 개선 로드맵

> 작성일: 2026-05-08 (KST)
> 컨텍스트: starter question 카드 작업(커밋 `00a4f1f`) 직후. 현재 시스템에서 "AI 인사이트를 더 짜내는 것"보다 **있는 데이터를 능동적으로 사용자에게 들이미는 방향**이 ROI 가 높다는 판단.

## 평가 기준

1. **데이터 재활용도** — 신규 수집 없이 기존 테이블/시그널/팩터 조합으로 가능한가
2. **사용자 행동 변화 가능성** — 매일 들어오지 않아도 핵심 이벤트를 놓치지 않게 만드는가
3. **구현 비용** — 라즈베리파이 운영기에 부담을 주지 않는 수준인가

## Tier 1 — 즉시 가치, 거의 다 데이터 재활용

### 1. 워치리스트 시그널 자동 매칭 푸시

이미 `daily_signals` 테이블이 있고 알림 시스템(`user_subscriptions` / `user_notifications`)이 있다. 그런데 둘이 안 이어져 있다.

- **What**: 워치리스트 종목에 시그널 발생 → `user_notifications` 자동 생성
- **시그널 종류**: 오늘 매수 streak, 52w high 돌파, 외국인 N일 연속 순매수, 거래량 spike, foreign_ownership_pct 급증
- **작업**: `analyzer/signals.py` 종료 시 hook 1개 + 워치리스트 매처. 신규 테이블 불필요.
- **공수**: 반나절
- **효과**: 사용자가 매일 들어오지 않아도 핵심 이벤트를 놓치지 않음

**구현 스케치**:
```python
# analyzer/signals.py 마지막에
def _notify_watchlist_signals(conn, signals: list[dict]) -> None:
    # signals: [{ticker, signal_type, severity, detail}, ...]
    # user_watchlist JOIN signals → user_notifications INSERT
```

### 2. "오늘의 한 줄" + 시장 체온계

이미 `analysis_sessions.market_regime` (v31), `pre_market_briefings.briefing_data` (v34) 있다. 사용자에게 와닿게 가공 안 됨.

- **What**: 매일 06:30 brief 생성 시 Haiku 1샷으로 **한 문장 요약**(80자 이내) + 시장 체온계(0~100 게이지)
- **체온계 산식**: regime(above_200ma + drawdown) + 시장폭(20일 상승 비율) + VIX-proxy(KOSPI vol60) 결합 정규화
- **작업**: `pre_market_briefings`에 `one_liner TEXT` + `market_temperature INT(0-100)` 컬럼 + 대시보드 hero 카드
- **공수**: 하루
- **효과**: 대시보드 진입 즉시 "장이 오늘 뜨거운지 식었는지" 인지

**스키마**:
```sql
ALTER TABLE pre_market_briefings
    ADD COLUMN one_liner TEXT,
    ADD COLUMN market_temperature INT;  -- 0(빙하) ~ 100(과열)
```

### 3. 포트폴리오 헬스 체크 (워치리스트 분산도)

룰 기반 즉시 가능. AI 호출 0.

- **What**: 워치리스트 분석 — 섹터 집중도(HHI), 시장 편향(KR vs US), 시총 분포, 평균 PER vs 시장 벤치
- **출력 예시**:
  - "반도체 65%, 금리 인상에 약함"
  - "KR 시장 92% — 통화 분산 부족"
  - "워치리스트 평균 PER 28x (KOSPI 평균 대비 +60%)"
- **작업**: `routes/watchlist.py`에 `/api/watchlist/health` + 워치리스트 페이지 카드
- **공수**: 반하루
- **효과**: 사용자가 자기 관심 종목을 객관화. 새로운 추천 종목과의 분산 가치 판단 근거.

---

## Tier 2 — 중간 가치, 일부 데이터 모델 확장

### 4. 시나리오 진행 추적 (테마 사후 검증)

`theme_scenarios`에 base/worse/better 시나리오 있고 `macro_impacts`에 변수 있다. 그런데 **사후 검증이 없다.**

- **What**: 매크로 변수(금리·환율·유가·VIX)를 외부 소스(FRED/yfinance)에서 추적 → base case 대비 ±% 측정
- **UI**: 테마 페이지에 "이 시나리오 적중률" 게이지 ("Base 62% / Worse 28% / Better 10% 진행 중")
- **작업**: `macro_observations` 테이블 + 일배치 fetch + 테마 상세 UI 카드
- **공수**: 2~3일
- **차별화**: "AI가 한 말의 검증 가능성" — 신뢰도 가장 큰 도약. 트랙레코드 페이지의 종목 단위 검증을 시나리오 단위로 확장한 셈.

**스키마**:
```sql
CREATE TABLE macro_observations (
    id SERIAL PRIMARY KEY,
    variable_name VARCHAR(80),  -- "10Y_treasury_yield", "USDKRW", "WTI" ...
    observed_at DATE,
    value NUMERIC,
    source VARCHAR(40),  -- "fred" / "yfinance" / "krx"
    UNIQUE (variable_name, observed_at)
);
```

### 5. 유사 종목 추천

이미 `factor_snapshot JSONB` 가 v30에서 추가됐다. 수익률/변동성/거래량 팩터 벡터.

- **What**: 코사인 유사도 + 같은 섹터 필터 → Top-5 유사 종목
- **위치**: 종목 상세 페이지(`stock_cockpit`) 하단 카드. (스크리너 행 액션 "유사 종목 찾기"가 이미 있는데 거기도 강화)
- **작업**: `routes/stocks.py`에 `/api/stocks/{ticker}/similar` + numpy 코사인
- **공수**: 하루
- **효과**: 사용자가 흥미로운 종목 1개를 발견 시 자연스러운 탐색 경로 제공

### 6. 채팅 인용 카드 (RAG 스타일 inline)

현재 채팅 답변은 plain text. 답변에 종목·테마·시그널 언급되면 inline 카드로 자동 표시.

- **What**: 답변 후처리 — regex + `stock_universe` / `investment_themes` 매칭 → 답변 하단 "인용 데이터" 섹션에 종목 카드 / 시그널 카드 / 테마 링크
- **작업**: 세 chat engine 응답 후처리 + `chat_bubble` 템플릿 확장
- **공수**: 하루
- **효과**: 사용자가 채팅에서 종목 발견 시 1클릭으로 종목 상세 진입. ticker injection 이 이미 있는데 input 단계라 답변에 등장한 새 종목은 누락됨 — 이걸 메우는 작업.

---

## Tier 3 — 욕심내면, 검증된 후

### 7. 개인 백테스트 — "내가 추천 그대로 따랐다면"

OHLCV 이력 + `investment_proposals.entry_price`/`post_return_*` 다 있다.

- **What**: 사용자가 "이 추천 따랐다" 체크 → 가상 포트폴리오 누적 수익률 계산
- **위치**: 트랙레코드 페이지에 "개인 모드" 토글
- **작업**: `user_proposal_followups` 테이블 + 일배치 + 차트
- **공수**: 3~4일
- **리스크**: 사용자가 안 쓰면 데드 기능. PoC 후 활용도 측정 필요.

### 8. 페르소나 토글 (Buffett / Greenblatt / O'Neil 모드)

스크리너에 이미 거장 5 시드 있다. 채팅·인사이트도 같은 페르소나로 분기 가능.

- **What**: 채팅 시스템 프롬프트에 `persona` 인젝트. 같은 종목도 페르소나별 다른 시각.
- **공수**: 하루
- **차별화 약함**: "그냥 다른 톤으로 말함" 수준이라 실효성 검증 필요. 사용자 설문이나 클릭률로 검증 후 확장.

---

## 안 추천하는 것

- **PWA 위젯 / Web Push** — 운영 부담 vs 효과 불투명. iOS Web Push 제약.
- **이메일·카카오 다이제스트** — 라즈베리파이 운영기 외부 시스템 의존 ↑. 미관리 시 사일런트 실패.
- **실시간 가격 스트림** — Free 사용자에게 줄 수 있는 게 아님 (yfinance rate limit, websocket 운영비).

---

## 권장 우선순위

| 순서 | 항목 | 공수 | 가치 |
|------|------|------|------|
| 1 | #1 워치리스트 시그널 알림 | 0.5d | ★★★★★ |
| 2 | #3 포트폴리오 헬스 체크 | 0.5d | ★★★★ |
| 3 | #2 오늘의 한 줄 + 체온계 | 1d | ★★★★ |
| 4 | #6 채팅 인용 카드 | 1d | ★★★ |
| 5 | #5 유사 종목 추천 | 1d | ★★★ |
| 6 | #4 시나리오 진행 추적 | 2~3d | ★★★★ (장기) |
| 7 | #7 개인 백테스트 | 3~4d | ★★ (검증 필요) |
| 8 | #8 페르소나 토글 | 1d | ★★ (실효성 검증 필요) |

**Sprint 1 (1주)** : #1 + #3 + #2 + #6 + #5 — 모두 신규 데이터 수집 0, 기존 테이블 조합만.
**Sprint 2 (2주)** : #4 시나리오 진행 추적 — 차별화 핵심.
**Sprint 3 (재량)** : #7, #8 — 사용자 피드백 보고 결정.

---

## 추가 메모

- 모든 Tier 1 항목은 **사용자가 들어오지 않을 때도 가치를 만든다** — 알림 / 다이제스트 / 백그라운드 분석. 즉, retention 기여도가 가장 크다.
- 시나리오 진행 추적(#4)은 다른 투자 앱과 가장 차별화되는 포인트. "AI 가 말한 것의 검증 가능성"은 트랙레코드와 함께 신뢰의 근간.
- 페르소나 토글(#8)은 ChatGPT 류와 차별점이 약하다. 후순위.
