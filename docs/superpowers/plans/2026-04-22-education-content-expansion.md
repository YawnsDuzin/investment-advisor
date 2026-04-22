# Education 컨텐츠 확장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Education 메뉴에 신규 토픽 15개를 추가(11→26)하고, `seeds.py`를 카테고리별 디렉토리로 분할하며, `stories` 카테고리를 신설한다.

**Architecture:** 단일 파일 `shared/db/migrations/seeds.py`의 토픽 데이터를 `seeds_education/` 디렉토리(카테고리당 1파일)로 분리. 신규 마이그레이션 v24를 신설하여 운영 DB에 신규 토픽 15개를 자동 적용한다. UI는 `_EDU_CATEGORIES` dict + `education.html` 아이콘 매핑에 `stories` 추가.

**Tech Stack:** Python 3.10+, PostgreSQL, FastAPI, Jinja2, psycopg2

**Spec:** [docs/superpowers/specs/2026-04-22-education-content-expansion-design.md](../specs/2026-04-22-education-content-expansion-design.md)

---

## File Structure

**Create:**
- `shared/db/migrations/seeds_education/__init__.py` — `ALL_TOPICS`, `NEW_TOPICS_V24` 집계
- `shared/db/migrations/seeds_education/basics.py` — basics 카테고리 (8개)
- `shared/db/migrations/seeds_education/analysis.py` — analysis 카테고리 (4개)
- `shared/db/migrations/seeds_education/risk.py` — risk 카테고리 (2개)
- `shared/db/migrations/seeds_education/macro.py` — macro 카테고리 (3개)
- `shared/db/migrations/seeds_education/practical.py` — practical 카테고리 (4개)
- `shared/db/migrations/seeds_education/stories.py` — stories 카테고리 (5개, 신규)

**Modify:**
- `shared/db/migrations/seeds.py` — `_seed_education_topics` 함수만 리팩토링, admin 시드 그대로
- `shared/db/migrations/versions.py` — `_migrate_to_v24` 함수 추가 (말미)
- `shared/db/migrations/__init__.py` — `_MIGRATIONS` dict에 `24: _v._migrate_to_v24` 한 줄
- `shared/db/schema.py` — `SCHEMA_VERSION = 23` → `24`
- `api/routes/education.py:23-29` — `_EDU_CATEGORIES`에 `"stories": "투자 이야기"` 추가
- `api/templates/education.html:33` — 아이콘 매핑에 `stories` → 📚 추가

**파일별 책임**
- 각 카테고리 파일: `TOPICS: list[dict]` + `V24_SLUGS: set[str]` 두 가지만 export
- `__init__.py`: 6개 모듈에서 import + `ALL_TOPICS`, `NEW_TOPICS_V24` 집계
- `seeds.py`: INSERT SQL 보유, 데이터는 `ALL_TOPICS`에서 가져옴

---

## 컨텐츠 작성 가이드 (Tasks 4~8 공통)

각 신규 토픽 작성 시 다음 기준을 따른다 (기존 11개 토픽 수준 유지):

1. **content** (마크다운, 1,000~2,000자):
   - `## 섹션` 3~5개로 구성
   - 표 1~2개 포함 (비교표·구조표·체크리스트)
   - 핵심 공식·정의는 굵은 글씨(`**`)
   - 마지막에 "핵심 원칙" 또는 "체크리스트" 같은 요약 섹션
2. **examples** (JSON, 1~2개):
   - 각 예시는 `title`, `description`, `period`, `lesson` 4개 키 필수
   - `description`은 실제 숫자·연도·종목 포함 (300~500자)
   - `lesson`은 한 문장 교훈 (80~150자)
3. **B 카테고리(앱 데이터 해석)**: content 본문 안에 이 앱의 실제 DB 필드명을 backtick으로 명시
   (예: `` `foreign_net_buy_signal` ``, `` `discovery_type` ``)
4. **C 카테고리(스토리)**: 실제 인물·연도·수치 포함 (팩트 기반)
5. **content 안에서 줄바꿈**: 빈 줄 하나로 단락 구분, `\n` raw 개행 금지 (Python `"""` 블록 자연 개행 사용)

각 토픽 dict의 정확한 형태:
```python
{
    "category": "<basics|analysis|risk|macro|practical|stories>",
    "slug": "<kebab-case>",
    "title": "<한글 제목>",
    "summary": "<한 문장 요약, 50~100자>",
    "difficulty": "<beginner|intermediate|advanced>",
    "sort_order": <int>,
    "content": """## 섹션1
...본문...
""",
    "examples": json.dumps([
        {"title": "...", "description": "...", "period": "...", "lesson": "..."},
    ]),
}
```

---

## Task 1: 디렉토리 + 빈 스켈레톤 생성

**Files:**
- Create: `shared/db/migrations/seeds_education/__init__.py`
- Create: `shared/db/migrations/seeds_education/basics.py`
- Create: `shared/db/migrations/seeds_education/analysis.py`
- Create: `shared/db/migrations/seeds_education/risk.py`
- Create: `shared/db/migrations/seeds_education/macro.py`
- Create: `shared/db/migrations/seeds_education/practical.py`
- Create: `shared/db/migrations/seeds_education/stories.py`

- [ ] **Step 1: 6개 카테고리 파일 생성 (빈 스켈레톤)**

각 파일 (`basics.py`, `analysis.py`, `risk.py`, `macro.py`, `practical.py`, `stories.py`) 내용은 동일하게:

```python
"""<카테고리명> 카테고리 교육 토픽."""
import json

TOPICS: list[dict] = []

# v24 마이그레이션에서 신규 추가되는 토픽의 slug 집합
V24_SLUGS: set[str] = set()
```

각 파일의 docstring만 카테고리에 맞게 변경:
- `basics.py`: `"""basics 카테고리 — 기초 개념 교육 토픽."""`
- `analysis.py`: `"""analysis 카테고리 — 분석 기법 교육 토픽."""`
- `risk.py`: `"""risk 카테고리 — 리스크 관리 교육 토픽."""`
- `macro.py`: `"""macro 카테고리 — 매크로 경제 교육 토픽."""`
- `practical.py`: `"""practical 카테고리 — 실전 활용 교육 토픽."""`
- `stories.py`: `"""stories 카테고리 — 투자 이야기 (신규 카테고리, v24)."""`

- [ ] **Step 2: `__init__.py` 작성**

