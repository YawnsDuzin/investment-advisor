# Education 토픽 시각화 Phase 2C 설계 — basics 카테고리 보강

- **작성일**: 2026-04-26
- **상태**: 설계 확정 (구현 플랜 작성 대기)
- **범위**: basics 카테고리 7 토픽에 손코딩 SVG 차트 8장 추가 + markdown content 갱신 + v38 UPDATE 마이그레이션
- **전제**: Phase 1 (`v36`) + Phase 2 (`v37`) 완료 — SVG 표준·UPDATE 패턴 확립
- **방식**: Phase 1/2 패턴 답습. 단일 PR, 카테고리 단일이라 commit 분해는 차트 종류별로.

## 1. 배경 / 동기

### Phase 1+2 결과
- Phase 1: 14 토픽 / 18 SVG (v36, commit `87ee6ce` ~ `2b7ab6d`)
- Phase 2: 8 토픽 / 9 SVG (v37, commit `4775ee6` ~ `79d7fb8`)
- 누적 22 토픽 / 27 SVG / 다크 테마 통일 팔레트·viewBox 800×450 표준

### Phase 2C 대상
basics 카테고리에서 Phase 1이 다룬 2 토픽(per-pbr-roe·business-cycle) 외 남은 8 토픽 중 **시각화 가치 큰 7개** 선별:
1. `market-cap` — 시총 구간 대표 종목 비교
2. `financial-statements` — 3종 재무제표 관계
3. `eps-fcf-ebitda` — 3 이익 지표 비교
4. `orderbook-and-trading` — 호가창 구조
5. `tax-and-accounts` — 절세 계좌 세금 비교
6. `ipo-subscription` — 청약 일정 + 배정 방식 (2장)
7. `rights-bonus-split` — 증자·분할 비교

### 제외
- `dividend-yield` — 분포 차트 가능하지만 정의 위주, 시각화 가치 낮음
- `business-cycle` / `per-pbr-roe` — Phase 1 이미 처리

## 2. 최종 구성

### 2.1 차트 매핑 (7 토픽 / 8 차트)

| # | 토픽 (slug) | 파일명 | 차트 종류 / 주제 |
|---|---|---|---|
| 1 | `market-cap` | `market-cap-1.svg` | 시총 구간별 대표 종목 비교 (수평 막대) |
| 2 | `financial-statements` | `financial-statements-1.svg` | 3종 재무제표 관계 다이어그램 (BS / IS / CF flow) |
| 3 | `eps-fcf-ebitda` | `eps-fcf-ebitda-1.svg` | 3 이익 지표 비교 (한 종목 4분기 그룹 막대) |
| 4 | `orderbook-and-trading` | `orderbook-and-trading-1.svg` | 호가창 구조 (매수/매도 호가 ladder) |
| 5 | `tax-and-accounts` | `tax-and-accounts-1.svg` | ISA·연금저축·일반계좌 세금 비교 (그룹 막대) |
| 6 | `ipo-subscription` | `ipo-subscription-1.svg` | 청약 5단계 타임라인 |
| 7 | `ipo-subscription` | `ipo-subscription-2.svg` | 균등배정 vs 비례배정 결과 비교 |
| 8 | `rights-bonus-split` | `rights-bonus-split-1.svg` | 3축 비교 매트릭스 (자금 유입 / 주주 부담 / 가치 변화) |

총 **8 차트**.

**중복 sanity check**: Phase 1+2 22 슬러그 + Phase 2C 7 슬러그 = **29 누적** disjoint.

### 2.2 SVG 작성 표준

Phase 1/2와 동일. viewBox 800×450, 다크 팔레트(`#0f1419`/`#4ade80`/`#ef4444`/`#f59e0b`/`#60a5fa`/`#c084fc` 등), 외부 의존성 0, off-palette 색상 자제.

### 2.3 markdown 본문 갱신 형식

