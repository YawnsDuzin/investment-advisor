# Education SVG Visualizations Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Phase 2 — 가치 중간 8 토픽에 손코딩 SVG 차트 10장 추가 + v37 UPDATE 마이그레이션. spec: `docs/superpowers/specs/2026-04-26-education-svg-phase-2-design.md`.

**Architecture:** Phase 1 패턴 답습. SVG 10장 정적 파일 추가, 시드 모듈 8 토픽 markdown 갱신, v37 마이그레이션은 v36과 동일한 `WHERE content IS DISTINCT FROM` 가드. 기존 검증 테스트 (`test_svg_files_exist`) 의 expected 리스트 확장 + Phase 2 전용 테스트 신설 (`test_v37_phase2_visual_topics_have_image_refs`).

**Tech Stack:** Phase 1과 동일. SVG 1.1 inline, viewBox 800×450, 다크 테마 6색 팔레트.

---

## File Structure

| 종류 | 경로 | 책임 |
|---|---|---|
| NEW | `api/static/edu/charts/stop-loss-1.svg` | 손실 비대칭성 곡선 |
| NEW | `api/static/edu/charts/position-sizing-1.svg` | 1% 룰 vs 몰빵 자본 곡선 |
| NEW | `api/static/edu/charts/foreign-institutional-flow-1.svg` | 외국인 누적 순매수 + 코스피 |
| NEW | `api/static/edu/charts/short-selling-squeeze-1.svg` | GameStop 2021.01 케이스 |
| NEW | `api/static/edu/charts/exchange-rates-1.svg` | 원달러·코스피 시계열 |
| NEW | `api/static/edu/charts/scenario-thinking-1.svg` | base/worse/better 의사결정 트리 |
| NEW | `api/static/edu/charts/legendary-crashes-1.svg` | 5대 폭락 비교 |
| NEW | `api/static/edu/charts/behavioral-biases-1.svg` | 7가지 심리 함정 매트릭스 |
| NEW | `api/static/edu/charts/behavioral-biases-2.svg` | 처분효과 보유기간 분포 |
| MOD | `tests/test_education_seeds.py` | `test_svg_files_exist` expected 확장 + `test_v37_phase2_visual_topics_have_image_refs` 신설 |
| MOD | `shared/db/migrations/seeds_education/risk.py` | stop-loss / position-sizing markdown |
| MOD | `shared/db/migrations/seeds_education/analysis.py` | foreign-institutional-flow / short-selling-squeeze |
| MOD | `shared/db/migrations/seeds_education/macro.py` | exchange-rates / scenario-thinking |
| MOD | `shared/db/migrations/seeds_education/stories.py` | legendary-crashes / behavioral-biases |
| MOD | `shared/db/migrations/versions.py` | `_migrate_to_v37()` |
| MOD | `shared/db/migrations/__init__.py` | `_MIGRATIONS[37]` |
| MOD | `shared/db/schema.py` | `SCHEMA_VERSION = 37` |

---

## SVG 작성 표준 (Phase 1과 동일)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450" font-family="system-ui, -apple-system, sans-serif">
  <rect width="800" height="450" fill="#0f1419"/>
</svg>
```

**팔레트**: `#0f1419`(배경) / `#1a1f2e`(보조) / `#2d3748`(그리드) / `#e2e8f0`(주요 텍스트) / `#94a3b8`(보조 텍스트) / `#4ade80`(녹) / `#ef4444`(적) / `#f59e0b`(황) / `#60a5fa`(청) / `#c084fc`(보).

**구조**: 제목(font 18, y=30) / 부제(font 12, y=50) / 차트(x=80, y=80, w=640, h=290) / 캡션(font 11, y=425~440). **모든 요소 y < 450**.

**Phase 1 follow-up**: off-palette 색상(`#dc2626`/`#fbbf24`/`#64748b`) 자제. 부득이한 경우 spec 메모.

---

## Task 1: 검증 테스트 보강 (TDD red)

**Files:** Modify `tests/test_education_seeds.py`

- [ ] **Step 1: `test_svg_files_exist` expected 리스트 확장 (Phase 2 10장 추가)**