```python
"""교육 토픽 시드 데이터 — 카테고리별 모듈 집계.

각 카테고리 모듈에서 TOPICS와 V24_SLUGS를 가져와
ALL_TOPICS (전체 시드용)와 NEW_TOPICS_V24 (v24 마이그레이션용)를 노출한다.
"""
from . import basics, analysis, risk, macro, practical, stories

_MODULES = [basics, analysis, risk, macro, practical, stories]

ALL_TOPICS: list[dict] = []
for _m in _MODULES:
    ALL_TOPICS.extend(_m.TOPICS)

_NEW_SLUGS: set[str] = set()
for _m in _MODULES:
    _NEW_SLUGS.update(getattr(_m, "V24_SLUGS", set()))

NEW_TOPICS_V24: list[dict] = [t for t in ALL_TOPICS if t["slug"] in _NEW_SLUGS]
```

- [ ] **Step 3: import 검증**

Run:
```bash
python -c "from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24; print(f'ALL={len(ALL_TOPICS)}, V24={len(NEW_TOPICS_V24)}')"
```
Expected: `ALL=0, V24=0`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/
git commit -m "feat(education): 시드 디렉토리 스켈레톤 (seeds_education/) 생성

6개 카테고리 파일(basics/analysis/risk/macro/practical/stories) +
__init__.py로 ALL_TOPICS, NEW_TOPICS_V24 집계 인터페이스 도입.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 기존 11개 토픽을 카테고리 파일로 이관

**Files:**
- Read: `shared/db/migrations/seeds.py` (29~502줄, `topics = [...]` 블록)
- Modify: `shared/db/migrations/seeds_education/basics.py` (기존 3개 토픽 추가)
- Modify: `shared/db/migrations/seeds_education/analysis.py` (기존 2개)
- Modify: `shared/db/migrations/seeds_education/risk.py` (기존 2개)
- Modify: `shared/db/migrations/seeds_education/macro.py` (기존 2개)
- Modify: `shared/db/migrations/seeds_education/practical.py` (기존 2개)

**원칙**: 데이터 1:1 복사. 어떤 변경도 가하지 않는다. `seeds.py:29-502`의 dict들을 그대로 옮겨 각 카테고리 파일의 `TOPICS = []`에 채워 넣는다.

- [ ] **Step 1: basics.py — 기존 3개 토픽 이관**

`seeds.py` 30~154줄의 토픽 3개(`per-pbr-roe`, `market-cap`, `dividend-yield`) dict를 그대로 복사해 `shared/db/migrations/seeds_education/basics.py`의 `TOPICS = [...]`에 넣는다.

파일 최종 형태:
```python
"""basics 카테고리 — 기초 개념 교육 토픽."""
import json

TOPICS: list[dict] = [
    {
        "category": "basics", "slug": "per-pbr-roe",
        "title": "PER·PBR·ROE — 가치평가의 3대 지표",
        ...  # seeds.py:32-81의 dict 그대로 복사
    },
    {
        "category": "basics", "slug": "market-cap",
        ...  # seeds.py:82-120
    },
    {
        "category": "basics", "slug": "dividend-yield",
        ...  # seeds.py:121-154
    },
]

V24_SLUGS: set[str] = set()
```

- [ ] **Step 2: analysis.py — 기존 2개 이관**

`seeds.py` 156~242줄의 `fundamental-vs-technical`, `momentum-investing` 토픽 2개를 `analysis.py`의 `TOPICS`에 복사.

- [ ] **Step 3: risk.py — 기존 2개 이관**

`seeds.py` 244~332줄의 `diversification`, `stop-loss` 토픽 2개를 `risk.py`의 `TOPICS`에 복사.

- [ ] **Step 4: macro.py — 기존 2개 이관**

`seeds.py` 334~411줄의 `interest-rates`, `exchange-rates` 토픽 2개를 `macro.py`의 `TOPICS`에 복사.

- [ ] **Step 5: practical.py — 기존 2개 이관**

`seeds.py` 413~502줄의 `reading-proposal-cards`, `using-track-record` 토픽 2개를 `practical.py`의 `TOPICS`에 복사.

- [ ] **Step 6: 토픽 수 검증**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24
from shared.db.migrations.seeds_education import basics, analysis, risk, macro, practical, stories
print(f'basics={len(basics.TOPICS)}, analysis={len(analysis.TOPICS)}, risk={len(risk.TOPICS)}, macro={len(macro.TOPICS)}, practical={len(practical.TOPICS)}, stories={len(stories.TOPICS)}')
print(f'ALL={len(ALL_TOPICS)}, V24={len(NEW_TOPICS_V24)}')
"
```
Expected: `basics=3, analysis=2, risk=2, macro=2, practical=2, stories=0` 그리고 `ALL=11, V24=0`

- [ ] **Step 7: 데이터 무결성 검증 (slug 중복 없음)**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS
slugs = [t['slug'] for t in ALL_TOPICS]
assert len(slugs) == len(set(slugs)), f'중복 slug 발견: {[s for s in slugs if slugs.count(s) > 1]}'
print('OK — 11개 slug 모두 유일')
"
```
Expected: `OK — 11개 slug 모두 유일`

- [ ] **Step 8: 커밋**

```bash
git add shared/db/migrations/seeds_education/
git commit -m "refactor(education): 기존 11개 토픽을 seeds_education/ 카테고리 파일로 이관

basics 3, analysis 2, risk 2, macro 2, practical 2 = 11개.
seeds.py의 데이터는 다음 task에서 제거.
1:1 복사로 데이터 변경 없음.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: seeds.py 리팩토링 — `_seed_education_topics`가 `ALL_TOPICS` 사용

**Files:**
- Modify: `shared/db/migrations/seeds.py` (27~514줄: `_seed_education_topics` 본문 교체)

- [ ] **Step 1: `_seed_education_topics` 함수 본문 교체**

[shared/db/migrations/seeds.py:27](shared/db/migrations/seeds.py#L27) ~ 끝(514줄)의 `_seed_education_topics(cur)` 함수 전체를 다음으로 교체. 파일 상단의 `import json`은 더 이상 seeds.py에서 직접 사용하지 않지만 남겨둬도 무방 (제거 권장).

```python
def _seed_education_topics(cur) -> None:
    """교육 토픽 시드 데이터 삽입 (seeds_education/에서 ALL_TOPICS 가져옴)."""
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

- [ ] **Step 2: seeds.py 상단 `import json` 제거 (더 이상 미사용)**