Phase 1/2와 동일 — 빈 줄 + `![alt](url)` + 빈 줄 + `*caption*` + 빈 줄.

## 3. 변경 영역

### 3.1 SVG 정적 파일 (신규 8장)

```
api/static/edu/charts/
├── market-cap-1.svg                   (NEW)
├── financial-statements-1.svg         (NEW)
├── eps-fcf-ebitda-1.svg               (NEW)
├── orderbook-and-trading-1.svg        (NEW)
├── tax-and-accounts-1.svg             (NEW)
├── ipo-subscription-1.svg             (NEW)
├── ipo-subscription-2.svg             (NEW)
├── rights-bonus-split-1.svg           (NEW)
└── (Phase 1+2의 27장 유지)
```

총 27 + 8 = **35장**.

### 3.2 시드 모듈 markdown content 수정

| 파일 | 수정 토픽 |
|---|---|
| `shared/db/migrations/seeds_education/basics.py` | market-cap, financial-statements, eps-fcf-ebitda, orderbook-and-trading, tax-and-accounts, ipo-subscription, rights-bonus-split |

basics.py 단일 모듈 변경.

### 3.3 v38 마이그레이션 — Phase 1/2 패턴 답습

| 파일 | 변경 |
|---|---|
| `shared/db/schema.py` | `SCHEMA_VERSION = 37` → `38` |
| `shared/db/migrations/__init__.py` | `_MIGRATIONS` dict에 `38: _v._migrate_to_v38,` |
| `shared/db/migrations/versions.py` | `_migrate_to_v38(cur)` 함수 신설 |

`_migrate_to_v38` 동작:
- Phase 2C 7 slug 리스트에서 시드 ALL_TOPICS의 최신 content lookup
- `UPDATE education_topics SET content = %s WHERE slug = %s AND content IS DISTINCT FROM %s`
- 영향 row 수 print
- v36/v37과 동일 멱등성

### 3.4 검증 테스트 보강 (`tests/test_education_seeds.py`)

- `test_svg_files_exist` expected 리스트에 Phase 2C 8개 SVG 추가 (총 35개)
- 신규 테스트 `test_v38_phase2c_visual_topics_have_image_refs` 추가

```python
def test_v38_phase2c_visual_topics_have_image_refs():
    """Phase 2C 시각화 적용된 7개 슬러그의 content 에 SVG 이미지 참조 존재."""
    phase2c_slugs = {
        "market-cap", "financial-statements", "eps-fcf-ebitda",
        "orderbook-and-trading", "tax-and-accounts",
        "ipo-subscription", "rights-bonus-split",
    }
    matched = [t for t in ALL_TOPICS if t["slug"] in phase2c_slugs]
    assert len(matched) == 7, f"expected 7 phase2c topics, found {len(matched)}"
    for t in matched:
        assert "/static/edu/charts/" in t["content"], \
            f"{t['slug']} missing SVG image reference"
```

기존 Phase 1+2 테스트 그대로 유지.

### 3.5 변경 없음

- API 라우트, 템플릿, 정적 mount 변경 무.

## 4. 멱등성 / 롤백

Phase 1/2 패턴 그대로. v38 두 번 호출 시 두 번째는 `IS DISTINCT FROM` 가드로 no-op.

## 5. 검증 계획

| 항목 | 방법 |
|---|---|
| 35 SVG 파일 모두 디스크에 존재 | `pytest tests/test_education_seeds.py::test_svg_files_exist -v` |
| Phase 2C 7 토픽 모두 이미지 참조 | `pytest tests/test_education_seeds.py::test_v38_phase2c_visual_topics_have_image_refs -v` |
| Phase 1+2 22 토픽 회귀 무 | 기존 두 테스트 PASS |
| 전체 검증 회귀 무 | `pytest tests/test_education_seeds.py -v` (15/15 PASS) |
| v38 등록 | `python -c "from shared.db.schema import SCHEMA_VERSION; assert SCHEMA_VERSION == 38"` |

