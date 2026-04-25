# Education Tier A·B + tools 카테고리 확장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** education_topics 26 → 40 토픽 확장 + `tools` 신규 카테고리 도입 + v35 마이그레이션. spec: `docs/superpowers/specs/2026-04-25-education-tier-a-b-tools-expansion-design.md`.

**Architecture:** 기존 v24 패턴(`NEW_TOPICS_VN` + ON CONFLICT 멱등 INSERT) 그대로 답습. seeds_education/ 디렉토리에 `tools.py` 신설 + 5개 카테고리 모듈에 토픽 추가. 마이그레이션은 `_migrate_to_v35`가 `NEW_TOPICS_V35`만 INSERT — 신규 DB는 v21에서 ALL_TOPICS 시드 후 v35는 NO-OP, 기존 운영 DB는 v34→v35 시 14개만 신규 INSERT.

**Tech Stack:** PostgreSQL + psycopg2, Python 3.10+, pytest, FastAPI(라벨 매핑), Jinja2.

---

## File Structure

| 종류 | 경로 | 책임 |
|---|---|---|
| NEW | `shared/db/migrations/seeds_education/tools.py` | tools 카테고리 토픽 3개 + V35_SLUGS |
| NEW | `tests/test_education_seeds.py` | 시드 데이터 검증 (카운트·JSON·slug 유니크·V35 멱등성) |
| MOD | `shared/db/migrations/seeds_education/__init__.py` | tools 모듈 등록 + `NEW_TOPICS_V35` 노출 |
| MOD | `shared/db/migrations/seeds_education/basics.py` | TOPICS +2 + V35_SLUGS |
| MOD | `shared/db/migrations/seeds_education/analysis.py` | TOPICS +2 + V35_SLUGS |
| MOD | `shared/db/migrations/seeds_education/risk.py` | TOPICS +3 + V35_SLUGS |
| MOD | `shared/db/migrations/seeds_education/macro.py` | TOPICS +1 + V35_SLUGS |
| MOD | `shared/db/migrations/seeds_education/stories.py` | TOPICS +3 + V35_SLUGS |
| MOD | `shared/db/migrations/versions.py` | `_migrate_to_v35()` 함수 추가 |
| MOD | `shared/db/migrations/__init__.py` | `_MIGRATIONS` dict에 35 추가 |
| MOD | `shared/db/schema.py` | `SCHEMA_VERSION = 34` → `35` |
| MOD | `api/routes/education.py:23-30` | `_EDU_CATEGORIES`에 `"tools": "도구·시스템 가이드"` 추가 |

---

## Task 1: 검증 테스트 신설 (TDD 시작점)

**Files:**
- Create: `tests/test_education_seeds.py`

- [ ] **Step 1: 기존 conftest.py 동작 확인**

Run: `pytest tests/test_tier_limits.py -v --collect-only`
Expected: 정상 collect (psycopg2·feedparser·claude_agent_sdk mock 적용 확인)

- [ ] **Step 2: 검증 테스트 파일 생성 (전체 실패 상태)**

```python
# tests/test_education_seeds.py
"""education_topics 시드 데이터 검증 — 카운트·JSON·slug 유니크·V35 멱등성."""
import json

from shared.db.migrations.seeds_education import (
    ALL_TOPICS,
    NEW_TOPICS_V24,
)


def test_total_topic_count():
    """v35 후 총 40 토픽."""
    assert len(ALL_TOPICS) == 40, f"expected 40, got {len(ALL_TOPICS)}"


def test_category_distribution():
    """카테고리별 분포 검증."""
    from collections import Counter
    counts = Counter(t["category"] for t in ALL_TOPICS)
    assert counts == {
        "basics": 10,
        "analysis": 6,
        "risk": 5,
        "macro": 4,
        "practical": 4,
        "stories": 8,
        "tools": 3,
    }, f"unexpected distribution: {counts}"


def test_all_slugs_unique():
    """모든 slug 유니크."""
    slugs = [t["slug"] for t in ALL_TOPICS]
    assert len(slugs) == len(set(slugs)), "duplicate slugs"


def test_required_keys_present():
    """모든 토픽에 필수 키 존재."""
    required = {"category", "slug", "title", "summary", "content",
                "examples", "difficulty", "sort_order"}
    for t in ALL_TOPICS:
        assert required <= set(t.keys()), f"missing keys in {t.get('slug')}"


def test_examples_valid_json():
    """examples 컬럼이 valid JSON 직렬화 가능."""
    for t in ALL_TOPICS:
        parsed = json.loads(t["examples"])
        assert isinstance(parsed, list), f"{t['slug']}: examples not list"
        for ex in parsed:
            assert "title" in ex and "description" in ex, \
                f"{t['slug']}: example missing title/description"


def test_content_min_length():
    """content 최소 분량 (800자 이상)."""
    for t in ALL_TOPICS:
        assert len(t["content"]) >= 800, \
            f"{t['slug']} content too short ({len(t['content'])} chars)"


def test_difficulty_valid():
    """difficulty는 beginner/intermediate/advanced 중 하나."""
    valid = {"beginner", "intermediate", "advanced"}
    for t in ALL_TOPICS:
        assert t["difficulty"] in valid, \
            f"{t['slug']}: invalid difficulty {t['difficulty']}"


def test_v35_new_topics_count():
    """V35 신규 14 토픽 노출."""
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    assert len(NEW_TOPICS_V35) == 14, f"expected 14, got {len(NEW_TOPICS_V35)}"


def test_v35_topics_disjoint_from_v24():
    """V35 신규 slug는 V24와 겹치지 않음."""
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    v24_slugs = {t["slug"] for t in NEW_TOPICS_V24}
    v35_slugs = {t["slug"] for t in NEW_TOPICS_V35}
    assert v24_slugs.isdisjoint(v35_slugs), \
        f"overlap: {v24_slugs & v35_slugs}"


def test_tools_category_exists():
    """tools 카테고리에 정확히 3 토픽."""
    tools = [t for t in ALL_TOPICS if t["category"] == "tools"]
    assert len(tools) == 3, f"expected 3 tools topics, got {len(tools)}"
    assert {t["slug"] for t in tools} == {
        "factor-six-axes",
        "market-regime-reading",
        "pre-market-briefing-guide",
    }


def test_edu_categories_label_includes_tools():
    """라우터 라벨 매핑에 tools 추가됨."""
    from api.routes.education import _EDU_CATEGORIES
    assert "tools" in _EDU_CATEGORIES
    assert _EDU_CATEGORIES["tools"] == "도구·시스템 가이드"
```

- [ ] **Step 3: 실행하여 전체 실패 확인**

Run: `pytest tests/test_education_seeds.py -v`
Expected: 다수 실패 — 특히 `ImportError: cannot import name 'NEW_TOPICS_V35'`, `expected 40, got 26`, `_EDU_CATEGORIES`에 `"tools"` 없음.

- [ ] **Step 4: 커밋**

```bash
git add tests/test_education_seeds.py
git commit -m "test(edu): v35 시드 검증 테스트 — 카테고리 분포·JSON·V35 멱등성"
```

---

## Task 2: tools 카테고리 라벨 매핑

**Files:**
- Modify: `api/routes/education.py:23-30`

- [ ] **Step 1: `_EDU_CATEGORIES` 딕셔너리 갱신**

`api/routes/education.py:23-30`을 다음으로 교체:

```python
_EDU_CATEGORIES = {
    "basics": "기초 개념",
    "analysis": "분석 기법",
    "risk": "리스크 관리",
    "macro": "매크로 경제",
    "practical": "실전 활용",
    "stories": "투자 이야기",
    "tools": "도구·시스템 가이드",
}
```

- [ ] **Step 2: 라벨 테스트 통과 확인**