[shared/db/migrations/seeds.py:1-2](shared/db/migrations/seeds.py#L1-L2)에서 `import json` 줄 삭제. (admin_user 시드는 json 미사용)

- [ ] **Step 3: import 경로 검증**

Run:
```bash
python -c "from shared.db.migrations.seeds import _seed_admin_user, _seed_education_topics; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: 라인 수 확인 (대폭 감소)**

Run:
```bash
wc -l shared/db/migrations/seeds.py
```
Expected: ~40줄 미만 (기존 514줄에서 감소)

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/seeds.py
git commit -m "refactor(education): seeds.py 슬림화 — ALL_TOPICS 위임으로 ~470줄 감소

_seed_education_topics는 seeds_education.ALL_TOPICS를 INSERT만 담당.
공개 시그니처 불변, _migrate_to_v21 호출부 변경 없음.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: A — basics 카테고리 5개 토픽 추가

**Files:**
- Modify: `shared/db/migrations/seeds_education/basics.py` (TOPICS에 5개 dict 추가, V24_SLUGS 갱신)

**5개 신규 토픽** (모두 `category: "basics"`):

| slug | title | summary | difficulty | sort_order |
|---|---|---|---|---|
| `financial-statements` | 재무제표 3종 한눈에 — 손익·재무상태·현금흐름 | 손익계산서·재무상태표·현금흐름표의 핵심 항목과 무엇을 봐야 하는지 배웁니다. | beginner | 4 |
| `eps-fcf-ebitda` | EPS·FCF·EBITDA — 이익의 세 얼굴 | 회계이익(EPS)·현금이익(FCF)·영업이익 변형(EBITDA)의 차이와 산업별 적합성을 배웁니다. | intermediate | 5 |
| `orderbook-and-trading` | 호가창·체결 구조 — 시가/종가/동시호가/상하한가 | 한국 시장의 거래 메커니즘과 호가창 읽는 법을 배웁니다. | beginner | 6 |
| `tax-and-accounts` | 투자 세금과 절세 계좌 — ISA·연금저축·해외주식 | 양도세·배당세·금융소득종합과세와 ISA/연금저축/IRP의 절세 효과를 배웁니다. | beginner | 7 |
| `business-cycle` | 경기 사이클 4단계와 섹터 로테이션 | 회복/확장/둔화/침체 4국면별 유리·불리 섹터를 배웁니다. | intermediate | 8 |

각 토픽의 **content 본문 작성 가이드** (위 "컨텐츠 작성 가이드" 섹션 준수):

#### A.1 `financial-statements` 본문 구성
- 섹션: ① 재무제표 3종 한눈에 / ② 손익계산서 / ③ 재무상태표 / ④ 현금흐름표 / ⑤ 한 페이지 체크리스트
- 표 1: 3종 재무제표 비교 (질문, 보는 항목, 주기)
- 표 2: 현금흐름표 3종 (영업/투자/재무) 부호 조합 해석
- examples: ①삼성전자 영업현금흐름 사례 (반도체 업·다운 사이클) — 회계이익 vs 현금이익 괴리

#### A.2 `eps-fcf-ebitda` 본문 구성
- 섹션: ① EPS - 회계이익 기준 / ② FCF - 진짜 현금 / ③ EBITDA - 산업 비교용 / ④ 어느 지표를 언제 / ⑤ 함정 사례
- 표 1: 3지표 비교 (정의, 강점, 한계)
- 표 2: 산업별 추천 지표 (제조/IT/통신/REITs/유틸리티)
- examples: ①GE의 EBITDA-FCF 괴리 (2017~2018 위기 직전) — EBITDA만 봐서 놓친 신호

#### A.3 `orderbook-and-trading` 본문 구성
- 섹션: ① 시가/종가/동시호가 / ② 호가창 구조 / ③ 시장가 vs 지정가 / ④ 상하한가와 VI / ⑤ 거래 비용
- 표 1: 호가창 매수/매도 잔량 의미
- 표 2: 주문 종류 (지정가, 시장가, 조건부, IOC, FOK)
- examples: ①동시호가 끝물 매도 폭탄으로 종가가 -5% 마감된 사례 — 종가 매매의 위험

#### A.4 `tax-and-accounts` 본문 구성
- 섹션: ① 국내주식 양도세·배당세 / ② 해외주식 22%·환율 / ③ 금융소득종합과세 2,000만원 / ④ ISA / ⑤ 연금저축·IRP / ⑥ 어떤 계좌부터
- 표 1: 계좌별 절세 한도·중도인출·연금소득세 비교
- 표 2: 국내·해외 세율 비교
- examples: ①해외주식 1억 차익 신고 vs 누락 가산세 사례

#### A.5 `business-cycle` 본문 구성
- 섹션: ① 4국면 정의 / ② 회복기 / ③ 확장기 / ④ 둔화기 / ⑤ 침체기 / ⑥ 한국·미국 사이클 차이
- 표 1: 국면별 금리·물가·실업·자산군
- 표 2: 국면별 유리/불리 섹터
- examples: ①2020 코로나 침체→회복→확장(2021) 섹터 로테이션 사례

- [ ] **Step 1: 5개 토픽 dict를 `basics.py`의 `TOPICS = [...]`에 추가**

기존 3개 뒤에 5개를 append하는 형식. 각 토픽은 위 가이드대로 1,000~2,000자 content와 1~2개 examples로 구성.

- [ ] **Step 2: V24_SLUGS 갱신**

`basics.py` 말미:
```python
V24_SLUGS: set[str] = {
    "financial-statements",
    "eps-fcf-ebitda",
    "orderbook-and-trading",
    "tax-and-accounts",
    "business-cycle",
}
```

- [ ] **Step 3: 카운트·중복 검증**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24, basics
assert len(basics.TOPICS) == 8, f'basics={len(basics.TOPICS)}'
assert len(basics.V24_SLUGS) == 5
assert len(NEW_TOPICS_V24) == 5
slugs = [t['slug'] for t in ALL_TOPICS]
assert len(slugs) == len(set(slugs)), '중복 slug'
print(f'OK — basics 8개, V24 5개, 전체 {len(ALL_TOPICS)}개')
"
```
Expected: `OK — basics 8개, V24 5개, 전체 16개`

- [ ] **Step 4: JSON 직렬화 검증 (examples가 valid JSON인지)**

Run:
```bash
python -c "
import json
from shared.db.migrations.seeds_education import basics
for t in basics.TOPICS:
    parsed = json.loads(t['examples'])
    assert isinstance(parsed, list)
    for ex in parsed:
        assert {'title', 'description', 'period', 'lesson'} <= set(ex.keys()), f'{t[\"slug\"]} 예시 키 누락'
print('OK — basics 8개 examples 모두 정상')
"
```
Expected: `OK — basics 8개 examples 모두 정상`

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/seeds_education/basics.py
git commit -m "feat(education): A — basics 5개 토픽 추가 (재무제표·EPS/FCF·호가창·세금·경기사이클)

financial-statements, eps-fcf-ebitda, orderbook-and-trading,
tax-and-accounts, business-cycle.
sort_order 4~8로 기존 3개 뒤에 배치.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: B.1 + B.2 — analysis 카테고리 2개 토픽 추가

**Files:**
- Modify: `shared/db/migrations/seeds_education/analysis.py`

**2개 신규 토픽**:

| slug | title | summary | difficulty | sort_order |
|---|---|---|---|---|
| `foreign-institutional-flow` | 외국인·기관 순매수 신호 읽기 (KRX 수급) | 외국인·기관의 순매수 추이로 종목 모멘텀과 추세 전환을 판단하는 법을 배웁니다. | intermediate | 12 |
| `short-selling-squeeze` | 공매도 잔고와 숏스퀴즈 위험도 | 공매도 잔고 비율로 숏스퀴즈 위험과 하방 압력을 동시에 읽는 법을 배웁니다. | intermediate | 13 |

#### B.1 `foreign-institutional-flow` 본문 구성
- 섹션: ① 왜 외국인·기관 수급을 보는가 / ② 외국인 순매수 패턴 / ③ 기관 순매수 패턴 / ④ 함께 보면 더 강력 / ⑤ 이 앱에서 활용
- 본문에 **`foreign_net_buy_signal`** 필드 명시 + 값 예시 (`strong_buy`/`buy`/`neutral`/`sell`)
- `foreign_ownership_pct`(외국인 보유비율) 함께 언급
- 표 1: 외국인 신호별 의미
- 표 2: 외국인+기관 동행/엇갈림 4사분면
- examples: ①삼성전자 외국인 5일 연속 순매수 후 기관 가세 — 추세 전환 사례 (2023 하반기)

#### B.2 `short-selling-squeeze` 본문 구성
- 섹션: ① 공매도란 / ② 공매도 잔고 비율 / ③ 숏스퀴즈 메커니즘 / ④ 위험도 판단 / ⑤ 이 앱에서 활용
- 본문에 **`squeeze_risk`** 필드 명시 + 값 예시 (`high`/`medium`/`low`)
- 표 1: 공매도 잔고 비율 구간별 의미 (1%/3%/5%/10%+)
- 표 2: 숏스퀴즈 5대 트리거 (실적 서프라이즈, M&A, FDA 승인 등)
- examples: ①GameStop 사태 (2021.01) — 공매도 잔고 140% 상황에서 숏스퀴즈 폭발

- [ ] **Step 1: 2개 토픽 dict를 `analysis.py`의 `TOPICS`에 append**

- [ ] **Step 2: V24_SLUGS 갱신**

```python
V24_SLUGS: set[str] = {
    "foreign-institutional-flow",
    "short-selling-squeeze",
}
```

- [ ] **Step 3: 검증**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24, analysis
assert len(analysis.TOPICS) == 4
assert len(analysis.V24_SLUGS) == 2
assert len(NEW_TOPICS_V24) == 7  # basics 5 + analysis 2
print(f'OK — analysis 4개, V24 누적 7개, 전체 {len(ALL_TOPICS)}개')
"
```
Expected: `OK — analysis 4개, V24 누적 7개, 전체 18개`

- [ ] **Step 4: examples JSON 검증**

Run:
```bash
python -c "
import json
from shared.db.migrations.seeds_education import analysis
for t in analysis.TOPICS:
    json.loads(t['examples'])
print('OK')
"
```
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/seeds_education/analysis.py
git commit -m "feat(education): B.1+B.2 — analysis 2개 토픽 추가 (KRX 수급, 공매도/숏스퀴즈)

foreign-institutional-flow (foreign_net_buy_signal 활용),
short-selling-squeeze (squeeze_risk 활용).
sort_order 12~13.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: B.3 — macro 카테고리 1개 토픽 추가

**Files:**
- Modify: `shared/db/migrations/seeds_education/macro.py`

**1개 신규 토픽**:

| slug | title | summary | difficulty | sort_order |
|---|---|---|---|---|
| `scenario-thinking` | base/worse/better — 시나리오 사고법 | 시나리오 3종(낙관·기본·비관)으로 매크로 충격에 대비하는 사고 프레임을 배웁니다. | intermediate | 32 |

#### B.3 `scenario-thinking` 본문 구성
- 섹션: ① 왜 시나리오인가 / ② 3시나리오 구조 / ③ 확률 가중 사고 / ④ 이 앱의 macro_impacts 읽는 법 / ⑤ 포트폴리오 적용
- 본문에 **`theme_scenarios`**, **`macro_impacts`** 필드명 + 각각의 `base_case`/`worse_case`/`better_case` 구조 명시
- 표 1: 3시나리오별 점검 질문
- 표 2: 시나리오별 자산 배분 예시 (현금/주식/채권/금)
- examples: ①Fed 피벗 시나리오 (2024) base/worse/better별 실제 결과 — 자산군별 명암

- [ ] **Step 1: 토픽 dict를 `macro.py`의 `TOPICS`에 append**

- [ ] **Step 2: V24_SLUGS 갱신**

```python
V24_SLUGS: set[str] = {
    "scenario-thinking",
}
```

- [ ] **Step 3: 검증**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24, macro
assert len(macro.TOPICS) == 3
assert len(macro.V24_SLUGS) == 1
assert len(NEW_TOPICS_V24) == 8  # basics 5 + analysis 2 + macro 1
print(f'OK — macro 3개, V24 누적 8개, 전체 {len(ALL_TOPICS)}개')
"
```
Expected: `OK — macro 3개, V24 누적 8개, 전체 19개`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/macro.py
git commit -m "feat(education): B.3 — macro 1개 토픽 추가 (base/worse/better 시나리오 사고법)

scenario-thinking. theme_scenarios·macro_impacts 필드 활용 가이드.
sort_order 32 (기존 30·31 뒤).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: B.4 + B.5 — practical 카테고리 2개 토픽 추가

**Files:**
- Modify: `shared/db/migrations/seeds_education/practical.py`

**2개 신규 토픽**:

| slug | title | summary | difficulty | sort_order |
|---|---|---|---|---|
| `discovery-type-guide` | discovery_type 4종 실전 판별 (consensus·early·contrarian·value) | AI 분석의 discovery_type별 특성과 투자 스타일별 우선순위를 배웁니다. | intermediate | 42 |
| `entry-price-tracking` | entry_price·post_return으로 내 매매 복기하기 | 추천 후 실제 가격 추적 데이터로 매매 의사결정을 복기·개선하는 법을 배웁니다. | intermediate | 43 |

#### B.4 `discovery-type-guide` 본문 구성
- 섹션: ① 4종 발견 유형 / ② consensus / ③ early_signal / ④ contrarian / ⑤ deep_value / ⑥ 투자자 유형별 매칭
- 본문에 **`discovery_type`** 필드 + 4가지 enum 값 명시
- 표 1: 4종 비교 (특징, 시장 반응, 리스크)
- 표 2: 투자자 유형(보수/공격/역발상/가치)별 우선 discovery_type
- examples: ①early_signal로 분류된 종목이 이후 컨센서스화되는 과정 사례

#### B.5 `entry-price-tracking` 본문 구성
- 섹션: ① 추천 후 추적의 의미 / ② entry_price 확정 / ③ post_return 1m/3m/6m/1y / ④ 매매 복기 워크플로우 / ⑤ 트랙레코드 페이지 활용
- 본문에 **`entry_price`**, **`post_return_1m_pct`**, **`post_return_3m_pct`**, **`post_return_6m_pct`**, **`post_return_1y_pct`**, **`post_return_snapshot`** 필드 명시
- 표 1: 추천 vs 실제 시점별 평가
- 표 2: 복기 체크리스트 (왜 샀나, 왜 팔았나, 시장 vs 종목 요인 분리)
- examples: ①추천 후 +30% 익절했지만 1년 +120%였던 사례 — 익절 타이밍 복기

- [ ] **Step 1: 2개 토픽 dict를 `practical.py`의 `TOPICS`에 append**

- [ ] **Step 2: V24_SLUGS 갱신**

```python
V24_SLUGS: set[str] = {
    "discovery-type-guide",
    "entry-price-tracking",
}
```

- [ ] **Step 3: 검증**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24, practical
assert len(practical.TOPICS) == 4
assert len(practical.V24_SLUGS) == 2
assert len(NEW_TOPICS_V24) == 10  # basics 5 + analysis 2 + macro 1 + practical 2
print(f'OK — practical 4개, V24 누적 10개, 전체 {len(ALL_TOPICS)}개')
"
```
Expected: `OK — practical 4개, V24 누적 10개, 전체 21개`

- [ ] **Step 4: 커밋**

```bash
git add shared/db/migrations/seeds_education/practical.py
git commit -m "feat(education): B.4+B.5 — practical 2개 토픽 추가 (discovery_type, entry_price 추적)

discovery-type-guide (4종 발견 유형 판별),
entry-price-tracking (post_return으로 매매 복기).
sort_order 42~43.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: C — stories 카테고리 5개 토픽 추가 (신규 카테고리)

**Files:**
- Modify: `shared/db/migrations/seeds_education/stories.py`

**5개 신규 토픽** (모두 `category: "stories"`):

| slug | title | summary | difficulty | sort_order |
|---|---|---|---|---|
| `investor-legends` | 투자 거장 5인 — 버핏·멍거·린치·달리오·코스톨라니 | 5인의 투자 철학·대표 실적·핵심 명언을 한 페이지로 배웁니다. | beginner | 50 |
| `legendary-crashes` | 전설의 폭락 5대 케이스 — LTCM·리먼·차화정·2차전지·코로나 | 다섯 번의 큰 폭락에서 공통된 전조와 교훈을 배웁니다. | intermediate | 51 |
| `what-if-2015` | 10년 전 그 종목을 샀다면? — 삼성전자·네이버·카카오 시뮬레이션 | 한국 대표 3종목을 10년 전 1억씩 샀다면 어떻게 됐을지 시뮬레이션합니다. | beginner | 52 |
| `behavioral-biases` | 내가 빠지는 7가지 투자 심리 함정 | FOMO·손실회피·확증편향 등 7대 심리 함정과 자가 진단 체크를 배웁니다. | intermediate | 53 |
| `korea-market-timeline` | 한국 증시 25년 키워드 타임라인 (2000~2025) | IT버블에서 2차전지까지, 25년 한국 증시를 한 페이지로 훑습니다. | beginner | 54 |

#### C.1 `investor-legends` 본문 구성
- 섹션: 5인 각각 (워런 버핏, 찰리 멍거, 피터 린치, 레이 달리오, 앙드레 코스톨라니)
- 각 인물 섹션: 한 줄 정의, 대표 실적(연도·수익률), 핵심 철학 3줄, 한 줄 명언
- 표 1: 5인 비교 (출생·대표 펀드·연평균 수익률·키워드)
- examples: ①린치의 "10루타" 사례 (Fidelity Magellan 1977~1990 연 29%) — 일상 관찰의 힘

#### C.2 `legendary-crashes` 본문 구성
- 섹션: ① LTCM (1998) / ② 리먼 (2008) / ③ 차화정 (2011) / ④ 2차전지 (2023) / ⑤ 코로나 (2020) / ⑥ 다섯 번의 공통점
- 각 케이스: 발생 배경, 정점 가격→저점, 회복 기간, 직접 교훈
- 표 1: 5대 폭락 비교 (피크→저점 %, 회복 기간, 트리거)
- examples: ①LTCM — 노벨상 수상자 2명 + 레버리지 25배 → 4개월 만에 -90%

#### C.3 `what-if-2015` 본문 구성
- 섹션: ① 시뮬레이션 가정 / ② 삼성전자 / ③ NAVER / ④ 카카오 / ⑤ 셋의 합산 비교 / ⑥ 교훈
- 각 종목: 2015-04 시점 주가, 2025-04 시점 주가, 누적 수익률, 배당 재투자 가산
- 표 1: 3종목 + 코스피 비교 (1억 → 현재 가치)
- 표 2: 같은 기간 인플레이션·정기예금·코스피 비교
- examples: ①분산투자(3종목 균등) vs 한 종목 몰빵의 위험·수익 분석

#### C.4 `behavioral-biases` 본문 구성
- 섹션: 7개 편향 각각 (FOMO, 손실회피, 확증편향, 기준점 효과, 처분효과, 과신, 군중심리)
- 각 편향: 정의 1줄, 투자에서의 발현 2줄, 대응 규칙 1줄
- 표 1: 7대 편향 자가 진단 체크리스트 (Y/N)
- examples: ①처분효과 — 한국 개인투자자 평균 보유: 수익 23일 vs 손실 45일 (한국거래소 연구)

#### C.5 `korea-market-timeline` 본문 구성
- 섹션: 5년 단위 (2000~2004, 2005~2009, 2010~2014, 2015~2019, 2020~2025)
- 각 구간: 주요 사건 3~4개 (연도·키워드·코스피 수준)
- 표 1: 25년간 코스피 주요 변곡점 (날짜·지수·트리거)
- 표 2: 시대별 주도 섹터 (IT/조선/자동차/바이오/2차전지)
- examples: ①박스피 시대(2011~2016) — 5년간 +1% — 글로벌 강세장에서 한국만 소외

- [ ] **Step 1: 5개 토픽 dict를 `stories.py`의 `TOPICS`에 추가**

처음에 빈 리스트였던 stories.py에 5개를 한 번에 작성. 모든 토픽의 `category`는 `"stories"`.

- [ ] **Step 2: V24_SLUGS 갱신 (5개 모두)**

```python
V24_SLUGS: set[str] = {
    "investor-legends",
    "legendary-crashes",
    "what-if-2015",
    "behavioral-biases",
    "korea-market-timeline",
}
```

- [ ] **Step 3: 최종 검증 (15개 V24 완성)**

Run:
```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24, stories
assert len(stories.TOPICS) == 5
assert len(stories.V24_SLUGS) == 5
assert len(NEW_TOPICS_V24) == 15  # 5+2+1+2+5
assert len(ALL_TOPICS) == 26      # 11 + 15
slugs = [t['slug'] for t in ALL_TOPICS]
assert len(slugs) == len(set(slugs)), '중복 slug'
cats = {t['category'] for t in stories.TOPICS}
assert cats == {'stories'}, f'stories 카테고리 오염: {cats}'
print(f'OK — stories 5개, V24 합계 15개, 전체 {len(ALL_TOPICS)}개')
"
```
Expected: `OK — stories 5개, V24 합계 15개, 전체 26개`

- [ ] **Step 4: 카테고리·sort_order 분포 확인**

Run:
```bash
python -c "
from collections import Counter
from shared.db.migrations.seeds_education import ALL_TOPICS
by_cat = Counter(t['category'] for t in ALL_TOPICS)
print('카테고리:', dict(by_cat))
sorts = sorted([(t['sort_order'], t['slug']) for t in ALL_TOPICS])
for s, slug in sorts:
    print(f'  sort={s:3d} {slug}')
"
```
Expected (카테고리 부분):
```
카테고리: {'basics': 8, 'analysis': 4, 'risk': 2, 'macro': 3, 'practical': 4, 'stories': 5}
```
sort_order는 1~8(basics), 10~13(analysis), 20~21(risk), 30~32(macro), 40~43(practical), 50~54(stories) 순으로 출력되어야 함.

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/seeds_education/stories.py
git commit -m "feat(education): C — stories 5개 토픽 추가 (신규 카테고리)

investor-legends, legendary-crashes, what-if-2015,
behavioral-biases, korea-market-timeline.
sort_order 50~54로 마지막 배치 (핵심 교육 우선).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: UI — `_EDU_CATEGORIES` + `education.html` 아이콘 매핑 추가

**Files:**
- Modify: `api/routes/education.py:23-29`
- Modify: `api/templates/education.html:33`

- [ ] **Step 1: `api/routes/education.py:23-29` — stories 라벨 추가**

기존:
```python
_EDU_CATEGORIES = {
    "basics": "기초 개념",
    "analysis": "분석 기법",
    "risk": "리스크 관리",
    "macro": "매크로 경제",
    "practical": "실전 활용",
}
```

다음으로 변경:
```python
_EDU_CATEGORIES = {
    "basics": "기초 개념",
    "analysis": "분석 기법",
    "risk": "리스크 관리",
    "macro": "매크로 경제",
    "practical": "실전 활용",
    "stories": "투자 이야기",
}
```

- [ ] **Step 2: `api/templates/education.html:33` — 아이콘 매핑 추가**

기존 33줄 (한 줄):
```jinja
{% if cat_key == 'basics' %}&#x1F4D6;{% elif cat_key == 'analysis' %}&#x1F4CA;{% elif cat_key == 'risk' %}&#x1F6E1;{% elif cat_key == 'macro' %}&#x1F30D;{% elif cat_key == 'practical' %}&#x1F3AF;{% endif %}
```

다음으로 변경 (말미에 stories 분기 추가):
```jinja
{% if cat_key == 'basics' %}&#x1F4D6;{% elif cat_key == 'analysis' %}&#x1F4CA;{% elif cat_key == 'risk' %}&#x1F6E1;{% elif cat_key == 'macro' %}&#x1F30D;{% elif cat_key == 'practical' %}&#x1F3AF;{% elif cat_key == 'stories' %}&#x1F4DA;{% endif %}
```

`&#x1F4DA;` = 📚 (Books emoji)

- [ ] **Step 3: import 검증**

Run:
```bash
python -c "from api.routes.education import _EDU_CATEGORIES; assert 'stories' in _EDU_CATEGORIES; print(_EDU_CATEGORIES['stories'])"
```
Expected: `투자 이야기`

- [ ] **Step 4: 템플릿 문법 검증 (Jinja2 파싱)**

Run:
```bash
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('api/templates'))
t = env.get_template('education.html')
print('OK — Jinja2 파싱 성공')
"
```
Expected: `OK — Jinja2 파싱 성공`

- [ ] **Step 5: 커밋**

```bash
git add api/routes/education.py api/templates/education.html
git commit -m "feat(education): stories 카테고리 UI 노출 (라벨 + 📚 아이콘)

_EDU_CATEGORIES['stories'] = '투자 이야기'.
education.html 카테고리 아이콘 매핑에 stories → 📚(&#x1F4DA;) 추가.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 마이그레이션 v24 + SCHEMA_VERSION 24

**Files:**
- Modify: `shared/db/migrations/versions.py` (말미에 `_migrate_to_v24` 추가)
- Modify: `shared/db/migrations/__init__.py` (`_MIGRATIONS` dict에 한 줄 추가)
- Modify: `shared/db/schema.py:12` (`SCHEMA_VERSION = 23` → `24`)

- [ ] **Step 1: `versions.py` 말미에 `_migrate_to_v24` 함수 추가**

`shared/db/migrations/versions.py` 가장 마지막에 다음 함수 추가:

```python


def _migrate_to_v24(cur):
    """Education 신규 토픽 15개 추가 (basics 5 + analysis 2 + macro 1 + practical 2 + stories 5).

    stories 카테고리 신규 도입. 기존 11개 토픽은 ON CONFLICT (slug) DO NOTHING으로 보호.
    신규 DB의 경우 v21에서 26개 전체가 이미 시드되었으므로 v24는 사실상 no-op이 됨 (멱등).
    """
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
    print(f"[DB] v24: 교육 토픽 {len(NEW_TOPICS_V24)}개 추가 (stories 카테고리 도입)")

    cur.execute("INSERT INTO schema_version (version) VALUES (24) ON CONFLICT DO NOTHING")
```

**중요**: 마지막 줄(`INSERT INTO schema_version`)이 누락되면 멱등성 손상. 기존 `_migrate_to_v23` 등 다른 함수의 마지막 줄과 동일 패턴인지 한 번 더 확인할 것 (versions.py grep으로 `INSERT INTO schema_version`).

- [ ] **Step 2: `versions.py`의 다른 마이그레이션과 schema_version 등록 패턴 비교**

Run:
```bash
grep -n "schema_version" shared/db/migrations/versions.py | tail -10
```
Expected: 각 `_migrate_to_vN` 끝에 `INSERT INTO schema_version (version) VALUES (N)` 패턴이 있어야 함. v24도 동일 형식인지 확인.

- [ ] **Step 3: `shared/db/migrations/__init__.py` — `_MIGRATIONS` dict에 한 줄 추가**

[shared/db/migrations/__init__.py:36](shared/db/migrations/__init__.py#L36)의 `23: _v._migrate_to_v23,` 다음 줄에 추가:

```python
    23: _v._migrate_to_v23,
    24: _v._migrate_to_v24,
}
```

- [ ] **Step 4: `shared/db/schema.py:12` — SCHEMA_VERSION 증가**

기존:
```python
SCHEMA_VERSION = 23  # v23: ai_query_archive + app_logs.context + incident_reports
```

다음으로 변경:
```python
SCHEMA_VERSION = 24  # v24: education 신규 15토픽 + stories 카테고리
```

- [ ] **Step 5: import 검증**

Run:
```bash
python -c "
from shared.db.schema import SCHEMA_VERSION
assert SCHEMA_VERSION == 24
from shared.db.migrations import _MIGRATIONS
assert 24 in _MIGRATIONS
from shared.db.migrations.versions import _migrate_to_v24
print('OK — SCHEMA_VERSION=24, _migrate_to_v24 등록됨')
"
```
Expected: `OK — SCHEMA_VERSION=24, _migrate_to_v24 등록됨`

- [ ] **Step 6: 신규 DB에서 풀 마이그레이션 테스트 (선택, DB 권한 있을 때)**

테스트용 DB가 있을 경우:
```bash
DB_NAME=test_edu_v24 python -c "
from shared.config import DatabaseConfig
from shared.db import init_db
init_db(DatabaseConfig())
"
```
Expected 출력에 다음이 포함되어야 함:
```
[DB] v1 기본 스키마 생성 완료
... (v2~v23 출력)
[DB] 교육 토픽 26건 시드 데이터 삽입       ← v21에서 ALL_TOPICS 26개 시드
[DB] v24: 교육 토픽 15개 추가 (stories ...) ← v24 실행, 모두 충돌 → 0개 실제 삽입
[DB] 테이블 초기화 완료
```

이후 카테고리별 카운트 확인:
```bash
DB_NAME=test_edu_v24 python -c "
import psycopg2
from shared.config import DatabaseConfig
cfg = DatabaseConfig()
conn = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.name, user=cfg.user, password=cfg.password)
cur = conn.cursor()
cur.execute('SELECT category, COUNT(*) FROM education_topics GROUP BY category ORDER BY category')
for row in cur.fetchall():
    print(row)