## 6. 작업 순서

1. 검증 테스트 보강 (test_svg_files_exist 35개 + test_v38 신설)
2. SVG 8장 작성 (단일 task로 묶거나 차트 종류별로 2~3 task 분해)
3. basics.py 7 토픽 markdown 갱신 (단일 모듈)
4. v38 마이그레이션
5. 통합 검증

총 **5~7 task** 예상 (Phase 1/2보다 작음).

## 7. Out of Scope

- `dividend-yield` 시각화 — 별도 Phase
- practical 카테고리 UI 가이드 — 시스템 스크린샷 SVG (별도 작업)
- 동적 차트 (Phase 3 제안)
- investor-legends 인물 인포그래픽 — 손코딩 SVG 부적합

---

## 부록: Phase 2C 차트 디자인 핵심

### market-cap-1: 시총 구간별 대표 종목 (수평 막대)

5개 구간 × 1 대표 종목, 시총 (조원) 막대:
- 메가캡 (300조+): 삼성전자 (380조)
- 라지캡 (10~300조): SK하이닉스 (75조)
- 미드캡 (1~10조): 카카오게임즈 (4조)
- 스몰캡 (1천억~1조): 펄어비스 (5천억)
- 마이크로캡 (1천억 미만): 임의 종목 (500억)
- 색상 강도: 메가 → 마이크로 점진 약화 (`#4ade80` 진→연)

### financial-statements-1: 3종 관계 다이어그램

3개 박스 (BS, IS, CF) + 화살표:
- IS → CF: "당기순이익이 영업CF의 시작점"
- BS ↔ CF: "현금흐름이 BS 현금잔고에 누적"
- BS ↔ IS: "이익잉여금 (BS) ← 순이익 (IS)"
- 각 박스 안에 핵심 항목 3~4개

### eps-fcf-ebitda-1: 3 지표 비교 (그룹 막대)

한 종목(예: 삼성전자) 4분기 데이터:
- X축: 4분기 (Q1~Q4)
- 각 분기에 3개 막대 (EPS / FCF / EBITDA)
- 분기별 절대값 비교 + 어느 지표가 안정/변동 큰지 시각화

### orderbook-and-trading-1: 호가창 구조

좌측 5개 매도 호가 (위로 갈수록 높은 가격, 적색 그라데이션)
우측 5개 매수 호가 (아래로 갈수록 낮은 가격, 녹색 그라데이션)
중앙: 현재가 라인 + "체결 가능" 표시
각 호가에 수량 막대 (가격 라벨 옆에 가로 막대)

### tax-and-accounts-1: 절세 계좌 비교 (그룹 막대)

3개 계좌 × 2 항목 (5천만원 수익 시 세금):
- ISA: 0원 (한도 내) / 9.9% (초과분)
- 연금저축: 3.3% / 5.5% (해약 시 16.5%)
- 일반: 22% / 22%
- 색상: ISA 녹 / 연금 청 / 일반 적

### ipo-subscription-1: 청약 5단계 타임라인

좌→우 5단계 박스 + 화살표:
1. 수요예측 (D-21)
2. 청약 (D-7~D-5)
3. 환불 (D-3)
4. 상장 (D-Day)
5. 락업 해제 (D+30/90/180)
- 각 박스 색상 차등화

### ipo-subscription-2: 균등 vs 비례배정 비교

2개 패널:
- 좌: 균등배정 — 청약자 N명 × 각 1주
- 우: 비례배정 — 큰 자금 1명 × N주

### rights-bonus-split-1: 증자/분할 3축 비교

3 컬럼 × 3 행 매트릭스:
- 컬럼: 유상증자 / 무상증자 / 액면분할
- 행: 자금 유입 / 주주 부담 / 가치 변화
- 각 셀: ✓/✗ + 한 줄 설명
- 색상: 자금 유입(`#f59e0b` 황) / 주주 부담(`#ef4444` 적) / 가치 변화(중요도)