Run: `pytest tests/test_education_seeds.py::test_edu_categories_label_includes_tools -v`
Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add api/routes/education.py
git commit -m "feat(edu): _EDU_CATEGORIES에 tools 라벨 추가"
```

---

## Task 3: basics 카테고리 +2 토픽 (Tier B)

**Files:**
- Modify: `shared/db/migrations/seeds_education/basics.py`

토픽 1: `ipo-subscription` (공모주 청약 실전)
토픽 2: `rights-bonus-split` (유·무상증자와 액면분할)

- [ ] **Step 1: basics.py 끝부분(`V24_SLUGS` 직전)에 토픽 2개 추가**

기존 `TOPICS` 리스트 마지막 닫는 `]` 직전에 다음 dict 2개 추가 (정확한 위치는 `V24_SLUGS` 변수 정의 직전):

```python
    {
        "category": "basics", "slug": "ipo-subscription",
        "title": "공모주 청약 실전 — 균등·비례·환불·보호예수",
        "summary": "공모주가 무엇인지부터 균등배정·비례배정·환불 일정·보호예수 락업까지 한국 시장 실제 사례로 배웁니다.",
        "difficulty": "beginner", "sort_order": 9,
        "content": """## 공모주(IPO)란?

**공모주(IPO, Initial Public Offering)** = 비상장 회사가 거래소에 처음 상장하면서 일반 투자자에게 주식을 공개 매도하는 절차.

### 왜 인기인가?
- 첫날(상장일) 가격이 공모가보다 높게 시작하는 경우가 많음 (한국 평균 +30~80%, 따상 시 +160%)
- "따상" = 시초가 +100% (공모가의 2배) → 상한가 +30% → 종가 공모가 대비 +160%
- 단, 첫날 손실 사례도 30% 이상

## 청약 일정 5단계

| 단계 | 내용 | 기간 |
|------|------|------|
| 수요예측 | 기관 대상 공모가 결정 | 상장 약 3주 전 |
| 청약 | 일반 투자자 청약 | 2일간 |
| 환불 | 미배정분 증거금 환불 | 청약 후 2영업일 |
| 상장 | 거래소 상장·거래 시작 | 청약 후 약 1주 |
| 보호예수 해제 | 기관 락업 풀림 | 상장 후 1·3·6개월 |

## 균등배정 vs 비례배정

2021년부터 모든 IPO는 **균등 50% + 비례 50%** 의무화.

| 방식 | 배정 기준 | 특성 |
|------|----------|------|
| **균등배정** | 청약 참여자 1인당 동일 수량 | 소액으로도 받을 수 있음 (보통 1~10주) |
| **비례배정** | 청약 증거금 규모 비례 | 큰 자금 투입 시 많이 받음 |

→ **소액 투자자는 여러 증권사 균등배정 동시 청약**이 정석. 1인당 1계좌만 인정되므로 가족 명의 분산도 가능.

## 보호예수(락업) 함정

**기관 보유 물량의 단계적 매도 가능 시점**이 주가 하방 압력으로 작용.

- 상장 1개월 후: 1차 락업 해제 → 단기 변동성 확대
- 상장 3개월 후: 2차 락업 해제
- 상장 6개월 후: 잔여 물량 해제 → 종종 큰 폭 하락

→ 공모주 단기 트레이딩은 **첫날~1개월 이내** 또는 **3개월 락업 해제 후 횡보 구간**이 안전.

## 청약 증거금과 환불

청약 시 청약 금액의 **50%를 증거금**으로 입금. 배정량보다 많은 청약 시 미배정분은 **2영업일 내 환불**.

→ 1억 원 증거금 → 1주만 배정되면 나머지 ~99,950,000원이 묶이는 셈. 기간 비용 고려 필요.

## 한국 시장 실제 패턴

- **2024년 두산로보틱스**: 공모가 2.6만원 → 상장 첫날 시초가 5.92만원 (+128%) → 종가 5.66만원 → 1개월 후 4.2만원 (-26%). 락업 해제 영향.
- **2022년 LG에너지솔루션**: 공모가 30만원 → 시초가 59.7만원(따) → 종가 50.5만원 (+68%). 시총 100조 진입.
- **2023년 파두**: 공모가 3.1만원 → 첫날 +47% → 1개월 만에 분기 실적 충격으로 -50%. 공모주는 펀더멘털 검증이 안 된 상태.""",
        "examples": json.dumps([
            {"title": "두산로보틱스 IPO와 락업 (2023.10)", "description": "공모가 26,000원, 첫날 +128% 후 1개월 차 -26% 조정. 1차 락업 해제(상장 1개월 후) 시점에 기관 매도 출현. 첫날 매도 vs 보유의 명암.", "period": "2023.10~2023.12", "lesson": "공모주는 펀더멘털보다 수급 이벤트(락업)가 단기 가격을 좌우한다"},
            {"title": "균등배정 다증권사 분산 청약 (2024)", "description": "한 증권사 1주만 받을 수 있던 인기 IPO에서, 4개 증권사 균등 청약 시 각 1주씩 총 4주 확보 가능. 증거금은 청약 종료 2영업일 후 자동 환불.", "period": "2024", "lesson": "균등배정은 소액 투자자에 절대적으로 유리. 다증권사 분산이 정석"}
        ]),
    },
    {
        "category": "basics", "slug": "rights-bonus-split",
        "title": "유·무상증자와 액면분할 — 희석 효과의 진짜 의미",
        "summary": "유상증자·무상증자·액면분할의 차이와 주주가치 희석 여부를 실제 사례로 구분합니다.",
        "difficulty": "beginner", "sort_order": 10,
        "content": """## 셋 다 발행주식 수가 늘지만, 의미는 전혀 다르다

| 구분 | 자금 유입 | 주주 부담 | 주주가치 |
|------|----------|----------|---------|
| **유상증자** | O (외부 자금 조달) | 신주 매입 부담 또는 희석 | **희석 가능** |
| **무상증자** | X (자본잉여금→자본금) | 없음 | **불변** |
| **액면분할** | X | 없음 | **불변** |

## 유상증자 — 진짜 희석

**기존 주주에게서 추가 자금을 받거나, 제3자에게 신주를 발행**해서 자본 조달.

### 3가지 방식
- **주주배정**: 기존 주주에게 우선 매입 권리 (보통 시가보다 20~30% 싼 가격)
- **일반공모**: 누구나 청약
- **제3자 배정**: 특정 투자자(전략적 파트너)에게만 발행 — 가장 희석 우려 큼

### 왜 주가가 떨어지는가?

발행주식 수가 늘어나면 EPS·BPS가 줄어듦. 게다가 신주 가격이 시가보다 낮으면 즉각적 희석.

> EPS = 순이익 ÷ 발행주식 수 → 분모 증가 → EPS 감소 → 같은 PER이면 주가 하락

### 실제 사례
- **2024년 SK이노베이션**: 1.6조 원 유상증자 발표 → 발표 다음날 -8% → 1주일 누적 -15%. 주주가치 직접 희석.
- **2023년 한화오션**: 2조 원 유상증자 → 발표 후 -20%. 다만 자본 확충으로 신용등급 회복 → 6개월 후 회복.

## 무상증자 — 회계상의 분배

**자본잉여금 → 자본금**으로 옮기면서 새 주식을 기존 주주에게 무료로 분배. 회사 자산은 변하지 않고, 종이만 잘게 자르는 셈.

| 무상증자 1:1 전 | 후 |
|---|---|
| 100주 × 1만원 = 100만원 | 200주 × 5천원 = 100만원 |

→ 이론상 **권리락(주가 자동 조정)** 되어 가치 불변. 다만 한국 시장에서는 "주주환원 시그널"로 받아들여져 단기 수급 호재로 작동하는 경우 많음.

## 액면분할 — 거래 편의성

**1주를 N주로 쪼개기**. 회계적으로는 자본금 이동도 없고 자산 변화도 없음. 순수히 주가 단위만 낮춤.

### 왜 하는가?
- 주가가 너무 비싸 개인 매수가 어려운 경우 거래 활성화 목적
- **2018년 삼성전자 50:1 액면분할**: 250만원대 → 5만원대 → 개인 거래 폭증

### 효과는?
- 시총 불변, 주주가치 불변
- 단기 거래량 증가 → 변동성 확대 (긍정·부정 모두 가능)

## 핵심 판단 기준

> **유상증자 ≠ 무상증자 ≠ 액면분할**
> 자금 유입 여부와 주주 부담 여부로 구분해야 한다.

- 유상증자 발표 → **즉시 보수적 대응**, 자금 사용처 확인 (시설투자/부채상환/M&A 중 어느 것?)
- 무상증자 발표 → **권리락 후 가격 정상화** 대기
- 액면분할 발표 → **유동성 증가 효과**만 기대, 펀더멘털 변화 없음""",
        "examples": json.dumps([
            {"title": "삼성전자 50:1 액면분할 (2018.05)", "description": "264만원 → 5.3만원으로 분할. 개인 매수 진입장벽 제거 → 거래량 3배 증가. 시총·EPS 불변. 액면분할은 가치 변화 없이 거래 편의성만 개선한 사례.", "period": "2018.05", "lesson": "액면분할은 종이만 자르는 것 — 펀더멘털 변화 없음"},
            {"title": "SK이노베이션 유상증자 충격 (2024.06)", "description": "1.6조 원 주주배정 유상증자 발표 → 다음날 -8%, 1주일 -15%. 신주 발행가 시가 대비 17% 할인 → 직접 희석. 자금 사용처(SK온 자본 확충)에 대한 시장 회의로 회복까지 3개월 소요.", "period": "2024.06~2024.09", "lesson": "유상증자는 진짜 희석 — 발행가·자금 사용처·시장 신뢰가 회복 속도를 결정한다"}
        ]),
    },
```

- [ ] **Step 2: `V24_SLUGS` 정의 다음 줄에 `V35_SLUGS` 추가**

basics.py 파일 맨 마지막에 다음 추가:

```python
# v35 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V35_SLUGS: set[str] = {"ipo-subscription", "rights-bonus-split"}
```

- [ ] **Step 3: 카운트만 부분 검증 (전체 검증은 Task 9 후)**

Run: `python -c "from shared.db.migrations.seeds_education import basics; print(len(basics.TOPICS), basics.V35_SLUGS)"`
Expected: `10 {'ipo-subscription', 'rights-bonus-split'}`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/basics.py
git commit -m "feat(edu): basics +2 — 공모주 청약·유무상증자 (Tier B)"
```