기존 18개 항목에 다음 10개 추가 (정확한 위치는 expected 리스트 마지막 `]` 직전):

```python
        # Phase 2
        "stop-loss-1.svg", "position-sizing-1.svg",
        "foreign-institutional-flow-1.svg", "short-selling-squeeze-1.svg",
        "exchange-rates-1.svg", "scenario-thinking-1.svg",
        "legendary-crashes-1.svg",
        "behavioral-biases-1.svg", "behavioral-biases-2.svg",
```

- [ ] **Step 2: 신규 테스트 함수 추가 (파일 끝)**

```python
def test_v37_phase2_visual_topics_have_image_refs():
    """Phase 2 시각화 적용된 8개 슬러그의 content 에 SVG 이미지 참조가 1개 이상 존재."""
    phase2_slugs = {
        "stop-loss", "position-sizing",
        "foreign-institutional-flow", "short-selling-squeeze",
        "exchange-rates", "scenario-thinking",
        "legendary-crashes", "behavioral-biases",
    }
    matched = [t for t in ALL_TOPICS if t["slug"] in phase2_slugs]
    assert len(matched) == 8, f"expected 8 phase2 topics, found {len(matched)}"
    for t in matched:
        assert "/static/edu/charts/" in t["content"], \
            f"{t['slug']} missing SVG image reference"
```

- [ ] **Step 3: 두 테스트 실패 확인 (TDD red)**

Run: `pytest tests/test_education_seeds.py::test_svg_files_exist tests/test_education_seeds.py::test_v37_phase2_visual_topics_have_image_refs -v`
Expected: 둘 다 FAIL (SVG 미생성, content 미갱신).

기존 Phase 1 테스트는 그대로 PASS 유지.

- [ ] **Step 4: 커밋**

```bash
git add tests/test_education_seeds.py
git commit -m "test(edu-svg): Phase 2 검증 — 28 SVG + Phase 2 8 토픽 이미지 참조"
```

(Co-Authored-By 추가)

---

## Task 2: SVG 작성 — risk 카테고리 (2장)

**Files:**
- Create: `api/static/edu/charts/stop-loss-1.svg`
- Create: `api/static/edu/charts/position-sizing-1.svg`

### stop-loss-1.svg — 손실 비대칭성 곡선

**디자인 사양**:
- 차트 종류: 단일 라인 + 데이터 포인트 + 강조 마커
- 제목: "손실 비대칭성 — 손실이 클수록 회복은 기하급수적"
- 부제: "원금 회복에 필요한 수익률"
- 차트 영역 x=80, y=80, w=640, h=290
- X축: 손실률 (%), 0~-90 (왼쪽으로 갈수록 큰 손실), 6 라벨 (-10/-20/-30/-50/-70/-90)
- Y축: 회복 필요 수익률 (%), 0~1000, 격자 0/200/400/600/800/1000
- 데이터 6 점:
  - x=-10, y=11
  - x=-20, y=25
  - x=-30, y=43
  - x=-50, y=100
  - x=-70, y=233
  - x=-90, y=900
- 좌표 변환: x_svg = 80 + ((|loss|-10)/80)*640, y_svg = 370 - (recovery/1000)*290
- 라인 색 `#ef4444`, stroke-width 2.5
- 각 점 마커 (r=5, fill `#ef4444`) + 수치 라벨 (e.g., "+11%", "+100%")
- -50% 위치 강조 (세로 점선 `#f59e0b`, "흔한 임계점" 라벨)
- 캡션 (y=425): "손실은 비대칭이다 — -50% 회복엔 +100%가 필요"

### position-sizing-1.svg — 1% 룰 vs 몰빵

**디자인 사양**:
- 차트 종류: 3 라인 차트 (자본 곡선)
- 제목: "포지션 사이징 — 10번 연속 손실 시 자본 변화"
- 부제: "1% 룰 / 5% 룰 / 몰빵(50%) 비교"
- 차트 영역 x=80, y=80, w=640, h=290
- X축: 매매 횟수, 0~10 (11 라벨), 균등 간격
  - x_svg = 80 + i * 64
- Y축: 잔여 자본 (%), 0~100, 격자 0/20/40/60/80/100
  - y_svg = 370 - capital * 290 / 100
