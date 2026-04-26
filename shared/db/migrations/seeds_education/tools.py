"""tools 카테고리 — 본 시스템 도구·시스템 사용 가이드 (신규 카테고리, v35).

B1 정량 팩터 (v30) / B2 시장 레짐 (v31) / v34 프리마켓 브리핑 등
본 시스템이 도입한 데이터 레이어 사용자 가이드 전용.
"""
import json

TOPICS: list[dict] = [
    {
        "category": "tools",
        "slug": "factor-six-axes",
        "title": "정량 팩터 6축 읽기 — r1m/3m/6m/12m·vol60·volume_ratio + percentile",
        "summary": "본 시스템이 모든 추천 종목에 자동 계산해주는 정량 팩터 6축의 의미와 해석법을 배웁니다.",
        "difficulty": "intermediate",
        "sort_order": 60,
        "content": """## 본 시스템이 자동 계산하는 6축

본 시스템 v30(B1 팩터 엔진)부터 모든 Stage 2 분석 종목에 **6개 정량 팩터**를 자동 계산하여 `factor_snapshot` JSONB로 저장한다. 제안 카드 우측에 노출.

| 팩터 | 의미 | 측정 기간 |
|------|------|---------|
| **r1m_pct** | 1개월 수익률 | 거래일 21일 |
| **r3m_pct** | 3개월 수익률 | 거래일 63일 |
| **r6m_pct** | 6개월 수익률 | 거래일 126일 |
| **r12m_pct** | 12개월 수익률 | 거래일 252일 |
| **vol60_pct** | 60일 변동성 (연환산) | 표준편차 × √252 |
| **volume_ratio** | 최근 5일 거래량 / 60일 평균 | 단기 매매 강도 |

## Percentile (분위) — 더 중요한 신호

각 팩터에는 `*_pctile` (예: `r6m_pctile`) 컬럼이 함께 있다.

> **percentile = 시장(KRX/US) 내 해당 종목이 몇 분위에 있는가**

- `r6m_pctile = 0.95` → "6개월 수익률 상위 5%"
- `vol60_pctile = 0.10` → "60일 변동성 하위 10% (저변동성)"
- `volume_ratio_pctile = 0.99` → "최근 거래량 폭증 상위 1%"

→ 절대 숫자보다 **시장 대비 위치**가 신호로 더 의미 있음. 시장 전체가 -20% 빠진 시점에 -10% 종목은 상대적으로 강한 종목이다.

## 6축 조합 패턴 5가지

### 패턴 1: 모멘텀 종목
- r1m·r3m·r6m_pctile 모두 0.8+
- vol60_pctile 0.5~0.8 (높은 편)
- → 이미 강한 추세 진행 중. 추격 매수 위험 vs 추세 지속 중 선택

### 패턴 2: 턴어라운드 후보
- r12m_pctile 0.2 미만 (장기 하락)
- r1m_pctile 0.7+ (최근 반등)
- volume_ratio_pctile 0.8+ (거래 폭증)
- → 바닥권에서 상승 전환 신호. 본 시스템 `discovery_type=contrarian` 매핑.

### 패턴 3: 저변동성 우량주
- vol60_pctile 0.2 미만
- r12m_pctile 0.6~0.8 (꾸준한 상승)
- → 안정적 우상향. 장기 코어 포지션 후보.

### 패턴 4: 과열 경고
- r1m_pctile 0.95+
- r3m_pctile 0.95+
- vol60_pctile 0.9+
- → 단기 급등 + 변동성 폭발. 정점 부근 가능.

### 패턴 5: 거래 부재 (의심)
- volume_ratio_pctile 0.1 미만
- → 거래 부진. 펀더멘털 좋아도 시장 관심 부재 → 상승 동력 약함.

## 한계 — 반드시 알 것

### 1. percentile 산출 종목군 제약
- KRX/US 시장 *분리* 산출
- 60일 거래대금 임계값 (`SCREENER_MIN_DAILY_VALUE_KRW` 5억원 이상) 종목군 한정
- → 신생 상장·저유동성 종목은 percentile 부정확 또는 누락

### 2. 후행 지표
- 모든 6축이 *과거 데이터 기반*
- 미래 예측 도구가 아니라 *현재 위치 진단* 도구

### 3. 펀더멘털과 분리
- 6축은 가격·거래량 정보만
- 실적·산업·매크로는 별도 분석 필요

## 활용 워크플로우

1. 제안 카드 → `factor_snapshot` 6축 확인
2. 5개 패턴 중 어느 것에 가장 가까운지 판단
3. AI 분석 본문(이유)과 일치하는지 교차 확인
4. 본인의 진입 의도(추세 추종 vs 역추세 vs 저변동)와 매칭

> AI는 *해석*을 한다. 6축 *수치 자체*는 OHLCV 이력에서 산출한 실측이다. 이 둘을 분리해서 봐야 한다.""",
        "examples": json.dumps([
            {"title": "본 시스템 실제 출력 예시 (가상 종목)", "description": "factor_snapshot = {r1m_pct: -3.2, r3m_pct: 12.5, r6m_pct: 28.7, r12m_pct: 8.1, vol60_pct: 32.4, volume_ratio: 1.8, r6m_pctile: 0.91, vol60_pctile: 0.65, volume_ratio_pctile: 0.85}. 해석: 6개월 상위 9%·거래량 폭증 → 모멘텀 진행 중인 단기 조정 구간.", "period": "예시", "lesson": "수치 자체보다 percentile + 패턴 매칭이 신호의 강도를 결정한다"}
        ]),
    },
    {
        "category": "tools",
        "slug": "market-regime-reading",
        "title": "시장 레짐 읽기 — above_200ma·vol_regime·drawdown 신호 해석",
        "summary": "본 시스템 v31(B2 레짐 레이어)이 매 분석마다 진단하는 시장 국면 데이터를 읽고 활용하는 법.",
        "difficulty": "intermediate",
        "sort_order": 61,
        "content": """## 시장 레짐이란?

**레짐 (Regime)** = 시장이 *현재 어떤 국면에 있는가*에 대한 진단. 같은 종목·같은 매크로여도 레짐에 따라 추천 신뢰도와 리스크 톤이 달라야 한다.

본 시스템 v31부터 `analysis_sessions.market_regime` JSONB에 매 분석 시점 레짐 스냅샷을 기록한다.

## 본 시스템이 측정하는 6가지 지표

| 지표 | 의미 | 시그널 |
|------|------|------|
| **above_200ma** | 종가가 200일 이동평균 위에 있는지 | True = 장기 상승 추세 |
| **pct_from_ma200** | 200MA로부터 % 거리 | +20%↑ 과열 / -20%↓ 침체 |
| **vol60_pct** | 60일 변동성 (연환산) | 시장 전체 변동성 |
| **vol_regime** | low/mid/high | 이력 분위 기반 자동 분류 |
| **drawdown_from_52w_high_pct** | 52주 고점 대비 하락률 | -10%↓ 정상 / -20%↑ 약세장 |
| **return_1m/3m_pct** | 1·3개월 시장 수익률 | 단기 모멘텀 |

대상 인덱스: KOSPI / KOSDAQ / S&P500 / NDX100. 본 시스템 `analyzer/regime.py`가 산출.

## 추가 — KRX 시장폭

`analyzer/regime.py`는 KRX의 **시장폭(Market Breadth)**도 추가 산출:
- 20일간 상승 종목 비율
- 50% 이상 = 건강한 상승, 30% 미만 = 약세 시그널

## 4가지 레짐 시나리오

### 시나리오 A: Risk-On (강세 확장)
- above_200ma = True
- pct_from_ma200 = +5~+15%
- vol_regime = low/mid
- drawdown < -8%
- → **공격적 포지셔닝**: 모멘텀·성장주·소형주 비중 확대

### 시나리오 B: Late-Cycle (피크 부근)
- above_200ma = True
- pct_from_ma200 = +20%+
- vol_regime = mid → high 전환
- → **방어 전환**: 우량주·배당주·현금 비중 확대. 단기 트레일링 스탑.

### 시나리오 C: Bear (약세 진행)
- above_200ma = False
- pct_from_ma200 = -10%~-20%
- vol_regime = high
- drawdown -20%+
- → **현금·헤지 우선**: 새 매수 유보, 기존 보유 손절 점검.

### 시나리오 D: Recovery (반등 초기)
- above_200ma = False (아직 아래)
- pct_from_ma200 = -5%~-10% (회복 중)
- return_1m_pct = +10%+
- vol_regime = high → mid
- → **신중한 진입**: contrarian/undervalued 종목 우선, 사이즈 작게.

## AI가 레짐을 어떻게 사용하는가

본 시스템 Stage 1(테마 발굴) 프롬프트에 `{market_regime_section}`이 자동 주입된다 → AI는 *레짐에 맞춰 테마 신뢰도·리스크 톤*을 조정.

예:
- Risk-On 레짐: 신규 테마 발굴에 더 적극적, conviction 점수 상향
- Bear 레짐: 신규 테마 자제, 방어 섹터 우선, conviction 점수 하향

## 사용자가 보는 법

`analysis_sessions.market_regime` JSONB는 **세션 상세 페이지**에 노출(예정). 직접 보는 방법:

```sql
SELECT analysis_date, market_regime
FROM analysis_sessions
ORDER BY analysis_date DESC
LIMIT 5;
```

또는 본 시스템 `dashboard` 페이지의 레짐 위젯(있는 경우) 참조.

## 한계

- **인덱스 레벨 진단**: 개별 종목·섹터의 미세 차이는 반영 안 함
- **후행성**: 200MA·52주 고점 모두 후행 지표 → 급변 국면에서 1~2주 늦음
- **시점 의존**: 레짐 진단은 *분석 시점* 스냅샷. 매일 변할 수 있음.

> 레짐은 *정답*이 아니라 *맥락*이다. 같은 추천이라도 레짐에 따라 받아들이는 무게가 달라야 한다.""",
        "examples": json.dumps([
            {"title": "2024년 KOSPI 약세장 진입", "description": "2024.07 KOSPI above_200ma = False 전환, drawdown_from_52w_high -15% 진입. 본 시스템은 이 시점부터 신규 테마 추천 conviction을 일괄 하향 + contrarian/undervalued 비중 증가. 8월 폭락(-12%) 이전에 방어 자세 전환.", "period": "2024.07~2024.08", "lesson": "레짐 변화는 폭락 직전의 사전 경고가 될 수 있다 — 무시하면 비싸게 든다"}
        ]),
    },
    {
        "category": "tools",
        "slug": "pre-market-briefing-guide",
        "title": "프리마켓 브리핑 활용법 — 미국 야간 → 한국 수혜 매핑",
        "summary": "본 시스템 v34에 도입된 프리마켓 브리핑이 무엇을 보여주고, 한국 시장 진입 결정에 어떻게 활용하는지 배웁니다.",
        "difficulty": "beginner",
        "sort_order": 62,
        "content": """## 매일 KST 06:30, 한국 장 시작 전에

본 시스템 v34부터 매일 KST 06:30 자동 실행되는 **프리마켓 브리핑**:

1. **미국 야간 OHLCV 집계** — S&P500·NASDAQ100 상승/하락 Top10·섹터별 등락
2. **Claude SDK 브리핑 생성** — 핵심 이슈·시그널·해석
3. **한국 수혜 매핑** — sector_norm 28버킷 공통키로 한국 종목 자동 매핑
4. **화이트리스트 검증** — `stock_universe`로 LLM hallucination 차단
5. **알림 자동 생성** — 워치리스트·구독 매칭 시 자동 알림

UI: `/pages/briefing` 페이지에서 매일 06:30 이후 확인 가능.

## 페이지 구성

### 1. 미국 야간 요약 (`us_summary` JSONB)
- 주요 인덱스 종가·등락률
- Top10 상승/하락 종목
- 섹터별 등락 분포
- 핵심 뉴스 헤드라인 (영문 → 한글)

### 2. AI 브리핑 (`briefing_data` JSONB)
- **시장 톤 진단**: Risk-On / Mixed / Risk-Off
- **핵심 이슈 3~5개**: 어젯밤 중요 이벤트
- **한국 시장 시사점**: 섹터별 영향
- **수혜 종목 후보**: 화이트리스트 검증된 한국 종목 매핑

### 3. 레짐 스냅샷 (`regime_snapshot` JSONB)
- 미국·한국 인덱스 레짐 (B2 레이어)
- 시장폭·변동성·드로다운

## 활용 워크플로우 — 정석

### 06:30 ~ 09:00 (장 시작 전)
1. 페이지 접속
2. **시장 톤** 먼저 확인 (Risk-On/Off 한 단어)
3. **핵심 이슈** 3개 훑기
4. **수혜 종목 후보**에서 본인 워치리스트와 교차 확인

### 09:00 ~ 09:30 (개장 직후)
- 워치리스트 매칭 종목의 시초가 모니터링
- 시초 갭 +3% 이상이면 추격 신중 → 09:30 이후 안정화 대기

### 09:30 이후
- 갭 메우기/확대 패턴 확인
- 매수 의사결정

## 알림 자동 생성

본 시스템은 브리핑 결과로 자동 알림 생성:

| 트리거 | 알림 종류 |
|--------|---------|
| 워치리스트 종목이 수혜 후보로 매핑 | 종목 알림 |
| 구독한 sector_norm 키워드 매칭 | 섹터 알림 |
| 본인 추천 이력 종목 출현 | 추천 이력 알림 |

→ `/pages/notifications`에서 확인.

## 주의사항

### 1. 미국 야간 = 한국 시장 수혜? 무조건 X
- **공통점이 강한 섹터** (반도체·바이오·전기차 부품)는 매핑 효과 큼
- **한국 고유 산업** (조선·철강 일부)은 미국 신호와 무관
- → AI가 "수혜"로 매핑해도 **한국 펀더멘털 따로 체크** 필수

### 2. 시초가 갭의 함정
- 미국 호재 → 한국 시초가 갭 상승 → 차익실현 매도 → 갭 메우기
- "갭 트레이딩"은 짧고 빠른 게임 — 초보자는 정상화 후 진입

### 3. 화이트리스트 한계
- `stock_universe`는 KOSPI+KOSDAQ+NASDAQ+NYSE 보통주 한정
- 우선주·ETF·신생 상장은 매핑 누락 가능

## 데이터 영속화

`pre_market_briefings` 테이블 (v34) — PK `briefing_date`로 매일 1건. 과거 브리핑은 `/pages/briefing?date=YYYY-MM-DD`로 조회 가능.

> 야간 미국 이벤트 → 06:30 브리핑 자동 생성 → 09:00 한국 장 시작. 30분의 시간차를 *준비 시간*으로 쓰는가, 아니면 *허비하는가*가 매일의 차이를 만든다.""",
        "examples": json.dumps([
            {"title": "2026.04 NVIDIA 실적 후 한국 매핑", "description": "NVIDIA 분기 실적 +15% 깜짝 상회 → 본 시스템 브리핑 06:30 발행 → HBM/메모리 섹터 수혜 매핑 → 워치리스트 SK하이닉스 보유 사용자에 자동 알림. 09:00 시초가 +4% 갭 상승 후 안정화 → 이후 추세 진행.", "period": "예시", "lesson": "야간 이벤트의 한국 매핑은 30분의 사전 준비를 가능케 한다"}
        ]),
    },
]

# v35 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V35_SLUGS: set[str] = {"factor-six-axes", "market-regime-reading", "pre-market-briefing-guide"}
