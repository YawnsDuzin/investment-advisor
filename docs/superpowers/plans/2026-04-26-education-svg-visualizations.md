# Education SVG Visualizations Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 14개 가치 ↑↑ Education 토픽에 손코딩 SVG 차트 18장 추가 + 시드 markdown content 갱신 + v36 UPDATE 마이그레이션. spec: `docs/superpowers/specs/2026-04-26-education-svg-visualizations-design.md`.

**Architecture:** 정적 SVG 18장을 `api/static/edu/charts/` 에 생성. 시드 모듈 `content` markdown 안에 `![](/static/edu/charts/<slug>-<n>.svg)` 참조 삽입. v36 마이그레이션이 운영 DB 의 `education_topics.content` 를 UPDATE 로 동기화 (멱등성: `WHERE content IS DISTINCT FROM` 가드). 라이브러리 의존성 0 — 모든 SVG 손코딩, viewBox 800×450, 다크 테마 통일 팔레트.

**Tech Stack:** SVG 1.1 inline, Python 3.10+, PostgreSQL psycopg2, pytest. FastAPI `/static` mount 기존 활용.

---

## File Structure

| 종류 | 경로 | 책임 |
|---|---|---|
| NEW | `api/static/edu/charts/*.svg` (18장) | 토픽별 시각화 SVG 정적 파일 |
| MOD | `tests/test_education_seeds.py` | 검증 테스트 2건 추가 (`test_svg_files_exist`, `test_v36_visual_topics_have_image_refs`) |
| MOD | `shared/db/migrations/seeds_education/basics.py` | per-pbr-roe / business-cycle content 갱신 |
| MOD | `shared/db/migrations/seeds_education/analysis.py` | chart-key-five / momentum-investing content 갱신 |
| MOD | `shared/db/migrations/seeds_education/risk.py` | diversification / risk-adjusted-return / correlation-trap content 갱신 |
| MOD | `shared/db/migrations/seeds_education/macro.py` | interest-rates / yield-curve-inversion content 갱신 (ASCII art 제거) |
| MOD | `shared/db/migrations/seeds_education/stories.py` | what-if-2015 / korea-market-timeline / tesla-eight-years content 갱신 |
| MOD | `shared/db/migrations/seeds_education/tools.py` | factor-six-axes / market-regime-reading content 갱신 |
| MOD | `shared/db/migrations/versions.py` | `_migrate_to_v36()` 함수 추가 — UPDATE 패턴 신설 |
| MOD | `shared/db/migrations/__init__.py` | `_MIGRATIONS` dict 에 `36: _v._migrate_to_v36,` 추가 |
| MOD | `shared/db/schema.py` | `SCHEMA_VERSION = 35` → `36` |

---

## SVG 작성 표준 (모든 Task 2~7 공통)

**필수 헤더 (모든 SVG 파일 시작)**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450" font-family="system-ui, -apple-system, sans-serif">
  <rect width="800" height="450" fill="#0f1419"/>
  <!-- 차트 본문 -->
</svg>
```

**색상 팔레트 (HEX)**:
- 배경: `#0f1419`
- 보조 배경: `#1a1f2e`
- 그리드선: `#2d3748` (opacity 0.5)
- 주요 텍스트: `#e2e8f0`
- 보조 텍스트: `#94a3b8`
- 액센트 1 녹 (상승·긍정): `#4ade80`
- 액센트 2 적 (하락·부정): `#ef4444`
- 액센트 3 황 (강조·경고): `#f59e0b`
- 액센트 4 청 (정보·중립): `#60a5fa`
- 액센트 5 보 (보조 시리즈): `#c084fc`

**구조 표준**:
- 상단 60px: 제목 (font-size 18, fill `#e2e8f0`) + 부제 (font-size 12, fill `#94a3b8`)
- 차트 영역: x=60, y=70, width=680, height=320
- 하단 30px: 출처/캡션 (font-size 11, fill `#94a3b8`)

---

## Task 1: 검증 테스트 신설 (TDD 시작점)

**Files:**
- Modify: `tests/test_education_seeds.py`

- [ ] **Step 1: 검증 테스트 2건 추가**

`tests/test_education_seeds.py` 파일 끝에 다음 함수 2개 추가 (기존 함수는 변경 금지):

```python
def test_v36_visual_topics_have_image_refs():
    """V36 시각화 적용된 14개 슬러그의 content 에 SVG 이미지 참조가 1개 이상 존재."""
    visual_slugs = {
        "per-pbr-roe", "business-cycle", "chart-key-five",
        "momentum-investing", "diversification", "risk-adjusted-return",
        "correlation-trap", "interest-rates", "yield-curve-inversion",
        "what-if-2015", "korea-market-timeline", "tesla-eight-years",
        "factor-six-axes", "market-regime-reading",
    }
    matched = [t for t in ALL_TOPICS if t["slug"] in visual_slugs]
    assert len(matched) == 14, f"expected 14 visual topics, found {len(matched)}"
    for t in matched:
        assert "/static/edu/charts/" in t["content"], \
            f"{t['slug']} missing SVG image reference"


def test_svg_files_exist():
    """모든 시각화 토픽의 차트 파일이 디스크에 존재."""
    import os
    base = "api/static/edu/charts"
    expected = [
        "per-pbr-roe-1.svg", "business-cycle-1.svg",
        "chart-key-five-1.svg", "chart-key-five-2.svg",
        "momentum-investing-1.svg", "diversification-1.svg",
        "risk-adjusted-return-1.svg", "risk-adjusted-return-2.svg",
        "correlation-trap-1.svg", "interest-rates-1.svg",
        "yield-curve-1.svg", "yield-curve-2.svg",
        "what-if-2015-1.svg", "korea-market-timeline-1.svg",
        "tesla-eight-years-1.svg", "factor-six-axes-1.svg",
        "factor-six-axes-2.svg", "market-regime-1.svg",
    ]
    missing = [f for f in expected if not os.path.exists(os.path.join(base, f))]
    assert not missing, f"missing SVG files: {missing}"
```