- **3 라인** (각 11점, idx 0~10):
  - **1% 룰** (`#4ade80`, stroke-width 2.5):
    - 100, 99.0, 98.0, 97.0, 96.1, 95.1, 94.1, 93.2, 92.3, 91.4, 90.4
  - **5% 룰** (`#f59e0b`, stroke-width 2.5):
    - 100, 95.0, 90.3, 85.7, 81.5, 77.4, 73.5, 69.8, 66.3, 63.0, 59.9
  - **몰빵 50%** (`#ef4444`, stroke-width 2.5):
    - 100, 50.0, 25.0, 12.5, 6.3, 3.1, 1.6, 0.8, 0.4, 0.2, 0.1
- 각 라인 끝점 라벨 (font-size 12, 같은 색):
  - "1% 룰: 90%"
  - "5% 룰: 60%"
  - "몰빵: 0.1%"
- 우상단 범례 박스 (x=130, y=95, w=180, h=70, fill `#1a1f2e` opacity 0.5)
- 캡션 (y=425): "사이징은 방어 — 종목 선정은 공격, 망하는 사람은 사이징을 빼먹는다"

- [ ] **Step 1~2: SVG 2장 작성**
- [ ] **Step 3: XML 유효성**

```bash
python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/stop-loss-1.svg', 'api/static/edu/charts/position-sizing-1.svg']]; print('OK')"
```

- [ ] **Step 4: 커밋**

```bash
git add api/static/edu/charts/stop-loss-1.svg api/static/edu/charts/position-sizing-1.svg
git commit -m "feat(edu-svg): Phase 2 risk 차트 2장 — 손실 비대칭성·포지션 사이징"
```

---

## Task 3: SVG 작성 — analysis 카테고리 (2장)

**Files:**
- Create: `api/static/edu/charts/foreign-institutional-flow-1.svg`
- Create: `api/static/edu/charts/short-selling-squeeze-1.svg`

### foreign-institutional-flow-1.svg — 외국인 누적 순매수 + 코스피

**디자인 사양**:
- 차트 종류: 막대(누적 순매수) + 라인(코스피) 이중 Y축
- 제목: "외국인 누적 순매수 + 코스피 (2023~2024)"
- 부제: "외국인 매수 → 코스피 상승 동행"
- 차트 영역 x=80, y=80, w=640, h=290
- X축: 분기 라벨 8개 (2023Q1, Q2, Q3, Q4, 2024Q1, Q2, Q3, Q4)
  - x_svg = 80 + i * 91.4
- 좌Y축: 누적 순매수 (조원), -10~+30 범위, 격자 -10/0/10/20/30
- 우Y축: 코스피 (천), 2.2~3.0 범위
- **외국인 누적 순매수 막대** (각 분기 누적 변화량):
  - 데이터 (조원): [+5, +12, +8, +18, +25, +20, +15, +20]
  - 막대 폭 50, 색상: 양수 `#4ade80`, 음수 `#ef4444` (모두 양수라 녹)
  - 막대 base = 0% line = 370 - 10/40*290 = 297.5
- **코스피 라인** (`#60a5fa`, stroke-width 2.5):
  - 데이터 (천): [2.42, 2.55, 2.45, 2.65, 2.75, 2.80, 2.59, 2.65]
  - y = 370 - (kospi - 2.2) * 290 / 0.8
- 우상단 범례
- 캡션 (y=425): "외국인 순매수가 누적 +25조 도달한 2024Q1, 코스피도 동반 상승"

### short-selling-squeeze-1.svg — GameStop 2021.01

**디자인 사양**:
- 차트 종류: 가격 라인 + 공매도 잔고 비율 라인 (이중 Y축)
- 제목: "GameStop 숏스퀴즈 (2021.01)"
- 부제: "공매도 잔고 30%+ → 폭등·청산"
- 차트 영역 x=80, y=80, w=640, h=290
- X축: 일자 (2020.12.01, 12.15, 2021.01.01, 01.15, 01.27, 02.01, 02.15)
  - x_svg = 80 + i * 106.7 (7 점 균등)