conn.close()
"
```
Expected:
```
('analysis', 4)
('basics', 8)
('macro', 3)
('practical', 4)
('risk', 2)
('stories', 5)
```

테스트 DB 권한이 없으면 이 step은 skip (다음 step에서 운영 DB 시뮬레이션).

- [ ] **Step 7: 운영 DB(이미 v23) 시뮬레이션 — v24만 적용**

기존 운영 DB는 이미 v21에서 11개를 시드한 상태. v24만 적용했을 때 신규 15개가 추가되는지 검증.

수동 시뮬레이션:
```bash
python -c "
import psycopg2
from shared.config import DatabaseConfig
from shared.db.migrations.versions import _migrate_to_v24
cfg = DatabaseConfig()
conn = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.name, user=cfg.user, password=cfg.password)
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM education_topics')
before = cur.fetchone()[0]
print(f'before v24: {before}개')
_migrate_to_v24(cur)
cur.execute('SELECT COUNT(*) FROM education_topics')
after = cur.fetchone()[0]
print(f'after v24:  {after}개 (+{after-before})')
conn.commit()
conn.close()
"
```
Expected:
```
before v24: 11개
after v24:  26개 (+15)
[DB] v24: 교육 토픽 15개 추가 (stories 카테고리 도입)
```

만약 로컬 개발 DB가 이미 v23 상태가 아니라면 이 step은 본 PR 머지 후 라즈베리파이 배포 시점에 검증.

- [ ] **Step 8: 커밋**

```bash
git add shared/db/migrations/versions.py shared/db/migrations/__init__.py shared/db/schema.py
git commit -m "feat(db): v24 마이그레이션 — education 신규 15토픽 + stories 카테고리

