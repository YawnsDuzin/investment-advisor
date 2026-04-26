# Education 토픽 시각화 Phase 2 설계

- **작성일**: 2026-04-26
- **상태**: 설계 확정 (구현 플랜 작성 대기)
- **범위**: 가치 중간 8개 토픽에 손코딩 SVG 차트 10장 추가 + markdown content 갱신 + v37 UPDATE 마이그레이션
- **전제**: Phase 1 (`2026-04-26-education-svg-visualizations-design.md`) 완료 — SVG 표준·v36 UPDATE 패턴 확립
- **방식**: Phase 1 패턴 답습. 단일 PR, 카테고리별 단계 commit.

## 1. 배경 / 동기

### Phase 1 결과
- 14 토픽 / 18 SVG 차트 추가 완료 (commit `87ee6ce` ~ `2b7ab6d`)
- v36 UPDATE 마이그레이션 패턴 정립 (`WHERE content IS DISTINCT FROM` 가드)
- 다크 테마 통일 팔레트 / viewBox 800×450 표준 확립

### Phase 2 대상
Phase 1에서 *가치 중간*으로 분류된 토픽 중 시각화 효과 큰 8개 선별:
1. 손실 비대칭성·포지션 사이징(risk) — 정량 곡선이 텍스트보다 강력
2. 외국인 수급·공매도 사례(analysis) — 시계열 차트가 본질
3. 환율·시나리오 사고(macro) — 시계열 + 의사결정 트리
4. 5대 폭락·심리 함정(stories) — 타임라인·인포그래픽

### 제외
- basics(공모주·증자·시총 등): 표·텍스트로 충분. Phase 2C 또는 생략
- practical(제안 카드·트랙레코드): UI 가이드 — 시스템 스크린샷이 더 적합 (별도 Phase)
- investor-legends: 인물 인포그래픽은 SVG 손코딩 부적합

## 2. 최종 구성

### 2.1 차트 매핑 (8 토픽 × 평균 1.25 = 10 차트)

| # | 카테고리 | 토픽 (slug) | 차트 파일명 | 차트 종류 / 주제 |
|---|---|---|---|---|
| 1 | risk | `stop-loss` | `stop-loss-1.svg` | 손실 비대칭성 곡선 — 손실률 vs 회복 필요 수익률 |
| 2 | risk | `position-sizing` | `position-sizing-1.svg` | 1% 룰 vs 몰빵 — 10번 연속 손실 시 자본 곡선 비교 |
| 3 | analysis | `foreign-institutional-flow` | `foreign-institutional-flow-1.svg` | 외국인 누적 순매수 + 코스피 시계열 (이중 Y축) |
| 4 | analysis | `short-selling-squeeze` | `short-selling-squeeze-1.svg` | GameStop 2021.01 — 가격 + 공매도 잔고 비율 시계열 |
| 5 | macro | `exchange-rates` | `exchange-rates-1.svg` | 원달러·코스피 역상관 시계열 (2018~2024) |
| 6 | macro | `scenario-thinking` | `scenario-thinking-1.svg` | base/worse/better 3분기 의사결정 트리 |
| 7 | stories | `legendary-crashes` | `legendary-crashes-1.svg` | 5대 폭락 타임라인 비교 (LTCM·리먼·차화정·2차전지·코로나) |
| 8 | stories | `behavioral-biases` | `behavioral-biases-1.svg` | 7가지 심리 함정 인포그래픽 매트릭스 |
| 9 | stories | `behavioral-biases` | `behavioral-biases-2.svg` | 처분효과 — 한국 개인 투자자 손익 보유기간 분포 |

총 **10 차트** (behavioral-biases 토픽 2장).

**중복 sanity check** — Phase 1과 겹치는 토픽 0건. visual_slugs Phase 1 (14) + Phase 2 (8) = **22 합산** disjoint.

### 2.2 SVG 작성 표준

Phase 1과 동일. viewBox 800×450, 다크 테마 팔레트(`#0f1419` / `#4ade80` / `#ef4444` / `#f59e0b` / `#60a5fa` / `#c084fc` 등), 외부 의존성 0.

**Phase 1 follow-up 반영**: off-palette 색상(`#dc2626`/`#fbbf24`/`#64748b`) 사용 자제. 부득이한 경우(예: 히트맵 그라데이션) 사용 후 spec 메모.

### 2.3 markdown 본문 갱신 형식

Phase 1과 동일 — 빈 줄 + `![alt](url)` + 빈 줄 + `*caption*` + 빈 줄.

## 3. 변경 영역