- [ ] **Step 2: 실행하여 두 테스트 모두 실패 확인 (TDD red)**

Run: `pytest tests/test_education_seeds.py::test_v36_visual_topics_have_image_refs tests/test_education_seeds.py::test_svg_files_exist -v`
Expected: 둘 다 FAIL — 시드 content 에 이미지 참조 없음, SVG 파일 미생성.

- [ ] **Step 3: 커밋**

```bash
git add tests/test_education_seeds.py
git commit -m "test(edu-svg): v36 시각화 검증 — SVG 파일 존재 + content 이미지 참조"
```

(Co-Authored-By 추가)

---

## Task 2: SVG 작성 — basics 카테고리 (2장)

**Files:**
- Create: `api/static/edu/charts/per-pbr-roe-1.svg`
- Create: `api/static/edu/charts/business-cycle-1.svg`

### per-pbr-roe-1.svg — 업종별 PER 분포 (수직 막대)

**디자인 사양**:
- 차트 종류: 수직 막대 차트 4개 (업종별 평균 PER)
- 제목: "업종별 PER 평균 (한국 시장)"
- 부제: "사이클 산업과 성장 산업의 차이"
- X축: 업종명 — 은행, 유틸리티, IT/플랫폼, 바이오
- Y축: PER (배), 0~50 범위, grid 10 단위
- 데이터:
  - 은행: PER 6 (액센트 4 청 `#60a5fa`)
  - 유틸리티: PER 12 (액센트 4 청)
  - IT/플랫폼: PER 30 (액센트 1 녹 `#4ade80`)
  - 바이오: PER 45+ (액센트 3 황 `#f59e0b`)
- 막대 폭 약 80px, 간격 약 100px, 좌측에서 시작
- 각 막대 위에 PER 수치 라벨 (font-size 12, fill `#e2e8f0`)
- 캡션: "Source: KRX 2024년 평균 (예시)"

### business-cycle-1.svg — 4단계 사이클 + 섹터 로테이션

**디자인 사양**:
- 차트 종류: 원형 다이어그램 (4분할) + 각 분할 라벨
- 제목: "경기 사이클 4단계와 섹터 로테이션"
- 중심점 (400, 240), 반지름 140px
- 4 분할 (12시 방향부터 시계방향):
  - **상단 (12시)**: 회복 (Recovery) — 색 `#4ade80` — 섹터: 경기소비재, 산업재
  - **우 (3시)**: 확장 (Expansion) — 색 `#60a5fa` — 섹터: 기술, 금융
  - **하단 (6시)**: 둔화 (Slowdown) — 색 `#f59e0b` — 섹터: 에너지, 원자재
  - **좌 (9시)**: 침체 (Recession) — 색 `#ef4444` — 섹터: 필수소비재, 헬스케어, 유틸리티
- 각 분할 외곽에 단계명 + 대표 섹터 2~3개 라벨
- 중앙 4분할 사이에 시계방향 화살표 (arc)
- 캡션: "각 단계 평균 12~18 개월 지속"

- [ ] **Step 1: per-pbr-roe-1.svg 작성**

`api/static/edu/charts/per-pbr-roe-1.svg` 신설. 위 디자인 사양을 따라 SVG 손코딩. 헤더 표준 + 색상 팔레트 준수.

- [ ] **Step 2: business-cycle-1.svg 작성**

`api/static/edu/charts/business-cycle-1.svg` 신설. 위 디자인 사양을 따라 SVG 손코딩.

- [ ] **Step 3: 두 파일 SVG 유효성 검증**

Run: `python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/per-pbr-roe-1.svg', 'api/static/edu/charts/business-cycle-1.svg']]; print('OK')"`
Expected: `OK` (XML parse 성공)

- [ ] **Step 4: 커밋**

```bash
git add api/static/edu/charts/per-pbr-roe-1.svg api/static/edu/charts/business-cycle-1.svg
git commit -m "feat(edu-svg): basics 차트 2장 — 업종별 PER + 경기 사이클"
```

---

## Task 3: SVG 작성 — analysis 카테고리 (3장)

**Files:**
- Create: `api/static/edu/charts/chart-key-five-1.svg`
- Create: `api/static/edu/charts/chart-key-five-2.svg`
- Create: `api/static/edu/charts/momentum-investing-1.svg`

### chart-key-five-1.svg — 가격 + RSI 다이버전스 (2-pane)

**디자인 사양**:
- 차트 종류: 2-pane 라인 차트 (상단 가격, 하단 RSI)
- 제목: "RSI 다이버전스 — 가격 신고가, RSI 하락"
- 상단 pane (y=70~250): 가격 라인. 우상향 추세 후 마지막 부분 신고가. 색 `#4ade80` 라인 + 마지막 고점 마커 `#f59e0b`
- 하단 pane (y=270~390): RSI 라인. 같은 기간이지만 마지막 부분 *하락* (다이버전스). 색 `#60a5fa` 라인. 70/30 점선 가로선 (color `#94a3b8` opacity 0.5).
- X축 시간 라벨 (3-4개 점만, 예: "2024.05" "06" "07" "08")
- 양 pane 사이 30px 분리 + RSI pane 라벨 "RSI"
- 다이버전스 발생 지점에 빨간 점선 박스 + "약세 다이버전스" 라벨
- 캡션: "예시 — 삼성전자 2024.07 패턴"

### chart-key-five-2.svg — 볼린저밴드 스퀴즈 → 폭발