- 좌Y축: 가격 ($), 0~500 범위
- 우Y축: 공매도 잔고 (% of float), 0~120 범위 (이상치 포함)
- **가격 라인** (`#e2e8f0`, stroke-width 2):
  - 데이터: [20, 25, 35, 80, 483, 250, 50] (2021.01.27 정점)
- **공매도 잔고 라인** (`#ef4444`, stroke-width 2):
  - 데이터: [110, 95, 85, 70, 50, 30, 25] (점진 감소)
- 1.27 정점 마커 (r=6, fill `#f59e0b`) + 라벨 "숏스퀴즈 정점 +2300%"
- 캡션 (y=425): "Reddit r/wallstreetbets — 개인 매수 vs 헤지펀드 청산 (Melvin Capital -53%)"

- [ ] **Step 1~2: SVG 2장 작성**
- [ ] **Step 3: XML 검증**
- [ ] **Step 4: 커밋**

```bash
git add api/static/edu/charts/foreign-institutional-flow-1.svg api/static/edu/charts/short-selling-squeeze-1.svg
git commit -m "feat(edu-svg): Phase 2 analysis 차트 2장 — 외국인 수급·GameStop 숏스퀴즈"
```

---

## Task 4: SVG 작성 — macro 카테고리 (2장)

**Files:**
- Create: `api/static/edu/charts/exchange-rates-1.svg`
- Create: `api/static/edu/charts/scenario-thinking-1.svg`

### exchange-rates-1.svg — 원달러·코스피 역상관

**디자인 사양**:
- 차트 종류: 이중 Y축 라인
- 제목: "원달러·코스피 (2018~2024) — 역상관 패턴"
- 부제: "원화 약세 → 외국인 이탈 → 코스피 하락"
- 차트 영역 x=80, y=80, w=640, h=290
- X축: 연도 7개 (2018, 2019, 2020, 2021, 2022, 2023, 2024)
- 좌Y축: 원달러 (원), 1100~1400, 격자 1100/1200/1300/1400
- 우Y축: 코스피 (천), 1.5~3.0
- **원달러 라인** (`#f59e0b`, stroke-width 2.5):
  - 데이터 (연말): [1115, 1158, 1090, 1188, 1265, 1290, 1310]
- **코스피 라인** (`#60a5fa`, stroke-width 2.5):
  - 데이터 (천): [2.04, 2.20, 2.87, 2.97, 2.24, 2.65, 2.59]
- 2022 역상관 강조 (점선 박스 `#ef4444`)
- 캡션 (y=425): "Source: 한국은행 / KRX"

### scenario-thinking-1.svg — base/worse/better 의사결정 트리

**디자인 사양**:
- 차트 종류: 의사결정 트리 (좌→우 분기)
- 제목: "시나리오 사고 — base/worse/better"
- 부제: "예: 2024 Fed 피벗 시나리오"
- 좌측 (x=100, y=210): "현 상황" 박스 (rect 120×60, fill `#1a1f2e`, stroke `#94a3b8`)
- 분기 3개 (우측 x=480~700, y 분산):
  - **better** (y=110): box 200×60, fill `#4ade80` opacity 0.2, stroke `#4ade80`
    - 텍스트 "Best 25%" + "Fed 6월 인하" + "코스피 +15%"
  - **base** (y=210): box, fill `#60a5fa` opacity 0.2, stroke `#60a5fa`
    - 텍스트 "Base 50%" + "9월 첫 인하" + "코스피 +5%"
  - **worse** (y=310): box, fill `#ef4444` opacity 0.2, stroke `#ef4444`
    - 텍스트 "Worse 25%" + "인하 지연·인플레 재발" + "코스피 -10%"
- 좌측 박스 → 각 분기 박스 연결선 (`#94a3b8`, stroke 1.5)
- 각 연결선 중간에 확률 라벨
- 캡션 (y=425): "확률 가중 기대값 = base 0.5 × +5 + better 0.25 × +15 + worse 0.25 × -10 = +3.75%"

- [ ] **Step 1~2: SVG 2장 작성**
- [ ] **Step 3: XML 검증**
- [ ] **Step 4: 커밋**

