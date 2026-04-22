"""practical 카테고리 — 실전 활용 교육 토픽."""
import json

TOPICS: list[dict] = [
    {
        "category": "practical", "slug": "reading-proposal-cards",
        "title": "제안 카드 200% 활용법",
        "summary": "이 앱의 투자 제안 카드에 담긴 정보를 제대로 읽고 활용하는 방법을 배웁니다.",
        "difficulty": "beginner", "sort_order": 40,
        "content": """## 제안 카드의 구성 요소

### 1. 기본 정보
- **asset_name / ticker**: 종목명과 코드
- **action**: `BUY` / `SELL` / `HOLD` / `WATCH` — 추천 행동
- **conviction**: `HIGH` / `MEDIUM` / `LOW` — AI의 확신도

### 2. 가격 정보
- **current_price**: 분석 시점의 실시간 가격 (yfinance/pykrx 출처)
- **target_price_low / high**: AI 추정 목표가 범위
- **upside_pct**: 상승 여력 (%) = (목표가_하한 - 현재가) / 현재가

⚠️ Stage 1의 목표가는 AI 추정치로 참고용. Stage 2 분석된 종목만 신뢰도 높음.

### 3. 분류·맥락
- **sector**: 섹터 분류
- **discovery_type**: 발견 유형
  - `consensus`: 시장 컨센서스와 일치
  - `early_signal`: 초기 신호 포착
  - `contrarian`: 역발상 관점
  - `deep_value`: 깊은 가치 발굴

### 4. 모멘텀 데이터
- **return_1m/3m/6m/1y_pct**: 기간별 과거 수익률
- **price_momentum_check**: `overheated` / `neutral` / `undervalued`

### 5. 리스크·근거
- **rationale**: 추천 이유 (가장 중요!)
- **risk_factors**: 주요 리스크

### 활용 팁

| 투자자 유형 | 집중할 필드 |
|------------|------------|
| 보수적 | conviction HIGH + action BUY + 배당 종목 |
| 성장 추구 | early_signal + 높은 upside_pct |
| 역발상 | contrarian + deep_value |
| 단기 트레이딩 | momentum 데이터 + 기술적 분석 조합 |""",
        "examples": json.dumps([
            {"title": "제안 카드 해석 실전", "description": "conviction: HIGH, action: BUY, discovery_type: early_signal, upside_pct: 35%, return_1m: +5%, return_3m: -2%. 해석: AI가 높은 확신으로 매수 추천. 아직 시장에 덜 반영된 초기 신호(early_signal). 3개월 수익률이 마이너스인데 upside가 35%면 아직 저평가 구간일 가능성.", "period": "활용 예시", "lesson": "discovery_type과 모멘텀 데이터를 조합하면 '이미 오른 종목'과 '아직 기회가 있는 종목'을 구분할 수 있다"}
        ]),
    },
    {
        "category": "practical", "slug": "using-track-record",
        "title": "트랙레코드로 AI 분석 신뢰도 검증하기",
        "summary": "이 앱의 과거 추천 성과(트랙레코드)를 통해 AI 분석의 강점과 한계를 파악하는 법을 배웁니다.",
        "difficulty": "beginner", "sort_order": 41,
        "content": """## 트랙레코드란?

과거 AI가 추천한 종목이 **실제로 얼마나 올랐는지/내렸는지** 추적한 성과 기록.

### 확인할 핵심 지표

| 지표 | 의미 | 좋은 수준 |
|------|------|-----------|
| **적중률** | 추천 중 실제 수익 낸 비율 | 55%+ |
| **평균 수익률** | 전체 추천의 평균 수익 | 시장 수익률 초과 |
| **최대 손실** | 가장 크게 실패한 추천 | -20% 이내 |
| **수익/손실 비율** | 평균 수익 ÷ 평균 손실 | 1.5:1 이상 |

### 올바른 해석법

1. **전체 기간으로 판단**: 한두 건의 대박/쪽박에 흔들리지 말 것
2. **시장 대비 비교**: 시장이 +20%일 때 +15%면 사실상 부진
3. **카테고리별 확인**: sector별, conviction별로 성과 차이 확인
4. **시장 국면별**: 상승장/하락장/횡보장에서 각각의 성과

### AI 분석의 강점과 한계

**강점:**
- 감정 없는 객관적 분석
- 대량의 뉴스·데이터 동시 처리
- 일관된 분석 프레임워크

**한계:**
- 돌발 이벤트(지정학, 자연재해) 예측 불가
- 과거 패턴에 기반 → 전례 없는 상황에 취약
- 시장 심리·수급 변화 실시간 반영 어려움

> AI 분석은 **의사결정 보조 도구**이지, 맹신하는 오라클이 아닙니다.""",
        "examples": json.dumps([
            {"title": "AI 투자 성과 사례 (참고)", "description": "AI 기반 헤지펀드 르네상스 테크놀로지의 메달리온 펀드: 30년간 연평균 66% 수익. 하지만 이는 극도로 정교한 퀀트 모델 + 초단타 매매. 일반적인 AI 분석은 이 수준을 기대하기 어렵지만, 인간의 편향을 줄이는 것만으로도 큰 가치.", "period": "1988~2018", "lesson": "AI의 가치는 '완벽한 예측'이 아니라 '인간의 감정적 실수를 줄여주는 것'에 있다"}
        ]),
    },
]

# v24 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V24_SLUGS: set[str] = set()
