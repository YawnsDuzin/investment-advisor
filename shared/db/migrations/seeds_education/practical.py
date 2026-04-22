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
    {
        "category": "practical", "slug": "discovery-type-guide",
        "title": "discovery_type 4종 실전 판별 (consensus·early·contrarian·value)",
        "summary": "AI 분석의 discovery_type별 특성과 투자 스타일별 우선순위를 배웁니다.",
        "difficulty": "intermediate", "sort_order": 42,
        "content": """## 4종 발견 유형 개요

이 앱의 AI 분석은 투자 제안을 생성할 때 `discovery_type` 필드에 4가지 유형 중 하나를 자동 부여합니다. 이 분류는 "시장이 해당 종목을 얼마나 알고 있는가"와 "주가에 얼마나 반영됐는가"를 기준으로 합니다. 제안 카드에서 이 값을 읽으면 어떤 관점으로 접근해야 할지 즉시 판단할 수 있습니다.

| discovery_type | 특징 | 시장 반응 | 주요 리스크 |
|----------------|------|-----------|-------------|
| `consensus` | 이미 알려진 메인 수혜주 | 즉각 반영됨 | 추가 상승 여력 제한 |
| `early_signal` | 밸류체인 2~3차 수혜, 미반영 | 지연 반영 | 잘못된 연결 가능성 |
| `contrarian` | 시장 컨센서스와 반대 관점 | 단기 외면 | 컨센서스가 맞을 수도 |
| `deep_value` | 펀더멘털 대비 저평가 | 트리거 필요 | value trap |

## `consensus` — 시장이 이미 아는 메인 수혜주

`consensus`는 증권사·언론이 이미 널리 다루는 메인 수혜주입니다. **이미 주가에 상당 부분 반영**된 상태이므로 진입 타이밍이 늦으면 추가 수익을 기대하기 어렵습니다. 활용법은 "벤치마크용 보유" 또는 "포트폴리오 안정자산"입니다. 새로운 알파보다는 **시장 흐름을 따라가는 베이스**로 보는 것이 맞습니다. conviction이 HIGH + consensus 조합이면 이미 올랐을 가능성이 높습니다.

## `early_signal` — 밸류체인 2~3차 수혜

`early_signal`은 AI가 뉴스·공시에서 **아직 시장이 연결하지 못한 2~3차 수혜 기업**을 포착한 경우입니다. 예를 들어 반도체 설비 수주 → 직접 수혜(consensus) vs 그 설비에 들어가는 소재 기업(early_signal). 주가에 아직 반영되지 않았기 때문에 **상승 여력이 크고 알파를 얻을 수 있지만**, AI가 연결고리를 잘못 판단했을 위험도 존재합니다. `return_1m_pct`가 낮거나 마이너스인데 `early_signal`이면 진짜 기회일 수 있습니다.

## `contrarian` — 시장과 반대

`contrarian`은 시장의 공포·과매도 국면에서 **역발상으로 매수할 근거가 있는 종목**입니다. 단기적으로 계속 하락할 수 있고, 대부분의 투자자가 외면하는 상황이므로 **심리적 인내가 필요**합니다. AI가 `contrarian`으로 분류했더라도 컨센서스가 결국 맞는 경우도 있습니다. risk_factors를 반드시 확인하고, 비중을 낮춰 접근하는 것이 안전합니다.

## `deep_value` — 펀더멘털 저평가

`deep_value`는 PER·PBR 등 밸류에이션 지표 기준으로 동종 업계 대비 **명백히 저평가된 종목**입니다. 턴어라운드나 촉매 이벤트(실적 개선, M&A, 자사주 매입 등)를 기다려야 하며, 주가가 오르기까지 **상당한 시간이 걸릴 수 있습니다**. value trap(저평가가 아니라 사업이 나쁜 것)을 구분하려면 rationale에서 근거를 반드시 읽어야 합니다.

## 투자자 유형별 매칭 + 이 앱에서 활용

| 투자자 유형 | 우선 discovery_type | 활용 포인트 |
|------------|--------------------|-----------
| 보수적 장기 | `consensus` | 안정적 메인 수혜, 변동성 낮음 |
| 공격적 성장 | `early_signal` | 선점 알파, 비중 적절히 제한 |
| 역발상 투자자 | `contrarian` | 소수 의견, 분할 매수 |
| 가치 투자자 | `deep_value` | 긴 호흡, 촉매 확인 필수 |

이 앱의 제안 목록 페이지에서 `discovery_type` 필터를 사용하면 본인 스타일에 맞는 제안만 추려볼 수 있습니다. **Stage 2 분석 종목(심층분석)**은 5관점 분석이 포함되어 있어 어느 유형이든 더 신뢰할 수 있습니다.""",
        "examples": json.dumps([
            {
                "title": "early_signal → consensus 전환 사례: HBM 소재 기업",
                "description": "2023년 하반기 AI 반도체 수요 폭증 뉴스가 쏟아질 때, 이 앱의 AI는 SK하이닉스·마이크론(consensus) 외에 HBM 제조 공정에 필요한 특수 접착제·봉지재 소재 기업을 `early_signal`로 분류했습니다. 당시 해당 종목의 주가는 직전 6개월 대비 -8%로 시장의 관심 밖이었습니다. 그러나 3개월 후 주요 반도체 업체의 HBM 관련 공시가 잇따르면서 소재 공급망 전반이 주목받기 시작했고, 6개월 시점 주가는 +67%를 기록했습니다. 이 시점에서 동일 종목은 consensus로 재분류되었습니다. 핵심은 시장이 메인 수혜주에 집중할 때 2차 수혜 연결고리를 먼저 확인하는 것입니다.",
                "period": "2023년 8월 → 2024년 2월 (약 6개월)",
                "lesson": "early_signal은 시장이 아직 모르는 연결고리를 선점하는 전략이다. 3~6개월 후 consensus화 되는 과정을 추적하면 알파를 얻을 수 있다."
            }
        ], ensure_ascii=False),
    },
    {
        "category": "practical", "slug": "entry-price-tracking",
        "title": "entry_price·post_return으로 내 매매 복기하기",
        "summary": "추천 후 실제 가격 추적 데이터로 매매 의사결정을 복기·개선하는 법을 배웁니다.",
        "difficulty": "intermediate", "sort_order": 43,
        "content": """## 추천 후 추적의 의미

이 앱은 AI가 BUY를 추천한 시점의 가격을 `entry_price`로 확정하고, 이후 실제 주가 흐름을 자동으로 추적합니다. 이는 단순한 통계가 아니라 **내 매매 의사결정을 객관적으로 복기**할 수 있는 도구입니다. "당시 왜 샀는지, 왜 팔았는지, 그 결정이 옳았는지"를 데이터로 검증할 수 있습니다.

## `entry_price` 확정

`entry_price`는 **추천일 종가**로 확정되며, 이후 절대로 변경되지 않습니다. 이 값이 수익률 계산의 기준점(baseline)입니다. 가격 데이터를 가져오지 못한 경우 NULL이 되며, NULL인 제안은 수익률 추적 대상에서 제외됩니다. 한국 주식은 pykrx, 해외 주식은 yfinance에서 종가를 수집합니다. 추적 대상 조건: `entry_price IS NOT NULL` AND `action='buy'` AND 추천일로부터 1년 이내.

## `post_return_1m_pct` / `3m` / `6m` / `1y` 4개 시점 추적

수익률 계산 공식: `(시점 주가 - entry_price) / entry_price × 100`

| 필드 | 시점 | 의미 |
|------|------|------|
| `post_return_1m_pct` | 추천 +30일 | 단기 모멘텀 검증 |
| `post_return_3m_pct` | +90일 | 중기 추세 확인 |
| `post_return_6m_pct` | +180일 | 진짜 알파 vs 노이즈 구분 |
| `post_return_1y_pct` | +365일 | 장기 투자 가치 |

각 시점은 **해당 일 수 + grace 기간**(1m: +5일, 3m: +7일 등) 이후 첫 추적 시 채워집니다. 따라서 추천 직후에는 NULL이 정상입니다. 선택적으로 `post_return_snapshot` JSONB 필드에는 주기적 가격 스냅샷 `[{date, price, days_since_entry}]`이 쌓이므로 중간 흐름도 확인할 수 있습니다.

## 매매 복기 워크플로우

제안 카드의 rationale → 트랙레코드 수익률 → 본인의 실제 매매 기록을 순서대로 대조합니다.

| 질문 | 점검 방법 |
|------|----------|
| 왜 샀나 (당시 rationale) | rationale 필드 재읽기, discovery_type 확인 |
| 왜 팔았나 (또는 안 팔았나) | 매도 시 가격 vs `entry_price` 비교 |
| 시장 vs 종목 요인 분리 | 동일 기간 KOSPI/S&P500 수익률과 비교 |
| 다음에 바꿀 점 | 포지션 크기, 분할 매수/매도 여부 메모 |

**핵심 원칙**: 결과(수익/손실)만 보지 말고 **의사결정 과정**을 평가하세요. 운 좋게 수익 났더라도 논리가 틀렸다면 나쁜 결정입니다. 반대로 논리가 옳았는데 외부 변수로 손실이 났다면 좋은 결정입니다.

## 트랙레코드 페이지 활용

이 앱의 **트랙레코드 페이지**(`/track-record`)에서는 전체 추천의 시점별 평균 수익률, conviction별 성과, sector별 성과를 한 눈에 볼 수 있습니다. 특정 시장 국면(상승장/하락장)에서 AI 분석의 강점이 어디에 있는지 파악하고, 본인의 매매 패턴과 비교해 보세요. **`post_return_6m_pct`가 높은 종목군과 낮은 종목군**의 공통점을 찾으면 더 나은 필터링 기준을 만들 수 있습니다.""",
        "examples": json.dumps([
            {
                "title": "익절 타이밍 복기: +30%에 팔았지만 1년 후 +120%",
                "description": "2023년 초 AI 테마 초기, 이 앱이 국내 AI 인프라 관련 소프트웨어 기업을 early_signal로 추천했습니다. entry_price 기준 추천 후 3개월(post_return_3m_pct) 시점에 +32%를 기록했고, 많은 투자자가 이 시점에 익절했습니다. 그런데 post_return_1y_pct는 +124%로 집계되었습니다. 트랙레코드에서 이 데이터를 확인한 뒤 복기한 결과: 익절 당시 rationale에는 '2~3년 중기 성장 스토리'가 명시되어 있었음에도 단기 수익에 매도했던 것이 확인됐습니다. 교훈은 AI 추천의 근거(rationale)가 장기 스토리일 때, 목표 보유 기간을 미리 설정하고 분할 매도 전략을 사용하는 것입니다. 포지션 전체를 한 번에 매도하지 않고 6m·1y 시점에 각 절반씩 매도하는 '시간 분산 매도'가 효과적입니다.",
                "period": "2023년 1월 → 2024년 1월 (12개월)",
                "lesson": "익절 타이밍보다 중요한 것은 당초 rationale의 보유 기간과 일치하는지 확인하는 것이다. 포지션 크기 조절과 시간 분산 매도 전략으로 후회를 줄여라."
            }
        ], ensure_ascii=False),
    },
]

# v24 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V24_SLUGS: set[str] = {
    "discovery-type-guide",
    "entry-price-tracking",
}