**디자인 사양**:
- 차트 종류: 라인 + 영역 (band fill)
- 제목: "볼린저밴드 스퀴즈 후 변동성 폭발"
- X축: 시간 (3개월), Y축: 가격
- 가격 라인 (`#e2e8f0`)
- 볼린저밴드 상하단 (`#60a5fa`, opacity 0.6) + band 영역 fill (`#60a5fa`, opacity 0.15)
- 좌반 (스퀴즈): band 폭 좁아짐, 가격 횡보
- 우반 (폭발): band 폭 확장, 가격 급등 (`#4ade80`)
- 폭발 시작 지점에 화살표 + "변동성 폭발" 라벨
- 캡션: "예시 — 에코프로 2023.04 패턴"

### momentum-investing-1.svg — 12-1 모멘텀 효과

**디자인 사양**:
- 차트 종류: 라인 차트 1개 (누적 수익률)
- 제목: "12-1 모멘텀 — 상위 10% vs 하위 10%"
- 부제: "12개월 수익률 상위 종목군의 6개월 후 누적 수익률"
- X축: 보유 개월 (0~6)
- Y축: 누적 수익률 (%), -10~+15
- 두 라인:
  - 상위 10% 모멘텀: 우상향 (`#4ade80`), 6개월 후 +12% 도달
  - 하위 10% 모멘텀: 우하향 후 횡보 (`#ef4444`), 6개월 후 -3%
- 각 라인 끝에 라벨
- 캡션: "Source: Jegadeesh-Titman (1993) 한국 시장 적용 예시"

- [ ] **Step 1~3: SVG 3장 작성**

각 파일을 디자인 사양에 따라 손코딩. 헤더·팔레트·구조 표준 준수.

- [ ] **Step 4: SVG 유효성 검증**

Run: `python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/chart-key-five-1.svg', 'api/static/edu/charts/chart-key-five-2.svg', 'api/static/edu/charts/momentum-investing-1.svg']]; print('OK')"`

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/chart-key-five-*.svg api/static/edu/charts/momentum-investing-1.svg
git commit -m "feat(edu-svg): analysis 차트 3장 — RSI 다이버전스·볼린저 스퀴즈·모멘텀 효과"
```

---

## Task 4: SVG 작성 — risk 카테고리 (4장)

**Files:**
- Create: `api/static/edu/charts/diversification-1.svg`
- Create: `api/static/edu/charts/risk-adjusted-return-1.svg`
- Create: `api/static/edu/charts/risk-adjusted-return-2.svg`
- Create: `api/static/edu/charts/correlation-trap-1.svg`

### diversification-1.svg — 종목 수 vs 분산 효과

**디자인 사양**:
- 차트 종류: 단일 라인 (감소 곡선)
- 제목: "분산 효과 — 종목 수가 늘어날수록 리스크 감소"
- X축: 종목 수 (1, 5, 10, 15, 20, 30, 50, 100)
- Y축: 포트폴리오 표준편차 (%) — 0~50
- 라인 데이터: 1=49.2, 5=23.5, 10=19.2, 15=17.0, 20=16.0, 30=15.2, 50=14.7, 100=14.5 (color `#4ade80`)
- 점근선 (체계적 리스크) 가로점선: 14% 위치, color `#94a3b8`, label "체계적 리스크 (분산 불가)"
- 10~15 종목 구간 강조 (배경 영역 fill `#4ade80` opacity 0.1) + 라벨 "최적 구간"
- 캡션: "Statman (1987) 한국 시장 적용 예시"

### risk-adjusted-return-1.svg — 동일 +15% 수익률·다른 변동성

**디자인 사양**:
- 차트 종류: 두 펀드의 일별 수익률 분포 (히스토그램 2개)
- 제목: "+15% 수익률, 다른 변동성"
- 부제: "A 펀드 σ=30% / B 펀드 σ=12%"
- 좌측 패널 (A 펀드, x=60~390): 폭넓은 종 모양 분포 (`#ef4444`, opacity 0.6) — 변동 -10~+12% 범위
- 우측 패널 (B 펀드, x=410~740): 좁은 종 모양 분포 (`#4ade80`, opacity 0.6) — 변동 -3~+4% 범위
- 각 패널 X축: 일별 수익률 (%), 0 위치 강조
- 각 패널 위에 sigma 표시
- 캡션: "같은 평균이라도 변동성이 다르면 위험이 다르다"

### risk-adjusted-return-2.svg — Sharpe·Sortino·MDD 시각 정의

**디자인 사양**:
- 차트 종류: 가격 시계열 라인 + 영역 강조
- 제목: "Sharpe·Sortino·MDD — 위험조정수익률 4지표"
- 가격 라인 (`#e2e8f0`) — 우상향 추세지만 중간 큰 낙폭 1회
- 직전 고점 → 최저점 영역에 빨간 점선 박스 + "MDD" 라벨
- Y축 우측에 Sharpe 화살표 (수익률/표준편차) + Sortino 화살표 (수익률/하락변동성)
- 우상단 표 (300×100 영역): Sharpe = 1.2, Sortino = 1.8, MDD = -38%, β = 0.9
- 캡션: "수익률만 보면 거짓말"

### correlation-trap-1.svg — 5×5 상관관계 행렬 히트맵

**디자인 사양**:
- 차트 종류: 5×5 색상 히트맵
- 제목: "한국 IT 5종목 상관관계 — 가짜 분산"
- 셀 라벨: 삼성전자, SK하이닉스, 카카오, 네이버, LG전자
- 셀 크기 60×60, 좌상단 (200, 90) 부터 격자
- 색상 매핑 (상관계수 0.0=진한 청 `#1e3a8a` → 1.0=진한 적 `#dc2626`, 중간 0.5=황 `#fbbf24`)
- 데이터 행렬 (대각선 = 1.0):
  ```
                삼성전자  SK하이닉스  카카오  네이버  LG전자
  삼성전자       1.00     0.85       0.62    0.65    0.78
  SK하이닉스    0.85     1.00       0.60    0.63    0.74
  카카오        0.62     0.60       1.00    0.82    0.55
  네이버        0.65     0.63       0.82    1.00    0.58
  LG전자        0.78     0.74       0.55    0.58    1.00
  ```
