# Education 컨텐츠 확장 설계 — 12 → 26 토픽 + `stories` 카테고리 신설

- **작성일**: 2026-04-22
- **상태**: 설계 확정 (구현 플랜 작성 대기)
- **범위**: `shared/db/migrations/seeds.py` 분할 + 교육 토픽 15개 추가 + `stories` 카테고리 신설
- **방식**: 일괄 작업(단일 PR), 단계별 커밋

## 1. 배경 / 동기

### 현재 상태
- 교육 메뉴는 5개 카테고리(`basics`/`analysis`/`risk`/`macro`/`practical`)에 11개 토픽으로 구성
- 시드 함수 `_seed_education_topics`는 `shared/db/migrations/seeds.py` (~488줄, 단일 파일에 데이터 집중)
- 시드는 `_migrate_to_v21`에서 `COUNT == 0`일 때만 실행 ([versions.py:812-814](../../../shared/db/migrations/versions.py#L812-L814))

### 문제
1. **컨텐츠 빈곤**: 입문자 필수 개념(재무제표·세금·경기 사이클) 부재
2. **앱 데이터 미활용**: KRX 수급/공매도/시나리오/discovery_type 등 이 앱 고유 데이터의 해석법이 교육에 없음 — 사용자가 데이터를 보고도 활용을 못 함
3. **흥미 컨텐츠 부재**: 거장·폭락·시뮬레이션 등 재방문 유도 컨텐츠가 없음 — Free 티어 메뉴인데도 리텐션 장치 없음
4. **시드 파일 비대화**: 추가 시 단일 파일이 ~1000줄로 비대화 → 데이터 변경이 로직 diff처럼 보임

### 목표
- 카테고리 재구성으로 일관성 유지하면서 15개 토픽 신규 추가 → 총 26개
- 신규 카테고리 `stories` 신설 (성격이 다른 컨텐츠 격리)
- `seeds.py` → `seeds_education/` 디렉토리로 분할 (카테고리별 1파일)
- 기존 운영 DB에도 신규 토픽이 자동 적용되도록 마이그레이션 v24 신설

## 2. 최종 구성

### 2.1 카테고리·난이도 분포

| 카테고리 | 기존 | 추가 | 최종 |
|---|---|---|---|
| basics | 3 | 5 (A) | 8 |
| analysis | 2 | 2 (B.1, B.2) | 4 |
| risk | 2 | 0 | 2 |
| macro | 2 | 1 (B.3) | 3 |
| practical | 2 | 2 (B.4, B.5) | 4 |
| **stories** (신규) | 0 | 5 (C) | **5** |
| **합계** | **11** | **15** | **26** |

난이도: beginner 12 / intermediate 14 / advanced 0 (advanced 세트는 후속 작업)

### 2.2 신규 토픽 15개

#### A. 기초 커버리지 확장 — `basics` 카테고리 (5개)

| slug | 제목 | 난이도 | sort | 핵심 내용 |
|---|---|---|---|---|
| `financial-statements` | 재무제표 3종 한눈에 — 손익·재무상태·현금흐름 | beginner | 4 | 3종 재무제표 구조, 핵심 항목, 무엇을 봐야 하는지 |
| `eps-fcf-ebitda` | EPS·FCF·EBITDA — 이익의 세 얼굴 | intermediate | 5 | 회계이익 vs 현금이익 차이, 어떤 지표가 어느 산업에 적합한지 |
| `orderbook-and-trading` | 호가창·체결 구조 — 시가/종가/동시호가/상하한가 | beginner | 6 | 한국 시장 거래 메커니즘, 호가창 읽는 법 |
| `tax-and-accounts` | 투자 세금과 절세 계좌 — ISA·연금저축·해외주식 | beginner | 7 | 양도세·배당세·금융소득종합과세, 절세 계좌 비교 |
| `business-cycle` | 경기 사이클 4단계와 섹터 로테이션 | intermediate | 8 | 회복/확장/둔화/침체별 유리/불리 섹터 |

#### B. 이 앱 데이터 해석 — `analysis`/`macro`/`practical` 분산 (5개)

| slug | 카테고리 | 제목 | 난이도 | sort | 참조 DB 필드 |
|---|---|---|---|---|---|
| `foreign-institutional-flow` | analysis | 외국인·기관 순매수 신호 읽기 (KRX 수급) | intermediate | 12 | `foreign_net_buy_signal` |
| `short-selling-squeeze` | analysis | 공매도 잔고와 숏스퀴즈 위험도 | intermediate | 13 | `squeeze_risk` |
| `scenario-thinking` | macro | base/worse/better — 시나리오 사고법 | intermediate | 32 | `theme_scenarios`, `macro_impacts` |
| `discovery-type-guide` | practical | discovery_type 4종 실전 판별 (consensus·early·contrarian·value) | intermediate | 42 | `discovery_type` |
| `entry-price-tracking` | practical | entry_price·post_return으로 내 매매 복기하기 | intermediate | 43 | `entry_price`, `post_return_*_pct` |

#### C. 스토리/흥미 — `stories` 신규 카테고리 (5개)

| slug | 제목 | 난이도 | sort | 핵심 내용 |
|---|---|---|---|---|
| `investor-legends` | 투자 거장 5인 — 버핏·멍거·린치·달리오·코스톨라니 | beginner | 50 | 각 거장의 철학 + 대표 실적 + 한 줄 명언 |
| `legendary-crashes` | 전설의 폭락 5대 케이스 — LTCM·리먼·차화정·2차전지·코로나 | intermediate | 51 | 폭락 원인·전조·교훈 |
| `what-if-2015` | 10년 전 그 종목을 샀다면? — 삼성전자·네이버·카카오 시뮬레이션 | beginner | 52 | 실제 수익률 + 배당 재투자 + 환율 효과 |
| `behavioral-biases` | 내가 빠지는 7가지 투자 심리 함정 | intermediate | 53 | FOMO, 손실회피, 확증편향, 기준점, 처분효과 등 |
| `korea-market-timeline` | 한국 증시 25년 키워드 타임라인 (2000~2025) | beginner | 54 | IT버블·서브프라임·박스피·동학개미·코로나·2차전지 등 연도별 |

### 2.3 컨텐츠 품질 기준 (기존 11개 토픽과 동등)

각 토픽 필수 요소:
- `content`: 1,000~2,000자 마크다운 본문
- 표 1~2개 포함 (비교·구조 정리용)
- `examples`: 1~2개 JSON 사례 (`title`/`description`/`period`/`lesson`)
- B 카테고리는 본문에서 이 앱의 실제 DB 필드명을 직접 인용 (사용자가 즉시 화면에서 찾도록)
- C 카테고리는 실제 숫자·연도·인용구 포함 (팩트 기반)

## 3. 파일 구조 (seeds.py 분할)

### Before
```
shared/db/migrations/
├── seeds.py                 (515줄: admin + education 토픽 11개 데이터 모두 포함)
└── versions.py              (v21에서 _seed_education_topics 호출)
```

### After
```
shared/db/migrations/
├── seeds.py                              (~80줄: admin + education 집계 엔트리)
├── seeds_education/                      (신규)
│   ├── __init__.py                       (~30줄: ALL_TOPICS, NEW_TOPICS_V24 export)
│   ├── basics.py                         (8개: 기존 3 + A 5개)
│   ├── analysis.py                       (4개: 기존 2 + B 2개)
│   ├── risk.py                           (2개: 기존 유지)
│   ├── macro.py                          (3개: 기존 2 + B 1개)
│   ├── practical.py                      (4개: 기존 2 + B 2개)
│   └── stories.py                        (5개: C 전부 신규)
└── versions.py                           (변경: _migrate_to_v24 추가만)
```

### 설계 원칙
- `_seed_education_topics(cur)` 공개 시그니처 **불변** — `versions.py:_migrate_to_v21` 변경 없음
- 각 카테고리 파일은 `TOPICS: list[dict]`와 `V24_SLUGS: set[str]`만 export — 단일 책임
- `seeds_education/__init__.py`가 6개 모듈을 합쳐 `ALL_TOPICS` (전체 26개) + `NEW_TOPICS_V24` (신규 15개) 두 리스트 노출
- INSERT SQL은 `seeds.py`에만, 컨텐츠 데이터는 카테고리 파일에만 (데이터/로직 분리)

### `seeds_education/__init__.py` 인터페이스 (예시)
```python
from . import basics, analysis, risk, macro, practical, stories

_MODULES = [basics, analysis, risk, macro, practical, stories]

ALL_TOPICS: list[dict] = []
for m in _MODULES:
    ALL_TOPICS.extend(m.TOPICS)

# v24에서 신규 추가되는 토픽만 (각 모듈의 V24_SLUGS 집합)
_NEW_SLUGS = set()
for m in _MODULES:
    _NEW_SLUGS.update(getattr(m, "V24_SLUGS", set()))

NEW_TOPICS_V24: list[dict] = [t for t in ALL_TOPICS if t["slug"] in _NEW_SLUGS]
```

### `seeds.py` 변경 후 (요지)
```python
def _seed_education_topics(cur) -> None:
    from shared.db.migrations.seeds_education import ALL_TOPICS
    for t in ALL_TOPICS:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )
    print(f"[DB] 교육 토픽 {len(ALL_TOPICS)}건 시드 데이터 삽입")
```

## 4. UI / 라우트 변경

### 4.1 `api/routes/education.py:23-29` — 카테고리 dict
```python
_EDU_CATEGORIES = {
    "basics": "기초 개념",
    "analysis": "분석 기법",
    "risk": "리스크 관리",
    "macro": "매크로 경제",
    "practical": "실전 활용",
    "stories": "투자 이야기",  # 신규
}
```

### 4.2 `api/templates/education.html:33` — 아이콘 매핑
- 추가: `stories` → 📚 (`&#x1F4DA;`)
- 기존 매핑 유지 (`basics` 📖, `analysis` 📊, `risk` 🛡, `macro` 🌍, `practical` 🎯)

### 4.3 정렬 결과
sort_order 기준 페이지 렌더링 순서:
1. basics (1~8) — 기초 8개
2. analysis (10~13) — 분석 4개
3. risk (20~21) — 리스크 2개
4. macro (30~32) — 매크로 3개
5. practical (40~43) — 실전 4개
6. **stories (50~54)** — 스토리 5개 (마지막)

stories를 마지막에 배치해 핵심 교육이 상단에 유지됨.

## 5. 마이그레이션 전략 — v24 신설

### 5.1 문제
`_migrate_to_v21` ([versions.py:812-814](../../../shared/db/migrations/versions.py#L812-L814)):
```python
cur.execute("SELECT COUNT(*) FROM education_topics")
if cur.fetchone()[0] == 0:
    _seed_education_topics(cur)
```
→ 이미 11개가 들어있는 운영 DB에서는 시드 함수에 신규 토픽을 추가해도 **삽입되지 않음**.

### 5.2 해법
**`_migrate_to_v24` 신설** — 신규 15개만 명시적 INSERT, 기존 토픽은 `ON CONFLICT DO NOTHING`으로 보호.

```python
# shared/db/migrations/versions.py 말미
def _migrate_to_v24(cur):
    """Education 토픽 15개 추가 (basics 5 + analysis 2 + macro 1 + practical 2 + stories 5).
    stories 카테고리 신규 도입."""
    from shared.db.migrations.seeds_education import NEW_TOPICS_V24
    for t in NEW_TOPICS_V24:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )
    print(f"[DB] v24: 교육 토픽 {len(NEW_TOPICS_V24)}개 추가")
```

### 5.3 SCHEMA_VERSION 증가
- [shared/db/schema.py](../../../shared/db/schema.py): `SCHEMA_VERSION = 23` → **`24`**
- 마이그레이션 등록 dict (`_MIGRATIONS`)에 `24: _migrate_to_v24` 추가

### 5.4 동작 시나리오

| DB 상태 | v21 실행 | v24 실행 | 최종 토픽 수 |
|---|---|---|---|
| 신규 DB (v0) | 26개 시드 (`ALL_TOPICS`) | 15개 시도 → 모두 충돌, 0개 삽입 | **26개** |
| 운영 DB (v21~23) | 스킵 (이미 11개 존재) | 15개 신규 삽입 | **26개** |

**중요**: 신규 DB 시나리오에서 v21이 `ALL_TOPICS` (26개) 전체를 삽입하므로 v24는 사실상 no-op이 됨. 이 동작은 의도된 것 — `ON CONFLICT DO NOTHING`이 멱등성 보장.

## 6. 작업 순서 (단계별 커밋)

| 단계 | 내용 | 검증 |
|---|---|---|
| **1** | `seeds_education/` 디렉토리 + 6개 카테고리 파일 스켈레톤(빈 `TOPICS = []`, 빈 `V24_SLUGS = set()`) + `__init__.py` | `python -c "from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24; print(len(ALL_TOPICS), len(NEW_TOPICS_V24))"` → `0 0` |
| **2** | 기존 11개 토픽을 카테고리 파일로 1:1 이관 (basics 3 / analysis 2 / risk 2 / macro 2 / practical 2) | `len(ALL_TOPICS) == 11`, `len(NEW_TOPICS_V24) == 0` |
| **3** | `seeds.py`의 `_seed_education_topics` 함수 본문을 `ALL_TOPICS` 사용으로 리팩토링. 기존 토픽 데이터 블록 제거 | `python -c "from shared.db.migrations.seeds import _seed_education_topics; print('ok')"` + `seeds.py` 줄 수 확인 (~80줄) |
| **4** | A 5개 추가 (`basics.py`) + 각 토픽의 slug를 `V24_SLUGS`에 등록 | `len(basics.TOPICS) == 8`, `len(NEW_TOPICS_V24) == 5` |
| **5** | B 5개 추가 (analysis 2 / macro 1 / practical 2) + V24_SLUGS 갱신 | 카테고리별 count 확인, `len(NEW_TOPICS_V24) == 10` |
| **6** | C 5개 추가 (`stories.py`) + V24_SLUGS 갱신 | `len(stories.TOPICS) == 5`, `len(NEW_TOPICS_V24) == 15` |
| **7** | `_EDU_CATEGORIES`에 `stories` 추가 + `education.html` 아이콘 매핑 추가 | UI 수동 확인 (랜딩 페이지) |
| **8** | `_migrate_to_v24` 함수 + `_MIGRATIONS[24]` 등록 + `SCHEMA_VERSION = 24` | fresh DB 풀 마이그레이션 + 기존 DB v24 적용 2종 테스트 |
| **9** | UI 스모크 테스트 — `/pages/education`에서 stories 섹션 렌더링, `/pages/education/topic/<slug>` 신규 토픽 상세 진입 | 아이콘·제목·난이도 배지 표시, prev/next 네비게이션 동작 |

각 단계는 독립 커밋. 컨벤션은 기존 커밋 스타일(`feat(education): ...`, `refactor(db): ...`) 따름.

## 7. 검증 쿼리

```sql
-- 카테고리별 분포
SELECT category, COUNT(*) FROM education_topics GROUP BY category ORDER BY category;
-- 예상: analysis 4, basics 8, macro 3, practical 4, risk 2, stories 5

-- 난이도별 분포
SELECT difficulty, COUNT(*) FROM education_topics GROUP BY difficulty;
-- 예상: beginner 12, intermediate 14

-- sort_order 정합성 (카테고리 내 중복 없음)
SELECT category, sort_order, COUNT(*) FROM education_topics
GROUP BY category, sort_order HAVING COUNT(*) > 1;
-- 예상: 0 rows
```

## 8. 리스크 / 완화

| ID | 리스크 | 가능성 | 영향 | 완화 |
|---|---|---|---|---|
| R1 | 컨텐츠 분량이 큼 (15 × ~1500자 + examples) — 단일 작업 시간이 길어짐 | 높음 | 중 | 단계 4·5·6을 카테고리별로 쪼개서 부분 검증·커밋 가능. 하나라도 컨텐츠 품질이 기존 수준 미달이면 해당 단계 재작업 |
| R2 | `NEW_TOPICS_V24` 추출 누락 — 모듈에서 `V24_SLUGS`에 등록 안 하면 운영 DB에 미반영 | 중 | 고 | 단계 4·5·6 종료 직후 매번 `len(NEW_TOPICS_V24)` 검증을 강제. 기대값 (5/10/15)과 다르면 다음 단계 진입 금지 |
| R3 | UI 아이콘 하드코딩 위치가 두 군데 (`_EDU_CATEGORIES` + `education.html`) — 한 쪽만 수정 위험 | 낮음 | 저 | 단계 7에서 두 파일 동시 수정. 향후 별도 작업으로 config화 가능하나 이번 범위 외 |
| R4 | `seeds.py` 리팩토링 시 import 순환 우려 (`seeds.py` ↔ `seeds_education/__init__.py`) | 낮음 | 중 | `seeds.py` 함수 내부에서 lazy import (`from shared.db.migrations.seeds_education import ALL_TOPICS`) — `versions.py` 패턴과 동일 |
| R5 | 운영 DB에 v24 적용 중 기존 토픽 데이터 손상 | 매우 낮음 | 고 | `ON CONFLICT (slug) DO NOTHING` 보장. v24는 INSERT만 수행, UPDATE/DELETE 없음 |

## 9. 범위 외 (Out of Scope)

- advanced 난이도 토픽 (별도 후속 작업 — 옵션·선물·리얼옵션·DCF 심화 등)
- 카테고리 아이콘의 config화 (현재 하드코딩 유지)
- 교육 페이지 UI 재디자인 (아이콘 추가만)
- AI 튜터 채팅 엔진 변경 (`api/education_engine.py`는 기존 그대로)
- 기존 11개 토픽의 컨텐츠 수정 (1:1 이관만, 데이터 변경 없음)
- 다국어 지원 (한국어 단일 유지)

## 10. 후속 작업 후보

이번 PR 완료 후 별도 트랙으로 검토할 항목:
- advanced 5종 토픽 (옵션 그릭스, DCF 가치평가, 리스크 패리티, 팩터 투자, 매크로 헤지)
- 카테고리 아이콘·라벨의 DB 테이블화 (`education_categories`)
- 토픽 검색·태그 기능
- 사용자별 학습 진척도 추적 (`education_progress`)
- 토픽 즐겨찾기·공유 기능