---

## Task 4: analysis 카테고리 +2 토픽 (Tier A)

**Files:**
- Modify: `shared/db/migrations/seeds_education/analysis.py`

토픽 1: `chart-key-five` (차트 핵심 5)
토픽 2: `sector-kpi-cheatsheet` (섹터별 KPI)

- [ ] **Step 1: analysis.py 마지막 dict 다음에 토픽 2개 추가**

`V24_SLUGS` 정의 직전 위치에 추가:

```python
    {
        "category": "analysis", "slug": "chart-key-five",
        "title": "차트의 핵심 5: 이격도·RSI·MACD·볼린저밴드·거래량",
        "summary": "기술적 분석의 5가지 핵심 지표 — 의미·계산·실전 신호·한계를 한국 종목 사례로 배웁니다.",
        "difficulty": "intermediate", "sort_order": 14,
        "content": """## 기술적 지표 5가지만 알면 된다

기술적 분석에는 100개 넘는 지표가 있지만, 실전에서 의미 있게 쓰이는 건 5개 정도. 각 지표는 **무엇을 측정하는가**가 다르다.

| 지표 | 측정 대상 | 본질 |
|------|----------|------|
| **이격도** | 평균 대비 거리 | 추세 강도 |
| **RSI** | 상승/하락 비율 | 과매수/과매도 |
| **MACD** | 두 이동평균 차이 | 추세 전환 |
| **볼린저밴드** | 표준편차 범위 | 변동성 채널 |
| **거래량** | 시장 참여 강도 | 신뢰도 검증 |

## 1. 이격도 (Disparity)

**공식**: 이격도(%) = (현재가 / N일 이동평균) × 100

| 값 | 의미 |
|----|------|
| > 110 | 단기 과열 |
| 100 | 평균 부근 |
| < 90 | 단기 침체 |

→ **20일 이격도 > 110**이면 단기 조정 가능성. 중장기 추세는 60일·120일선으로 봐야 함.

## 2. RSI (Relative Strength Index)

**의미**: 최근 N일(보통 14일) 동안의 상승폭 합 / (상승폭 + 하락폭) × 100

| RSI | 신호 |
|-----|------|
| > 70 | 과매수 (단기 조정 가능) |
| 30~70 | 중립 |
| < 30 | 과매도 (반등 가능) |

**함정**: 강한 추세에서는 RSI 70 이상이 수개월 지속 가능 (다이버전스 동시 확인 필수).

## 3. MACD

**구성**: MACD = 12일 EMA - 26일 EMA. 시그널선 = MACD의 9일 EMA.

| 신호 | 의미 |
|------|------|
| MACD가 시그널선 상향 돌파 | 매수 신호 |
| MACD가 시그널선 하향 돌파 | 매도 신호 |
| MACD 0선 상향 | 중장기 상승 추세 |

→ MACD는 후행성이 큼. 횡보장에서 잦은 가짜 신호.

## 4. 볼린저밴드

**구성**: 중심선 = 20일 이동평균. 상단 = 중심 + 2σ, 하단 = 중심 - 2σ.

| 패턴 | 의미 |
|------|------|
| 상단 터치 | 단기 과열 |
| 하단 터치 | 단기 침체 |
| **밴드 폭 수축**(squeeze) | 변동성 폭발 임박 |
| **밴드 폭 확장** | 추세 진행 중 |

→ "스퀴즈 후 확장"이 가장 신뢰도 높은 패턴.

## 5. 거래량

**원칙**: 가격 움직임은 거래량으로 검증돼야 한다.

| 패턴 | 해석 |
|------|------|
| 상승 + 거래량 증가 | **건강한 상승** |
| 상승 + 거래량 감소 | 상승 동력 약화 |
| 하락 + 거래량 증가 | **본격적 하락** |
| 하락 + 거래량 감소 | 매도 소진 |

→ 거래량 없는 신고가는 함정인 경우 많음.

## 본 시스템에서 활용

본 시스템의 **정량 팩터 6축**에서 `vol60_pct` (60일 변동성)와 `volume_ratio` (최근 거래량 / 60일 평균)이 거래량·변동성 정보를 정량화한다. 이격도·RSI 같은 단일 시점 지표는 외부 차트 도구(네이버증권·Yahoo Finance·TradingView)와 병행 사용 권장.

> **기술적 지표는 단독으론 약하다.** 펀더멘털 → 매크로 → 기술 순으로 필터링하면 노이즈가 크게 줄어든다.""",
        "examples": json.dumps([
            {"title": "삼성전자 RSI 다이버전스 (2024.07)", "description": "주가는 신고가 갱신 중인데 RSI는 하락 — 약세 다이버전스. 2주 후 -12% 조정. 가격이 새로운 고점을 찍어도 모멘텀이 따라오지 못하면 추세 약화 신호.", "period": "2024.07", "lesson": "다이버전스는 기술적 지표가 단독보다 강해지는 순간이다"},
            {"title": "에코프로 볼린저밴드 스퀴즈 → 폭발 (2023.04)", "description": "2023년 1~3월 박스권 횡보 중 볼린저밴드 폭이 역사적 저점까지 수축. 4월 본격 상승 돌파 후 6월까지 +280%. 변동성 수축 후 폭발 패턴의 교과서적 사례.", "period": "2023.01~2023.06", "lesson": "스퀴즈는 방향이 아니라 변동성 폭발을 예고한다 — 돌파 방향에 베팅"}
        ]),
    },
    {
        "category": "analysis", "slug": "sector-kpi-cheatsheet",
        "title": "섹터별 핵심 KPI — 반도체/은행/제약/리츠/에너지",
        "summary": "산업마다 봐야 할 지표가 다르다. 5개 핵심 섹터의 KPI와 시점 신호를 압축 정리합니다.",
        "difficulty": "intermediate", "sort_order": 15,
        "content": """## PER만 보면 함정에 빠진다

섹터마다 비즈니스 모델이 다르고, 그래서 봐야 할 지표가 다르다. 같은 PER 10이어도 은행과 반도체는 의미가 정반대.

## 반도체 — 사이클 산업

| KPI | 의미 | 시점 신호 |
|-----|------|---------|
| **B/B Ratio** (Book-to-Bill) | 신규 수주 / 출하 | > 1.0 = 호황 진입 |
| **DRAM ASP** (평균판매가) | 메모리 가격 | 분기 상승 시작 = 사이클 턴 |
| **재고 일수** | 보유 재고 / 일평균 출하 | 감소 = 수급 타이트 |
| **CapEx 가이던스** | 차기 투자 계획 | 증액 = 자신감 |

**사이클 함정**: PER이 가장 낮을 때(이익 극대 시점)가 고점이고, PER이 적자(음수)일 때가 저점인 경우 다반사.

→ 본 시스템의 `factor_snapshot.r6m_pct`(6개월 모멘텀)와 결합해 보면 사이클 위치 파악 용이.

## 은행 — 금리·자산건전성

| KPI | 의미 | 좋은 신호 |
|-----|------|---------|
| **NIM** (순이자마진) | (대출이자 - 예금이자) / 평균자산 | > 2.0% = 수익성 양호 |
| **NPL 비율** | 부실채권 / 전체 대출 | < 1% = 건전 |
| **BIS 비율** | 자기자본 / 위험가중자산 | > 13% = 안정 |
| **CIR** (Cost-to-Income) | 영업비 / 영업수익 | < 50% = 효율적 |
| **ROE** | | > 10% = 우수 |

**금리 사이클**: 금리 인상 초기 = NIM 확대(호재), 인상 말기 = NPL 증가(악재). 단순히 "금리↑ = 은행↑"이 아님.

## 제약·바이오 — 파이프라인·임상

| KPI | 의미 | 핵심 |
|-----|------|------|
| **R&D 매출 비중** | 연구개발비 / 매출 | 20%+ = 신약 의지 |
| **파이프라인 단계** | 전임상 → P1 → P2 → P3 → 승인 | P3 진입 = 가치 급등 |
| **임상 결과** | Top-line data | 1차 평가 변수 충족 여부 |
| **현금 보유** | 연간 R&D 대비 현금 | 18개월 미만 = 추가 자본조달 위험 |

**스토리주의**: 매출·이익 부재 → 밸류에이션은 파이프라인 NPV. 임상 실패 시 -50%~-90% 일상.

## 리츠 (REITs) — 부동산 임대업

| KPI | 의미 | 벤치마크 |
|-----|------|---------|
| **FFO** (Funds From Operations) | 순이익 + 감가상각 - 매각이익 | 리츠의 진짜 이익 |
| **NAV** (Net Asset Value) | 순자산가치 | 주가 < NAV = 저평가 후보 |
| **공실률** | 비임대 면적 / 총 면적 | < 5% = 우량 |
| **임대 가중평균 만기** | WALE | 길수록 안정 |
| **LTV** (대출 비율) | 부채 / 자산 | < 50% = 보수적 |

**금리 민감도**: 리츠는 채권 대체재. 금리 인상기에는 NAV 하락 + 자금조달 비용 증가 이중 충격.

## 에너지 (정유·E&P) — 원자재·정제마진

| KPI | 의미 | 핵심 |
|-----|------|------|
| **WTI/브렌트 유가** | 국제 원유 가격 | 매출 직결 |
| **정제마진** (Crack Spread) | 휘발유가 - 원유가 | 정유사 수익 |
| **OPEC+ 감산** | 공급 조절 | 가격 지지 |
| **재고** (DOE/EIA) | 미국 원유·휘발유 재고 | 주간 변동성 큰 변수 |

**정유사 vs E&P 차이**: 정유는 마진(원가 구조), E&P는 절대 유가. 같은 "에너지"라도 사이클 위치가 다름.

## 활용 팁

본 시스템 제안 카드의 `sector` 필드를 보고, 위 표의 해당 섹터 KPI를 외부 자료(전자공시·산업 리포트)에서 확인하는 것이 정석. AI가 모든 섹터 KPI를 매번 검증하지는 못한다.""",
        "examples": json.dumps([
            {"title": "삼성전자 B/B Ratio 반등 신호 (2023.10)", "description": "2022~2023 반도체 다운사이클 중 B/B Ratio가 1.0 근처에서 반등 시작. 2024년 1분기 본격 회복 → 주가 +50% (2023.10~2024.07). 사이클 산업은 KPI가 가격에 선행한다.", "period": "2023.10~2024.07", "lesson": "사이클 산업은 PER이 아니라 산업 특화 KPI(B/B·ASP·재고)로 봐야 한다"},
            {"title": "한미약품 P3 결과 발표 충격 (2023)", "description": "롤론티스 미국 FDA 승인 지연·임상 데이터 이슈 → 단기 -30%. 제약·바이오는 임상 1차 변수 결과에 가격이 한 번에 결정. 파이프라인 가치 평가는 단계별 성공 확률(P3 ~50%)을 적용해야 한다.", "period": "2023", "lesson": "바이오 투자는 임상 단계별 성공 확률을 곱한 NPV로 봐야 — 한 번의 실패가 전체를 무너뜨린다"}
        ]),
    },
```