- 각 셀에 상관계수 수치 (font-size 11, fill 흑/백 자동)
- 캡션: "평균 ρ ≈ 0.69 → 사실상 1종목과 동일"

- [ ] **Step 1~4: SVG 4장 작성**

각 파일을 디자인 사양에 따라 손코딩.

- [ ] **Step 5: 유효성 검증**

Run: `python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/diversification-1.svg', 'api/static/edu/charts/risk-adjusted-return-1.svg', 'api/static/edu/charts/risk-adjusted-return-2.svg', 'api/static/edu/charts/correlation-trap-1.svg']]; print('OK')"`

- [ ] **Step 6: 커밋**

```bash
git add api/static/edu/charts/diversification-1.svg api/static/edu/charts/risk-adjusted-return-*.svg api/static/edu/charts/correlation-trap-1.svg
git commit -m "feat(edu-svg): risk 차트 4장 — 분산효과·위험조정수익·상관행렬"
```

---

## Task 5: SVG 작성 — macro 카테고리 (3장)

**Files:**
- Create: `api/static/edu/charts/interest-rates-1.svg`
- Create: `api/static/edu/charts/yield-curve-1.svg`
- Create: `api/static/edu/charts/yield-curve-2.svg`

### interest-rates-1.svg — 한국 기준금리·코스피 시계열

**디자인 사양**:
- 차트 종류: 이중 Y축 라인 차트
- 제목: "한국 기준금리·코스피 (2018~2024)"
- X축: 연도 (2018~2024 7개 라벨)
- 좌 Y축: 기준금리 (%), 0~4 범위, color `#f59e0b`
- 우 Y축: 코스피 지수 (천), 1.5~3.5 범위, color `#60a5fa`
- 기준금리 라인 데이터 (분기말): 2018Q1=1.5, 2018Q4=1.75, 2019Q4=1.25, 2020Q2=0.5, 2021Q4=1.0, 2022Q4=3.25, 2023Q4=3.5, 2024Q3=3.5
- 코스피 라인 데이터 (분기말, 천): 2018Q1=2.45, 2018Q4=2.04, 2019Q4=2.20, 2020Q1=1.75, 2020Q4=2.87, 2021Q4=2.97, 2022Q4=2.24, 2023Q4=2.65, 2024Q3=2.59
- 두 라인이 ↔ 역상관 패턴 보임 (금리 ↑ = 코스피 ↓ 시점 강조)
- 범례 우상단
- 캡션: "Source: 한국은행 / KRX"

### yield-curve-1.svg — 정상 금리 곡선

**디자인 사양**:
- 차트 종류: 단일 라인 + 점 (4개 만기점)
- 제목: "정상 금리 곡선 (Normal Yield Curve)"
- 부제: "장기 > 단기 — 기간 프리미엄"
- X축: 만기 (3M, 2Y, 10Y, 30Y) 균등 간격
- Y축: 금리 (%), 0~5 범위
- 곡선 데이터: 3M=2.0, 2Y=3.0, 10Y=4.0, 30Y=4.5 (우상향)
- 라인 색 `#4ade80`, 각 점 크기 6px, 점 옆 수치 라벨
- 곡선이 부드럽게 우상향 (Bezier 또는 직선 연결)
- 캡션: "건강한 경제 상황의 형태"

### yield-curve-2.svg — 역전 금리 곡선

**디자인 사양**:
- 차트 종류: 단일 라인 + 점 (4개 만기점) — yield-curve-1.svg 와 같은 형식
- 제목: "역전 금리 곡선 (Inverted Yield Curve)"
- 부제: "단기 > 장기 — 침체 12~18개월 선행 신호"
- X축: 만기 (3M, 2Y, 10Y, 30Y) 균등 간격
- Y축: 금리 (%), 0~6 범위
- 곡선 데이터: 3M=5.4, 2Y=4.8, 10Y=4.2, 30Y=4.4 (우하향, 30Y 살짝 반등)
- 라인 색 `#ef4444`, 각 점 크기 6px, 점 옆 수치 라벨
- 우상단 경고 라벨 박스: "⚠ 역전 발생 — 침체 시그널"
- 캡션: "예: 2022.07 미국 10Y-3M 역전"

- [ ] **Step 1~3: SVG 3장 작성**

- [ ] **Step 4: 유효성 검증**

Run: `python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/interest-rates-1.svg', 'api/static/edu/charts/yield-curve-1.svg', 'api/static/edu/charts/yield-curve-2.svg']]; print('OK')"`

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/interest-rates-1.svg api/static/edu/charts/yield-curve-*.svg
git commit -m "feat(edu-svg): macro 차트 3장 — 금리·코스피 + yield curve 정상/역전"
```

---

## Task 6: SVG 작성 — stories 카테고리 (3장)

**Files:**
- Create: `api/static/edu/charts/what-if-2015-1.svg`
- Create: `api/static/edu/charts/korea-market-timeline-1.svg`
- Create: `api/static/edu/charts/tesla-eight-years-1.svg`

### what-if-2015-1.svg — 10년 시뮬레이션

**디자인 사양**:
- 차트 종류: 라인 차트 3개 (각 종목 누적 수익률)
- 제목: "10년 전 그 종목을 샀다면 (2015~2025)"
- X축: 연도 (2015~2025 11개 라벨)
- Y축: 누적 수익률 (%), -50~+250 범위
- 3 라인:
  - 삼성전자 (`#4ade80`): +120%
  - 네이버 (`#60a5fa`): +80% (2021 +200% 정점 후 -50% 조정)
  - 카카오 (`#ef4444`): -10% (2021 +180% 정점 후 -70% 조정)
