# Education 토픽 시각화 (선별 SVG) 설계 — Phase 1

- **작성일**: 2026-04-26
- **상태**: 설계 확정 (구현 플랜 작성 대기)
- **범위**: 가치 ↑↑ 14개 토픽에 손코딩 SVG 차트 약 20장 추가 + markdown content 갱신 + v36 UPDATE 마이그레이션
- **방식**: 일괄 작업(단일 PR), 카테고리별 단계 commit. 새 라이브러리·마이그레이션 패턴 도입 1건 (UPDATE).

## 1. 배경 / 동기

### 현재 상태
- v35로 education_topics 40 토픽 / 7 카테고리 확장 완료
- 모든 토픽 본문이 **순수 markdown 텍스트** — 표·인용·리스트만 사용. 시각화 0건 (예외: yield-curve-inversion 의 ASCII art 박스 그림 1건)
- Education 메뉴 UI: `api/templates/education_topic.html`이 markdown → HTML 렌더링 (`marked` 또는 유사) — 이미지 태그 자동 렌더 가능

### 문제
1. **차트 본질 토픽이 텍스트로**: `chart-key-five`(이격도/RSI/MACD/볼린저), `factor-six-axes`(6축 레이더), `correlation-trap`(상관행렬) 등은 그림 없이는 학습 효과 절반 이하.
2. **시계열 비교 부재**: `interest-rates`(금리·주가), `tesla-eight-years`, `korea-market-timeline` — 시간축 시각화가 없으면 패턴 인지 불가.
3. **다이어그램 부재**: `business-cycle`(4단계 사이클), `yield-curve-inversion`(정상 vs 역전) — 구조도가 핵심 학습 포인트.
4. **모바일 가독성**: ASCII art 박스 그림은 폰트·줄바꿈 깨짐 (yield-curve 토픽이 그 예).

### 목표
- **선별** 14 토픽에만 SVG 추가 (모든 토픽이 시각화가 필요한 건 아님 — 정의·세금·법규 같은 텍스트 본질 토픽은 제외)
- **손코딩 SVG 단일 표준** — matplotlib/Chart.js/Mermaid 등 라이브러리 도입 0건
- **콘텐츠 갱신 마이그레이션 패턴** v36 신설 — 기존 운영 DB의 markdown content 도 UPDATE로 동기화

## 2. 최종 구성

### 2.1 차트 매핑 (14 토픽 × 평균 1.5장 = 20장)

| # | 카테고리 | 토픽 (slug) | 차트 파일명 | 차트 종류 / 주제 |
|---|---|---|---|---|
| 1 | basics | `per-pbr-roe` | `per-pbr-roe-1.svg` | 업종별 PER 분포 (수직 막대) — 은행/IT/바이오/유틸 |
| 2 | basics | `business-cycle` | `business-cycle-1.svg` | 4단계 사이클 원형 다이어그램 + 섹터 로테이션 라벨 |
| 3 | analysis | `chart-key-five` | `chart-key-five-1.svg` | 가격 + RSI 다이버전스 (2-pane 라인) |
| 4 | analysis | `chart-key-five` | `chart-key-five-2.svg` | 볼린저밴드 스퀴즈 → 폭발 (라인 + 밴드 영역) |
| 5 | analysis | `momentum-investing` | `momentum-investing-1.svg` | 12-1 모멘텀 효과 (월별 누적 수익률 곡선) |
| 6 | risk | `diversification` | `diversification-1.svg` | 종목 수 vs 분산 효과 곡선 (포트폴리오 변동성 감소) |
| 7 | risk | `risk-adjusted-return` | `risk-adjusted-return-1.svg` | 동일 +15% 수익률 두 펀드의 변동성 분포 비교 (히스토그램 2개) |
| 8 | risk | `risk-adjusted-return` | `risk-adjusted-return-2.svg` | Sharpe·Sortino·MDD 시각 정의 (라인 차트 + 영역 강조) |
| 9 | risk | `correlation-trap` | `correlation-trap-1.svg` | 5×5 상관관계 행렬 히트맵 (한국 IT 5종목 가짜 분산 케이스) |
| 10 | macro | `interest-rates` | `interest-rates-1.svg` | 한국 기준금리·코스피 시계열 (이중 Y축, 2018~2024) |
| 11 | macro | `yield-curve-inversion` | `yield-curve-1.svg` | 정상 곡선 (3M/2Y/10Y/30Y 점·곡선) |
| 12 | macro | `yield-curve-inversion` | `yield-curve-2.svg` | 역전 곡선 (단기 > 장기) — 기존 ASCII art 대체 |
| 13 | stories | `what-if-2015` | `what-if-2015-1.svg` | 10년 시뮬레이션 라인 (삼성전자/네이버/카카오 누적 수익률) |
| 14 | stories | `korea-market-timeline` | `korea-market-timeline-1.svg` | 25년 KOSPI 라인 + 주요 이벤트 마커 (IT버블·서브프라임·코로나·2차전지) |
| 15 | stories | `tesla-eight-years` | `tesla-eight-years-1.svg` | 2017~2024 분할 후 환산 가격 + 3대 폭락 마커 |
| 16 | tools | `factor-six-axes` | `factor-six-axes-1.svg` | 6축 레이더 차트 (예시 종목 percentile) |
| 17 | tools | `factor-six-axes` | `factor-six-axes-2.svg` | 5가지 패턴 비교 (5개 미니 레이더 그리드) |
| 18 | tools | `market-regime-reading` | `market-regime-1.svg` | 4 레짐 시나리오 (above_200ma × vol_regime 2×2 매트릭스) |