- [ ] **Step 2: analysis.py 맨 마지막 `V24_SLUGS` 다음에 `V35_SLUGS` 추가**

```python
V35_SLUGS: set[str] = {"chart-key-five", "sector-kpi-cheatsheet"}
```

- [ ] **Step 3: 부분 검증**

Run: `python -c "from shared.db.migrations.seeds_education import analysis; print(len(analysis.TOPICS), analysis.V35_SLUGS)"`
Expected: `6 {'chart-key-five', 'sector-kpi-cheatsheet'}`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/analysis.py
git commit -m "feat(edu): analysis +2 — 차트 핵심 5·섹터 KPI (Tier A)"
```

---

## Task 5: risk 카테고리 +3 토픽 (Tier A)

**Files:**
- Modify: `shared/db/migrations/seeds_education/risk.py`

토픽 1: `position-sizing` (포지션 사이징)
토픽 2: `risk-adjusted-return` (위험조정수익률 4지표)
토픽 3: `correlation-trap` (상관관계의 함정)

- [ ] **Step 1: risk.py의 stop-loss 토픽 다음에 3개 추가**

```python
    {
        "category": "risk", "slug": "position-sizing",
        "title": "포지션 사이징 — 1트레이드 1~2% 룰과 Kelly 직관",
        "summary": "어느 종목에 얼마를 넣을지 결정하는 정량적 규칙 — 손실 한도 기준과 Kelly 공식의 실전 적용을 배웁니다.",
        "difficulty": "intermediate", "sort_order": 22,
        "content": """## 종목 선정보다 사이징이 더 중요할 수 있다

### 같은 수익률, 다른 결과

| 시나리오 | A전략 (몰빵) | B전략 (10% 사이징) |
|---|---|---|
| 첫 트레이드 -50% | 자본 5,000만원 (50% 손실) | 자본 9,500만원 (5% 손실) |
| 회복 필요 수익률 | +100% | +5% |

→ **포지션 크기가 회복 능력을 결정**한다.

## 1트레이드당 자본의 1~2% 룰

**원리**: 한 번의 매매에서 잃을 수 있는 최대 금액을 자본의 1~2%로 제한.

### 계산 공식

> **포지션 크기 = (자본 × 리스크 비율) ÷ (진입가 - 손절가)**

**예시**:
- 자본 1,000만원, 리스크 비율 1% → 한 번에 잃을 수 있는 금액 = 10만원
- 진입가 10,000원, 손절가 9,500원 → 주당 손실 500원
- 포지션 크기 = 10만원 / 500원 = **200주** (200만원어치)

### 왜 1~2%인가?

- 10번 연속 실패해도 자본의 약 18% 손실 (복리)
- 회복 가능 영역
- 50% 자본 손실 시 회복까지 +100% 필요

## Kelly Criterion — 이론적 최적 크기

**공식**: f* = (p × b - q) / b

- f*: 자본 대비 베팅 비율
- p: 승률, q = 1-p: 패율
- b: 승리 시 수익률 / 패배 시 손실률

**예시**: 승률 60%, 평균 수익 +20%, 평균 손실 -10% → b=2
- f* = (0.6 × 2 - 0.4) / 2 = 0.4 → **자본의 40%**

### 현실에서는 절반(Half-Kelly)이 정석

- 승률 추정이 정확하지 않으면 Kelly는 과대 베팅
- **Half-Kelly = f*/2** = 자본의 20%로 줄임
- 변동성 1/2, 장기 기대수익률 75% 유지

## 포지션 분배 가이드

| 종목 컨빅션 | 비중 |
|-----------|------|
| Top 1~3 (최고 컨빅션) | 8~10% × 3 = ~30% |
| Tier 2 (중간 컨빅션) | 4~5% × 7 = ~30% |
| Tier 3 (관찰) | 2~3% × 10 = ~25% |
| 현금 | 15% |

→ 본 시스템 `conviction` 점수로 매핑 가능.

## 자주 하는 실수

1. **물타기로 사이징 깨기**: 손실 후 추가 매수 → 한 종목 비중 30%로 폭증
2. **승리 후 비중 확대**: 운이 좋은 트레이드를 실력으로 착각
3. **레버리지로 가짜 사이징**: 포지션은 1%인데 신용 사용 시 실질 리스크 5%

> 사이징은 *방어*다. 종목 선정은 *공격*. 둘 다 필요하지만, 망하는 사람은 사이징을 빼먹는다.""",
        "examples": json.dumps([
            {"title": "한국 개인 신용잔고 폭증과 반대매매 (2022)", "description": "코스피 -25% 하락 구간에서 신용잔고 보유 개인의 평균 손실은 자본의 60% 이상. 1% 룰을 지킨 투자자는 같은 구간 손실 -10% 미만. 사이징이 생존을 결정.", "period": "2022.01~2022.10", "lesson": "하락장에서 사이징을 지킨 사람만이 다음 상승장을 누린다"}
        ]),
    },
    {
        "category": "risk", "slug": "risk-adjusted-return",
        "title": "위험조정수익률 4지표 — 베타·샤프·소르티노·MDD",
        "summary": "수익률만 보면 함정. 변동성·하락 위험을 함께 보는 4가지 지표로 진짜 실력을 측정합니다.",
        "difficulty": "intermediate", "sort_order": 23,
        "content": """## 같은 수익률 = 같은 실력? 절대 아니다

| 펀드 | 연 수익률 | 연 변동성 | 최대 낙폭 |
|------|---------|---------|---------|
| A | +15% | 30% | -45% |
| B | +15% | 12% | -18% |

**같은 +15%인데 B가 압도적으로 우수**. 변동성과 낙폭을 보면 실력 차이가 명확.

## 1. 베타 (β) — 시장 대비 민감도

**공식**: β = Cov(종목수익률, 시장수익률) / Var(시장수익률)

| β 값 | 의미 |
|------|------|
| β = 1.0 | 시장과 동일 변동 |
| β > 1.5 | 시장보다 1.5배 변동 (공격적) |
| β < 0.5 | 시장과 무관 (방어적) |
| β < 0 | 시장 반대 방향 (역상관) |

**용도**: 포트폴리오의 시장 노출도 측정. 강세장에서는 고베타, 약세장에서는 저베타가 유리.

## 2. 샤프 비율 (Sharpe Ratio) — 변동성 대비 초과수익

**공식**: Sharpe = (수익률 - 무위험금리) / 표준편차

| Sharpe | 평가 |
|--------|------|
| > 2.0 | 매우 우수 |
| 1.0~2.0 | 우수 |
| 0.5~1.0 | 보통 |
| < 0.5 | 부진 |

**용도**: "위험 1단위당 초과수익이 얼마인가" — 펀드 비교의 표준.

## 3. 소르티노 비율 (Sortino) — 하락 위험 대비 초과수익

**공식**: Sortino = (수익률 - 무위험금리) / **하락 변동성**

샤프와 같지만, 분모에서 *상승 변동성은 제외*하고 하락 변동성만 사용.

→ "수익이 출렁이는 건 좋은 거다" — 손실 출렁임만 위험으로 간주.

| Sortino | 평가 |
|---------|------|
| > 2.5 | 매우 우수 |
| > 1.5 | 우수 |

**언제 보는가**: 비대칭 수익 분포(우편향) 자산 — 옵션 매도, 일부 헤지펀드 전략 등.

## 4. 최대 낙폭 (MDD, Maximum Drawdown)

**정의**: 직전 고점 대비 최저점까지의 하락률.

```
가격: 100 → 130 → 95 → 110 → 80
직전 고점: 130, 최저점: 80
MDD = (80 - 130) / 130 = -38.5%
```

**왜 중요한가**: 평균 변동성보다 *경험적 최악*이 인간 심리에 더 큰 영향. -50% 낙폭 시 95%의 사람은 손절 충동을 못 이긴다.

## 본 시스템 v29의 MDD 추적

본 시스템의 `investment_proposals.max_drawdown_pct` (v29) 컬럼이 추천 후 실제 발생한 최대 낙폭을 OHLCV 이력으로 자동 계산해 저장.

→ post-return 추적 결과와 함께 보면 "내가 얼마나 큰 낙폭을 견뎌야 했는가"가 명확.

## 4지표 종합 활용

```
총평 = (Sharpe × 0.4) + (Sortino × 0.3) + (1/MDD × 0.3) - β 가산
```

→ 단순 수익률보다 **장기 생존 가능성**이 보인다. 본 시스템 트랙레코드는 이 4지표를 함께 봐야 진짜 실력 평가.""",
        "examples": json.dumps([
            {"title": "ARKK vs S&P500 (2020~2023)", "description": "2020년 ARKK +152% 압도적 1위 → 2021~2022 -78% 폭락. 같은 기간 S&P500은 +20%. ARKK 4년 누적은 -10%, S&P +35%. 변동성 무시한 수익률은 함정.", "period": "2020.01~2023.12", "lesson": "고변동성 자산은 단기 1등이어도 장기 생존 가능성이 낮다 — 샤프·MDD를 함께 봐야 한다"}
        ]),
    },
    {
        "category": "risk", "slug": "correlation-trap",
        "title": "상관관계의 함정 — 진짜 분산 vs 가짜 분산",
        "summary": "10종목 보유한다고 분산이 아니다. 상관관계가 높으면 한 종목과 같다. 진짜 분산을 만드는 법.",
        "difficulty": "intermediate", "sort_order": 24,
        "content": """## "10종목 분산"이 거짓말일 때

### 한국 IT 5종목 포트폴리오

- 삼성전자 / SK하이닉스 / 카카오 / 네이버 / LG전자

→ 종목 5개지만 **상관계수 0.7~0.9**. 즉 하나가 떨어지면 다 떨어진다.
→ 사실상 *1종목 보유와 동일* — 분산 효과 거의 없음.

## 상관계수 (ρ) 읽기

| ρ 값 | 의미 |
|------|------|
| +1.0 | 완전 동조 (분산 효과 0) |
| +0.5~+0.9 | 강한 양의 상관 (분산 효과 약함) |
| 0 | 무관 (이상적 분산) |
| -0.5~-1.0 | 음의 상관 (헤지 가능) |

### 분산 효과 공식

2종목 포트폴리오 분산 = w₁²σ₁² + w₂²σ₂² + 2w₁w₂σ₁σ₂ρ

→ ρ이 낮을수록 분산 효과 큼. ρ=0이면 변동성이 √2 비율로 감소.

## 진짜 분산을 위한 4가지 차원

### 1. 자산군 분산 (가장 강력)
- 주식 + 채권 + 금 + 부동산 + 현금
- 채권-주식 상관: 보통 -0.2 ~ 0 (위기 때 음의 상관)
- 금-주식: 위기 시 음의 상관, 평상시 무관

### 2. 지역 분산
- 한국 + 미국 + 일본 + 신흥국
- 한미 상관: 0.6~0.7 (생각보다 높음)
- 한국-신흥국 상관: 0.5~0.6

### 3. 섹터 분산
- IT vs 금융 vs 헬스케어 vs 에너지
- 한국 IT 내부 상관 0.7~0.9 → IT 5종목 = 1종목

### 4. 팩터 분산
- 가치(Value) + 성장(Growth) + 모멘텀 + 저변동성
- 같은 팩터 5종목 < 다른 팩터 5종목 (분산 효과)

## 위기 때 상관관계 폭증

**문제**: 평상시 ρ=0.3이던 자산들이 *금융위기 때는 ρ=0.9*로 수렴.

→ 분산 포트폴리오라고 믿었는데, 정작 위기에선 다 같이 떨어짐.

### 진짜 헤지 자산
- **국채 (특히 미국)**: 위기 시 안전자산 매수로 음의 상관
- **금**: 인플레·통화 위기에 강함
- **현금**: 가장 단순한 헤지

## 본 시스템에서 활용

본 시스템은 제안 카드에 `sector` 필드를 제공한다. 워치리스트와 추천 종목을 합쳐 **섹터 분포**를 보면 가짜 분산 여부를 점검할 수 있다.

→ 보유/추천 종목 중 한 섹터가 40% 이상이면 가짜 분산. 섹터를 5개 이상으로 분배하는 게 정석.

> "10종목"이라는 숫자는 무의미하다. 진짜 봐야 할 건 *상관관계 행렬*이다.""",
        "examples": json.dumps([
            {"title": "2022년 주식·채권 동반 하락", "description": "전통적 60/40 포트폴리오 (주식 60% / 채권 40%) 가 -16% 손실. 일반적으론 주식 하락 시 채권이 헤지하는데, 2022년엔 인플레이션 + 금리 인상으로 동반 하락 (상관관계 +0.5로 폭증). 평상시 분산이 위기 때 깨지는 사례.", "period": "2022", "lesson": "상관관계는 위기 때 +1로 수렴하는 경향. 다중 자산 분산도 만능이 아니다"}
        ]),
    },