### 3.1 SVG 정적 파일 (신규 10장)

```
api/static/edu/charts/
├── stop-loss-1.svg                       (NEW)
├── position-sizing-1.svg                 (NEW)
├── foreign-institutional-flow-1.svg      (NEW)
├── short-selling-squeeze-1.svg           (NEW)
├── exchange-rates-1.svg                  (NEW)
├── scenario-thinking-1.svg               (NEW)
├── legendary-crashes-1.svg               (NEW)
├── behavioral-biases-1.svg               (NEW)
├── behavioral-biases-2.svg               (NEW)
└── (Phase 1의 18장 유지)
```

총 18 + 10 = **28장**.

### 3.2 시드 모듈 markdown content 수정

| 파일 | 수정 토픽 |
|---|---|
| `shared/db/migrations/seeds_education/risk.py` | stop-loss, position-sizing |
| `shared/db/migrations/seeds_education/analysis.py` | foreign-institutional-flow, short-selling-squeeze |
| `shared/db/migrations/seeds_education/macro.py` | exchange-rates, scenario-thinking |
| `shared/db/migrations/seeds_education/stories.py` | legendary-crashes, behavioral-biases |

basics·tools 모듈은 변경 없음.

### 3.3 v37 마이그레이션 — Phase 1 v36 패턴 그대로

| 파일 | 변경 |
|---|---|
| `shared/db/schema.py` | `SCHEMA_VERSION = 36` → `37` |
| `shared/db/migrations/__init__.py` | `_MIGRATIONS` dict에 `37: _v._migrate_to_v37,` 추가 |
| `shared/db/migrations/versions.py` | `_migrate_to_v37(cur)` 함수 신설 |

`_migrate_to_v37` 동작:
- Phase 2 8개 slug 리스트로부터 시드 ALL_TOPICS 의 최신 content lookup
- `UPDATE education_topics SET content = %s WHERE slug = %s AND content IS DISTINCT FROM %s`
- 영향받은 row 수 print
- v36과 동일 멱등성

### 3.4 검증 테스트 보강 (`tests/test_education_seeds.py`)

기존 두 테스트 (`test_v36_visual_topics_have_image_refs`, `test_svg_files_exist`) 의 visual_slugs / SVG 파일 목록을 Phase 2 합산으로 확장.

옵션 A (단순): 기존 테스트 두 개에 Phase 2 slug + 파일을 추가.
옵션 B (분리): Phase 1/Phase 2 분리 테스트 (`test_v37_phase2_visual_topics_have_image_refs` + 기존 테스트는 Phase 1 한정).

**선택**: 옵션 A. 단순화. 단, 테스트 함수명 의미를 유지하기 위해 합산 의미 명확화 — `test_visual_topics_have_image_refs` 로 rename 도 가능하지만 호출자 없음 → 그대로 두고 *내부 슬러그 셋만 22개로 확장*.

추가 테스트 1건:

```python
def test_v37_phase2_visual_topics_have_image_refs():
    """Phase 2 시각화 적용된 8개 슬러그의 content 에 SVG 이미지 참조 존재."""
    phase2_slugs = {
        "stop-loss", "position-sizing", "foreign-institutional-flow",
        "short-selling-squeeze", "exchange-rates", "scenario-thinking",
        "legendary-crashes", "behavioral-biases",
    }
    matched = [t for t in ALL_TOPICS if t["slug"] in phase2_slugs]
    assert len(matched) == 8
    for t in matched:
        assert "/static/edu/charts/" in t["content"], \
            f"{t['slug']} missing SVG image reference"
```

기존 `test_v36_visual_topics_have_image_refs` 는 그대로 유지 (Phase 1 14 슬러그 한정).
기존 `test_svg_files_exist` 의 expected 리스트에 10개 신규 파일 *추가*. 함수명 그대로.

### 3.5 변경 없음

- API 라우트, 템플릿, 정적 mount 변경 무 (Phase 1에서 이미 검증됨).

## 4. 멱등성 / 롤백

Phase 1 v36 패턴 그대로. v37 두 번 호출 시 두 번째는 `IS DISTINCT FROM` 가드로 no-op.

## 5. 검증 계획

| 항목 | 방법 |
|---|---|
| 28 SVG 파일 모두 디스크에 존재 | `pytest tests/test_education_seeds.py::test_svg_files_exist -v` |
| Phase 2 8 토픽 모두 이미지 참조 포함 | `pytest tests/test_education_seeds.py::test_v37_phase2_visual_topics_have_image_refs -v` |
| Phase 1 14 토픽 회귀 무 | `pytest tests/test_education_seeds.py::test_v36_visual_topics_have_image_refs -v` |
| 전체 검증 테스트 회귀 무 | `pytest tests/test_education_seeds.py -v` (전 PASS) |
| v37 마이그레이션 등록 | `python -c "from shared.db.schema import SCHEMA_VERSION; assert SCHEMA_VERSION == 37"` |