_migrate_to_v24가 NEW_TOPICS_V24 (15개)를 ON CONFLICT DO NOTHING으로 INSERT.
SCHEMA_VERSION 23 → 24, _MIGRATIONS dict에 24번 등록.

운영 DB(v23): 신규 15개만 추가됨 (11→26).
신규 DB: v21에서 26개 전체 시드, v24는 멱등 no-op.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: 통합 검증 — UI 스모크 테스트

**Files:**
- Read-only: `api/main.py`, `api/templates/education.html`, `api/templates/education_topic.html`

- [ ] **Step 1: API 서버 기동 (개발)**

Run:
```bash
python -m api.main
```
또는 백그라운드:
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &
```

서버 로그에 다음이 보여야 함:
```
[DB] v24: 교육 토픽 15개 추가 (stories 카테고리 도입)
[DB] 테이블 초기화 완료
INFO:     Application startup complete.
```

만약 로컬 DB가 이미 v24 상태라면 v24 로그는 안 보임. `schema_version` 테이블에서 24가 있는지 확인:
```bash
python -c "
import psycopg2
from shared.config import DatabaseConfig
cfg = DatabaseConfig()
conn = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.name, user=cfg.user, password=cfg.password)
cur = conn.cursor()
cur.execute('SELECT version FROM schema_version ORDER BY version DESC LIMIT 5')
print([r[0] for r in cur.fetchall()])
conn.close()
"
```
Expected: `[24, 23, 22, 21, 20]` (또는 24가 최상위)

- [ ] **Step 2: 교육 페이지 HTTP 응답 확인**

브라우저에서 `http://localhost:8000/pages/education` 접속.