```bash
git add api/static/edu/charts/exchange-rates-1.svg api/static/edu/charts/scenario-thinking-1.svg
git commit -m "feat(edu-svg): Phase 2 macro 차트 2장 — 환율·코스피 + 시나리오 트리"
```

---

## Task 5: SVG 작성 — stories 카테고리 (3장)

**Files:**
- Create: `api/static/edu/charts/legendary-crashes-1.svg`
- Create: `api/static/edu/charts/behavioral-biases-1.svg`
- Create: `api/static/edu/charts/behavioral-biases-2.svg`

### legendary-crashes-1.svg — 5대 폭락 비교

**디자인 사양**:
- 차트 종류: 수평 막대 비교 (5개)
- 제목: "전설의 폭락 5대 — 최대 낙폭 비교"
- 부제: "1998~2023 글로벌·한국 5대 폭락"
- 차트 영역 x=200, y=80, w=520, h=290
- 5개 막대 (각 폭 25, 간격 50):
  - LTCM (1998, y=110): -90%, fill `#ef4444`, 라벨 "LTCM 1998 (헤지펀드 청산)"
  - 리먼 (2008, y=160): -55%, fill `#ef4444` opacity 0.85
  - 차화정 (2011, y=210): -50%
  - 2차전지 (2023, y=260): -40%
  - 코로나 (2020, y=310): -34%
- 좌측 사건명 라벨 (text-anchor end, x=190)
- 막대 길이: -90% = 520, -34% = 196 (선형 매핑)
  - bar_width = abs(loss) / 100 * 520
- 막대 우측 끝에 % 수치
- 캡션 (y=425): "각 폭락의 직접 원인은 다르지만 '레버리지·과열·지나친 자신감' 공통"

### behavioral-biases-1.svg — 7가지 심리 함정 매트릭스

**디자인 사양**:
- 차트 종류: 7개 셀 그리드 (정사각형 또는 3+4 배치)
- 제목: "투자자가 빠지는 7가지 심리 함정"
- 부제: "행동경제학이 알려주는 비합리적 패턴"
- 그리드 레이아웃: 4 + 3 (위 4셀, 아래 3셀 가운데 정렬)
  - 셀 크기 165×130 (간격 10)
  - 위 행 (y=80~210, 4셀): x = 80, 255, 430, 605
  - 아래 행 (y=220~350, 3셀, 가운데 정렬): x = 167, 342, 517
- 7개 함정 (각 셀에 함정명 + 1줄 설명):
  1. **FOMO** (`#ef4444`): "남들 다 사니까 나도..." 추격 매수
  2. **손실회피**: 작은 이익은 빨리, 큰 손실은 끝까지
  3. **확증편향**: 내 판단을 지지하는 정보만 본다
  4. **처분효과**: 수익 23일·손실 45일 보유
  5. **닻 효과**: 매수가가 기준점이 된다
  6. **과신**: "이번엔 다르다"
  7. **후회회피**: 결정 자체를 미룬다
- 셀 배경: `#1a1f2e` opacity 0.5, stroke 1
- 함정명 폰트 14, fill `#f59e0b`
- 설명 폰트 11, fill `#e2e8f0`
- 캡션 (y=425): "Source: Daniel Kahneman 'Thinking, Fast and Slow' + 한국 거래소 분석"

### behavioral-biases-2.svg — 처분효과 보유기간 분포

**디자인 사양**:
- 차트 종류: 두 분포 (히스토그램)
- 제목: "처분효과 — 한국 개인 투자자 보유기간"
- 부제: "수익 종목 평균 23일 / 손실 종목 평균 45일"
- 차트 영역 x=80, y=80, w=640, h=290
- X축: 보유 일수, 0~120 (10 단위)
- Y축: 빈도 (상대), 0~100
- **수익 분포** (`#4ade80`, opacity 0.6):
  - 종모양, 정점 23일, 우편향 가벼움
  - 막대 12개: x_centers = 5, 15, 25, 35, ...; heights = [40, 75, 95, 70, 45, 25, 15, 8, 5, 3, 2, 1]
- **손실 분포** (`#ef4444`, opacity 0.6):
  - 평탄한 종모양, 정점 45일, 긴 꼬리
  - heights = [10, 25, 40, 55, 70, 75, 65, 50, 35, 25, 15, 10]