- 2021 정점에 마커 + "코로나 유동성 정점" 라벨
- 라인 끝에 종목명 + 누적 수익률 라벨
- 캡션: "배당·수수료 미반영, 가상 시뮬레이션"

### korea-market-timeline-1.svg — 25년 KOSPI 타임라인

**디자인 사양**:
- 차트 종류: 단일 라인 + 이벤트 마커
- 제목: "한국 증시 25년 (2000~2025)"
- X축: 연도 (2000, 2005, 2010, 2015, 2020, 2025)
- Y축: KOSPI 지수, 500~3500 범위
- 라인 색 `#60a5fa`, 데이터 (연말):
  - 2000=505, 2002=627, 2005=1379, 2007=1897, 2008=1124, 2010=2051, 2011=1825, 2015=1961, 2018=2041, 2020=2873, 2021=2977, 2022=2236, 2024=2400
- 이벤트 마커 (역삼각형 ▼):
  - 2000.04 IT버블 붕괴 (라벨 위)
  - 2008.10 글로벌 금융위기
  - 2011~2016 박스피 (영역 강조 박스 `#94a3b8` opacity 0.1)
  - 2020.03 코로나 폭락
  - 2023~2024 2차전지 부진
- 마커 색 `#f59e0b`, 라벨 폰트 작게 (10px)
- 캡션: "Source: KRX 연말 종가"

### tesla-eight-years-1.svg — 테슬라 8년 변동성

**디자인 사양**:
- 차트 종류: 라인 + 폭락 영역 강조
- 제목: "테슬라 2017~2024 — 분할 후 환산 가격"
- X축: 연도 (2017~2024 8개 라벨)
- Y축: 가격 ($), 30~1100 범위 (log scale 권장 — 손코딩 시 단순화 가능)
- 라인 색 `#e2e8f0`
- 데이터 (분할 후 환산, 연말):
  - 2017=63, 2018=66, 2019=83, 2020=705, 2021=1057, 2022=339, 2023=664, 2024=415
- 3대 폭락 영역 강조 (`#ef4444` opacity 0.15):
  - 2018: -50% (모델3 양산 위기)
  - 2022: -75% (이자율·X 인수)
  - 2024: -38% (EV 둔화)
- 각 폭락 위에 라벨
- 우상단 박스: "8년 누적 +540%"
- 캡션: "분할 5:1(2020) + 3:1(2022) 반영"

- [ ] **Step 1~3: SVG 3장 작성**

- [ ] **Step 4: 유효성 검증**

Run: `python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/what-if-2015-1.svg', 'api/static/edu/charts/korea-market-timeline-1.svg', 'api/static/edu/charts/tesla-eight-years-1.svg']]; print('OK')"`

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/what-if-2015-1.svg api/static/edu/charts/korea-market-timeline-1.svg api/static/edu/charts/tesla-eight-years-1.svg
git commit -m "feat(edu-svg): stories 차트 3장 — 10년 시뮬·KOSPI 타임라인·테슬라 8년"
```

---

## Task 7: SVG 작성 — tools 카테고리 (3장)

**Files:**
- Create: `api/static/edu/charts/factor-six-axes-1.svg`
- Create: `api/static/edu/charts/factor-six-axes-2.svg`
- Create: `api/static/edu/charts/market-regime-1.svg`

### factor-six-axes-1.svg — 6축 레이더 차트 (예시 종목)

**디자인 사양**:
- 차트 종류: 레이더 차트 (육각형, 6축)
- 제목: "정량 팩터 6축 — 예시 종목 percentile"
- 중심점 (400, 240), 반지름 140px
- 6축 (12시 시작, 시계방향 60도 간격):
  - r1m_pct (상)
  - r3m_pct (우상)
  - r6m_pct (우하)
  - r12m_pct (하)
  - vol60_pct (좌하)
  - volume_ratio (좌상)
- 동심 육각형 4개 (반지름 35, 70, 105, 140 → percentile 0.25, 0.5, 0.75, 1.0)
- 동심 육각형 라인 `#2d3748` opacity 0.5
- 데이터 (percentile, 0~1.0): r1m=0.45, r3m=0.62, r6m=0.91, r12m=0.55, vol60=0.65, volume_ratio=0.85
- 데이터 polygon `#4ade80`, fill opacity 0.3, stroke opacity 1.0
- 각 축 끝점에 라벨
- 캡션: "6개월 모멘텀 상위 9% / 거래량 폭증 → 모멘텀 진행 중"

### factor-six-axes-2.svg — 5가지 패턴 비교 (5개 미니 레이더)

**디자인 사양**:
- 차트 종류: 5개 미니 레이더 그리드 (1행 × 5열, 각 약 140×200)
- 제목: "팩터 6축 — 5가지 패턴 비교"
- 각 미니 레이더 중심: x=120, 240, 400, 560, 720 / y=200, 반지름 50
- 패턴별 polygon 색상:
  1. 모멘텀 종목 (`#4ade80`): r1m=r3m=r6m=0.85, vol60=0.7, volume_ratio=0.8
  2. 턴어라운드 (`#f59e0b`): r12m=0.15, r1m=0.75, volume_ratio=0.9
  3. 저변동 우량주 (`#60a5fa`): r12m=0.7, vol60=0.15, 나머지 중간
  4. 과열 경고 (`#ef4444`): r1m=0.97, r3m=0.96, vol60=0.95, 나머지 높음
  5. 거래 부재 (`#c084fc`): volume_ratio=0.08, 나머지 다양
- 각 패턴 아래 라벨 (font-size 11, fill `#e2e8f0`)
- 캡션: "패턴 매칭으로 신호 강도 판단"