확인 항목:
- 6개 카테고리 섹션 모두 보임 (basics 📖, analysis 📊, risk 🛡, macro 🌍, practical 🎯, stories 📚)
- stories 섹션이 페이지 최하단에 위치
- 각 토픽 카드에 제목·요약·난이도 배지 표시
- 카테고리 필터 드롭다운에 "투자 이야기" 옵션 존재

스크린샷 또는 curl로 확인:
```bash
curl -s http://localhost:8000/pages/education | grep -oE '(stories|투자 이야기|&#x1F4DA;)' | sort -u
```
Expected: 3가지 모두 매칭

- [ ] **Step 3: 신규 토픽 상세 페이지 확인**

각 카테고리에서 신규 토픽 1개씩 진입해 본문·examples·prev/next 네비게이션 확인:
- `/pages/education/topic/financial-statements` (basics)
- `/pages/education/topic/foreign-institutional-flow` (analysis)
- `/pages/education/topic/scenario-thinking` (macro)
- `/pages/education/topic/discovery-type-guide` (practical)
- `/pages/education/topic/investor-legends` (stories)

각 페이지에서 확인:
- content 본문 마크다운 렌더링 (표 포함)
- examples 섹션 표시
- prev/next 토픽 네비게이션 동작

- [ ] **Step 4: AI 튜터 채팅 진입 가능 확인 (Free 티어)**