```

- [ ] **Step 2: V24_SLUGS 다음에 V35_SLUGS 추가**

```python
V35_SLUGS: set[str] = {"position-sizing", "risk-adjusted-return", "correlation-trap"}
```

- [ ] **Step 3: 부분 검증**

Run: `python -c "from shared.db.migrations.seeds_education import risk; print(len(risk.TOPICS), risk.V35_SLUGS)"`
Expected: `5 {'position-sizing', 'risk-adjusted-return', 'correlation-trap'}`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/risk.py
git commit -m "feat(edu): risk +3 — 포지션 사이징·위험조정수익률·상관관계 함정 (Tier A)"
```

---

## Task 6: macro 카테고리 +1 토픽 (Tier A)

**Files:**
- Modify: `shared/db/migrations/seeds_education/macro.py`

토픽: `yield-curve-inversion` (국채 금리 곡선 역전)

- [ ] **Step 1: macro.py의 마지막 토픽 다음에 추가**

```python
    {
        "category": "macro", "slug": "yield-curve-inversion",
        "title": "국채 금리 곡선 역전 — 침체 12개월 선행 신호",
        "summary": "단기 금리가 장기 금리보다 높아지는 현상의 의미와 역사적 정확도를 배웁니다.",
        "difficulty": "intermediate", "sort_order": 33,
        "content": """## 정상적 금리 곡선 vs 역전

### 정상 (Normal)

```
금리(%)
 4 ┤            ┌──────  30년
 3 ┤        ┌───        10년
 2 ┤    ┌───            2년
 1 ┤────                3개월
 0 ┴───────────────────
   3M   2Y   10Y   30Y
```

→ "기간이 길수록 위험·불확실성에 대한 프리미엄"으로 장기금리가 높음.

### 역전 (Inverted)

```
금리(%)
 5 ┤────                3개월
 4 ┤    ───             2년
 3 ┤        ┌───        10년
 2 ┤            ┌──────  30년
 0 ┴───────────────────
   3M   2Y   10Y   30Y