### market-regime-1.svg — 4 레짐 시나리오 매트릭스

**디자인 사양**:
- 차트 종류: 2×2 매트릭스 (산점도 영역)
- 제목: "시장 레짐 — above_200ma × vol_regime"
- X축: vol_regime (low → high) 좌→우
- Y축: above_200ma (False → True) 하→상
- 4 사분면 (250×140 각각):
  - **우상 (True, low)**: Risk-On (`#4ade80` opacity 0.2 fill) — 라벨 "Risk-On (강세 확장)"
  - **우하 (False, low)**: Recovery (`#60a5fa` opacity 0.2) — "Recovery (반등 초기)"
  - **좌상 (True, high)**: Late-Cycle (`#f59e0b` opacity 0.2) — "Late-Cycle (피크)"
  - **좌하 (False, high)**: Bear (`#ef4444` opacity 0.2) — "Bear (약세)"
- 각 사분면 안에 시나리오 이름 + 권장 포지셔닝 (예: "공격적 비중", "현금 우선")
- 매트릭스 외곽 grid + 축 라벨
- 캡션: "본 시스템 v31 B2 레짐 레이어 분류"

- [ ] **Step 1~3: SVG 3장 작성**

- [ ] **Step 4: 유효성 검증**

Run: `python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/factor-six-axes-1.svg', 'api/static/edu/charts/factor-six-axes-2.svg', 'api/static/edu/charts/market-regime-1.svg']]; print('OK')"`

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/factor-six-axes-*.svg api/static/edu/charts/market-regime-1.svg
git commit -m "feat(edu-svg): tools 차트 3장 — 팩터 6축 레이더·패턴 비교·레짐 매트릭스"
```

---

## Task 8: 시드 모듈 markdown content 갱신

**Files:**
- Modify: `shared/db/migrations/seeds_education/basics.py` (per-pbr-roe, business-cycle)
- Modify: `shared/db/migrations/seeds_education/analysis.py` (chart-key-five, momentum-investing)
- Modify: `shared/db/migrations/seeds_education/risk.py` (diversification, risk-adjusted-return, correlation-trap)
- Modify: `shared/db/migrations/seeds_education/macro.py` (interest-rates, yield-curve-inversion)
- Modify: `shared/db/migrations/seeds_education/stories.py` (what-if-2015, korea-market-timeline, tesla-eight-years)
- Modify: `shared/db/migrations/seeds_education/tools.py` (factor-six-axes, market-regime-reading)

각 토픽의 `content` 필드에 markdown 이미지 참조 + caption 1~2줄 추가. 다른 필드(`title`/`summary`/`difficulty`/`sort_order`/`examples`) 변경 금지.

### 삽입 형식

```markdown
![차트: <한 줄 설명>](/static/edu/charts/<slug>-<n>.svg)

*<선택: caption 1~2줄>*
```

### 토픽별 삽입 위치 가이드

각 토픽의 *해당 시각화가 가장 핵심을 보여주는 섹션 직후* 에 이미지 참조 삽입.

| 토픽 (slug) | 삽입 위치 (섹션 헤더 기준) | 이미지 (n) |
|---|---|---|
| per-pbr-roe | "### 업종별 PER 기준이 다르다" 표 다음 | 1 |
| business-cycle | "## 경기 사이클 4단계" 첫 단락 다음 | 1 |
| chart-key-five | "## 2. RSI" 끝 / "## 4. 볼린저밴드" 끝 | 1 / 2 |
| momentum-investing | "## 12-1 효과" 또는 그에 해당하는 정의 섹션 끝 | 1 |
| diversification | "### 적정 종목 수" 표 다음 | 1 |
| risk-adjusted-return | "## 같은 수익률 = 같은 실력? 절대 아니다" 표 다음 / "## 4. 최대 낙폭 (MDD)" 섹션 끝 | 1 / 2 |
| correlation-trap | "### 한국 IT 5종목 포트폴리오" 다음 | 1 |
| interest-rates | "## 금리와 주가의 관계" 또는 본문 첫 단락 다음 | 1 |
| yield-curve-inversion | "### 정상 (Normal)" → ASCII art 제거 + 1.svg / "### 역전 (Inverted)" → ASCII art 제거 + 2.svg | 1 / 2 |
| what-if-2015 | "## 시뮬레이션 결과" 등 핵심 결과 섹션 첫 단락 다음 | 1 |
| korea-market-timeline | 본문 첫 단락 또는 "## 25년 키워드" 다음 | 1 |
| tesla-eight-years | "## 8년간 +1,500%, 그러나 길은 잔혹했다" 표 다음 | 1 |
| factor-six-axes | "## 본 시스템이 자동 계산하는 6축" 표 다음 / "## 6축 조합 패턴 5가지" 첫 단락 다음 | 1 / 2 |
| market-regime-reading | "## 4가지 레짐 시나리오" 첫 단락 다음 | 1 |

### 특별 주의: yield-curve-inversion ASCII art 제거

기존 content 안에 두 개의 ASCII art 박스 그림 (정상/역전 yield curve) 이 있다. 이를 **완전히 제거**하고 SVG 이미지 참조 두 개로 대체. Triple-backticks 코드 블록 자체를 삭제 (해당 라인부터 닫는 ` ``` ` 까지).

- [ ] **Step 1: basics.py 갱신** (per-pbr-roe, business-cycle 두 토픽 content 수정)

각 토픽의 content 안에서 상기 "삽입 위치" 가이드에 따라 이미지 마크다운 + caption 추가.

검증: `python -c "from shared.db.migrations.seeds_education import basics; assert all('/static/edu/charts/' in t['content'] for t in basics.TOPICS if t['slug'] in ('per-pbr-roe', 'business-cycle')); print('OK')"`