총 **18장** (사양 ±2 장 — 작업 시 추가/축소 가능).

### 2.2 SVG 작성 표준

**필수 사항**:
- viewBox: `0 0 800 450` (16:9, 모바일 폭 380px 미만에서도 가독)
- font-family: `system-ui, -apple-system, sans-serif`
- 한글 라벨 inline 사용 OK (모든 한글 폰트는 시스템 폰트로)
- 외부 의존성 0 (CSS·JS·외부 폰트 import 금지)
- xmlns 속성: `xmlns="http://www.w3.org/2000/svg"`
- 인코딩: UTF-8

**색상 팔레트** (다크 테마 기본):
```
배경        #0f1419
보조 배경   #1a1f2e
그리드선    #2d3748 (얇게, opacity 0.5)
주요 텍스트 #e2e8f0
보조 텍스트 #94a3b8
액센트 1 (녹) #4ade80   ← 상승·긍정
액센트 2 (적) #ef4444   ← 하락·부정
액센트 3 (황) #f59e0b   ← 강조·경고
액센트 4 (청) #60a5fa   ← 정보·중립
액센트 5 (보) #c084fc   ← 보조 시리즈
```

**구조 표준**:
- 상단 약 60px: 제목 + 부제 (있으면)
- 하단 약 30px: 출처/기간 라벨 (있으면)
- 좌우 여백 60px, 상하 여백 40px → 차트 영역 약 680×340

**축·범례**:
- X축·Y축 라벨 항상 표시
- 범례는 우상단 또는 차트 내부 라벨링
- 격자선은 보조용으로만, 강조 X

### 2.3 markdown 본문 갱신 형식

기존 토픽 content 안에 markdown 이미지 참조 삽입:

```markdown
## 기존 섹션 헤더

기존 본문 텍스트…

![짧은 설명 — 차트 종류](/static/edu/charts/<slug>-<n>.svg)

기존 본문 계속…
```

이미지 위치는 *해당 개념 직후* 섹션. 이미지 다음에 1~2줄 caption (markdown 본문 안에서 텍스트로) 권장.

## 3. 변경 영역

### 3.1 SVG 정적 파일 (신규 18장)

```
api/static/edu/charts/         ← 신규 디렉토리
├── per-pbr-roe-1.svg
├── business-cycle-1.svg
├── chart-key-five-1.svg
├── chart-key-five-2.svg
├── momentum-investing-1.svg
├── diversification-1.svg
├── risk-adjusted-return-1.svg
├── risk-adjusted-return-2.svg
├── correlation-trap-1.svg
├── interest-rates-1.svg
├── yield-curve-1.svg
├── yield-curve-2.svg
├── what-if-2015-1.svg
├── korea-market-timeline-1.svg
├── tesla-eight-years-1.svg
├── factor-six-axes-1.svg
├── factor-six-axes-2.svg
└── market-regime-1.svg
```