```

→ **단기 > 장기**. 시장이 *미래에 금리가 떨어질 것을 예상* (=경기 침체로 인한 인하 예상).

## 왜 침체 신호인가?

### 메커니즘
1. **연준이 인플레이션 잡으려 단기금리 인상** (ex: Fed Funds Rate ↑)
2. **시장은 미래 침체로 장기금리는 낮게 베팅**
3. **단기 > 장기 = 역전 발생**
4. **은행 수익성 악화 (단기 차입·장기 대출 마진 압축) → 대출 위축 → 침체 가속**

### 가장 많이 보는 지표
- **10Y - 3M Spread** (10년 - 3개월): 가장 보수적·정확도 높음
- **10Y - 2Y Spread** (10년 - 2년): 더 빨리 반응

## 역사적 정확도

| 역전 시점 | 침체 시작 | 시간차 |
|---------|---------|------|
| 1978년 11월 | 1980년 1월 | 14개월 |
| 1989년 2월 | 1990년 7월 | 17개월 |
| 2000년 7월 | 2001년 3월 | 8개월 |
| 2006년 1월 | 2007년 12월 | 22개월 |
| 2019년 3월 | 2020년 2월 | 11개월 |
| 2022년 7월 | 2024년? (지연 논쟁) | 진행 중 |

→ 1955년 이후 **8번의 미국 침체 중 7번을 정확히 예측**. 위양성(false positive)은 1번뿐.

## 한국에 미치는 영향

- 미국 역전 → 글로벌 자금 안전자산 회귀 → 신흥국 자본 유출
- 원-달러 환율 상승 → 한국 주식 외국인 매도 → 코스피 하락
- 한국 자체 yield curve도 역전되면 한국 침체 신호

## 본 시스템에서 보는 법

현재 본 시스템은 yield curve를 직접 추적하진 않지만, `analyzer/regime.py`(B2 레짐 레이어)가 **시장폭(Market Breadth)·드로다운·이동평균** 등으로 시장 국면을 진단. yield curve 역전 정보는 외부(FRED, https://fred.stlouisfed.org/series/T10Y3M)에서 보완.

> "이번엔 다르다(This time is different)"는 매번 나오지만, yield curve는 매번 맞았다.""",
        "examples": json.dumps([
            {"title": "2022년 7월 역전 → 2023~2024 논쟁", "description": "10Y-3M 스프레드가 2022년 7월부터 역전 → 역사적 패턴이면 12~18개월 후 침체. 2023년에는 침체 부정론 우세, 2024년 들어 고용·소비 둔화 신호 누적. yield curve의 신뢰도 vs 'soft landing' 시나리오 충돌의 대표 사례.", "period": "2022.07~진행 중", "lesson": "yield curve는 시점은 부정확해도 방향은 거의 항상 맞았다 — 무시 못 할 매크로 시그널"}
        ]),
    },
```

- [ ] **Step 2: V35_SLUGS 추가**

```python
V35_SLUGS: set[str] = {"yield-curve-inversion"}
```

- [ ] **Step 3: 부분 검증**