- [ ] **Step 2: analysis.py 갱신** (chart-key-five 2개, momentum-investing 1개)

검증: `python -c "from shared.db.migrations.seeds_education import analysis; t1 = next(t for t in analysis.TOPICS if t['slug'] == 'chart-key-five'); assert t1['content'].count('/static/edu/charts/') == 2, 'chart-key-five 이미지 2개 필요'; print('OK')"`

- [ ] **Step 3: risk.py 갱신** (diversification 1, risk-adjusted-return 2, correlation-trap 1)

검증: `python -c "from shared.db.migrations.seeds_education import risk; t = next(t for t in risk.TOPICS if t['slug'] == 'risk-adjusted-return'); assert t['content'].count('/static/edu/charts/') == 2; print('OK')"`

- [ ] **Step 4: macro.py 갱신** — yield-curve-inversion ASCII art 제거 + 두 SVG 참조 + interest-rates 1개

특별 검증: `python -c "from shared.db.migrations.seeds_education import macro; t = next(t for t in macro.TOPICS if t['slug'] == 'yield-curve-inversion'); assert '┤' not in t['content'], 'ASCII art 제거 안됨'; assert t['content'].count('/static/edu/charts/') == 2; print('OK')"`

- [ ] **Step 5: stories.py 갱신** (what-if-2015, korea-market-timeline, tesla-eight-years 각 1개)

- [ ] **Step 6: tools.py 갱신** (factor-six-axes 2, market-regime-reading 1)

- [ ] **Step 7: 통합 검증 — `test_v36_visual_topics_have_image_refs` 통과**

Run: `pytest tests/test_education_seeds.py::test_v36_visual_topics_have_image_refs -v`
Expected: PASS — 14 토픽 모두 이미지 참조 포함.

추가 검증: `pytest tests/test_education_seeds.py -v`
Expected: 모든 기존 테스트도 PASS (회귀 무).

- [ ] **Step 8: 커밋**

```bash
git add shared/db/migrations/seeds_education/*.py
git commit -m "feat(edu-svg): 시드 markdown 14 토픽에 SVG 이미지 참조 삽입"
```

---

## Task 9: v36 마이그레이션 추가 — UPDATE 패턴 신설

**Files:**
- Modify: `shared/db/schema.py`
- Modify: `shared/db/migrations/__init__.py`
- Modify: `shared/db/migrations/versions.py`

### 9.1 versions.py 에 `_migrate_to_v36` 함수 추가

`shared/db/migrations/versions.py` 파일 마지막(`_migrate_to_v35` 다음 또는 같은 영역) 에 다음 함수 추가:

```python
def _migrate_to_v36(cur) -> None:
    """Education 시각화 적용 — 14개 토픽 markdown content 갱신 (UPDATE 패턴 신설).

    v35까지의 모든 마이그레이션은 신규 row INSERT (ON CONFLICT DO NOTHING).
    본 v36은 본 시스템 첫 콘텐츠 갱신 마이그레이션 — 기존 row 의 content 를
    SVG 이미지 참조 포함 버전으로 UPDATE 한다.

    멱등성: WHERE content IS DISTINCT FROM 가드로 동일 content 재할당 시 no-op.
    신규 DB 의 경우 v21에서 ALL_TOPICS 전체가 이미 시각화 포함 버전으로 시드되었으므로
    v36 UPDATE 도 변화 없음 (멱등).

    대상 슬러그: spec doc 2026-04-26-education-svg-visualizations-design.md 참조.
    """
    from shared.db.migrations.seeds_education import ALL_TOPICS

    visual_slugs = {
        "per-pbr-roe", "business-cycle", "chart-key-five",
        "momentum-investing", "diversification", "risk-adjusted-return",
        "correlation-trap", "interest-rates", "yield-curve-inversion",
        "what-if-2015", "korea-market-timeline", "tesla-eight-years",
        "factor-six-axes", "market-regime-reading",
    }

    affected = 0
    for t in ALL_TOPICS:
        if t["slug"] not in visual_slugs:
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
        INSERT INTO schema_version (version) VALUES (36)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v36: 시각화 토픽 content 갱신 {affected}건 (대상 14건, 동일 content 는 no-op)")
```

### 9.2 __init__.py 의 `_MIGRATIONS` dict 에 36 추가

`shared/db/migrations/__init__.py` 의 `_MIGRATIONS` 딕셔너리에서 `35: _v._migrate_to_v35,` 라인 다음에 한 줄 추가:

```python
    35: _v._migrate_to_v35,
    36: _v._migrate_to_v36,
}
```

### 9.3 schema.py 의 `SCHEMA_VERSION` 증가

`shared/db/schema.py` line 12 의 `SCHEMA_VERSION = 35` 라인을 다음으로 교체:

```python
SCHEMA_VERSION = 36  # v36: education 14 토픽 시각화 SVG 참조 삽입 (UPDATE 패턴 신설)
```

- [ ] **Step 1: versions.py 에 `_migrate_to_v36` 함수 추가**

위 9.1 코드를 versions.py 끝에 추가. 함수 정의 위치는 v35 와 동일 영역 (모놀리식 함수 모음).

- [ ] **Step 2: __init__.py 갱신**

위 9.2 변경.

- [ ] **Step 3: schema.py 갱신**

위 9.3 변경.

- [ ] **Step 4: 동적 검증**