- 두 분포 평균 표시 (수직 점선): 23일 (`#4ade80`), 45일 (`#ef4444`)
- 우상단 범례
- 캡션 (y=425): "사람의 본능 — '본전 회복하면 팔자' (이미 손실은 더 커지고 있다)"

- [ ] **Step 1~3: SVG 3장 작성**
- [ ] **Step 4: XML 검증**

```bash
python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/legendary-crashes-1.svg', 'api/static/edu/charts/behavioral-biases-1.svg', 'api/static/edu/charts/behavioral-biases-2.svg']]; print('OK')"
```

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/legendary-crashes-1.svg api/static/edu/charts/behavioral-biases-*.svg
git commit -m "feat(edu-svg): Phase 2 stories 차트 3장 — 5대 폭락·심리 함정·처분효과"
```

---

## Task 6: 시드 markdown 8 토픽 갱신

**Files:**
- Modify: `shared/db/migrations/seeds_education/risk.py` (stop-loss, position-sizing)
- Modify: `shared/db/migrations/seeds_education/analysis.py` (foreign-institutional-flow, short-selling-squeeze)
- Modify: `shared/db/migrations/seeds_education/macro.py` (exchange-rates, scenario-thinking)
- Modify: `shared/db/migrations/seeds_education/stories.py` (legendary-crashes, behavioral-biases)

각 토픽 content 안에 markdown 이미지 + caption 삽입. 다른 필드 변경 금지.

### 삽입 위치 가이드

| 토픽 | 위치 | 이미지 |
|---|---|---|
| stop-loss | "## 손실의 비대칭성" 표 *직후* | stop-loss-1.svg |
| position-sizing | "### 같은 수익률, 다른 결과" 표 *직후* | position-sizing-1.svg |
| foreign-institutional-flow | 본문 핵심 사례 섹션 *직후* (실제 토픽 read 후 결정) | foreign-institutional-flow-1.svg |
| short-selling-squeeze | GameStop 사례 섹션 *직후* | short-selling-squeeze-1.svg |
| exchange-rates | 환율↔주가 관계 핵심 단락 *직후* | exchange-rates-1.svg |
| scenario-thinking | base/worse/better 정의 단락 *직후* | scenario-thinking-1.svg |
| legendary-crashes | 5대 폭락 도입 단락 *직후* | legendary-crashes-1.svg |
| behavioral-biases | "## 7가지 심리 함정" 도입 *직후* (1.svg) / "## 처분효과" 섹션 *직후* (2.svg) | behavioral-biases-1.svg / behavioral-biases-2.svg |

### 삽입 형식 (Phase 1과 동일)

```markdown

![차트: <한 줄 설명>](/static/edu/charts/<slug>-<n>.svg)

*<caption 1~2줄>*