`/pages/education/chat`에서 stories 카테고리 토픽으로 새 채팅 생성 가능 여부 확인:
- 토픽 드롭다운에 신규 15개 모두 노출
- "투자 이야기" 카테고리의 `investor-legends` 선택 → 채팅방 진입
- 일일 5턴 제한 정상 동작 (Free 티어)

- [ ] **Step 5: DB 최종 상태 검증**

```bash
python -c "
import psycopg2
from shared.config import DatabaseConfig
cfg = DatabaseConfig()
conn = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.name, user=cfg.user, password=cfg.password)
cur = conn.cursor()

# 카테고리별 분포
cur.execute('SELECT category, COUNT(*) FROM education_topics GROUP BY category ORDER BY category')
print('카테고리:', dict(cur.fetchall()))

# 난이도별
cur.execute('SELECT difficulty, COUNT(*) FROM education_topics GROUP BY difficulty')
print('난이도:', dict(cur.fetchall()))

# sort_order 중복 확인
cur.execute('SELECT category, sort_order, COUNT(*) FROM education_topics GROUP BY category, sort_order HAVING COUNT(*) > 1')
dups = cur.fetchall()
print(f'중복 sort_order: {dups if dups else \"없음\"}')

# 전체
cur.execute('SELECT COUNT(*) FROM education_topics')
print(f'전체: {cur.fetchone()[0]}개')

conn.close()
"
```
Expected:
```
카테고리: {'analysis': 4, 'basics': 8, 'macro': 3, 'practical': 4, 'risk': 2, 'stories': 5}
난이도: {'beginner': 12, 'intermediate': 14}
중복 sort_order: 없음
전체: 26개
```