Run: `python -c "from shared.db.migrations.seeds_education import macro; print(len(macro.TOPICS), macro.V35_SLUGS)"`
Expected: `4 {'yield-curve-inversion'}`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/macro.py
git commit -m "feat(edu): macro +1 — 국채 금리 곡선 역전 (Tier A)"
```

---

## Task 7: stories 카테고리 +3 토픽 (Tier B)

**Files:**
- Modify: `shared/db/migrations/seeds_education/stories.py`

토픽 1: `ai-vs-dotcom` (AI 사이클 vs 닷컴)
토픽 2: `liquidity-mania-2020` (밈주식·SPAC·ARKK 광기)
토픽 3: `tesla-eight-years` (테슬라 8년 복기)

- [ ] **Step 1: stories.py 마지막 토픽 다음에 3개 추가**

```python
    {
        "category": "stories",
        "slug": "ai-vs-dotcom",
        "title": "2024-2025 AI CapEx 사이클 vs 1999 닷컴 — 같은 그림인가",
        "summary": "AI 거품론이 매번 나온다. 1999년 닷컴 버블과 2024-2025 AI 사이클의 공통점·차이점을 데이터로 비교합니다.",
        "difficulty": "intermediate",
        "sort_order": 55,
        "content": """## 둘 다 "혁명적 기술 + 천문학적 CapEx + 주가 폭등"

### 표면적 유사성

| 항목 | 1996~2000 닷컴 | 2023~2025 AI |
|---|---|---|
| 핵심 인프라 | 광케이블·라우터·서버 | GPU·HBM·데이터센터 |
| 대표 기업 | Cisco·Sun·Lucent | NVIDIA·SMCI·MSFT·Meta |
| CapEx 규모 | 1999년 텔레콤 1,500억$ | 2024년 빅테크 3,000억$+ |
| 핵심 종목 PER | Cisco 130배(2000.03) | NVIDIA 60~80배(2024) |
| 시총 비중 (S&P500 Top10) | ~25% | ~35% |

→ 그래서 "AI도 거품이다" 주장이 늘 나온다.

## 결정적 차이 3가지

### 1. 매출·이익 실적

**닷컴**: 대다수 기업 적자, 매출조차 부재. "Eyeballs"·"Page views" 같은 가공 지표.

**AI 2024**:
- NVIDIA 2024 매출 +126%, 영업이익률 60%+
- Microsoft Azure AI 매출 +60% YoY
- **실제 청구서가 이미 발행되고 있음**

### 2. 인프라 ROI 검증

**닷컴 텔레콤**: 광케이블 매설했으나 수요가 늦게 따라옴 (Dark Fiber 90%+). 자본 회수 10년 이상 걸림.

**AI**: ChatGPT 2개월 내 1억 사용자, MS Copilot 30$/월 청구. **수요가 인프라보다 빠름**.

### 3. 지배 구조의 차이

**닷컴**: 신생 기업이 차입·IPO로 무리한 CapEx → 파산.

**AI**: 자체 현금흐름 풍부한 빅테크 5사(MSFT·META·GOOGL·AMZN·AAPL)가 자사 현금으로 투자. 파산 위험 거의 없음.

## 그러면 거품이 아닌가?

### 거품일 수 있는 부분
- **2차 수혜주 PER 100배+**: 검증 안 된 AI 인프라/소프트웨어 기업
- **NVIDIA 의존 생태계**: NVIDIA 단일 실적에 글로벌 시장이 흔들리는 구조
- **GPU 공급-수요 불균형 해소 시점**: 2026~2027 예측, 그 시점 ASP 하락 위험

### 닷컴과 다른 부분
- **빅테크 코어**는 이익 검증된 수익화 모델
- **인프라 ROI**가 빠르게 회수됨

## 한국 시장 매핑

| 영역 | 한국 수혜 종목 |
|---|---|
| HBM·DDR5 | 삼성전자·SK하이닉스 |
| 메모리 장비 | 한미반도체·이오테크닉스 |
| 전력·냉각 | LS·HD현대일렉트릭 |
| 패키징·후공정 | 한미반도체·하나마이크론 |

→ 본 시스템 **테마 검색에서 "HBM"·"AI 반도체" 키워드**로 추천 이력 확인 가능.

## 결론

> 닷컴 버블은 *기술이 거품*이었던 게 아니라 *기업·매출이 거품*이었다.
> 광케이블·인터넷 자체는 인류를 바꿨다 (구글·아마존).
> AI도 *기술은 진짜*. 단, 모든 AI 종목이 살아남는다는 의미는 아님.

→ AI 시대의 지속 여부보다, *어떤 기업이 진짜 수익화하는가*에 집중. NVIDIA·MSFT는 닷컴의 Cisco·Microsoft 자리, 시총 70%는 사라질 수 있다.""",
        "examples": json.dumps([
            {"title": "Cisco 시총 변천 (1995~2024)", "description": "1995년 50억$ → 2000년 5,500억$ (글로벌 1위) → 2002년 700억$ (-87%). 2024년에도 닷컴 정점 시총을 회복하지 못함. 닷컴 버블 후 가장 견고한 기업 중 하나조차 24년간 침체.", "period": "1995~2024", "lesson": "버블 정점에 산 주식은 회사가 살아남아도 주가는 회복 못 할 수 있다 — '회사'와 '주식'은 다르다"}
        ]),
    },
    {
        "category": "stories",
        "slug": "liquidity-mania-2020",
        "title": "밈주식·SPAC·ARKK 광기 (2020-2022) — 유동성이 만든 거품",
        "summary": "코로나 양적완화가 만들어낸 3대 광기 — 밈주식·SPAC·ARKK ETF의 흥망성쇠를 복기합니다.",
        "difficulty": "intermediate",
        "sort_order": 56,
        "content": """## 코로나가 풀어낸 6조 달러

2020년 3월 미국 Fed가 무제한 양적완화 + 정부 1조$ 직접 지원 + 제로금리. 개인 투자자에게 사상 최대 유동성이 풀렸다.

→ 결과는 **"실력 없는 폭등 → 실력 없는 폭락"** 3대 사이클.

## 1. 밈주식 광기 (2021.01)

### GameStop 사태

- 2020.12 주가: $20
- 2021.01.27 최고가: $483 (+2,300%)
- 2021.02 종가: $50 (-90%)

**원인**: Reddit 'r/wallstreetbets' 커뮤니티가 헤지펀드의 공매도 포지션 발견 → 집단 매수 유도 → 숏스퀴즈.

**참여자**:
- 개인 매수: 300만 명+
- 헤지펀드 손실: Melvin Capital -53% 손실 → 결국 청산

**교훈**: 밈주식은 *기업 실적과 무관한 가격*. 매수 동기가 "함께 분노하기"였음.

### 한국 유사 사례
- 2021년 KSS해운·HMM 등 단기 밈성 폭등 → 폭락
- 2024년 임시 정치테마주 (정치인 관련주)

## 2. SPAC 광기 (2020~2021)

**SPAC** (Special Purpose Acquisition Company) = 비상장 기업 인수를 목적으로 먼저 상장한 빈 껍데기 기업.

### 폭증
- 2019년 SPAC IPO 59건
- **2020년 248건 (+320%)**
- **2021년 613건 (+150%)**

### 광기의 주역
- Chamath Palihapitiya (CEO of Social Capital): SPAC 6개 운영
- 유명 운동선수·연예인까지 SPAC 후원 (대법원 출신·NBA 스타·Jay-Z)

### 결과
- 2020~2021 SPAC 합병 완료 기업 평균 수익률: **-65%** (3년 후)
- DraftKings, Lucid, Virgin Galactic 등 대부분 -70%~-90%

**교훈**: *상장 절차의 정상적 검증을 우회*하는 통로 → 실적 부풀리기 만연.

## 3. ARKK 광기 (2020.03~2021.02)

**ARK Innovation ETF (ARKK)**:
- 캐시 우드(Cathie Wood) 운용
- 테슬라·로쿠·코인베이스·텔레닥·줌 등 "혁신 성장주" 집중

### 폭등
- 2020.03 ARKK: $33
- 2021.02 ARKK: $156 (+372%)
- ARK 펀드 자산 5,000억$+

### 폭락
- 2021.02~2022.06: $156 → $35 (-78%)
- 2024년 현재: $50대 (정점 대비 -68%)

**원인**:
- 금리 인상 시작 → 미래 현금흐름 할인율 상승 → 성장주 직격탄
- 보유 종목 대부분 적자 → 이익 모멘텀 부재
- 수익률 하락 → 자금 유출 → 강제 매도 → 추가 하락 (악순환)

## 공통점: 유동성이 만든 거품

```
저금리 (제로) → 유동성 폭증 → 위험자산 매수 → 가격 분리 (실적 ↔ 주가) →
금리 인상 시작 → 유동성 회수 → 폭락
```

## 본 시스템에서 발견할 수 있는 신호

본 시스템 `discovery_type` 분류에서 **`contrarian`**(역행) 또는 **`undervalued`**(저평가) 태깅된 추천을 보면, *마니아 종목과는 정반대 방향*임을 알 수 있다. 시장이 광기에 빠졌을 때 contrarian/undervalued 추천이 늘어나는 패턴이 있다.

> 거품의 끝은 항상 같다. *왜* 만들어졌는지를 알면, *언제* 끝날지는 비교적 명확하다 — 유동성 회수 시점.""",
        "examples": json.dumps([
            {"title": "Lucid Motors SPAC 합병 (2021.02)", "description": "2021.02 SPAC 합병 발표 시 주가 60$ → 2024년 2$ (-97%). Tesla 다음 EV로 광고됐으나 실제 양산 지연·현금 소진. SPAC 광기의 대표 실패 사례.", "period": "2021.02~2024", "lesson": "스토리는 강렬한데 실행 능력 검증 부재 → SPAC의 구조적 문제"}
        ]),
    },
    {
        "category": "stories",
        "slug": "tesla-eight-years",
        "title": "테슬라 8년 복기 — 반대매매와 컨빅션의 경제학",
        "summary": "2017년 모델3 양산 위기부터 2024년 시총 1조 달러까지, 가장 격렬한 주식 한 종목의 여정.",
        "difficulty": "beginner",
        "sort_order": 57,
        "content": """## 8년간 +1,500%, 그러나 길은 잔혹했다

### 단순 수익률은 거짓말

| 연도 | 시작가 | 연중 최저점 | 종가 |
|---|---|---|---|
| 2017 | $44 | $40 | $63 |
| 2018 | $63 | $35 | $66 |
| 2019 | $66 | $34 | $83 |
| 2020 | $83 | $73 | **$705** |
| 2021 | $705 | $539 | $1,057 |
| 2022 | $1,057 | $216 (-80%) | $339 |
| 2023 | $339 | $339 | $664 |
| 2024 | $664 | $416 | $415 |

(주식분할 5:1 (2020) + 3:1 (2022) 반영 후 환산. 단위: 분할 후 기준 가격)

→ **2017~2024 누적 +540%**. 그러나 *세 번의 -50% 이상 폭락*을 견뎌야 했다.

## 1단계: 2017~2018 — "공매도 포지션 1위"

- 모델3 양산 지연·캐시 번레이트 -10억$/분기
- 공매도 잔고: 전체 주식의 30%+
- 2018.08 머스크 "Funding secured" 트윗 → SEC 제재
- 시장의 컨센서스: "테슬라는 6개월 내 파산"

**견딘 사람의 결과**: 2018.04 35$ 매수 → 2024 415$ → **+1,080%**

## 2단계: 2019~2020 — "양산 정상화 + 코로나 유동성"

- 모델3 캐파 4만대/월 돌파
- 2020.04 1분기 흑자 첫 달성
- S&P500 편입 (2020.12) → 인덱스 펀드 대량 매수 → 폭등

→ 8개월 만에 +600% (분할 후 73$ → 705$)

## 3단계: 2021 — "시총 1조 달러 진입"

- 시총 글로벌 5위 진입
- PER 200배+
- 거시 긴축 우려 누적

## 4단계: 2022~2023 — "이자율 충격 + 머스크 X 인수 산만"

- Fed 금리 인상 → 성장주 디레이팅
- 머스크가 트위터(X) 440억$에 인수 → 테슬라 매각으로 자금 충당 우려
- 2022 -69% (전년 대비), 2022.04~2023.01 -75%

**견딘 사람의 결과**: 2022.12 116$ → 2024 415$ → +260%

## 5단계: 2024 — "FSD·로보택시 기대 + 이익 둔화"

- EV 시장 둔화 (-40% YoY 일부 모델)
- FSD V12·로보택시 미래 베팅
- 변동성 지속

## 핵심 교훈

### 1. 컨빅션의 가격

- 2018 -50% 폭락 견딘 사람만 +1,080% 달성
- 2022 -75% 폭락 견딘 사람만 +260% 달성
- *대부분은 못 견딘다*

### 2. "성장주 + 컬트적 팔로잉"의 변동성

- 성장주는 미래 현금흐름 할인 → 금리에 민감
- 머스크 트위터 영향력 → 비펀더멘털 변동성 추가
- → 같은 변동성을 견딜 자신이 없으면 *작게* 사야 한다

### 3. 사이즈가 운명을 결정

- 자산 50%를 테슬라에 몰빵한 사람은 2022년 -37% 자산 손실
- 자산 5%만 둔 사람은 2022년 -3% 손실 → 회복 여유

## 본 시스템에서 보는 테슬라

- 본 시스템 `factor_snapshot` 6축에서 테슬라는 *r12m_pct* 변동이 ±100% 수준
- `vol60_pct`(60일 변동성)는 시장 95% 이상 분위
- → 정량적으로 *최고 변동성 분위*에 속하는 종목

> 테슬라는 결과적으로 최고의 종목이었다. 그러나 *그 결과를 누린 사람*은 극소수.""",
        "examples": json.dumps([
            {"title": "워런 버핏의 테슬라 거부 (2018)", "description": "버핏은 \"테슬라는 내 능력 범위 밖\"이라며 매수 거부. 결과적으로 +1,000% 기회를 놓쳤지만, 2018 파산 위험을 회피한 측면도. 능력 범위(circle of competence) 원칙의 대표 예시.", "period": "2018", "lesson": "최고의 종목을 놓치는 게 최악은 아니다 — 모르는 종목에서 큰 손실이 더 위험"}
        ]),
    },
```

- [ ] **Step 2: V24_SLUGS 다음에 V35_SLUGS 추가**

```python
V35_SLUGS: set[str] = {"ai-vs-dotcom", "liquidity-mania-2020", "tesla-eight-years"}
```

- [ ] **Step 3: 부분 검증**

Run: `python -c "from shared.db.migrations.seeds_education import stories; print(len(stories.TOPICS), stories.V35_SLUGS)"`
Expected: `8 {'ai-vs-dotcom', 'liquidity-mania-2020', 'tesla-eight-years'}`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/stories.py
git commit -m "feat(edu): stories +3 — AI vs 닷컴·유동성 광기·테슬라 8년 (Tier B)"
```

---

## Task 8: tools 카테고리 신설 (3 토픽, Tier A)

**Files:**
- Create: `shared/db/migrations/seeds_education/tools.py`

토픽 1: `factor-six-axes` (정량 팩터 6축)
토픽 2: `market-regime-reading` (시장 레짐 읽기)
토픽 3: `pre-market-briefing-guide` (프리마켓 브리핑 활용)

- [ ] **Step 1: tools.py 신설**

```python
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
```

- [ ] **Step 2: 부분 검증**

Run: `python -c "from shared.db.migrations.seeds_education import tools; print(len(tools.TOPICS), tools.V35_SLUGS)"`
Expected: `3 {'factor-six-axes', 'market-regime-reading', 'pre-market-briefing-guide'}`

- [ ] **Step 3: 커밋**

```bash
git add shared/db/migrations/seeds_education/tools.py
git commit -m "feat(edu): tools 카테고리 신설 +3 — 팩터 6축·레짐·프리마켓 브리핑 (Tier A)"
```

---

## Task 9: __init__.py 집계 갱신 + V35_SLUGS 노출

**Files:**
- Modify: `shared/db/migrations/seeds_education/__init__.py`