### 3.2 시드 모듈 markdown content 수정

해당 토픽이 있는 카테고리 모듈에서 `content` 문자열만 수정 (기타 필드 무변경):

| 파일 | 수정 토픽 |
|---|---|
| `shared/db/migrations/seeds_education/basics.py` | per-pbr-roe, business-cycle |
| `shared/db/migrations/seeds_education/analysis.py` | chart-key-five, momentum-investing |
| `shared/db/migrations/seeds_education/risk.py` | diversification, risk-adjusted-return, correlation-trap |
| `shared/db/migrations/seeds_education/macro.py` | interest-rates, yield-curve-inversion (ASCII art 제거) |
| `shared/db/migrations/seeds_education/stories.py` | what-if-2015, korea-market-timeline, tesla-eight-years |
| `shared/db/migrations/seeds_education/tools.py` | factor-six-axes, market-regime-reading |

### 3.3 v36 마이그레이션 — UPDATE 패턴 신설

| 파일 | 변경 |
|---|---|
| `shared/db/schema.py` | `SCHEMA_VERSION = 35` → `36` |
| `shared/db/migrations/__init__.py` | `_MIGRATIONS` dict에 `36: _v._migrate_to_v36,` 추가 |
| `shared/db/migrations/versions.py` | `_migrate_to_v36(cur)` 함수 신설 |

`_migrate_to_v36` 동작:
- 시각화 적용된 14개 slug 리스트로부터 각 토픽의 *최신 시드 content* 를 ALL_TOPICS 에서 lookup
- `UPDATE education_topics SET content = %s WHERE slug = %s` 로 갱신
- 실행 결과: 영향받은 row 수 print
- 멱등성: 같은 content 재할당이라 재실행 안전 (또는 `WHERE slug = %s AND content != %s` 로 no-op 가드)

### 3.4 검증 테스트 보강 (`tests/test_education_seeds.py`)

추가 테스트:

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

기존 `test_content_min_length` 조정 불요 — 시각화 토픽 모두 V35 신규 토픽이거나 V21/V24 기존 토픽이며, content 길이는 *늘어나기만* 한다.

### 3.5 변경 없음 (검증)

- `api/main.py` `/static` mount 이미 존재 — 변경 무
- `api/routes/education.py` markdown → HTML 렌더 로직 — 변경 무 (markdown 이미지 태그가 자동 처리됨)
- `api/templates/education_topic.html` — 기본 markdown 렌더로 충분, CSS 미세 조정 불필요 (SVG가 viewBox 기반이라 반응형)

## 4. 멱등성 / 롤백

### 멱등성
- **신규 DB**: v21에서 `_seed_education_topics(ALL_TOPICS)` → 시드된 content 가 이미 시각화 포함 버전 → v36 UPDATE는 *content 변화 없음* (동일 문자열) → no-op
- **기존 운영 DB (v35)**: v36 실행 시 14개 row 의 content 가 시각화 포함 버전으로 UPDATE → 한 번만 변경 → 재실행 시 no-op
- 재실행 안전: `WHERE slug = %s AND content IS DISTINCT FROM %s` 가드로 *content 가 다를 때만* UPDATE

### 롤백
- DB 단위: `git revert` 후 v37 마이그레이션에서 *이전 버전 content* 를 다시 UPDATE — 별도 마이그레이션 필요
- 빠른 임시 우회: nginx/static 레벨에서 SVG 파일 404 응답 → markdown 이 alt text 만 표시 (다소 어색하지만 기능 유지)

## 5. 검증 계획