## 6. 작업 순서 (구현 플랜에서 분해)

1. 검증 테스트 보강 (test_svg_files_exist 확장 + test_v37_phase2 신설)
2. SVG 10장 작성 (카테고리별 4 task: risk 2 / analysis 2 / macro 2 / stories 3)
3. 시드 markdown 8 토픽 갱신
4. v37 마이그레이션
5. 통합 검증

총 **약 8~9 task** 예상.

## 7. Out of Scope (Phase 2C / Phase 3)

- basics 추가 토픽 (공모주·증자) → Phase 2C 후보
- practical UI 가이드 (시스템 스크린샷 SVG) → 별도 Phase
- 동적 차트 — Phase 3 제안 (사용자 보유 종목 실시간 레이더)
- investor-legends 인물 인포그래픽 — 손코딩 SVG 부적합

---

## 부록: Phase 2 차트 디자인 핵심 (작성자용 가이드)

### stop-loss-1: 손실 비대칭성

X축: 손실률(%) -10~-90, Y축: 회복 필요 수익률(%) 0~+1000
- 데이터: -10% → +11%, -20% → +25%, -30% → +43%, -50% → +100%, -70% → +233%, -90% → +900%
- 곡선 색 `#ef4444`, 강조 점 + 라벨
- -50% 위치 강조 (가장 흔한 임계점)

### position-sizing-1: 1% 룰 vs 몰빵

X축: 매매 횟수 1~10, Y축: 잔여 자본(%) 0~100
- 1% 룰 라인 (`#4ade80`): 99, 98, 97, ..., 90 (10번 연속 손실 시 ~90%)
- 5% 룰 라인 (`#f59e0b`): 95, 90, 85, ..., 60 (~60%)
- 몰빵 (50%) 라인 (`#ef4444`): 50, 25, 12, 6, 3, ... (~0.1%)
- 대비 강력

### foreign-institutional-flow-1

X축: 시간(2023.01~2024.06 분기), 좌Y축: 누적 순매수(억원), 우Y축: 코스피
- 외국인 누적 순매수 막대 (음/양 색)
- 코스피 라인 (`#60a5fa`)
- 동행 패턴 시각화

### short-selling-squeeze-1

GameStop 2021.01 케이스
- X축: 일자(2020.12~2021.02 일별)
- 가격 라인 (`#e2e8f0`) — 20$ → 483$ → 50$
- 공매도 잔고 비율 라인 (`#ef4444`) — 30%+ → 급감
- 1.27 최고점 마커 + 라벨 "숏스퀴즈 정점"

### exchange-rates-1

원달러·코스피 시계열 (2018~2024)
- 좌Y축: 원달러(원, 1100~1400)
- 우Y축: 코스피(천, 1.5~3.5)
- 역상관 패턴 (원달러 ↑ → 코스피 ↓ 시점 강조)

### scenario-thinking-1

3분기 의사결정 트리
- 좌측: "현 상황" 박스
- 우측 3개 분기: base (`#60a5fa`), worse (`#ef4444`), better (`#4ade80`)
- 각 분기 확률 + 예상 결과 라벨
- Fed 피벗 시나리오 사례

### legendary-crashes-1

5대 폭락 타임라인 (1998~2020)
- X축: 연도, Y축: 최대 낙폭 %
- 각 폭락 막대(또는 점):
  - LTCM 1998: -90%
  - 리먼 2008: -55%
  - 차화정 2011: -50%
  - 2차전지 2023: -40%
  - 코로나 2020: -34%
- 색상 `#ef4444`, 각 사건 라벨

### behavioral-biases-1

7가지 심리 함정 매트릭스 (3×3 그리드의 7 셀, 1셀은 정의)
- 각 셀: 함정 이름 + 1줄 설명
- FOMO, 손실회피, 확증편향, 처분효과, 닻 효과, 과신, 후회회피
- 셀 색상으로 영향력 차등

### behavioral-biases-2

처분효과 — 한국 개인 투자자 보유기간 분포
- X축: 보유 일수 (0~100)
- 두 분포: 수익 종목(평균 23일, `#4ade80`) vs 손실 종목(평균 45일, `#ef4444`)
- 처분효과 시각화