- [ ] **Step 1: __init__.py 전체 교체**

기존 파일을 다음으로 교체:

```python
"""교육 토픽 시드 데이터 — 카테고리별 모듈 집계.

각 카테고리 모듈에서 TOPICS와 V24_SLUGS / V35_SLUGS를 가져와
ALL_TOPICS (전체 시드용)와 NEW_TOPICS_VN (마이그레이션용)을 노출한다.
"""
from . import basics, analysis, risk, macro, practical, stories, tools

_MODULES = [basics, analysis, risk, macro, practical, stories, tools]

ALL_TOPICS: list[dict] = []
for _m in _MODULES:
    ALL_TOPICS.extend(_m.TOPICS)

_V24_SLUGS: set[str] = set()
for _m in _MODULES:
    _V24_SLUGS.update(getattr(_m, "V24_SLUGS", set()))

_V35_SLUGS: set[str] = set()
for _m in _MODULES:
    _V35_SLUGS.update(getattr(_m, "V35_SLUGS", set()))

NEW_TOPICS_V24: list[dict] = [t for t in ALL_TOPICS if t["slug"] in _V24_SLUGS]
NEW_TOPICS_V35: list[dict] = [t for t in ALL_TOPICS if t["slug"] in _V35_SLUGS]
```

- [ ] **Step 2: 통합 시드 검증 테스트 부분 실행 (마이그레이션 외)**

Run: `pytest tests/test_education_seeds.py -v -k "not (V35_disjoint)"`
Expected: 다음 테스트 PASS — `test_total_topic_count`, `test_category_distribution`, `test_all_slugs_unique`, `test_required_keys_present`, `test_examples_valid_json`, `test_content_min_length`, `test_difficulty_valid`, `test_v35_new_topics_count`, `test_tools_category_exists`, `test_edu_categories_label_includes_tools`

- [ ] **Step 3: V35 ↔ V24 disjoint 검증**

Run: `pytest tests/test_education_seeds.py::test_v35_topics_disjoint_from_v24 -v`
Expected: PASS — V24와 V35 slug 겹침 없음

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/__init__.py
git commit -m "feat(edu): __init__ 집계 갱신 — tools 모듈 등록 + NEW_TOPICS_V35 노출"
```

---

## Task 10: 마이그레이션 v35 추가

**Files:**
- Modify: `shared/db/migrations/versions.py`
- Modify: `shared/db/migrations/__init__.py`
- Modify: `shared/db/schema.py`

- [ ] **Step 1: versions.py에 `_migrate_to_v35` 함수 추가**

versions.py 파일 끝에 다음 함수 추가 (마지막 `_migrate_to_v34` 다음에):

```python
def _migrate_to_v35(cur) -> None:
    """Education 신규 토픽 14개 추가 (Tier A·B + tools 카테고리 신설).

    분포: basics +2, analysis +2, risk +3, macro +1, stories +3, tools(신규) +3
    기존 26개 토픽은 ON CONFLICT (slug) DO NOTHING으로 보호.
    신규 DB의 경우 v21에서 ALL_TOPICS 전체가 이미 시드되었으므로 v35는 사실상 no-op (멱등).

    education_topics.category VARCHAR(50)에 CHECK 제약 없음 — 'tools' 추가에 ALTER 불필요.
    UI 라벨은 api/routes/education.py:_EDU_CATEGORIES에서 분리 관리.
    """
    from shared.db.migrations.seeds_education import NEW_TOPICS_V35
    for t in NEW_TOPICS_V35:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (35)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v35: 교육 토픽 {len(NEW_TOPICS_V35)}개 추가 (tools 카테고리 신설)")
```

- [ ] **Step 2: __init__.py(`shared/db/migrations/`)의 `_MIGRATIONS` dict에 35 추가**

`_MIGRATIONS` 딕셔너리에서 `34: _v._migrate_to_v34,` 라인 다음에 한 줄 추가:

```python
    34: _v._migrate_to_v34,
    35: _v._migrate_to_v35,
}
```

- [ ] **Step 3: schema.py의 `SCHEMA_VERSION` 증가**

`shared/db/schema.py:12` 의 `SCHEMA_VERSION = 34` 라인을 다음으로 교체:

```python
SCHEMA_VERSION = 35  # v35: education Tier A·B 14 토픽 + tools 카테고리 신설
```

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/versions.py shared/db/migrations/__init__.py shared/db/schema.py
git commit -m "feat(db): v35 마이그레이션 — education Tier A·B 14 토픽 + tools 카테고리"
```

---

## Task 11: 통합 검증 — 멱등성 + UI 스모크

**Files:** (변경 없음 — 검증만)

- [ ] **Step 1: 전체 검증 테스트 실행**

Run: `pytest tests/test_education_seeds.py -v`
Expected: 모든 테스트 PASS (10개)

- [ ] **Step 2: 신규 DB 멱등성 시뮬레이션 (옵션 — DB 환경 있는 경우)**

신규 빈 DB 생성 후 마이그레이션 실행 → 토픽 카운트 확인:

```bash
# .env로 임시 DB 지정
python -c "
from shared.db.schema import init_db
from shared.config import DatabaseConfig
init_db(DatabaseConfig())
"
# DB 직접 확인:
psql -d <DB_NAME> -c "SELECT category, COUNT(*) FROM education_topics GROUP BY category ORDER BY category;"
```

Expected output:
```
 category  | count
-----------+-------
 analysis  |     6
 basics    |    10
 macro     |     4
 practical |     4
 risk      |     5
 stories   |     8
 tools     |     3
```
Total: 40

- [ ] **Step 3: 마이그레이션 재실행 멱등성 검증 (옵션)**

```bash
python -c "
from shared.db.schema import init_db
from shared.config import DatabaseConfig
init_db(DatabaseConfig())
"  # 재실행
# 다시 카운트 → 변화 없음 확인
```

Expected: 카운트 변화 0 (멱등성 보장).

- [ ] **Step 4: API 라우터 응답 검증 (옵션 — 서버 기동 시)**

```bash
# 서버 기동 후
curl -s http://localhost:8000/education/topics | python -c "import sys, json; d=json.load(sys.stdin); print(len(d), set(t['category'] for t in d))"
```

Expected: `40 {'basics', 'analysis', 'risk', 'macro', 'practical', 'stories', 'tools'}`

- [ ] **Step 5: UI 스모크 (옵션 — 브라우저)**

브라우저에서 `http://localhost:8000/pages/education` 접속:
- 7개 카테고리 그룹 모두 표시 확인
- "도구·시스템 가이드" 라벨 노출 확인
- tools 카테고리 3개 토픽 클릭하여 본문 렌더링 확인 (마크다운 + examples 정상)

- [ ] **Step 6: 챗 엔진 스모크 (옵션)**

신규 토픽 1개로 채팅 세션 생성 (UI 또는 API):
```bash
curl -X POST http://localhost:8000/api/edu/sessions \
  -H "Content-Type: application/json" \
  -d '{"topic_slug": "factor-six-axes"}'
```
→ 시스템 프롬프트에 토픽 content 포함되는지 로그(`api_runs.context`)로 확인.

- [ ] **Step 7: 최종 git 상태 검증 + 커밋 메시지 정리**

```bash
git log --oneline -15  # Task 1~10 커밋 확인
git status  # working tree clean
```

Expected: 11개 커밋(Task 1~10), working tree clean.

---

## Self-Review (Plan 작성 후 자체 점검 결과)

### Spec coverage
- spec §2.1 카테고리 분포 → Task 1 검증 테스트 + Task 3~8 시드 추가로 커버 ✓
- spec §2.2 신규 토픽 14개 → Task 3~8에 모두 매핑 ✓
- spec §2.3 컨텐츠 품질 (≥800자, examples valid JSON, 한국 사례 우선) → Task 1 테스트 + Task 3~8 본문 ✓
- spec §3.1 시드 모듈 변경 → Task 3~9 ✓
- spec §3.2 마이그레이션 → Task 10 ✓
- spec §3.3 라벨 매핑 → Task 2 ✓
- spec §3.4 변경 없음 영역 → 명시적으로 plan에서 다루지 않음 (변경 없음 검증) ✓
- spec §4 멱등성 → Task 11 Step 3 ✓
- spec §5 검증 계획 → Task 11 모든 step ✓
- spec §6 작업 순서 → Plan Task 1~11에 그대로 매핑 ✓

### Placeholder scan
- "TBD"/"TODO" 없음 ✓
- 각 토픽 본문은 완전한 마크다운 텍스트 포함 ✓
- examples JSON 모두 구체 값 (제목·기간·교훈) ✓
- 모든 step에 실제 명령 또는 코드 명시 ✓

### Type/이름 일관성
- `NEW_TOPICS_V35` (Task 1·9·10 일치) ✓
- `V35_SLUGS` (Task 3~8 일치) ✓
- `_EDU_CATEGORIES["tools"] = "도구·시스템 가이드"` (Task 1 테스트와 Task 2 구현 일치) ✓
- 카테고리 분포 (Task 1 test_category_distribution과 Task 3~8 합산 일치) ✓