| 항목 | 방법 |
|---|---|
| 새 SVG 파일 18장 생성 확인 | `pytest tests/test_education_seeds.py::test_svg_files_exist -v` |
| 시각화 토픽 content 안에 이미지 참조 존재 | `pytest tests/test_education_seeds.py::test_v36_visual_topics_have_image_refs -v` |
| 기존 검증 테스트 11개 회귀 무 | `pytest tests/test_education_seeds.py -v` (전 테스트 PASS) |
| 신규 DB 시드 → 14 토픽 모두 이미지 참조 포함 | DB 환경 가용 시 `init_db()` 후 SELECT |
| 기존 v35 DB → v36 UPDATE → 14 row 영향 | DB 환경 가용 시 `init_db()` 호출 후 row count 확인 |
| 정적 파일 서빙 | `curl http://localhost:8000/static/edu/charts/per-pbr-roe-1.svg` → SVG XML 응답 |
| UI 렌더링 (옵션) | 브라우저로 `/pages/education/per-pbr-roe` → 차트 인라인 표시 |

## 6. 작업 순서 (구현 플랜에서 분해)

1. **SVG 18장 작성** — 가장 시간 소요. 다이어그램/시계열/히트맵/레이더 등 차트 종류별로 묶어서 작업
2. **시드 markdown content 수정** — 카테고리별 6개 모듈에 이미지 참조 삽입
3. **v36 마이그레이션 추가** — UPDATE 패턴 신설 (3 파일)
4. **검증 테스트 2건 추가** — `test_svg_files_exist`, `test_v36_visual_topics_have_image_refs`
5. **회귀 검증** — pytest 전체 + 정적 파일 서빙 + (옵션) UI 스모크
6. **커밋** — 카테고리별 분리 (e.g., `feat(edu-svg): risk 카테고리 차트 4장 추가`)

## 7. Out of Scope (Phase 2/3)

- **Phase 2**: 가치 중간 8~10 토픽 (공모주, 손절·익절, 외국인 수급 등) 차트 추가
- **Phase 3**: *동적* 차트 — `factor_snapshot` 실측값으로 사용자 보유 종목 레이더 차트 *실시간* 생성. `tools/render_edu_charts.py` 빌드 도구 도입
- 정적 SVG → JSON 메타데이터 분리 (slug 와 캡션 매핑) — 일단 markdown inline 으로 충분
- Mermaid·Chart.js 도입 (라이브러리 디펜던시) — 지속적으로 거부
- 차트 다국어 (영문 라벨 버전) — 한국어 단일

---

## 부록: SVG 작성 가이드 (작성자용)

### 다이어그램 vs 데이터 차트 구분

- **다이어그램** (사이클·구조·관계): 손코딩 도형 + 텍스트 라벨. 정확한 좌표 직접 명시.
  - 예: `business-cycle-1`, `yield-curve-1/2`, `market-regime-1`
- **시계열·라인 차트**: SVG path `d="M x1 y1 L x2 y2 …"` 로 점 좌표 연결.
  - 예: `interest-rates-1`, `tesla-eight-years-1`, `korea-market-timeline-1`
- **막대·박스플롯**: SVG rect 요소로 막대.
  - 예: `per-pbr-roe-1`, `risk-adjusted-return-1`
- **레이더 차트**: 6개 축 polygon (점 좌표 직접 계산 — 정육각형 = `(r·cos θ, r·sin θ)` for θ in 0,60,120,…)
  - 예: `factor-six-axes-1`, `factor-six-axes-2`
- **히트맵**: 5×5 rect 그리드 + 색상 보간 (HSL 또는 RGB 선형보간).
  - 예: `correlation-trap-1`

### 작성 톤

- 한글 라벨 권장 (한국 사용자 우선)
- 데이터 출처 표시 (e.g., "한국은행 / 한국거래소 (2018-2024)")
- 정확한 수치보다 *패턴 인식* 우선 — Y축 단위 표시는 필수지만 모든 grid line 라벨링 불필요
- 현실 데이터 가능한 한 사용 (예: 삼성전자 사례, KOSPI 실측). 가상 예시는 명시적 라벨

### SVG 검증 체크리스트

- [ ] viewBox 정확
- [ ] 외부 폰트 import 없음
- [ ] 인라인 CSS 또는 `<style>` 가능 (외부 CSS 링크 X)
- [ ] 한글 텍스트 깨짐 없음 (UTF-8 저장 확인)
- [ ] 흑백 모드에서도 패턴 식별 가능 (색상 단독 의존 X — 위치·크기·라벨로 보강)
- [ ] 모바일 380px 폭에서 가독 (svg `width="100%"` 또는 max-width 적용 시)