Run:
```bash
python -c "
from shared.db.schema import SCHEMA_VERSION
from shared.db.migrations import _MIGRATIONS
from shared.db.migrations.versions import _migrate_to_v36
print('SCHEMA_VERSION:', SCHEMA_VERSION)
print('36 in _MIGRATIONS:', 36 in _MIGRATIONS)
print('callable:', callable(_migrate_to_v36))
"
```
Expected: `SCHEMA_VERSION: 36`, `36 in _MIGRATIONS: True`, `callable: True`

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/versions.py shared/db/migrations/__init__.py shared/db/schema.py
git commit -m "feat(db): v36 마이그레이션 — education 시각화 content 갱신 (UPDATE 패턴 신설)"
```

---

## Task 10: 통합 검증 — 전체 pytest + (옵션) DB·UI 스모크

**Files:** (변경 없음 — 검증만)

- [ ] **Step 1: 전체 검증 테스트 실행**

Run: `pytest tests/test_education_seeds.py -v`
Expected: 13/13 PASS (기존 11 + Task 1 신규 2 모두).

특히 다음 두 테스트 명시 통과 확인:
- `test_svg_files_exist` — 18 SVG 파일 모두 디스크에 존재
- `test_v36_visual_topics_have_image_refs` — 14 토픽 모두 `/static/edu/charts/` 참조 포함

- [ ] **Step 2: 전 프로젝트 pytest 회귀 검증**

Run: `pytest`
Expected: 기존 모든 테스트 PASS (시각화 작업이 다른 영역 회귀 일으키지 않음).

- [ ] **Step 3: SVG 18장 XML 유효성 일괄 검증**

Run:
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
Expected: `Total SVG files: 18`, `All SVG files valid XML`.

- [ ] **Step 4: yield-curve-inversion ASCII art 제거 확인**

Run: `python -c "from shared.db.migrations.seeds_education import macro; t = next(t for t in macro.TOPICS if t['slug'] == 'yield-curve-inversion'); print('ASCII art removed:', '┤' not in t['content'] and '┴' not in t['content'])"`
Expected: `ASCII art removed: True`

- [ ] **Step 5 (옵션): DB 멱등성 시뮬레이션**

DB 환경 가용 시:
```bash
python -c "from shared.db.schema import init_db; from shared.config import DatabaseConfig; init_db(DatabaseConfig())"
```
2회 호출 후 row count 확인:
```bash
psql -d <DB_NAME> -c "SELECT COUNT(*) FROM education_topics WHERE content LIKE '%/static/edu/charts/%';"
```
Expected: 14

DB 환경 없으면 skip.

- [ ] **Step 6 (옵션): 정적 파일 서빙 스모크**

dev 서버 기동 후:
```bash
curl -I http://localhost:8000/static/edu/charts/per-pbr-roe-1.svg
```
Expected: `HTTP/1.1 200 OK` + `Content-Type: image/svg+xml`.

서버 미기동 시 skip.

- [ ] **Step 7 (옵션): UI 렌더 스모크**

브라우저로 `http://localhost:8000/pages/education/per-pbr-roe` 접속 → 차트 인라인 표시 확인. 모바일 viewport 380px 폭에서도 가독.

서버 미기동 시 skip.

- [ ] **Step 8: 최종 git 상태 검증**

Run:
```bash
git log --oneline -15
git status
```
Expected:
- Task 1~9 의 9개 commit + 본 Task 10 검증 작업은 *코드 변경 없음* (검증만) 이라 별도 commit 없음.
- working tree clean.

---

## Self-Review (Plan 작성 후 자체 점검)

### Spec coverage

- spec §2.1 차트 매핑 18장 → Task 2~7 의 카테고리별 분배에 모두 매핑 ✓
- spec §2.2 SVG 표준 (viewBox·색상·구조) → Plan 상단 "SVG 작성 표준" 섹션 + 각 task 디자인 사양에 명시 ✓
- spec §2.3 markdown 본문 갱신 형식 → Task 8 삽입 위치 가이드 + 검증 방법 ✓
- spec §3.1 SVG 정적 파일 18장 → Task 2~7 ✓
- spec §3.2 시드 모듈 6개 markdown content 수정 → Task 8 ✓
- spec §3.3 v36 마이그레이션 → Task 9 ✓
- spec §3.4 검증 테스트 보강 → Task 1 (TDD red phase 시작점) + Task 10 통합 검증 ✓
- spec §4 멱등성 → Task 9 의 `WHERE content IS DISTINCT FROM` 가드 ✓
- spec §5 검증 계획 → Task 10 모든 step ✓
- spec §6 작업 순서 → Task 1~10 에 매핑 ✓

### Placeholder scan

- "TBD" / "TODO" 없음 ✓
- 각 SVG 디자인 사양은 구체적 데이터 포인트·색상·라벨 포함 (작성자가 사양 기반 SVG 코드 생성 가능) ✓
- 마이그레이션 함수 코드 전체 명시 ✓
- 테스트 함수 코드 전체 명시 ✓
- 모든 step 에 실제 명령 또는 코드 ✓

### Type/이름 일관성

- `visual_slugs` 14개 set 이 Task 1 (test) / Task 9 (migration) 양쪽에서 동일 (14 slug) ✓
- SVG 파일명 명명 (`<slug>-<n>.svg`) 이 spec / plan / Task 모두에서 일치 ✓
- 색상 팔레트 HEX 값이 spec / plan 모두에서 동일 ✓
- viewBox `0 0 800 450` 일관성 ✓

### 잠재 위험 / 후속 작업 가능 항목

- SVG 손코딩 시 일관된 다크 테마 / 한글 라벨 / 모바일 가독성 확보가 작성자 (subagent) 역량에 의존. 첫 1~2장 완료 후 비주얼 점검 권장.
- 운영 DB 의 v36 UPDATE 는 *처음으로* 콘텐츠를 덮어쓴다. 사용자가 admin 페이지 등에서 토픽 content 를 직접 수정한 경우 그 변경분이 *덮어씌워진다*. 본 시스템에 admin content 편집 UI 가 없으므로 현재는 안전. 미래에 도입 시 재검토 필요.
- 16/18 차트가 한국어 라벨 — 글로벌 사용자 확장 시 i18n 도입 별도 task.