- [ ] **Step 6: API 서버 종료**

`python -m api.main`이 foreground이면 Ctrl+C, background면:
```bash
pkill -f "uvicorn api.main:app"
```

- [ ] **Step 7: 통합 검증 결과 기록 (선택, 이슈 발생 시)**

이슈가 있으면 [_docs/_exception/](../../_docs/_exception/)에 리포트 작성. 정상이면 skip.

- [ ] **Step 8: 검증 완료 표시 커밋 (선택)**

검증만 했고 코드 변경이 없으면 별도 커밋 불필요. 변경이 있었다면 (예: 본문 typo 수정) 별도 커밋:

```bash
git commit -m "fix(education): 검증 중 발견한 [구체적 이슈] 수정"
```

---

## 검증 체크리스트 (전체 PR 완료 시)

PR 생성 전 한 번 더 점검:

- [ ] `wc -l shared/db/migrations/seeds.py` → ~40줄 미만
- [ ] `git log --oneline | head -11` → Task 1~10 커밋 확인 (각 단계별 1개)
- [ ] `python -c "from shared.db.migrations.seeds_education import ALL_TOPICS, NEW_TOPICS_V24; assert len(ALL_TOPICS)==26 and len(NEW_TOPICS_V24)==15; print('OK')"`
- [ ] `python -c "from shared.db.schema import SCHEMA_VERSION; assert SCHEMA_VERSION==24; print('OK')"`
- [ ] DB에서 `SELECT COUNT(*) FROM education_topics` → 26
- [ ] UI 6개 카테고리 모두 렌더링 + stories 아이콘 표시
- [ ] 신규 토픽 5개(카테고리당 1개) 상세 페이지 정상 진입

## 라즈베리파이 배포 절차 (PR 머지 후)

```bash
cd ~/investment-advisor
git pull origin main                                  # PR 머지 완료된 main 받기
sudo systemctl restart investment-advisor-api.service # init_db()가 v24 자동 실행
sudo journalctl -u investment-advisor-api.service -f  # 로그 확인
# Expected: "[DB] v24: 교육 토픽 15개 추가 (stories 카테고리 도입)"
# Expected: "[DB] 테이블 초기화 완료"
# Expected: "Application startup complete."
```

확인:
- 브라우저에서 `https://<pi-domain>/pages/education` 새로고침 → stories 섹션 + 신규 토픽 15개 표시
- analyzer 서비스 restart 불필요 (교육은 API 측만 사용)
- pip install 불필요 (의존성 변화 없음)