```

### 권장 caption

- stop-loss: "*-50% 손실은 +100% 수익으로만 회복 — 손절은 작게, 익절은 크게*"
- position-sizing: "*1% 룰을 지킨 사람만 다음 상승장을 누린다*"
- foreign-institutional-flow: "*외국인 순매수 누적은 코스피의 선행 지표*"
- short-selling-squeeze: "*공매도 잔고 30%+ 종목은 단기 폭등 후 폭락 — 양면의 칼*"
- exchange-rates: "*원달러 ↑ → 외국인 이탈 → 코스피 ↓ — 글로벌 자금의 신호*"
- scenario-thinking: "*확률 × 결과 = 기대값. 시나리오 사고가 감정적 매매를 막는다*"
- legendary-crashes: "*직접 원인은 달라도 패턴은 같다 — 레버리지·과열·자신감*"
- behavioral-biases (1): "*7가지 함정 — 알고 있어도 빠진다*"
- behavioral-biases (2): "*수익은 빨리, 손실은 오래 — 본능과 정반대로 행동해야 한다*"

- [ ] **Step 1: risk.py 갱신** (stop-loss, position-sizing)

검증: `python -c "from shared.db.migrations.seeds_education import risk; assert all('/static/edu/charts/' in t['content'] for t in risk.TOPICS if t['slug'] in ('stop-loss', 'position-sizing')); print('OK')"`

- [ ] **Step 2: analysis.py 갱신** (foreign-institutional-flow, short-selling-squeeze)

검증: `python -c "from shared.db.migrations.seeds_education import analysis; assert all('/static/edu/charts/' in t['content'] for t in analysis.TOPICS if t['slug'] in ('foreign-institutional-flow', 'short-selling-squeeze')); print('OK')"`

- [ ] **Step 3: macro.py 갱신** (exchange-rates, scenario-thinking)

검증: `python -c "from shared.db.migrations.seeds_education import macro; assert all('/static/edu/charts/' in t['content'] for t in macro.TOPICS if t['slug'] in ('exchange-rates', 'scenario-thinking')); print('OK')"`

- [ ] **Step 4: stories.py 갱신** (legendary-crashes, behavioral-biases)

특별: behavioral-biases는 *2장* 삽입.

검증: `python -c "from shared.db.migrations.seeds_education import stories; t = next(t for t in stories.TOPICS if t['slug'] == 'behavioral-biases'); assert t['content'].count('/static/edu/charts/') == 2; print('OK')"`

- [ ] **Step 5: 통합 검증**

```bash
pytest tests/test_education_seeds.py::test_v37_phase2_visual_topics_have_image_refs -v
```
Expected: PASS

또한 전체 회귀:
```bash
pytest tests/test_education_seeds.py -v
```
Expected: 14/14 PASS (기존 13 + Phase 2 신규 1).

- [ ] **Step 6: 커밋**

```bash
git add shared/db/migrations/seeds_education/*.py
git commit -m "feat(edu-svg): Phase 2 시드 markdown 8 토픽에 SVG 이미지 참조 삽입"
```

---

## Task 7: v37 마이그레이션

**Files:**
- Modify: `shared/db/schema.py`
- Modify: `shared/db/migrations/__init__.py`
- Modify: `shared/db/migrations/versions.py`

### Step 1: versions.py 끝에 `_migrate_to_v37` 추가

```python
def _migrate_to_v37(cur) -> None:
    """Education Phase 2 시각화 — 8개 토픽 markdown content 갱신.

    v36 (Phase 1, 14 토픽) 와 동일한 UPDATE 패턴.
    멱등성: WHERE content IS DISTINCT FROM 가드.
    신규 DB 의 경우 v21 시드에 이미 Phase 2 content 가 들어가므로 v37 도 no-op.
    """
    from shared.db.migrations.seeds_education import ALL_TOPICS

    phase2_slugs = {
        "stop-loss", "position-sizing",
        "foreign-institutional-flow", "short-selling-squeeze",
        "exchange-rates", "scenario-thinking",
        "legendary-crashes", "behavioral-biases",
    }

    affected = 0
    for t in ALL_TOPICS:
        if t["slug"] not in phase2_slugs:
            continue
        cur.execute(
            """UPDATE education_topics
               SET content = %s
               WHERE slug = %s
                 AND content IS DISTINCT FROM %s""",
            (t["content"], t["slug"], t["content"]),
        )
        affected += cur.rowcount

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (37)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v37: Phase 2 시각화 토픽 content 갱신 {affected}건 (대상 8건)")
```

### Step 2: __init__.py 의 `_MIGRATIONS` 에 37 추가

```python
    36: _v._migrate_to_v36,
    37: _v._migrate_to_v37,
}
```

### Step 3: schema.py 갱신

```python
SCHEMA_VERSION = 37  # v37: education Phase 2 — 8 토픽 시각화 SVG 참조 삽입
```

- [ ] **Step 4: 동적 검증**

```bash
python -c "
from shared.db.schema import SCHEMA_VERSION
from shared.db.migrations import _MIGRATIONS
from shared.db.migrations.versions import _migrate_to_v37
print('SCHEMA_VERSION:', SCHEMA_VERSION)
print('37 in _MIGRATIONS:', 37 in _MIGRATIONS)
print('callable:', callable(_migrate_to_v37))
"
```
Expected: `37`, `True`, `True`.

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/versions.py shared/db/migrations/__init__.py shared/db/schema.py
git commit -m "feat(db): v37 마이그레이션 — education Phase 2 시각화 content 갱신"
```

---

## Task 8: 통합 검증 + 프롬프트 commit

**Files:** (검증만 + 프롬프트 commit)

- [ ] **Step 1: 전체 검증 테스트**

```bash
pytest tests/test_education_seeds.py -v
```
Expected: 14/14 PASS — 기존 13 + Phase 2 신규 1.

- [ ] **Step 2: SVG 28장 일괄 XML 검증**

```bash
python -c "
import os
import xml.etree.ElementTree as ET
base = 'api/static/edu/charts'
files = sorted(os.listdir(base))
print(f'Total SVG files: {len(files)}')
for f in files:
    ET.parse(os.path.join(base, f))
print('All SVG files valid XML')
"
```
Expected: `Total SVG files: 28`, `All SVG files valid XML`.

- [ ] **Step 3: v37 마이그레이션 등록 확인**

```bash
python -c "from shared.db.schema import SCHEMA_VERSION; assert SCHEMA_VERSION == 37, f'expected 37, got {SCHEMA_VERSION}'; print('OK')"
```

- [ ] **Step 4: 22 토픽 / 28 SVG 참조 카운트**

```bash
python -c "
from shared.db.migrations.seeds_education import ALL_TOPICS
visual_slugs = {
    'per-pbr-roe', 'business-cycle', 'chart-key-five', 'momentum-investing',
    'diversification', 'risk-adjusted-return', 'correlation-trap',
    'interest-rates', 'yield-curve-inversion',
    'what-if-2015', 'korea-market-timeline', 'tesla-eight-years',
    'factor-six-axes', 'market-regime-reading',
    'stop-loss', 'position-sizing',
    'foreign-institutional-flow', 'short-selling-squeeze',
    'exchange-rates', 'scenario-thinking',
    'legendary-crashes', 'behavioral-biases',
}
total_refs = sum(t['content'].count('/static/edu/charts/') for t in ALL_TOPICS if t['slug'] in visual_slugs)
print(f'Total SVG references in {len(visual_slugs)} visual topics: {total_refs}')
assert total_refs == 28, f'expected 28 SVG references, got {total_refs}'
"
```
Expected: `Total SVG references in 22 visual topics: 28`.

- [ ] **Step 5: 프롬프트 기록 commit (CLAUDE.md 룰)**

`_docs/_prompts/20260426_prompt.md` 가 본 conversation 내 modified 상태인지 확인.

```bash
git status --short _docs/_prompts/
```

modified 라면:
```bash
git add _docs/_prompts/20260426_prompt.md
git commit -m "docs(prompts): 2026-04-26 conversation Phase 2 시각화 — 통합 검증 완료"
```

(Co-Authored-By 추가)

unmodified 라면 skip + 보고에 명시.

- [ ] **Step 6: 최종 git log + 통계 보고**

```bash
git log --oneline -15
```

기대: Phase 2 의 8 task 분 commit + 프롬프트 commit 모두 정상.

---

## Self-Review

**Spec coverage**:
- spec §2.1 차트 매핑 10장 → Task 2~5 분배 ✓
- spec §2.2 SVG 표준 → Plan 상단 + 각 task 디자인 사양 ✓
- spec §2.3 markdown 형식 → Task 6 가이드 ✓
- spec §3.1 SVG 정적 파일 10장 → Task 2~5 ✓
- spec §3.2 시드 모듈 4개 markdown → Task 6 ✓
- spec §3.3 v37 마이그레이션 → Task 7 ✓
- spec §3.4 검증 테스트 → Task 1 + Task 8 ✓
- spec §5 검증 계획 → Task 8 ✓

**Placeholder scan**: TBD/TODO 없음. 모든 SVG 디자인 사양 구체적. 모든 마이그레이션 코드 명시.

**Type/이름 일관성**: phase2_slugs (8개 set) Task 1 / Task 7 / Task 8 일치. SVG 파일명 (`<slug>-<n>.svg`) 일관.

**잠재 위험**: SVG 손코딩 시 디자인 일관성은 작성자 (subagent) 역량 의존 — Phase 1 패턴 답습으로 완화. v37 UPDATE 는 v36과 동일 패턴이라 위험 0.
